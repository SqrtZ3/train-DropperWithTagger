# webapp/routes_system.py — index / app.jsx / favicon / 关机 / 队列状态

import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from webapp.config import SCRIPT_DIR
from webapp.session_manager import session
from webapp.thumbnails import remove_thumb_cache
from webapp.vendor import vendor_replacements


router = APIRouter()


# screens/tagger/ 7 个子文件，按依赖顺序拼成一个串后由前端单 <script> 加载。
# 历史：拆成多个 <script type="text/babel"> 后，babel-standalone 偶发把列表里
# 最后一个文件（index.jsx）整段跳过，导致 window.TaggerScreen 不被赋值，
# tagger 整屏空白。把它们拼成一个 bundle 后，babel-standalone 只处理一个文件，
# 这个 race 就消失了。
_TAGGER_BUNDLE_ORDER = [
    "common.jsx",      # Slider/Check/Stat/CollapsiblePanel/DEFAULTS
    "confidence.jsx",  # ConfidencePanel/GroupedConfidencePanel
    "sidebar.jsx",     # 左侧 5 面板
    "single.jsx",      # 单图模式
    "batch.jsx",       # 批量目录模式
    "queue.jsx",       # 联动队列模式
    "index.jsx",       # TaggerScreen 组装根（依赖以上所有）
]


@router.get("/tagger.bundle.jsx")
async def tagger_bundle():
    """把 screens/tagger/ 下的 7 个子文件按依赖顺序拼成一个 JSX 串。"""
    parts = []
    for name in _TAGGER_BUNDLE_ORDER:
        p = os.path.join(SCRIPT_DIR, "screens", "tagger", name)
        if not os.path.exists(p):
            return Response(content=f"/* missing: {name} */", media_type="text/plain", status_code=404)
        with open(p, "r", encoding="utf-8") as f:
            parts.append(f"/* === {name} === */\n{f.read()}\n")
    return Response(content="\n".join(parts), media_type="text/plain")


@router.get("/")
async def read_index():
    index_path = os.path.join(SCRIPT_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
        for k, v in vendor_replacements().items():
            html = html.replace(k, v)
        return HTMLResponse(content=html)
    return HTMLResponse(content="<h1>Error: index.html not found</h1>", status_code=404)


@router.get("/app.jsx")
async def read_app_jsx():
    """新前端的根脚本；用 text/plain 让浏览器原样读到，Babel 在前端解析。"""
    p = os.path.join(SCRIPT_DIR, "app.jsx")
    if not os.path.exists(p):
        return JSONResponse(content={"error": "app.jsx not found"}, status_code=404)
    return FileResponse(p, media_type="text/plain")


@router.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


@router.get("/api/queue_status")
async def get_queue_status():
    return {
        "queue_len": len(session.queue),
        "current_index": session.current_index,
        "total_count": len(session.files),
        "can_undo": bool(session.op_history),
        "output_folder": session.output_folder,
        "processed_count": sum(
            1 for item in session.items.values()
            if item.get("status") in ("cropped", "skipped")
        ),
    }


@router.post("/api/shutdown")
async def shutdown_server():
    print("🔔 正在关闭服务器...")
    # 退出前同步清缩略图缓存：os._exit 会绕过 atexit / FastAPI lifespan
    remove_thumb_cache(session.output_folder)
    import threading

    def kill_self():
        import time
        time.sleep(1)
        print("👋 Bye!")
        os._exit(0)

    threading.Thread(target=kill_self).start()
    return {"status": "shutting_down", "message": "Server is shutting down..."}
