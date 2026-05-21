# tagger/api_router.py - FastAPI APIRouter for the tagger module.
#
# Mounted onto Drop Studio's main app at /api/tagger/*. Owns:
#   - 全局 interrogator 单例 + model_lock（与 queue_worker 共用）
#   - 单图 multipart 推理（同步）
#   - 批量目录任务（异步 job_id + 轮询）
#   - 后台联动队列（TaggerQueue）状态/控制 endpoints
#   - 预设管理
#
# Bug fixes vs. 2_tagger/backend/api.py:
#   1. /interrogate 接收 replace_underscore_excludes + use_weight_mapping + tag_precision Form fields
#   2. /batch 异步化为 job_id 模式
#   3. 真正 batch 推理（utils.BatchProcessor 自带）

import asyncio
import io
import time
import uuid
import threading
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, File, UploadFile, Form, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from PIL import Image

from tagger.config import config
from tagger.interrogator import (
    Interrogator,
    WaifuDiffusionInterrogator,
    CustomLocalInterrogator,
    DeepDanbooruInterrogator,
    CamieTaggerInterrogator,
    ALL_TAG_CATEGORIES,
)
from tagger.preset import preset_manager
from tagger.utils import (
    get_available_models,
    parse_tag_list,
    format_tags_output,
    PREDEFINED_HF_MODELS,
    BatchProcessor,
)
from tagger.queue_worker import TaggerQueue


router = APIRouter()

# ============== 全局状态 ==============
_current_interrogator: Optional[Interrogator] = None
_current_model_name: str = ""
_model_lock = threading.Lock()    # 守 _current_interrogator 切换以及推理本身


# ============== Interrogator 工厂 ==============
def get_interrogator(model_name: str) -> Interrogator:
    """根据 model id 字符串解析并返回 interrogator。线程安全：调用方需先 acquire _model_lock。"""
    global _current_interrogator, _current_model_name

    if _current_interrogator is not None and _current_model_name == model_name:
        return _current_interrogator

    # 卸载旧的
    if _current_interrogator is not None:
        try:
            _current_interrogator.unload()
        except Exception:
            pass
        _current_interrogator = None
        _current_model_name = ""

    new: Optional[Interrogator] = None

    if model_name.startswith("custom:"):
        custom_path = Path(model_name[7:])
        if not custom_path.exists():
            raise ValueError(f"Custom path missing: {custom_path}")
        if (custom_path / "metadata.json").exists():
            new = CamieTaggerInterrogator(folder_path=str(custom_path))
        elif (custom_path / "project.json").exists():
            new = DeepDanbooruInterrogator(custom_path.name, custom_path)
        else:
            new = CustomLocalInterrogator(str(custom_path))

    elif model_name.startswith("local:"):
        folder = model_name[6:]
        path = config.models_path / folder
        if not path.exists():
            raise ValueError(f"local model not found: {path}")
        if (path / "metadata.json").exists():
            new = CamieTaggerInterrogator(folder_path=str(path))
        else:
            new = CustomLocalInterrogator(str(path))

    elif model_name.startswith("onnx:"):
        folder = model_name[5:]
        path = config.onnx_models_path / folder
        if not path.exists():
            # 兼容：onnx:<folder> 也可能直接落在 models/ 下
            alt = config.models_path / folder
            if alt.exists():
                path = alt
            else:
                raise ValueError(f"onnx model not found: {path}")
        if (path / "metadata.json").exists():
            new = CamieTaggerInterrogator(folder_path=str(path))
        else:
            new = CustomLocalInterrogator(str(path))

    elif model_name.startswith("deepdanbooru:"):
        folder = model_name[13:]
        path = config.deepdanbooru_projects_path / folder
        if not path.exists():
            raise ValueError(f"DeepDanbooru not found: {path}")
        new = DeepDanbooruInterrogator(folder, path)

    elif model_name.startswith("hf:"):
        repo_id = model_name[3:]
        info = PREDEFINED_HF_MODELS.get(repo_id, {"kind": "wd"})
        kind = info.get("kind", "wd")
        if kind == "camie":
            new = CamieTaggerInterrogator(repo_id=repo_id)
        else:
            new = WaifuDiffusionInterrogator(
                name=repo_id,
                repo_id=repo_id,
                model_path='model.onnx',
                tags_path='selected_tags.csv',
            )

    else:
        # 兼容旧前端：把裸名当 hf:SmilingWolf/<name>
        if model_name.startswith("wd-"):
            new = WaifuDiffusionInterrogator(
                name=f"SmilingWolf/{model_name}",
                repo_id=f"SmilingWolf/{model_name}",
                model_path='model.onnx',
                tags_path='selected_tags.csv',
            )
        else:
            raise ValueError(f"Unknown model id: {model_name}")

    new.load()
    if new.model is None:
        # interrogator 在 load/download 失败时把人类可读原因写到 last_error。
        # 比之前的「Model load failed: hf:xxx」一句通用话有用得多——通常带着
        # 网络/镜像/磁盘等具体建议。
        detail = getattr(new, "last_error", None) or "未知原因（详见 server 日志）"
        raise RuntimeError(f"加载模型失败：{model_name}\n{detail}")
    _current_interrogator = new
    _current_model_name = model_name
    return new


def current_model() -> str:
    return _current_model_name


# ============== 联动队列实例 ==============
# 队列的运行时设置（model + 后处理参数）由前端 POST /queue/config 设定
_queue_settings: Dict[str, Any] = {
    "model": config.default_model,
    "threshold": 0.35,
    "additional_tags": "",
    "exclude_tags": "",
    "excluded_categories": "",
    "replace_underscore": True,
    "replace_underscore_excludes": "",
    "escape_tag": False,
    "sort_alphabetical": False,
    "core_tags": "",
    "core_tags_weight": 1.0,
    "add_confident_as_weight": False,
    "use_weight_mapping": False,
    "weight_mapping_exponent": 1.0,
    "weight_min": 0.9,
    "weight_max": 1.1,
    "max_tags": 0,
    "token_limit": 75,
    "tag_precision": 2,
    "overwrite_existing_txt": False,
}


def _settings_provider() -> Dict[str, Any]:
    return dict(_queue_settings)


tagger_queue = TaggerQueue(
    get_interrogator=get_interrogator,
    get_settings=_settings_provider,
    model_lock=_model_lock,
)


# ============== Batch Job 管理 ==============
class BatchJob:
    def __init__(self, job_id: str, settings: Dict[str, Any]):
        self.job_id = job_id
        self.settings = settings
        self.status = "queued"     # queued | running | done | cancelled | failed
        self.processed = 0
        self.total = 0
        self.current_file = ""
        self.errors: List[Dict[str, str]] = []
        self.result: Optional[Dict[str, Any]] = None
        self.started_at = 0.0
        self.finished_at = 0.0
        self.processor: Optional[BatchProcessor] = None
        self.thread: Optional[threading.Thread] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "processed": self.processed,
            "total": self.total,
            "current_file": self.current_file,
            "error_count": len(self.errors),
            "errors": self.errors[-10:],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": (self.finished_at or time.time()) - self.started_at if self.started_at else 0.0,
            "result": self.result if self.status in ("done", "failed", "cancelled") else None,
        }


_jobs: "OrderedDict[str, BatchJob]" = OrderedDict()
_jobs_lock = threading.Lock()
_MAX_JOBS = 16


def _gc_jobs():
    """从 _jobs 里挤出最老的，控在 _MAX_JOBS 之内。

    必须在 caller 已持有 _jobs_lock 的前提下调用——之前这里又 `with _jobs_lock:`
    自己 acquire 一次，threading.Lock 是非递归的，同一线程二次 acquire 立刻死锁；
    并且 caller `start_batch` 是 async def，这个同步死锁会卡死整个 event loop，
    所有 HTTP 请求都不再响应（前端表现：点了「启动」之后 server 彻底无反应、后端
    没任何输出），其它路由也全部假死。
    """
    while len(_jobs) > _MAX_JOBS:
        _jobs.popitem(last=False)


def _run_batch_job(job: BatchJob, input_path: Path, output_dir: Optional[Path],
                   output_format: str, recursive: bool, batch_size: int):
    job.status = "running"
    job.started_at = time.time()
    try:
        model_name = job.settings.get("model") or config.default_model
        with _model_lock:
            interrogator = get_interrogator(model_name)
        proc = BatchProcessor(interrogator, job.settings, batch_size=batch_size)
        job.processor = proc

        def cb(processed, total, current):
            job.processed = processed
            job.total = total
            job.current_file = current

        # BatchProcessor 内部不会同时和 _model_lock 冲突，但它持续访问 interrogator
        # 为防止用户在中途切换模型，整个 process_directory 也在 model_lock 内执行
        with _model_lock:
            result = proc.process_directory(
                input_path=input_path,
                output_dir=output_dir,
                output_format=output_format,
                recursive=recursive,
                progress_cb=cb,
            )
        job.result = result
        job.errors.extend(result.get("errors", []))
        job.status = "cancelled" if result.get("cancelled") else "done"
    except Exception as e:
        job.status = "failed"
        job.errors.append({"file": "", "error": str(e)})
        print(f"[tagger.batch] job {job.job_id} failed: {e}")
        traceback.print_exc()
    finally:
        job.finished_at = time.time()


# ============== Pydantic 模型 ==============
class BatchRequest(BaseModel):
    input_path: str
    output_path: Optional[str] = None
    recursive: bool = False
    output_format: str = "[name].txt"
    batch_size: int = 8

    # 推理设置 — 复用队列设置那一套
    model: Optional[str] = None
    threshold: float = 0.35
    additional_tags: str = ""
    exclude_tags: str = ""
    excluded_categories: str = ""
    replace_underscore: bool = True
    replace_underscore_excludes: str = ""
    escape_tag: bool = False
    sort_alphabetical: bool = False
    core_tags: str = ""
    core_tags_weight: float = 1.0
    add_confident_as_weight: bool = False
    use_weight_mapping: bool = False
    weight_mapping_exponent: float = 1.0
    weight_min: float = 0.9
    weight_max: float = 1.1
    max_tags: int = 0
    token_limit: int = 75
    tag_precision: int = 2
    conflict_mode: str = "ignore"   # ignore | append | prepend | overwrite


class QueueEnqueue(BaseModel):
    paths: List[str]


class QueueConfig(BaseModel):
    model: Optional[str] = None
    threshold: Optional[float] = None
    additional_tags: Optional[str] = None
    exclude_tags: Optional[str] = None
    excluded_categories: Optional[str] = None
    replace_underscore: Optional[bool] = None
    replace_underscore_excludes: Optional[str] = None
    escape_tag: Optional[bool] = None
    sort_alphabetical: Optional[bool] = None
    core_tags: Optional[str] = None
    core_tags_weight: Optional[float] = None
    add_confident_as_weight: Optional[bool] = None
    use_weight_mapping: Optional[bool] = None
    weight_mapping_exponent: Optional[float] = None
    weight_min: Optional[float] = None
    weight_max: Optional[float] = None
    max_tags: Optional[int] = None
    token_limit: Optional[int] = None
    tag_precision: Optional[int] = None
    overwrite_existing_txt: Optional[bool] = None


# ============== 路由 ==============
@router.get("/models")
async def list_models():
    return {"models": get_available_models(), "current": _current_model_name}


@router.get("/categories")
async def list_categories():
    """前端 chip 排除选项用的标签类别清单。不含 rating（rating 永远单独走 ratings dict）。"""
    # 如果当前已加载模型，把它实际拥有的类别也带上，方便前端做"只显示当前模型有的类别"
    present = None
    if _current_interrogator is not None:
        try:
            cmap = _current_interrogator.get_tag_category_map()
            present = sorted({c for c in cmap.values() if c not in ('rating', 'unknown')})
        except Exception:
            present = None
    return {
        "all": ALL_TAG_CATEGORIES,
        "present": present,
        "labels": {
            'general': '一般',
            'character': '角色',
            'copyright': '版权',
            'artist': '画师',
            'meta': '元数据',
            'model': '模型',
            'quality': '质量',
            'year': '年份',
        },
    }


@router.post("/unload")
async def unload_model():
    global _current_interrogator, _current_model_name
    with _model_lock:
        if _current_interrogator is not None:
            _current_interrogator.unload()
            _current_interrogator = None
            _current_model_name = ""
            return {"success": True, "message": "unloaded"}
    return {"success": True, "message": "no model loaded"}


class PreloadRequest(BaseModel):
    model: str


@router.post("/preload")
def preload_model(req: PreloadRequest):
    """
    把指定模型加载进内存（必要时先从 HF 下载）。前端「加载」按钮调它。
    与 /interrogate 解耦：用户可以先 preload 模型、再上传图片推理，而不是
    在第一张图的 /interrogate 调用里阻塞几分钟做下载，看着像「按了没反应」。

    注意：def 而不是 async def —— FastAPI 会自动把同步路由放到默认 threadpool。
    之前是 async def，里面同步阻塞地做 ONNX 加载/HF 下载（可能 5-10 分钟），
    会卡住整个 event loop：前端轮询 /api/session、/api/tagger/queue 全部排队、
    UI 看起来「按了没反应、后端没任何输出」。改成 def 后这条路由跑在 worker
    线程，主 event loop 继续处理 GET 请求，加载过程中的 [tagger] 进度日志也
    会随 line-buffered stdout 实时刷出来。
    """
    try:
        with _model_lock:
            interrogator = get_interrogator(req.model)
        if interrogator is None or interrogator.model is None:
            return JSONResponse(status_code=500,
                                content={"success": False, "error": f"加载失败: {req.model}"})
        return {"success": True, "model": req.model, "current": _current_model_name}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/interrogate")
async def interrogate(
    image: UploadFile = File(...),
    model: str = Form(...),
    threshold: float = Form(0.35),
    additional_tags: str = Form(""),
    exclude_tags: str = Form(""),
    excluded_categories: str = Form(""),
    replace_underscore: bool = Form(True),
    replace_underscore_excludes: str = Form(""),
    escape_tag: bool = Form(False),
    sort_alphabetical: bool = Form(False),
    core_tags: str = Form(""),
    core_tags_weight: float = Form(1.0),
    add_confident_as_weight: bool = Form(False),
    use_weight_mapping: bool = Form(False),
    weight_mapping_exponent: float = Form(1.0),
    weight_min: float = Form(0.9),
    weight_max: float = Form(1.1),
    max_tags: int = Form(0),
    token_limit: int = Form(75),
    tag_precision: int = Form(2),
):
    """单图推理：multipart 上传 + 表单参数。

    保留 async def 是因为 UploadFile.read() 必须 await；剩下的同步阻塞部分
    （ONNX 加载、推理、postprocess）放到 asyncio.to_thread 里跑，避免 70k 标签
    的 Camie v2 模型推理卡死前端轮询。
    """
    try:
        data = await image.read()

        def _do_inference():
            pil = Image.open(io.BytesIO(data))
            if pil.mode == 'RGBA':
                bg = Image.new('RGB', pil.size, (255, 255, 255))
                bg.paste(pil, mask=pil.split()[3])
                pil = bg
            elif pil.mode != 'RGB':
                pil = pil.convert('RGB')

            with _model_lock:
                interrogator = get_interrogator(model)
                ratings, tags, meta = interrogator.interrogate(pil)
                full_cat_map = interrogator.get_tag_category_map() if interrogator else {}

            present = set(tags.keys()) | set(meta.keys()) | set(ratings.keys())
            tag_categories_subset = {k: full_cat_map.get(k, 'unknown')
                                     for k in present if k in full_cat_map}

            processed = Interrogator.postprocess_tags(
                tags=tags,
                threshold=threshold,
                additional_tags=parse_tag_list(additional_tags),
                exclude_tags=parse_tag_list(exclude_tags),
                excluded_categories=parse_tag_list(excluded_categories),
                tag_category_map=full_cat_map,
                replace_underscore=replace_underscore,
                replace_underscore_excludes=parse_tag_list(replace_underscore_excludes),
                escape_tag=escape_tag,
                sort_by_alphabetical_order=sort_alphabetical,
                core_tags=parse_tag_list(core_tags),
                core_tags_weight=core_tags_weight,
                add_confident_as_weight=add_confident_as_weight,
                use_weight_mapping=use_weight_mapping,
                weight_mapping_exponent=weight_mapping_exponent,
                weight_min=weight_min,
                weight_max=weight_max,
                max_tags=max_tags,
                token_limit=token_limit,
                tag_confidence_precision=tag_precision,
            )
            return ratings, tags, meta, processed, tag_categories_subset

        ratings, tags, meta, processed, tag_categories_subset = await asyncio.to_thread(_do_inference)

        return {
            "success": True,
            "tags": format_tags_output(processed),
            "tags_dict": processed,
            "ratings": ratings,
            "meta": meta,
            "raw_tags": {k: round(v, 4) for k, v in sorted(tags.items(), key=lambda x: -x[1])[:50]},
            "tag_categories": tag_categories_subset,
            "count": len(processed),
        }
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/batch")
async def start_batch(req: BatchRequest, background_tasks: BackgroundTasks):
    """启动批量任务，立刻返回 job_id；调用方再轮询 /batch/{job_id}。"""
    input_path = Path(req.input_path)
    if not input_path.exists():
        return JSONResponse(status_code=400, content={"success": False, "error": f"input not found: {input_path}"})

    settings = req.dict()
    job = BatchJob(job_id=uuid.uuid4().hex[:12], settings=settings)
    with _jobs_lock:
        _jobs[job.job_id] = job
        _gc_jobs()    # 注意：_gc_jobs 内部不再 acquire 锁，避免非递归 Lock 二次 acquire 死锁

    output_dir = Path(req.output_path) if req.output_path else None
    job.thread = threading.Thread(
        target=_run_batch_job,
        args=(job, input_path, output_dir, req.output_format, req.recursive, req.batch_size),
        name=f"tagger-batch-{job.job_id}", daemon=True,
    )
    job.thread.start()
    print(f"[tagger.batch] job {job.job_id} queued: {input_path} ({req.recursive=}, {req.batch_size=})", flush=True)
    return {"success": True, "job_id": job.job_id}


@router.get("/batch/{job_id}")
async def batch_progress(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "job not found"})
    return {"success": True, **job.to_dict()}


@router.post("/batch/{job_id}/cancel")
async def batch_cancel(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "job not found"})
    if job.processor:
        job.processor.cancel()
    return {"success": True, "message": "cancel signaled"}


# ============== 联动队列路由 ==============
@router.get("/queue")
async def queue_snapshot():
    return {"success": True, **tagger_queue.snapshot(),
            "settings": _queue_settings,
            "model": _queue_settings.get("model")}


@router.post("/queue/enqueue")
async def queue_enqueue(req: QueueEnqueue):
    added = tagger_queue.enqueue_many(req.paths)
    return {"success": True, "added": added}


@router.post("/queue/clear")
async def queue_clear():
    tagger_queue.clear()
    return {"success": True, **tagger_queue.snapshot()}


@router.post("/queue/config")
async def queue_config(req: QueueConfig):
    data = req.dict(exclude_unset=True)
    _queue_settings.update(data)
    return {"success": True, "settings": _queue_settings}


# ============== 预设 ==============
@router.get("/presets")
async def list_presets():
    try:
        return {"success": True, "presets": preset_manager.list_presets(),
                "default": preset_manager.get_default()}
    except Exception as e:
        return {"success": False, "error": str(e), "presets": []}


@router.post("/presets")
async def save_preset(request: Request):
    try:
        data = await request.json()
        name = (data.get("name") or "").strip()
        preset_data = data.get("data") or data.get("values") or {}
        if not name:
            return {"success": False, "error": "name required"}
        ok = preset_manager.save(name, preset_data)
        return {"success": ok}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/presets/{name}")
async def load_preset(name: str):
    data = preset_manager.load(name)
    if data:
        return {"success": True, "data": data}
    return JSONResponse(status_code=404, content={"success": False, "error": "preset not found"})


@router.delete("/presets/{name}")
async def delete_preset(name: str):
    ok = preset_manager.delete(name)
    return {"success": ok}


# ============== 健康检查 ==============
@router.get("/health")
async def health():
    return {"status": "ok", "model_loaded": _current_model_name or None,
            "queue": tagger_queue.snapshot()}
