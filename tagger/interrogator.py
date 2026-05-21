# tagger/interrogator.py - ONNX inference backends.
#
# Differences vs. 2_tagger/backend/interrogator.py:
#   * interrogate_batch / interrogate return 3-tuples (ratings, tags, meta).
#     meta carries Camie's year/meta/artist categories so they don't collide with the 4-way rating dict.
#     Existing WD / Custom / DeepDanbooru subclasses return an empty meta dict.
#   * New CamieTaggerInterrogator: dual-output (initial / refined), sigmoid on logits,
#     ImageNet normalization, 70k-tag pre-threshold filter to keep postprocess cheap.

import os
import gc
import csv
import json
import math
import numpy as np
from typing import Tuple, List, Dict, Optional, Set
from PIL import Image
from pathlib import Path
from huggingface_hub import hf_hub_download

from tagger.config import config, re_special as tag_escape_pattern
from tagger import dbimutils


def _format_hf_error(repo_id: str, exc: BaseException) -> str:
    """把 hf_hub_download 抛出来的各种异常翻成人话 + 给出可执行建议。

    判断顺序按「最具体 → 最笼统」：
    1. RepositoryNotFoundError / 404 → 仓库写错或被删
    2. 401/403/gated                  → 需要授权
    3. 磁盘空间                       → ENOSPC
    4. 真·网络故障                    → 连接 / 超时 / DNS
    5. 其它                           → 原文回显

    顺序很关键：网络层的 HTTPError 异常名也叫 HTTPError，但实际是 404；
    如果先匹配网络分支会把 404 误归为"国内访问 hf 不通"。
    """
    msg = str(exc)
    name = type(exc).__name__
    msg_lower = msg.lower()

    # 1) 仓库不存在（最容易让人误以为是网络问题）
    if "RepositoryNotFound" in name or "EntryNotFound" in name or \
            "repository not found" in msg_lower or "entry not found" in msg_lower:
        return (
            f"下载 {repo_id} 失败：仓库或文件不存在\n"
            f"原始错误: {name}: {msg[:250]}\n"
            f"建议：核对模型 id 是否写错；或检查 HuggingFace 上该 repo 是否仍然公开"
        )

    # 2) 授权 / 限制
    if "GatedRepoError" in name or "gated" in msg_lower or \
            "403" in msg or "Forbidden" in msg or \
            ("401" in msg and "RepositoryNotFound" not in name):
        return (
            f"下载 {repo_id} 失败：仓库需要授权或已被限制访问\n"
            f"原始错误: {name}: {msg[:250]}\n"
            f"建议：在 HuggingFace 网站上接受该模型的使用条款，"
            f"并设置 HF_TOKEN 环境变量后重启 server"
        )

    # 3) 磁盘
    if "No space left" in msg or "ENOSPC" in msg:
        return f"下载 {repo_id} 失败：磁盘空间不足（{msg[:200]}）"

    # 4) 网络（放在最后；只有名字明确是连接/超时类才算）
    net_signals = (
        "ConnectionError", "ReadTimeout", "ConnectTimeout", "SSLError",
        "RemoteDisconnected", "ProtocolError", "NewConnectionError",
        "MaxRetryError", "URLError", "ConnectionResetError",
    )
    is_network = any(s in name for s in net_signals) or any(
        kw in msg for kw in (
            "Max retries exceeded", "Failed to establish a new connection",
            "getaddrinfo failed", "EOF occurred",
            "[Errno 11001]", "Name or service not known",
            "TimeoutError", "Network is unreachable",
        )
    )
    if is_network:
        return (
            f"下载 {repo_id} 失败：网络无法连接 HuggingFace Hub\n"
            f"原始错误: {name}: {msg[:200]}\n"
            f"建议：\n"
            f"  1) 国内常用镜像——在启动 server 前设置环境变量再重启：\n"
            f"     PowerShell:  $env:HF_ENDPOINT='https://hf-mirror.com'; python server.py\n"
            f"     CMD:         set HF_ENDPOINT=https://hf-mirror.com && python server.py\n"
            f"     Bash:        HF_ENDPOINT=https://hf-mirror.com python server.py\n"
            f"  2) 或者手动下载 model.onnx + metadata.json 放到\n"
            f"     tagger/models/{repo_id.replace('/', '_')}/ 下"
        )

    # 5) 兜底
    return f"下载 {repo_id} 失败：{name}: {msg[:300]}"

# ============== ONNX Runtime 配置缓存（与原版一致）==============
_ort_initialized = False
_ort_session_options = None
_ort_providers = None
_ort_provider_name = "Unknown"

tf = None
tf_device_name = '/cpu:0'
_tf_initialized = False


def _init_onnx_runtime():
    global _ort_initialized, _ort_session_options, _ort_providers, _ort_provider_name
    if _ort_initialized:
        return _ort_session_options, _ort_providers

    import onnxruntime as ort
    print(f"[tagger] ONNX Runtime version: {ort.__version__}")
    print(f"[tagger] Available providers: {ort.get_available_providers()}")

    _ort_session_options = ort.SessionOptions()
    # 图优化级别：默认 ORT_ENABLE_ALL（最高），但对 Camie v2 这种 788 MB transformer
    # 在 CPU 上首次加载会触发 5-10 分钟的 fusion + layout pass，体感是死锁。
    # 提供环境变量出口，让卡在加载阶段的用户可以降级：
    #   TAGGER_ORT_OPT_LEVEL=all       (默认，运行时最快、加载最慢)
    #   TAGGER_ORT_OPT_LEVEL=extended  (中间，去掉 layout pass，加载快很多)
    #   TAGGER_ORT_OPT_LEVEL=basic     (最低，几乎瞬时加载、推理略慢)
    #   TAGGER_ORT_OPT_LEVEL=disable   (调试用，禁所有优化)
    _opt_level_map = {
        "all":      ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
        "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
        "basic":    ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
        "disable":  ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
    }
    _opt_choice = os.environ.get("TAGGER_ORT_OPT_LEVEL", "all").lower()
    _ort_session_options.graph_optimization_level = _opt_level_map.get(
        _opt_choice, ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
    print(f"[tagger] ONNX graph_optimization_level: {_opt_choice} "
          f"(env TAGGER_ORT_OPT_LEVEL 可改 all/extended/basic/disable)")

    cpu_count = os.cpu_count() or 4
    _ort_session_options.intra_op_num_threads = max(1, cpu_count // 2)
    _ort_session_options.inter_op_num_threads = max(1, cpu_count // 2)
    _ort_session_options.enable_mem_pattern = True
    _ort_session_options.enable_mem_reuse = True
    _ort_session_options.enable_cpu_mem_arena = True

    available = ort.get_available_providers()
    if config.use_cpu:
        _ort_providers = ['CPUExecutionProvider']
        _ort_provider_name = "CPU (forced)"
    elif 'CUDAExecutionProvider' in available:
        device_id = config.device_id if config.device_id is not None else 0
        _ort_providers = [
            ('CUDAExecutionProvider', {
                'device_id': device_id,
                'arena_extend_strategy': 'kSameAsRequested',
                'gpu_mem_limit': 4 * 1024 * 1024 * 1024,
                'cudnn_conv_algo_search': 'HEURISTIC',
                'do_copy_in_default_stream': True,
            }),
            'CPUExecutionProvider',
        ]
        _ort_provider_name = f"CUDA (GPU:{device_id})"
    elif 'DmlExecutionProvider' in available:
        device_id = config.device_id if config.device_id is not None else 0
        _ort_providers = [('DmlExecutionProvider', {'device_id': device_id}), 'CPUExecutionProvider']
        _ort_provider_name = "DirectML (GPU)"
    elif 'CoreMLExecutionProvider' in available:
        _ort_providers = ['CoreMLExecutionProvider', 'CPUExecutionProvider']
        _ort_provider_name = "CoreML (Apple)"
    else:
        _ort_providers = ['CPUExecutionProvider']
        _ort_provider_name = "CPU (no GPU available)"

    print(f"[tagger] ★ Using provider: {_ort_provider_name}")
    _ort_initialized = True
    return _ort_session_options, _ort_providers


def create_onnx_session(model_path: str):
    """创建 ONNX Runtime InferenceSession。

    踩坑历史：默认 graph_optimization_level=ORT_ENABLE_ALL 对 Camie v2 这种
    788 MB 的 transformer ONNX，首次加载（图优化 + fusion）在 4 核 CPU 上能
    阻塞 5-10 分钟，过程没有任何反馈——前端只看到 spinner 转圈，体感是死锁。
    现在加进度 print + 计时；同时**显式 fallback** 到 CPU 而不是 silent。
    """
    import os as _os
    import time
    import onnxruntime as ort
    sess_opts, providers = _init_onnx_runtime()

    try:
        size_mb = _os.path.getsize(str(model_path)) / (1024 * 1024)
    except Exception:
        size_mb = -1

    fname = _os.path.basename(str(model_path))
    print(f"[tagger] Creating ONNX session for {fname} ({size_mb:.1f} MB)...")
    print(f"[tagger]   提示：首次加载大模型会做图优化 + fusion，"
          f"CPU 下可能 3-10 分钟；GPU 通常几十秒")

    t0 = time.time()
    session = None
    try:
        session = ort.InferenceSession(str(model_path), sess_options=sess_opts, providers=providers)
        print(f"[tagger]   InferenceSession ready in {time.time() - t0:.1f}s "
              f"(providers={[p for p in providers]})")
    except Exception as e:
        print(f"[tagger]   GPU/Default providers failed after {time.time() - t0:.1f}s: {e}")
        print(f"[tagger]   Retrying with CPU only...")
        t0 = time.time()
        session = ort.InferenceSession(str(model_path), sess_options=sess_opts,
                                       providers=['CPUExecutionProvider'])
        print(f"[tagger]   CPU session ready in {time.time() - t0:.1f}s")

    actual = session.get_providers()
    print(f"[tagger]   Model loaded with providers: {actual}")
    if any('CUDA' in p or 'Dml' in p or 'CoreML' in p for p in actual):
        print("[tagger]   ✓ GPU acceleration ACTIVE")
    else:
        print("[tagger]   ⚠ Running on CPU only")
    return session


def _init_tensorflow():
    """延迟初始化 TF（仅 DeepDanbooru 需要）"""
    global tf, tf_device_name, _tf_initialized
    if _tf_initialized:
        return tf is not None
    _tf_initialized = True
    try:
        import tensorflow as tf_module
        tf = tf_module
        if config.use_cpu:
            tf_device_name = '/cpu:0'
            return True
        gpus = tf.config.experimental.list_physical_devices('GPU')
        if gpus:
            gpu_id = config.device_id if config.device_id is not None else 0
            if gpu_id >= len(gpus):
                gpu_id = 0
            tf.config.experimental.set_visible_devices(gpus[gpu_id], 'GPU')
            tf_device_name = '/gpu:0'
            for gpu in gpus:
                tf.config.experimental.set_memory_growth(gpu, True)
        else:
            tf_device_name = '/cpu:0'
        return True
    except ImportError:
        print("[tagger] TensorFlow not installed (DeepDanbooru unavailable)")
        return False
    except Exception as e:
        print(f"[tagger] TF setup error: {e}")
        tf_device_name = '/cpu:0'
        return tf is not None


# ============== 标签文件读取（替代 pandas）==============
#
# 不同模型对 category 的命名/编码不一样，归一化到这套小写名字：
#   general / character / copyright / artist / meta / model / rating / quality / year / unknown
_CATEGORY_ALIASES = {
    # WD v3 CSV: category 是整数字符串
    '0': 'general',
    '1': 'artist',
    '3': 'copyright',
    '4': 'character',
    '5': 'meta',
    '9': 'rating',
    # 已是小写名（Camie v2）
    'general': 'general',
    'character': 'character',
    'copyright': 'copyright',
    'artist': 'artist',
    'meta': 'meta',
    'rating': 'rating',
    'year': 'year',
    'model': 'model',
    'quality': 'quality',
    # 首字母大写（cl_tagger）
    'General': 'general',
    'Character': 'character',
    'Copyright': 'copyright',
    'Artist': 'artist',
    'Meta': 'meta',
    'Rating': 'rating',
    'Year': 'year',
    'Model': 'model',
    'Quality': 'quality',
}

# 已知的标签类别 ID 顺序（前端 chip 用这个顺序展示；rating 单独处理不出现在 chip）
ALL_TAG_CATEGORIES: List[str] = [
    'general', 'character', 'copyright', 'artist',
    'meta', 'model', 'quality', 'year',
]


def normalize_category(raw) -> str:
    """统一类别名。未知的归到 'unknown'。"""
    if raw is None:
        return 'unknown'
    key = str(raw).strip()
    return _CATEGORY_ALIASES.get(key, _CATEGORY_ALIASES.get(key.lower(), 'unknown'))


def _read_tag_list(tags_path: Path) -> Tuple[List[str], List[str]]:
    """读 JSON / CSV 标签文件，返回 (names, categories) 两个平行列表（按 index 升序）。

    支持：
      - JSON list: ["1girl", "solo", ...] 或 [{"name": "1girl", "category": "general"}, ...]
      - JSON dict (idx → name): {"0": "1girl", ...} 或 {"0": {"tag": "1girl", "category": "Rating"}, ...}
      - CSV 带表头含 'name' 列（可选 'category' 列）
      - CSV 单列无表头
    """
    suffix = tags_path.suffix.lower()
    if suffix == '.json':
        with open(tags_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            names, cats = [], []
            for t in data:
                if isinstance(t, str):
                    names.append(t); cats.append('unknown')
                else:
                    names.append(t.get('name') or t.get('tag') or '')
                    cats.append(normalize_category(t.get('category')))
            return names, cats
        if isinstance(data, dict):
            items = []
            for k, v in data.items():
                try:
                    idx = int(k)
                except (TypeError, ValueError):
                    continue
                if isinstance(v, str):
                    items.append((idx, v, 'unknown'))
                elif isinstance(v, dict):
                    name = v.get('name') or v.get('tag') or ''
                    cat = normalize_category(v.get('category'))
                    items.append((idx, name, cat))
            items.sort(key=lambda x: x[0])
            return [n for _, n, _ in items], [c for _, _, c in items]
        raise ValueError(f"Unexpected JSON shape in {tags_path}: {type(data)}")
    # CSV
    with open(tags_path, 'r', encoding='utf-8', newline='') as f:
        sample = f.read(4096)
        f.seek(0)
        first_line = sample.split('\n', 1)[0].lower() if sample else ''
        if ',' in first_line and 'name' in first_line:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            name_field = 'name' if 'name' in fields else (fields[0] if fields else None)
            cat_field = 'category' if 'category' in fields else None
            if not name_field:
                return [], []
            names, cats = [], []
            for row in reader:
                names.append(row.get(name_field, ''))
                cats.append(normalize_category(row.get(cat_field)) if cat_field else 'unknown')
            return names, cats
        # 无表头：单列纯标签名，category 全标 unknown
        reader = csv.reader(f)
        names = [row[0] for row in reader if row]
        return names, ['unknown'] * len(names)


# ============== Interrogator 基类 ==============
class Interrogator:
    _token_cache: Dict[str, int] = {}

    @staticmethod
    def estimate_token_count(tag: str, has_weight: bool = False) -> int:
        key = f"{tag}_{has_weight}"
        if key in Interrogator._token_cache:
            return Interrogator._token_cache[key]
        text = tag.replace('_', ' ')
        estimated = int(len(text.split()) * 1.3) + 1
        if has_weight:
            estimated += 6
        Interrogator._token_cache[key] = estimated
        return estimated

    @staticmethod
    def postprocess_tags(
        tags: Dict[str, float],
        threshold: float = 0.35,
        additional_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None,
        sort_by_alphabetical_order: bool = False,
        add_confident_as_weight: bool = False,
        use_weight_mapping: bool = False,
        tag_confidence_precision: int = 2,
        replace_underscore: bool = False,
        replace_underscore_excludes: Optional[List[str]] = None,
        escape_tag: bool = False,
        core_tags: Optional[List[str]] = None,
        core_tags_weight: float = 1.0,
        weight_mapping_exponent: float = 1.0,
        max_tags: int = 0,
        weight_min: float = 1.0,
        weight_max: float = 1.0,
        token_limit: int = 75,
        excluded_categories: Optional[List[str]] = None,
        tag_category_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, float]:
        additional_tags = additional_tags or []
        exclude_tags = exclude_tags or []
        replace_underscore_excludes = replace_underscore_excludes or []
        core_tags = core_tags or []
        excluded_category_set = {c.strip().lower() for c in (excluded_categories or []) if c}
        tag_category_map = tag_category_map or {}

        exclude_set = set(exclude_tags)
        if replace_underscore:
            exclude_set |= {t.replace('_', ' ') for t in exclude_tags}
        core_tags_set = set(core_tags)

        final_tags_list: List[Tuple[str, float, bool]] = []
        for tag in core_tags:
            final_tags_list.append((tag, core_tags_weight, True))

        model_tags: List[Tuple[str, float, bool]] = []
        for tag, confidence in tags.items():
            clean = tag
            if replace_underscore and tag not in replace_underscore_excludes:
                clean = tag.replace('_', ' ')
            if clean in exclude_set or tag in exclude_set:
                continue
            if clean in core_tags_set or tag in core_tags_set:
                continue
            if confidence < threshold:
                continue
            # 按类别排除（rating 不通过 tags 进来，所以不用考虑）
            if excluded_category_set and tag_category_map:
                cat = tag_category_map.get(tag, 'unknown')
                if cat in excluded_category_set:
                    continue
            model_tags.append((tag, float(confidence), False))
        model_tags.sort(key=lambda x: x[1], reverse=True)

        all_candidates = final_tags_list + model_tags

        selected: List[Tuple[str, float, bool, float]] = []
        current_tokens = 0
        additional_tokens = sum(Interrogator.estimate_token_count(t, False) for t in additional_tags)
        effective_limit = (token_limit - additional_tokens) if token_limit > 0 else 9999

        for tag, val, is_forced_weight in all_candidates:
            weight_val = 1.0
            has_weight_format = False
            if is_forced_weight:
                weight_val = val
                has_weight_format = (abs(weight_val - 1.0) > 0.001) or add_confident_as_weight or use_weight_mapping
            elif add_confident_as_weight or use_weight_mapping:
                # 权重映射
                if abs(weight_max - weight_min) < 0.01 and abs(weight_max - 1.0) < 0.01:
                    weight_val = 1.0
                    has_weight_format = False
                else:
                    normalized = (val - threshold) / (1.0 - threshold + 1e-5)
                    normalized = max(0.0, min(1.0, normalized))
                    if weight_mapping_exponent != 1.0:
                        normalized = normalized ** weight_mapping_exponent
                    weight_val = weight_min + normalized * (weight_max - weight_min)
                    has_weight_format = True

            tag_tokens = Interrogator.estimate_token_count(tag, has_weight_format)
            if token_limit > 0 and current_tokens + tag_tokens > effective_limit:
                continue
            current_tokens += tag_tokens
            selected.append((tag, weight_val, has_weight_format, val))

            if max_tags > 0 and len(selected) >= max_tags:
                break

        final_output: Dict[str, float] = {}

        for t in additional_tags:
            if not t:
                continue
            pt = t
            if replace_underscore and t not in replace_underscore_excludes:
                pt = pt.replace('_', ' ')
            if escape_tag:
                pt = tag_escape_pattern.sub(r'\\\1', pt)
            if pt not in final_output:
                final_output[pt] = 1.0

        for tag, weight, has_weight, original_conf in selected:
            processed_tag = tag
            if replace_underscore and tag not in replace_underscore_excludes:
                processed_tag = processed_tag.replace('_', ' ')
            if escape_tag:
                processed_tag = tag_escape_pattern.sub(r'\\\1', processed_tag)
            if has_weight and abs(weight - 1.0) > 0.001:
                processed_tag = f"({processed_tag}:{weight:.{tag_confidence_precision}f})"
            if processed_tag not in final_output:
                final_output[processed_tag] = original_conf

        if sort_by_alphabetical_order:
            final_output = dict(sorted(final_output.items()))
        return final_output

    def __init__(self, name: str) -> None:
        self.name = name
        self.model = None
        self.tags = None
        self._warmed_up = False
        self._tag_names: Optional[List[str]] = None
        self._tag_categories: Optional[List[str]] = None    # 与 _tag_names 平行索引
        # load()/download() 失败时把人类可读的错误塞这里，让 api_router 在抛
        # RuntimeError 时一并带给前端（之前只 print 到 server stdout，前端只能
        # 拿到「Model load failed: xxx」一句通用话）。
        self.last_error: Optional[str] = None

    def get_tag_category_map(self) -> Dict[str, str]:
        """返回 {tag_name: category} 完整映射。前端用于排除某一类、分组展示。"""
        if not self._tag_names or not self._tag_categories:
            return {}
        return dict(zip(self._tag_names, self._tag_categories))

    def load(self):
        raise NotImplementedError()

    def unload(self) -> bool:
        unloaded = False
        if hasattr(self, 'model') and self.model is not None:
            del self.model
            self.model = None
            unloaded = True
            self._warmed_up = False
            print(f"[tagger] Unloaded {self.name}")
        if hasattr(self, 'tags') and self.tags is not None:
            del self.tags
            self.tags = None
        gc.collect()
        return unloaded

    def _warmup(self, input_shape: tuple):
        if self._warmed_up or self.model is None:
            return
        try:
            dummy = np.zeros(input_shape, dtype=np.float32)
            in_name = self.model.get_inputs()[0].name
            out_names = [o.name for o in self.model.get_outputs()]
            self.model.run(out_names, {in_name: dummy})
            self._warmed_up = True
        except Exception as e:
            print(f"[tagger]   warmup failed (non-critical): {e}")

    def interrogate_batch(self, images: List[Image.Image]
                          ) -> List[Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]]:
        raise NotImplementedError()

    def interrogate(self, image: Image.Image
                    ) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
        results = self.interrogate_batch([image])
        return results[0] if results else ({}, {}, {})


# ============== WD 系列（HuggingFace 预定义）==============
class WaifuDiffusionInterrogator(Interrogator):
    RATING_TAGS = frozenset([
        'general', 'sensitive', 'questionable', 'explicit', 'safe', 'nsfw',
        'rating:general', 'rating:sensitive', 'rating:questionable', 'rating:explicit',
    ])

    def __init__(self, name: str, repo_id: str,
                 model_path: str = 'model.onnx',
                 tags_path: str = 'selected_tags.csv',
                 **kwargs) -> None:
        super().__init__(name)
        self.repo_id = repo_id
        self.model_filename = model_path
        self.tags_filename = tags_path
        self.hf_download_kwargs = {'repo_id': repo_id, **kwargs}
        self.model_path: Optional[Path] = None
        self.tags_path: Optional[Path] = None
        self._input_size = 448

    def download(self) -> bool:
        try:
            base = config.models_path
            repo_subdir = self.repo_id.replace('/', '_')
            model_dir = base / repo_subdir
            model_dir.mkdir(parents=True, exist_ok=True)

            model_p = next((model_dir / m for m in ('model_optimized.onnx', 'model.onnx', self.model_filename)
                            if (model_dir / m).exists()), None)
            tags_p = next((model_dir / t for t in ('tag_mapping.json', 'selected_tags.csv', self.tags_filename)
                           if (model_dir / t).exists()), None)

            if not model_p:
                print(f"[tagger] Downloading {self.name} → {model_dir}")
                downloaded = hf_hub_download(**self.hf_download_kwargs,
                                             filename='model.onnx',
                                             local_dir=str(model_dir))
                model_p = Path(downloaded)
            if not tags_p:
                print(f"[tagger] Downloading tags for {self.name}")
                downloaded = hf_hub_download(**self.hf_download_kwargs,
                                             filename='selected_tags.csv',
                                             local_dir=str(model_dir))
                tags_p = Path(downloaded)

            self.model_path = model_p
            self.tags_path = tags_p
            if self.model_path.is_file() and self.tags_path.is_file():
                return True
            self.last_error = (
                f"下载完成但文件缺失：{model_dir}\n"
                f"model exists={self.model_path.is_file()}, "
                f"tags exists={self.tags_path.is_file()}"
            )
            return False
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[tagger] Download {self.name} error: {e}\n{tb}")
            self.last_error = _format_hf_error(self.repo_id, e)
            return False

    def _load_tags(self) -> bool:
        if not self.tags_path or not self.tags_path.exists():
            return False
        try:
            self._tag_names, self._tag_categories = _read_tag_list(self.tags_path)
            self.tags = self._tag_names  # 兼容 unload()
            print(f"[tagger]   Loaded {len(self._tag_names)} tags")
            return True
        except Exception as e:
            print(f"[tagger] Tag load error: {e}")
            return False

    def load(self) -> None:
        if not self.model_path or not self.tags_path:
            if not self.download():
                return
        print(f"[tagger] Loading {self.name} from {self.model_path}")
        try:
            self.model = create_onnx_session(str(self.model_path))
            shape = self.model.get_inputs()[0].shape
            if isinstance(shape[1], int) and shape[1] > 10:
                self._input_size = shape[1]
            elif isinstance(shape[2], int) and shape[2] > 10:
                self._input_size = shape[2]
            else:
                self._input_size = 448
            print(f"[tagger]   Input size: {self._input_size}")
            if not self._load_tags():
                raise ValueError("Tag load failed")
            self._warmup((1, self._input_size, self._input_size, 3))
            self.last_error = None
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[tagger] Load {self.name} error: {e}\n{tb}")
            if self.last_error is None:
                self.last_error = f"加载 ONNX 失败: {type(e).__name__}: {e}"
            self.model = None
            self.tags = None

    def _preprocess_images(self, images: List[Image.Image]) -> np.ndarray:
        batch = []
        size = self._input_size
        for image in images:
            if image.mode == 'RGBA':
                bg = Image.new('RGB', image.size, (255, 255, 255))
                bg.paste(image, mask=image.split()[3])
                image = bg
            elif image.mode != 'RGB':
                image = image.convert('RGB')
            arr = np.array(image, dtype=np.uint8)[:, :, ::-1]   # RGB → BGR
            arr = dbimutils.make_square(arr, size)
            arr = dbimutils.smart_resize(arr, size)
            batch.append(arr)
        return np.stack(batch, axis=0).astype(np.float32)

    def interrogate_batch(self, images):
        if self.model is None or self._tag_names is None:
            self.load()
            if self.model is None:
                return [({}, {}, {}) for _ in images]
        try:
            arr = self._preprocess_images(images)
            in_name = self.model.get_inputs()[0].name
            out_name = self.model.get_outputs()[0].name
            confidences = self.model.run([out_name], {in_name: arr})[0]
            tag_count = len(self._tag_names)
            cats = self._tag_categories or ['unknown'] * tag_count
            results = []
            for i in range(len(images)):
                probs = confidences[i]
                ratings, tags_out, meta_out = {}, {}, {}
                for j in range(min(tag_count, len(probs))):
                    tag = self._tag_names[j]
                    conf = float(probs[j])
                    cat = cats[j] if j < len(cats) else 'unknown'
                    # WD CSV 里 rating tag 的 category 通常被打成 9（=rating），
                    # 但少数旧文件把它当 copyright，加 name 兜底
                    is_rating = (cat == 'rating'
                                 or tag in self.RATING_TAGS
                                 or tag.startswith('rating:'))
                    if is_rating:
                        ratings[tag] = conf
                    elif cat in ('meta', 'year'):
                        meta_out[tag] = conf
                    else:
                        tags_out[tag] = conf
                results.append((ratings, tags_out, meta_out))
            return results
        except Exception as e:
            print(f"[tagger] Inference {self.name} error: {e}")
            return [({}, {}, {}) for _ in images]


# ============== 自定义本地 ONNX ==============
class CustomLocalInterrogator(Interrogator):
    """通用本地 ONNX 模型加载器（兼容 cl_tagger_1_01 等单输出模型）"""

    RATING_TAGS = frozenset([
        'general', 'sensitive', 'questionable', 'explicit',
        'safe', 'nsfw', 'rating:safe', 'rating:general',
        'rating:sensitive', 'rating:questionable', 'rating:explicit',
    ])

    def __init__(self, folder_path: str, name: Optional[str] = None) -> None:
        super().__init__(name or f"local:{Path(folder_path).name}")
        self.folder_path = Path(folder_path)
        self.model_path: Optional[Path] = None
        self.tags_path: Optional[Path] = None
        self._input_size = 448
        self._input_format = 'NHWC'

    def load(self) -> None:
        if not self.folder_path.exists() or not self.folder_path.is_dir():
            print(f"[tagger] Path does not exist: {self.folder_path}")
            return
        # 模型
        for m in ('model_optimized.onnx', 'model.onnx'):
            p = self.folder_path / m
            if p.exists():
                self.model_path = p
                break
        if not self.model_path:
            onnx_files = list(self.folder_path.glob("*.onnx"))
            if onnx_files:
                self.model_path = onnx_files[0]
        if not self.model_path:
            print(f"[tagger] No .onnx found in {self.folder_path}")
            return
        # 标签
        for t in ('tag_mapping.json', 'selected_tags.csv', 'tags.json', 'tags.csv'):
            p = self.folder_path / t
            if p.exists():
                self.tags_path = p
                break
        if not self.tags_path:
            print(f"[tagger] No tag mapping in {self.folder_path}")
            return
        print(f"[tagger] Loading local model: {self.model_path}")
        try:
            self.model = create_onnx_session(str(self.model_path))
            shape = self.model.get_inputs()[0].shape
            if len(shape) == 4:
                if isinstance(shape[1], int) and shape[1] == 3:
                    self._input_format = 'NCHW'
                    self._input_size = shape[2] if isinstance(shape[2], int) else 448
                else:
                    self._input_format = 'NHWC'
                    self._input_size = shape[1] if isinstance(shape[1], int) else 448
            print(f"[tagger]   Format: {self._input_format}, size: {self._input_size}")
            self._load_tags()
            if self._input_format == 'NCHW':
                self._warmup((1, 3, self._input_size, self._input_size))
            else:
                self._warmup((1, self._input_size, self._input_size, 3))
        except Exception as e:
            print(f"[tagger] Local load error: {e}")
            self.model = None
            self.tags = None

    def _load_tags(self):
        self._tag_names, self._tag_categories = _read_tag_list(self.tags_path)
        self.tags = self._tag_names
        cats_summary = ''
        if self._tag_categories:
            from collections import Counter
            top = Counter(self._tag_categories).most_common()
            cats_summary = ' [' + ', '.join(f"{c}={n}" for c, n in top) + ']'
        print(f"[tagger]   Loaded {len(self._tag_names)} tags{cats_summary}")

    def _preprocess_images(self, images):
        batch = []
        size = self._input_size
        for image in images:
            if image.mode != 'RGB':
                if image.mode == 'RGBA':
                    bg = Image.new('RGB', image.size, (255, 255, 255))
                    bg.paste(image, mask=image.split()[3])
                    image = bg
                else:
                    image = image.convert('RGB')
            arr = np.array(image, dtype=np.uint8)[:, :, ::-1]
            arr = dbimutils.make_square(arr, size)
            arr = dbimutils.smart_resize(arr, size)
            batch.append(arr)
        batch_array = np.stack(batch, axis=0).astype(np.float32)
        if self._input_format == 'NCHW':
            batch_array = batch_array.transpose(0, 3, 1, 2) / 255.0
        return batch_array

    def interrogate_batch(self, images):
        if self.model is None or self._tag_names is None:
            self.load()
            if self.model is None:
                return [({}, {}, {}) for _ in images]
        try:
            arr = self._preprocess_images(images)
            in_name = self.model.get_inputs()[0].name
            out_name = self.model.get_outputs()[0].name
            confidences = self.model.run([out_name], {in_name: arr})[0]
            tag_count = len(self._tag_names)
            cats = self._tag_categories or ['unknown'] * tag_count
            results = []
            for i in range(len(images)):
                probs = confidences[i]
                ratings, tags_out, meta_out = {}, {}, {}
                for j in range(min(tag_count, len(probs))):
                    tag = self._tag_names[j]
                    conf = float(probs[j])
                    cat = cats[j] if j < len(cats) else 'unknown'
                    tag_lower = tag.lower()
                    is_rating = (cat == 'rating'
                                 or tag_lower in self.RATING_TAGS
                                 or tag_lower.startswith('rating:'))
                    if is_rating:
                        ratings[tag] = conf
                    elif cat in ('meta', 'year', 'quality', 'model'):
                        # meta/year 是真元数据；cl_tagger 的 quality/model 也归到这里
                        meta_out[tag] = conf
                    else:
                        tags_out[tag] = conf
                results.append((ratings, tags_out, meta_out))
            return results
        except Exception as e:
            print(f"[tagger] Local inference error: {e}")
            return [({}, {}, {}) for _ in images]


# ============== Camie Tagger v2（2026-01 最新）==============
class CamieTaggerInterrogator(Interrogator):
    """Camais03/camie-tagger-v2 适配器

    - 输入 512×512 RGB + ImageNet 归一化 → NCHW float32
    - 双输出：initial_logits + refined_logits（按 name 取 refined）
    - 输出是 logits，需 sigmoid
    - 70k 标签：在 batch 内用 threshold 预过滤，避免下游 postprocess 处理整个 dict
    - 7 个类别拆分：rating → ratings, year/meta → meta, 其他 → tags
    """

    INPUT_SIZE = 512
    IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    REPO_ID = "Camais03/camie-tagger-v2"

    # 类别 ID 见 metadata.json (HuggingFace 上文档)
    META_CATEGORIES = {"meta", "year"}
    RATING_CATEGORIES = {"rating"}
    # 其余: general, character, copyright, artist → tags
    PREFILTER_THRESHOLD = 0.05  # 推理时先把 < 5% 的概率全砍掉，余下交给 postprocess_tags

    def __init__(self, folder_path: Optional[str] = None,
                 repo_id: str = "Camais03/camie-tagger-v2") -> None:
        super().__init__(f"camie:{repo_id}")
        self.repo_id = repo_id
        self.folder_path = Path(folder_path) if folder_path else (
            config.models_path / repo_id.replace('/', '_')
        )
        self.model_path: Optional[Path] = None
        self.metadata_path: Optional[Path] = None
        self._output_name: Optional[str] = None

    def download(self) -> bool:
        """从 HF 下载 Camie tagger 模型 + metadata。

        Camie 系列各发布版本的文件命名并不一致——
        - 旧版本：model.onnx + metadata.json
        - v2 当前版本：camie-tagger-v2.onnx + camie-tagger-v2-metadata.json
        - 也存在 model_optimized.onnx / model_initial.onnx 等
        所以不能硬编码文件名（之前就是因为这个 404），改成先 list_repo_files
        看远程实际有什么、挑一个最合适的下载，然后 rename 成本地约定名
        （model.onnx + metadata.json）——这样 get_available_models 的本地
        识别逻辑（看 metadata.json 是否存在）就能工作。
        """
        try:
            self.folder_path.mkdir(parents=True, exist_ok=True)

            # 1) 已经下载过 → 直接复用
            local_onnx = self.folder_path / "model.onnx"
            local_meta = self.folder_path / "metadata.json"
            ok = (local_onnx.exists() and local_onnx.stat().st_size > 1024
                  and local_meta.exists() and local_meta.stat().st_size > 1024)
            if ok:
                self.model_path = local_onnx
                self.metadata_path = local_meta
                return True

            # 2) 列远程文件，挑实际名字
            from huggingface_hub import list_repo_files
            try:
                files = list_repo_files(repo_id=self.repo_id)
            except Exception as e:
                # 列文件本身失败（网络/repo 不存在）→ 直接走错误格式化
                raise
            root_files = [f for f in files if "/" not in f]

            onnx_files = [f for f in root_files if f.lower().endswith(".onnx")]
            if not onnx_files:
                self.last_error = (
                    f"仓库 {self.repo_id} 根目录里找不到 .onnx 文件；可用文件：\n  "
                    + ", ".join(root_files[:20])
                )
                return False

            # ONNX 文件优先级：model.onnx > *optimized* > *refined* > 第一个
            def _rank_onnx(name: str) -> int:
                n = name.lower()
                if n == "model.onnx": return 0
                if "optimized" in n: return 1
                if "refined"   in n: return 2
                return 3
            remote_onnx = sorted(onnx_files, key=_rank_onnx)[0]

            meta_files = [f for f in root_files
                          if f.lower().endswith("metadata.json")
                          or f.lower().endswith("_metadata.json")
                          or f.lower().endswith("-metadata.json")]
            if not meta_files:
                self.last_error = (
                    f"仓库 {self.repo_id} 里找不到 metadata.json；Camie 模型需要 tag 列表元数据"
                )
                return False
            remote_meta = ("metadata.json" if "metadata.json" in meta_files
                           else meta_files[0])

            # 3) 下载 + rename 到本地约定名
            import shutil
            for remote_fn, local_p in [(remote_onnx, local_onnx), (remote_meta, local_meta)]:
                if local_p.exists() and local_p.stat().st_size > 1024:
                    continue
                print(f"[tagger] Downloading {self.repo_id}/{remote_fn} → {local_p}")
                downloaded = hf_hub_download(repo_id=self.repo_id, filename=remote_fn,
                                             local_dir=str(self.folder_path))
                downloaded_p = Path(downloaded)
                # 远程名 != 本地约定名 → 改名（hf_hub_download 把远程文件名原样
                # 拷到 local_dir，所以 downloaded_p 通常是 folder_path/<remote_fn>）
                if downloaded_p.resolve() != local_p.resolve():
                    if local_p.exists():
                        local_p.unlink()
                    shutil.move(str(downloaded_p), str(local_p))

            self.model_path = local_onnx
            self.metadata_path = local_meta
            if local_onnx.exists() and local_meta.exists():
                return True
            self.last_error = (
                f"下载完成但文件缺失：{self.folder_path}\n"
                f"model.onnx exists={local_onnx.exists()}, "
                f"metadata.json exists={local_meta.exists()}"
            )
            return False
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[tagger] Camie download error: {e}\n{tb}")
            self.last_error = _format_hf_error(self.repo_id, e)
            return False

    def _load_metadata(self) -> bool:
        if not self.metadata_path or not self.metadata_path.exists():
            return False
        try:
            with open(self.metadata_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            # Camie v2 实际 metadata.json 把 idx_to_tag / tag_to_category 嵌套在
            # dataset_info.tag_mapping 下。早期版本（以及部分 fork）也可能直接放在
            # 顶层。先逐层向下找一次，再走原来的 key 候选列表。
            tag_count = 0
            if isinstance(meta, dict):
                # 优先在 tag_mapping 容器里找（Camie v2 实际位置：
                # dataset_info.tag_mapping.{idx_to_tag, tag_to_category}），
                # 顶层只作为旧版/fork 的兜底。注意 dataset_info.categories 是
                # 类别名数组而不是 tag→category 映射，必须避免误用 —— 因此
                # 不把 dataset_info 加进容器列表，否则 `categories` 这个 key
                # 会把 7 条字符串当成 70k 条标签的分类，对位完全错。
                containers = []
                ds = meta.get("dataset_info")
                if isinstance(ds, dict):
                    tm = ds.get("tag_mapping")
                    if isinstance(tm, dict):
                        containers.append(tm)
                tm_top = meta.get("tag_mapping")
                if isinstance(tm_top, dict):
                    containers.append(tm_top)
                containers.append(meta)  # 最后兜底：fork 把 idx_to_tag 直接放顶层

                def _pick(*keys):
                    for c in containers:
                        for k in keys:
                            v = c.get(k)
                            if v:
                                return v
                    return None

                idx_to_tag = _pick("idx_to_tag", "idx2tag", "id_to_tag", "tags")
                tag_to_cat = _pick("tag_to_category", "tag2category", "categories")

                if isinstance(idx_to_tag, dict):
                    sorted_items = sorted([(int(k), v) for k, v in idx_to_tag.items()], key=lambda x: x[0])
                    tag_list = [v if isinstance(v, str) else v.get('name', str(v)) for _, v in sorted_items]
                elif isinstance(idx_to_tag, list):
                    tag_list = [t if isinstance(t, str) else t.get('name', str(t)) for t in idx_to_tag]
                else:
                    available = list(meta.keys())
                    if isinstance(meta.get("dataset_info"), dict):
                        ds_keys = list(meta["dataset_info"].keys())
                        available_msg = f"top={available}, dataset_info={ds_keys}"
                    else:
                        available_msg = f"top={available}"
                    print(f"[tagger] Camie metadata: idx_to_tag 字段不可识别，可用 keys: {available_msg}")
                    return False

                if isinstance(tag_to_cat, dict):
                    cat_list = [tag_to_cat.get(t, "general") for t in tag_list]
                elif isinstance(tag_to_cat, list):
                    cat_list = list(tag_to_cat)
                else:
                    cat_list = ["general"] * len(tag_list)

                self._tag_names = tag_list
                self._tag_categories = [normalize_category(c) for c in cat_list]
                tag_count = len(tag_list)
            else:
                print(f"[tagger] Camie metadata: 顶层不是 dict (got {type(meta)})")
                return False

            print(f"[tagger]   Camie metadata: {tag_count} tags loaded")
            unique_cats = set(self._tag_categories)
            print(f"[tagger]   Categories present: {sorted(unique_cats)}")
            return True
        except Exception as e:
            print(f"[tagger] Camie metadata load error: {e}")
            return False

    def _pick_output_name(self) -> str:
        outputs = self.model.get_outputs()
        names = [o.name for o in outputs]
        # 优先 refined
        for n in names:
            if 'refined' in n.lower():
                return n
        # 退化到包含 logit / output 的，最后回退到最后一个
        for n in names:
            if 'logit' in n.lower() or 'output' in n.lower():
                return n
        return names[-1]

    def load(self) -> None:
        if not self.model_path or not self.metadata_path:
            # 检查是否已下载
            local_model = self.folder_path / "model.onnx"
            local_meta = self.folder_path / "metadata.json"
            if local_model.exists() and local_meta.exists():
                self.model_path = local_model
                self.metadata_path = local_meta
            else:
                if not self.download():
                    print(f"[tagger] Camie download/find failed in {self.folder_path}")
                    return
        print(f"[tagger] Loading Camie v2 from {self.model_path}")
        try:
            self.model = create_onnx_session(str(self.model_path))
            self._output_name = self._pick_output_name()
            print(f"[tagger]   Using output: {self._output_name} (available: "
                  f"{[o.name for o in self.model.get_outputs()]})")
            if not self._load_metadata():
                raise ValueError("Camie metadata failed to load")
            self._warmup((1, 3, self.INPUT_SIZE, self.INPUT_SIZE))
            self.last_error = None
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[tagger] Camie load error: {e}\n{tb}")
            # 注意：download() 失败的 last_error 已经在那里赋值；只有「下载成功
            # 但 ONNX 加载/metadata 解析等阶段失败」时才在这里覆盖。
            if self.last_error is None:
                self.last_error = f"加载 ONNX 失败: {type(e).__name__}: {e}"
            self.model = None
            self.tags = None

    def _preprocess_images(self, images: List[Image.Image]) -> np.ndarray:
        size = self.INPUT_SIZE
        batch = np.empty((len(images), 3, size, size), dtype=np.float32)
        for i, image in enumerate(images):
            if image.mode != 'RGB':
                if image.mode == 'RGBA':
                    bg = Image.new('RGB', image.size, (255, 255, 255))
                    bg.paste(image, mask=image.split()[3])
                    image = bg
                else:
                    image = image.convert('RGB')
            # 保持长宽比 + 中心填充到 512x512（与 Camie 训练一致）
            # 简化：直接 resize（Camie 文档说 maintain aspect ratio，我们居中 letterbox）
            w, h = image.size
            scale = size / max(w, h)
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
            resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas = Image.new('RGB', (size, size), (0, 0, 0))
            canvas.paste(resized, ((size - new_w) // 2, (size - new_h) // 2))
            arr = np.asarray(canvas, dtype=np.float32) / 255.0      # H W C
            arr = (arr - self.IMAGENET_MEAN) / self.IMAGENET_STD
            arr = arr.transpose(2, 0, 1)                              # C H W
            batch[i] = arr
        return batch

    @staticmethod
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        # 防 overflow
        return 1.0 / (1.0 + np.exp(-np.clip(x, -80, 80)))

    def interrogate_batch(self, images):
        if self.model is None or self._tag_names is None:
            self.load()
            if self.model is None:
                return [({}, {}, {}) for _ in images]
        try:
            arr = self._preprocess_images(images)
            in_name = self.model.get_inputs()[0].name
            logits = self.model.run([self._output_name], {in_name: arr})[0]
            probs = self._sigmoid(logits)
            results = []
            tag_count = len(self._tag_names)
            threshold = self.PREFILTER_THRESHOLD
            for i in range(len(images)):
                p = probs[i]
                ratings: Dict[str, float] = {}
                tags_out: Dict[str, float] = {}
                meta_out: Dict[str, float] = {}
                # 70k 全扫一遍但只保留 > threshold 的
                idxs = np.where(p[:tag_count] > threshold)[0]
                for j in idxs:
                    name = self._tag_names[j]
                    cat = self._tag_categories[j] if j < len(self._tag_categories) else "general"
                    conf = float(p[j])
                    cat_lower = cat.lower() if isinstance(cat, str) else "general"
                    if cat_lower in self.RATING_CATEGORIES:
                        ratings[name] = conf
                    elif cat_lower in self.META_CATEGORIES:
                        meta_out[name] = conf
                    else:
                        tags_out[name] = conf
                results.append((ratings, tags_out, meta_out))
            return results
        except Exception as e:
            print(f"[tagger] Camie inference error: {e}")
            import traceback; traceback.print_exc()
            return [({}, {}, {}) for _ in images]


# ============== DeepDanbooru ==============
class DeepDanbooruInterrogator(Interrogator):
    def __init__(self, name: str, project_path) -> None:
        super().__init__(name)
        self.project_path = Path(project_path)

    def load(self) -> None:
        if not self.project_path.exists() or not (self.project_path / 'project.json').is_file():
            print(f"[tagger] Invalid DeepDanbooru project: {self.project_path}")
            return
        if not _init_tensorflow():
            return
        global tf
        try:
            import deepdanbooru.project as ddp
            with tf.device(tf_device_name):
                self.model = ddp.load_model_from_project(project_path=self.project_path, compile_model=False)
                self.tags = ddp.load_tags_from_project(project_path=self.project_path)
            print(f"[tagger] DeepDanbooru loaded ({len(self.tags)} tags)")
        except ImportError:
            print("[tagger] deepdanbooru package missing")
        except Exception as e:
            print(f"[tagger] DeepDanbooru load error: {e}")

    def unload(self) -> bool:
        unloaded = super().unload()
        if unloaded:
            try:
                global tf
                if tf is not None:
                    tf.keras.backend.clear_session()
            except Exception:
                pass
        return unloaded

    def interrogate_batch(self, images):
        if self.model is None or self.tags is None:
            self.load()
            if self.model is None:
                return [({}, {}, {}) for _ in images]
        try:
            global tf
            if tf is None and not _init_tensorflow():
                return [({}, {}, {}) for _ in images]
            try:
                h, w = self.model.input_shape[1:3]
            except Exception:
                h, w = 512, 512
            tensors = []
            for image in images:
                image = image.convert('RGB')
                img = tf.image.resize(
                    tf.convert_to_tensor(np.array(image), dtype=tf.uint8),
                    [h, w], method=tf.image.ResizeMethod.AREA)
                tensors.append(tf.cast(img, tf.float32) / 255.0)
            batch_tensor = tf.stack(tensors, axis=0)
            with tf.device(tf_device_name):
                batch_results = self.model.predict(batch_tensor, batch_size=len(images), verbose=0)
            results = []
            for confidences in batch_results:
                tags_dict = {self.tags[i]: float(confidences[i]) for i in range(len(confidences))}
                results.append(({}, tags_dict, {}))
            return results
        except Exception as e:
            print(f"[tagger] DeepDanbooru inference error: {e}")
            return [({}, {}, {}) for _ in images]
