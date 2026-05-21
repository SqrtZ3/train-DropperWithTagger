# tagger/utils.py - model registry + batch processing helpers
#
# Changes vs. 2_tagger/backend/utils.py:
#   * get_available_models scans tagger/models/<folder>/ (any folder containing model.onnx)
#     and tagger/models/onnx/<folder>/ (legacy) and tagger/models/deepdanbooru/<folder>/.
#   * BatchProcessor.process_directory now uses real batched interrogate_batch (8 images/batch),
#     not per-image interrogate(). Yields progress via callback for the job worker.
#   * format_tags_output understands the parenthesized weight format already produced upstream
#     by postprocess_tags, so it's now a plain joiner.

from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple

from PIL import Image

from tagger.config import config
from tagger.preset import preset_manager  # re-export for api_router


# 预定义可下载的 HuggingFace 模型（v3 系列 + Camie v2 + PixAI v0.9）
PREDEFINED_HF_MODELS: Dict[str, Dict[str, str]] = {
    # 2026-01 最新（推荐）
    "Camais03/camie-tagger-v2": {"kind": "camie", "label": "camie-tagger-v2 (2026-01 · 70k tags)"},
    # 2025 备选
    "deepghs/pixai-tagger-v0.9-onnx": {"kind": "wd", "label": "pixai-tagger-v0.9 (2025-01 · 13k tags)"},
    # WD v3 系列（2024-07 · legacy 默认）
    "SmilingWolf/wd-eva02-large-tagger-v3": {"kind": "wd", "label": "wd-eva02-large-v3 (legacy)"},
    "SmilingWolf/wd-vit-large-tagger-v3":  {"kind": "wd", "label": "wd-vit-large-v3 (legacy)"},
    "SmilingWolf/wd-vit-tagger-v3":         {"kind": "wd", "label": "wd-vit-v3 (legacy)"},
    "SmilingWolf/wd-swinv2-tagger-v3":      {"kind": "wd", "label": "wd-swinv2-v3 (legacy)"},
    "SmilingWolf/wd-convnext-tagger-v3":    {"kind": "wd", "label": "wd-convnext-v3 (legacy)"},
}


def _has_model_onnx(folder: Path) -> bool:
    return (folder / "model.onnx").exists() or (folder / "model_optimized.onnx").exists() or \
        any(folder.glob("*.onnx"))


def get_available_models() -> List[Dict[str, str]]:
    """返回模型列表，每项含 {id, label, kind, downloaded}。

    id 形式：
      - hf:<repo_id>     ← 预定义 HF 模型
      - local:<folder>   ← tagger/models/<folder>/
      - onnx:<folder>    ← tagger/models/onnx/<folder>/ (legacy)
      - deepdanbooru:<folder>
      - custom:<abs_path> ← API 调用方传入的任意绝对路径
    """
    models: List[Dict[str, str]] = []
    seen_ids: set = set()

    base = config.models_path

    # 1) tagger/models/<folder>/ — 任意带 model.onnx 的目录
    if base.exists():
        for item in base.iterdir():
            if not item.is_dir():
                continue
            if item.name in ("onnx", "deepdanbooru"):
                continue
            if _has_model_onnx(item):
                # 检测 Camie 标志：含 metadata.json + model.onnx
                kind = "camie" if (item / "metadata.json").exists() else "local"
                mid = f"local:{item.name}"
                models.append({"id": mid, "label": item.name, "kind": kind, "downloaded": True})
                seen_ids.add(mid)

    # 2) Legacy: tagger/models/onnx/<folder>/
    onnx_root = config.onnx_models_path
    if onnx_root.exists():
        for item in onnx_root.iterdir():
            if item.is_dir() and _has_model_onnx(item):
                mid = f"onnx:{item.name}"
                if mid not in seen_ids:
                    models.append({"id": mid, "label": f"{item.name} (legacy)", "kind": "local",
                                   "downloaded": True})
                    seen_ids.add(mid)
            elif item.suffix == '.onnx':
                mid = f"onnx:{item.stem}"
                if mid not in seen_ids:
                    models.append({"id": mid, "label": item.stem, "kind": "local", "downloaded": True})
                    seen_ids.add(mid)

    # 3) DeepDanbooru
    dd_root = config.deepdanbooru_projects_path
    if dd_root.exists():
        for item in dd_root.iterdir():
            if item.is_dir() and (item / 'project.json').exists():
                mid = f"deepdanbooru:{item.name}"
                if mid not in seen_ids:
                    models.append({"id": mid, "label": f"{item.name} (DeepDanbooru)",
                                   "kind": "deepdanbooru", "downloaded": True})
                    seen_ids.add(mid)

    # 4) 预定义 HF 模型（无论是否下载都显示，未下载会在加载时下载）
    for repo_id, info in PREDEFINED_HF_MODELS.items():
        local_dir = base / repo_id.replace('/', '_')
        downloaded = local_dir.exists() and _has_model_onnx(local_dir)
        mid = f"hf:{repo_id}"
        if mid not in seen_ids:
            models.append({
                "id": mid, "label": info.get("label", repo_id),
                "kind": info.get("kind", "wd"), "downloaded": downloaded,
            })
            seen_ids.add(mid)

    # 按 kind 优先级排序（camie 最前），再按 label
    kind_order = {"camie": 0, "local": 1, "wd": 2, "deepdanbooru": 3}
    models.sort(key=lambda m: (kind_order.get(m.get("kind"), 9), m["label"].lower()))
    return models


def parse_tag_list(text: str) -> List[str]:
    if not text or not text.strip():
        return []
    return [t.strip() for t in text.split(',') if t.strip()]


def format_tags_output(tags: Dict[str, float], separator: str = ", ") -> str:
    """把 postprocess_tags 已格式化好的 dict join 成字符串。

    postprocess_tags 已经把权重格式化为 `(tag:weight)` 写进 key，所以这里只 join。
    """
    return separator.join(tags.keys())


def get_image_files(directory: Path, recursive: bool = False) -> List[Path]:
    extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    files: List[Path] = []
    if recursive:
        for ext in extensions:
            files.extend(directory.rglob(f"*{ext}"))
            files.extend(directory.rglob(f"*{ext.upper()}"))
    else:
        for ext in extensions:
            files.extend(directory.glob(f"*{ext}"))
            files.extend(directory.glob(f"*{ext.upper()}"))
    return sorted(set(files))


def _open_rgb(path: Path) -> Image.Image:
    img = Image.open(path)
    img.load()  # 强制 IO 完成，规避后续多线程下的文件句柄惊扰
    if img.mode == 'RGBA':
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    return img


class BatchProcessor:
    """批量处理器（真·批量推理 + 进度回调）"""

    def __init__(self, interrogator, settings: Dict[str, Any], batch_size: int = 8):
        self.interrogator = interrogator
        self.settings = settings
        self.batch_size = max(1, int(batch_size))
        self.results: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def process_directory(
        self,
        input_path: Path,
        output_dir: Optional[Path] = None,
        output_format: str = "[name].txt",
        recursive: bool = False,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        from tagger.interrogator import Interrogator
        from tagger.format import format_string, Info

        if not input_path.exists():
            return {"success": False, "error": f"Path not found: {input_path}"}

        files = [input_path] if input_path.is_file() else get_image_files(input_path, recursive)
        if not files:
            return {"success": False, "error": "No image files"}

        total = len(files)
        success_count = 0
        error_count = 0

        conflict_mode = self.settings.get('conflict_mode', 'ignore')
        replace_underscore_excludes = parse_tag_list(
            self.settings.get('replace_underscore_excludes', ''))
        excluded_categories = parse_tag_list(self.settings.get('excluded_categories', ''))
        # 拿模型的 tag→category 完整映射（只在按类排除时才需要）
        tag_category_map = self.interrogator.get_tag_category_map() if excluded_categories else {}

        # 把文件按 batch_size 分块
        for batch_start in range(0, total, self.batch_size):
            if self._cancel:
                break
            chunk = files[batch_start: batch_start + self.batch_size]
            images: List[Image.Image] = []
            valid: List[Path] = []
            for fp in chunk:
                try:
                    images.append(_open_rgb(fp))
                    valid.append(fp)
                except Exception as e:
                    error_count += 1
                    self.errors.append({'file': str(fp), 'error': f"open: {e}"})

            if not valid:
                continue

            try:
                batch_results = self.interrogator.interrogate_batch(images)
            except Exception as e:
                for fp in valid:
                    error_count += 1
                    self.errors.append({'file': str(fp), 'error': f"inference: {e}"})
                continue

            for fp, (ratings, tags, meta) in zip(valid, batch_results):
                try:
                    processed = Interrogator.postprocess_tags(
                        tags=tags,
                        threshold=float(self.settings.get('threshold', 0.35)),
                        additional_tags=parse_tag_list(self.settings.get('additional_tags', '')),
                        exclude_tags=parse_tag_list(self.settings.get('exclude_tags', '')),
                        excluded_categories=excluded_categories,
                        tag_category_map=tag_category_map,
                        replace_underscore=bool(self.settings.get('replace_underscore', True)),
                        replace_underscore_excludes=replace_underscore_excludes,
                        escape_tag=bool(self.settings.get('escape_tag', False)),
                        sort_by_alphabetical_order=bool(self.settings.get('sort_alphabetical', False)),
                        core_tags=parse_tag_list(self.settings.get('core_tags', '')),
                        core_tags_weight=float(self.settings.get('core_tags_weight', 1.0)),
                        add_confident_as_weight=bool(self.settings.get('add_confident_as_weight', False)),
                        use_weight_mapping=bool(self.settings.get('use_weight_mapping', False)),
                        weight_mapping_exponent=float(self.settings.get('weight_mapping_exponent', 1.0)),
                        weight_min=float(self.settings.get('weight_min', 0.9)),
                        weight_max=float(self.settings.get('weight_max', 1.1)),
                        max_tags=int(self.settings.get('max_tags', 0)),
                        token_limit=int(self.settings.get('token_limit', 75)),
                        tag_confidence_precision=int(self.settings.get('tag_precision', 2)),
                    )

                    info = Info(path=fp, output_ext='txt')
                    out_name = format_string(output_format, info)
                    out_dir = output_dir if output_dir else fp.parent
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / out_name

                    tags_text = format_tags_output(processed)
                    if out_path.exists() and conflict_mode != 'overwrite':
                        if conflict_mode == 'ignore':
                            success_count += 1
                            self.results.append({'file': str(fp), 'output': str(out_path),
                                                 'tags_count': len(processed), 'skipped': True})
                            continue
                        existing = out_path.read_text(encoding='utf-8').strip()
                        if conflict_mode == 'append':
                            tags_text = f"{existing}, {tags_text}" if existing else tags_text
                        elif conflict_mode == 'prepend':
                            tags_text = f"{tags_text}, {existing}" if existing else tags_text

                    out_path.write_text(tags_text, encoding='utf-8')
                    success_count += 1
                    self.results.append({'file': str(fp), 'output': str(out_path),
                                         'tags_count': len(processed)})
                except Exception as e:
                    error_count += 1
                    self.errors.append({'file': str(fp), 'error': str(e)})

                if progress_cb:
                    try:
                        progress_cb(success_count + error_count, total, fp.name)
                    except Exception:
                        pass

        return {
            'success': True,
            'total_count': total,
            'success_count': success_count,
            'error_count': error_count,
            'results': self.results[-50:],   # 不返回全部，前端展示用
            'errors': self.errors[:50],
            'cancelled': self._cancel,
        }
