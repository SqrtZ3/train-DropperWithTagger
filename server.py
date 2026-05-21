# -*- coding: utf-8 -*-
"""
Drop WebUI Server V3.1
- 队列缓存与断点续传
- 自由裁切，动态分桶
- 视频智能抽帧
- 相似图片去重
- 支持子文件夹和 ZIP 压缩包
- ONNX 反推标签 + 联动队列（tagger 模块）

历史背景：早期所有逻辑都在这个 2600 行的单文件里。现在按职责拆到 webapp/ 包：
  * webapp/config.py             常量
  * webapp/state.py              进程级单例占位
  * webapp/session_manager.py    SessionManager（断点续作）
  * webapp/similarity.py         相似度聚类
  * webapp/buckets.py            分桶数学
  * webapp/thumbnails.py         .thumb_cache 维护
  * webapp/snapshots.py          相似度结果快照
  * webapp/vendor.py             前端 CDN 本地镜像
  * webapp/routes_*.py           分域 APIRouter
本文件只剩：进程入口 + 子模块加载 + lifespan + 路由注册 + main。
"""

import atexit
import mimetypes
import os
import subprocess
import sys
from contextlib import asynccontextmanager

# ============== stdout/stderr UTF-8（Windows 控制台默认 cp936 会乱码）==============
# line_buffering=True 是关键：之前没开导致 ONNX 模型 5-10 分钟加载期间 [tagger]
# 日志一行都看不到（被 block-buffered 在 stdout 里），用户感觉「按了启动后没反
# 应、后端没任何输出」——其实日志早就 print 了，只是没刷出来。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

# ============== 第三方依赖 ==============
try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError as exc:
    required = ["fastapi", "uvicorn", "python-multipart", "pillow", "imagehash"]
    print(f"缺少依赖 {getattr(exc, 'name', exc)}，正在使用当前解释器安装基础依赖...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *required])
    except Exception as install_exc:
        raise RuntimeError(
            "自动安装依赖失败。请先执行：python -m pip install -r requirements.txt"
        ) from install_exc
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    import uvicorn

# ============== 子模块（webapp 包） ==============
from webapp import state
from webapp.config import (
    BUCKET_STEP_SIZE,
    TARGET_PIXEL_AREA,
    VENDOR_DIR,
)
from webapp.lan import get_lan_addresses
from webapp.session_manager import session
from webapp.thumbnails import remove_thumb_cache
from webapp.vendor import ensure_vendor_files

# ============== 可选业务模块（auto_crop / upscale / get_pictures / tagger） ==============
# 自动裁切建议（边缘 + 显著性 + 阈值融合），抽自旧 drop.py
try:
    from webapp import auto_crop as _auto_crop_mod
    state.HAS_AUTO_CROP = True
    state.auto_crop = _auto_crop_mod
    print("✅ 已加载自动裁切模块 (webapp.auto_crop)")
except Exception as e:
    print(f"⚠️ 自动裁切模块加载失败: {e}")

# RealESRGAN 超分，抽自旧 drop.py。缺 torch/realesrgan/权重时仍可 import，
# upscale.upscale() 内部会自动回退到 PIL LANCZOS。
try:
    from webapp import upscale as _upscale_mod
    state.HAS_UPSCALE = True
    state.upscale = _upscale_mod
    print(f"✅ 已加载超分模块 (webapp.upscale, ENABLE_UPSCALING={_upscale_mod.ENABLE_UPSCALING})")
except Exception as e:
    print(f"⚠️ 超分模块加载失败: {e}")

# 视频关键帧抽取（旧 get_pictures.py 抽到 webapp.video）
try:
    from webapp import video as _video_mod
    state.HAS_VIDEO = True
    state.video = _video_mod
    print("✅ 已加载视频模块 (webapp.video)")
except Exception as e:
    print(f"⚠️ 视频模块加载失败: {e}")

# Tagger（ONNX 反推标签 + 后台联动队列）
try:
    from tagger.api_router import router as tagger_router, tagger_queue
    state.HAS_TAGGER = True
    state.tagger_queue = tagger_queue
    print("✅ 已加载 tagger 模块（合并自 2_tagger）")
except Exception as e:
    tagger_router = None
    print(f"⚠️ tagger 模块加载失败: {e}")
    import traceback as _tb
    _tb.print_exc()


# ============== Lifespan ==============
@asynccontextmanager
async def _lifespan(app: FastAPI):
    # uvicorn server:app 也会走这里；不要只在 __main__ 里准备前端依赖。
    ensure_vendor_files()
    if state.HAS_TAGGER and state.tagger_queue is not None:
        try:
            state.tagger_queue.start()
            # 注册观察者：每次 session 写完一张输出 PNG → 自动入队
            # （受 session.auto_tag_enabled 控制）
            if state.tagger_queue.enqueue not in session.artifact_listeners:
                session.artifact_listeners.append(state.tagger_queue.enqueue)
            print("✅ tagger 后台队列已启动")
        except Exception as e:
            print(f"⚠️ tagger 后台队列启动失败: {e}")
    yield
    if state.HAS_TAGGER and state.tagger_queue is not None:
        try:
            state.tagger_queue.stop(timeout=1.0)
        except Exception:
            pass
    try:
        remove_thumb_cache(session.output_folder)
    except Exception as e:
        print(f"lifespan shutdown cleanup error: {e}")


# ============== App 构建 ==============
app = FastAPI(lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============== 静态资源挂载 ==============
# Babel-loaded .jsx 需要明确的 MIME 才能被部分浏览器读取
mimetypes.add_type("text/plain", ".jsx")

try:
    os.makedirs(VENDOR_DIR, exist_ok=True)
    app.mount("/vendor", StaticFiles(directory=VENDOR_DIR), name="vendor")
except Exception as e:
    print(f"⚠️ 无法挂载 /vendor 静态目录: {e}")

for _sub in ("lib", "screens"):
    _dir = os.path.join(SCRIPT_DIR, _sub)
    try:
        if os.path.isdir(_dir):
            app.mount(f"/{_sub}", StaticFiles(directory=_dir), name=_sub)
    except Exception as e:
        print(f"⚠️ 无法挂载 /{_sub} 静态目录: {e}")

# ============== 路由注册 ==============
from webapp.routes_system import router as system_router
from webapp.routes_session import router as session_router
from webapp.routes_image import router as image_router
from webapp.routes_crop import router as crop_router
from webapp.routes_tagger import router as tagger_link_router
from webapp.routes_video import router as video_router, cleanup_all_video_jobs
from webapp.routes_similarity import router as similarity_router

app.include_router(system_router)
app.include_router(session_router)
app.include_router(image_router)
app.include_router(crop_router)
app.include_router(tagger_link_router)
app.include_router(video_router)
app.include_router(similarity_router)

# Tagger 自己的 /api/tagger/* 路由
if state.HAS_TAGGER and tagger_router is not None:
    app.include_router(tagger_router, prefix="/api/tagger")


# ============== 退出钩子 ==============
atexit.register(lambda: remove_thumb_cache(session.output_folder))
atexit.register(cleanup_all_video_jobs)


# ============== Main ==============
if __name__ == "__main__":
    import argparse
    import math

    print("=" * 50)
    print("Drop WebUI Server V3.1 - Enhanced Edition")
    print("=" * 50)

    _parser = argparse.ArgumentParser(add_help=False)
    _parser.add_argument("--port", type=int, default=int(os.environ.get("DROP_PORT", 6008)))
    _parser.add_argument("--host", default=os.environ.get("DROP_HOST", "0.0.0.0"))
    _args, _ = _parser.parse_known_args()
    _PORT = _args.port
    _HOST = _args.host

    print(f"本机访问: http://localhost:{_PORT}")
    for ip in get_lan_addresses():
        print(f"局域网: http://{ip}:{_PORT}  （手机用同 Wi-Fi 访问此地址）")
    print(f"目标像素面积: {TARGET_PIXEL_AREA} ({int(math.sqrt(TARGET_PIXEL_AREA))})")
    print(f"尺寸对齐: {BUCKET_STEP_SIZE}px")
    print("=" * 50)
    uvicorn.run(app, host=_HOST, port=_PORT)
