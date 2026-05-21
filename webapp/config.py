# webapp/config.py — 常量集中地，避免散落在 server.py 中

import os
import hashlib
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ================= 集中缓存目录 =================
CACHE_DIR = os.path.join(SCRIPT_DIR, ".drop_cache")

def get_session_cache_dir(output_folder: Optional[str]) -> Optional[str]:
    """根据输出目录的绝对路径生成唯一的会话缓存目录，并创建它。"""
    if not output_folder:
        return None
    try:
        h = hashlib.md5(os.path.abspath(output_folder).encode("utf-8")).hexdigest()[:12]
        d = os.path.join(CACHE_DIR, h)
        os.makedirs(d, exist_ok=True)
        return d
    except Exception as e:
        print(f"⚠️ 创建会话缓存目录失败: {e}")
        return None

# ================= 分桶 =================
TARGET_PIXEL_AREA = 1024 * 1024     # 默认 1MP；运行时由前端覆盖
BUCKET_STEP_SIZE = 64
DEFAULT_TARGET_MP = 1.0
MIN_TARGET_MP = 0.05
MAX_TARGET_MP = 9.0
MIN_STEP = 8
MAX_STEP = 512

# ================= 文件位置 =================
HISTORY_FILE = os.path.join(SCRIPT_DIR, "webui_history.json")
THUMB_CACHE_DIRNAME = ".thumb_cache"


# ================= 缩略图 =================
THUMB_MAX_SIDE = 384
THUMB_QUALITY = 78

# ================= 前端依赖 =================
VENDOR_DIR = os.path.join(SCRIPT_DIR, "vendor")
VENDOR_FILES = [
    ("react.min.js", "https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js",
     "https://unpkg.com/react@18/umd/react.production.min.js"),
    ("react-dom.min.js", "https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js",
     "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"),
    ("babel.min.js", "https://cdn.jsdelivr.net/npm/@babel/standalone@7/babel.min.js",
     "https://unpkg.com/@babel/standalone@7/babel.min.js"),
    ("tailwind.js", "https://cdn.tailwindcss.com", None),
]

# ================= 视频 job =================
VIDEO_JOB_LIMIT = 8
VIDEO_JOB_TTL_SEC = 3600

# ================= 快照 =================
SNAPSHOT_LIMIT = 12
