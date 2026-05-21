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
# 自动裁切建议（旧 drop.calculate_auto_crop_box 抽到 webapp.auto_crop）
HAS_AUTO_CROP: bool = False
auto_crop: Any = None

# RealESRGAN 超分（旧 drop.get_upscaler 抽到 webapp.upscale）
HAS_UPSCALE: bool = False
upscale: Any = None

HAS_VIDEO: bool = False
video: Any = None  # 引用 webapp.video 模块（旧 get_pictures.py 抽过来的）

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
