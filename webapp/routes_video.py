# webapp/routes_video.py — 视频抽帧：两步式 /api/video/probe + /api/video/save
#
# 旧 /api/extract_video 一键路由已删除：前端 (screens/video.jsx) 走 probe + save 流程，
# 没人调旧路由。需要的话从 git 历史里捞回来。

import os
import shutil
import secrets
import traceback
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from webapp import state
from webapp.config import SCRIPT_DIR, VIDEO_JOB_LIMIT, VIDEO_JOB_TTL_SEC
from webapp.schemas import VideoProbeRequest, VideoSaveRequest


router = APIRouter()


# ============== 视频 job 管理 ==============
_VIDEO_JOBS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _video_job_dir(job_id: str) -> str:
    return os.path.join(SCRIPT_DIR, ".video_jobs", job_id)


def _gc_video_jobs(force_all: bool = False) -> None:
    """清理过期 / 超出 LRU 上限的 job。"""
    now = datetime.now()
    expired = []
    for jid, j in list(_VIDEO_JOBS.items()):
        try:
            last = datetime.fromisoformat(j.get("last_access", j.get("created_at")))
            if force_all or (now - last).total_seconds() > VIDEO_JOB_TTL_SEC:
                expired.append(jid)
        except Exception:
            expired.append(jid)
    while len(_VIDEO_JOBS) - len(expired) > VIDEO_JOB_LIMIT:
        oldest = next(iter(_VIDEO_JOBS))
        if oldest not in expired:
            expired.append(oldest)
        else:
            break
    for jid in expired:
        d = _video_job_dir(jid)
        _VIDEO_JOBS.pop(jid, None)
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


def _touch_video_job(job_id: str) -> None:
    job = _VIDEO_JOBS.get(job_id)
    if job is not None:
        job["last_access"] = datetime.now().isoformat()
        _VIDEO_JOBS.move_to_end(job_id)


def cleanup_all_video_jobs() -> None:
    """供 server.py 的 atexit 钩子调用。"""
    _gc_video_jobs(force_all=True)


# ============== 路由 ==============
@router.post("/api/video/probe")
async def video_probe(req: VideoProbeRequest):
    """分析视频，返回所有候选关键帧 + 它们的缩略图 URL。不写出任何最终文件。"""
    if not state.HAS_VIDEO:
        return JSONResponse(content={"error": "视频模块未加载"}, status_code=500)
    video_path = (req.video_path or "").strip().strip('"').strip("'")
    if not video_path:
        return JSONResponse(content={"error": "请提供视频路径"}, status_code=400)
    if not os.path.exists(video_path):
        return JSONResponse(content={"error": f"找不到视频文件: {video_path}"}, status_code=404)
    if not state.video.is_video_path(video_path):
        return JSONResponse(content={
            "error": f"不支持的视频扩展名: {os.path.splitext(video_path)[1]}",
            "supported": list(state.video.SUPPORTED_VIDEO_EXTENSIONS),
        }, status_code=400)

    _gc_video_jobs()
    job_id = secrets.token_urlsafe(8)
    job_dir = _video_job_dir(job_id)
    os.makedirs(job_dir, exist_ok=True)

    scene_thr = req.scene_threshold if req.scene_threshold is not None else 0.4
    try:
        info = state.video.probe_video_keyframes(
            video_path,
            scene_threshold=float(scene_thr),
            max_candidates=int(req.max_candidates or 0),
            sample_rate_hz=float(req.sample_rate_hz or 4.0),
            thumb_max_side=512,
            write_thumbs_to=job_dir,
        )
    except Exception as e:
        traceback.print_exc()
        shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse(content={"error": f"分析失败: {e}"}, status_code=500)

    if not info.get("ok"):
        shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse(content={"error": info.get("error") or "未能识别候选关键帧"},
                            status_code=500)

    _VIDEO_JOBS[job_id] = {
        "video_path": video_path,
        "job_dir": job_dir,
        "info": info,
        "created_at": datetime.now().isoformat(),
        "last_access": datetime.now().isoformat(),
    }
    while len(_VIDEO_JOBS) > VIDEO_JOB_LIMIT:
        oldest_id, oldest_job = next(iter(_VIDEO_JOBS.items()))
        _VIDEO_JOBS.pop(oldest_id, None)
        try:
            shutil.rmtree(oldest_job.get("job_dir", ""), ignore_errors=True)
        except Exception:
            pass

    candidates = [{
        "idx": c["idx"], "ts": c["ts"],
        "thumb_url": f"/api/video/thumb/{job_id}/{c['idx']}",
    } for c in info.get("candidates", [])]
    print(f"🎞️ probe 完成: {video_path} → {len(candidates)} 候选 ({job_id})")
    return {
        "job_id": job_id,
        "video_path": video_path,
        "total_frames": info["total_frames"],
        "fps": info["fps"], "duration": info["duration"],
        "width": info["width"], "height": info["height"],
        "candidates": candidates,
        "default_output_dir": os.path.join(os.path.dirname(video_path), "extracted_frames"),
    }


@router.get("/api/video/thumb/{job_id}/{idx}")
async def video_thumb(job_id: str, idx: int):
    job = _VIDEO_JOBS.get(job_id)
    if not job:
        return JSONResponse(content={"error": "job 已过期，请重新分析"}, status_code=410)
    _touch_video_job(job_id)
    name = f"{int(idx):06d}.jpg"
    p = os.path.join(job["job_dir"], name)
    if not os.path.exists(p):
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    return FileResponse(p, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.post("/api/video/save")
async def video_save(req: VideoSaveRequest):
    """把用户选中的候选帧（按 idx）从原始视频里抠出来写入 output_dir。"""
    job = _VIDEO_JOBS.get(req.job_id)
    if not job:
        return JSONResponse(content={"error": "job 已过期，请重新分析"}, status_code=410)
    _touch_video_job(req.job_id)

    if not req.indices:
        return JSONResponse(content={"error": "未选中任何候选帧"}, status_code=400)

    video_path = job["video_path"]
    if not os.path.exists(video_path):
        return JSONResponse(content={"error": f"原视频已不存在: {video_path}"}, status_code=404)

    output_dir = (req.output_dir or "").strip()
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(video_path), "extracted_frames")
    os.makedirs(output_dir, exist_ok=True)

    try:
        saved, written = state.video.save_selected_keyframes(
            video_path, output_dir, req.indices,
            name_prefix=req.name_prefix or None,
            start_counter=1, zero_pad=4,
        )
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(content={"error": f"保存失败: {e}"}, status_code=500)

    print(f"💾 已保存 {saved} 帧 → {output_dir}")
    return {
        "status": "ok",
        "saved": saved,
        "output_dir": output_dir,
        "files": [os.path.basename(p) for p in written],
    }


@router.post("/api/video/cleanup/{job_id}")
async def video_cleanup(job_id: str):
    job = _VIDEO_JOBS.pop(job_id, None)
    if job:
        try:
            shutil.rmtree(job.get("job_dir", ""), ignore_errors=True)
        except Exception:
            pass
    _gc_video_jobs()
    return {"status": "ok"}


@router.get("/api/video/supported")
async def video_supported():
    if not state.HAS_VIDEO:
        return {"supported": [], "enabled": False}
    return {
        "supported": list(state.video.SUPPORTED_VIDEO_EXTENSIONS),
        "enabled": True,
    }
