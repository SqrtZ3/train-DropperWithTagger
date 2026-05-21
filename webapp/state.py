# webapp/state.py — 进程级共享状态。
#
# 这里只放变量名占位；server.py 启动时按可用性回填。
# 其他模块通过 `from webapp import state` 然后 `state.foo` 访问，
# 这样能在 server.py 写完赋值后被实时读到（避免 `from webapp.state import foo` 在导入时把绑定固化）。

from typing import Any, Optional

# ============== 主会话单例（由 webapp.session_manager 提供） ==============
# 在 SessionManager 模块导入时创建并赋值到这里。
session: Any = None

# ============== 子模块可用性 + 句柄 ==============
HAS_CORE: bool = False
core_logic: Any = None  # 引用 drop.py 模块（含 calculate_auto_crop_box / get_upscaler / ENABLE_UPSCALING）

HAS_VIDEO: bool = False
video_logic: Any = None  # 引用 get_pictures.py 模块

HAS_TAGGER: bool = False
tagger_queue: Optional[Any] = None  # 引用 tagger.api_router.tagger_queue

# ============== 相似度扫描进度（前端轮询） ==============
similarity_progress = {
    "running": False,
    "phase": "",      # 'hashing' | 'clustering' | 'done'
    "current": 0,
    "total": 0,
    "cancel": False,
    "message": "",
}


def reset_similarity_progress() -> None:
    similarity_progress.update({
        "running": False, "phase": "", "current": 0,
        "total": 0, "cancel": False, "message": "",
    })
