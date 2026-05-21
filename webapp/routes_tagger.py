# webapp/routes_tagger.py — Drop ↔ Tagger 联动入口
# /api/tagger/* 路由是 tagger.api_router 提供的；这里只放跨模块整合用的两个端点。

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from webapp import state
from webapp.config import THUMB_CACHE_DIRNAME
from webapp.schemas import AutoTagToggle
from webapp.session_manager import session


router = APIRouter()


@router.post("/api/session/auto_tag")
async def set_auto_tag(req: AutoTagToggle):
    """切换「自动标注 Drop Studio 输出」开关。开/关都会持久化到 session_state.json。"""
    session.auto_tag_enabled = bool(req.enabled)
    session._save_state()
    return {"success": True, "auto_tag_enabled": session.auto_tag_enabled}


@router.post("/api/session/tag_output")
async def tag_current_output():
    """把当前 output_folder 下所有 PNG 一次性塞进 tagger 队列。"""
    if not state.HAS_TAGGER or state.tagger_queue is None:
        return JSONResponse(content={"error": "tagger 模块不可用"}, status_code=500)
    if not session.output_folder or not os.path.isdir(session.output_folder):
        return JSONResponse(content={"error": "尚未初始化会话或输出目录不存在"}, status_code=400)

    paths = []
    for root, dirs, files in os.walk(session.output_folder):
        if THUMB_CACHE_DIRNAME in dirs:
            dirs.remove(THUMB_CACHE_DIRNAME)
        for fn in files:
            if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                paths.append(os.path.join(root, fn))
    added = state.tagger_queue.enqueue_many(paths)
    return {"success": True, "added": added, "total_scanned": len(paths)}
