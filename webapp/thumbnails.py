# webapp/thumbnails.py — .thumb_cache 维护
#
# 缩略图缓存放在输出目录里的 .thumb_cache 子目录，跟会话生命周期一致。
# 退出/重置会被清理。

import hashlib
import io
import os
import shutil
from typing import Callable, Optional

from PIL import Image

from webapp.config import THUMB_CACHE_DIRNAME, THUMB_MAX_SIDE, THUMB_QUALITY


def remove_thumb_cache(folder: Optional[str]) -> None:
    """删除某个输出目录下的 .thumb_cache（best-effort）。"""
    if not folder:
        return
    from webapp.config import get_session_cache_dir
    cache_dir = get_session_cache_dir(folder)
    targets = []
    if cache_dir:
        targets.append(os.path.join(cache_dir, THUMB_CACHE_DIRNAME))
    targets.append(os.path.join(folder, THUMB_CACHE_DIRNAME))
    
    for target in targets:
        if not os.path.isdir(target):
            continue
        try:
            shutil.rmtree(target, ignore_errors=True)
            if not os.path.exists(target):
                print(f"🧹 已清理缩略图缓存: {target}")
        except Exception as e:
            print(f"⚠️ 清理缩略图缓存失败 {target}: {e}")


def thumb_cache_dir(base_folder: Optional[str]) -> Optional[str]:
    if not base_folder:
        return None
    from webapp.config import get_session_cache_dir
    cache_dir = get_session_cache_dir(base_folder)
    if not cache_dir:
        cache_dir = base_folder
    d = os.path.join(cache_dir, THUMB_CACHE_DIRNAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    return d


def _thumb_key(filepath: str) -> str:
    """根据原文件路径 + 修改时间生成缓存键，避免源文件改了仍取旧缩略图。"""
    try:
        if filepath.startswith("zip://"):
            mtime = 0
        else:
            mtime = int(os.path.getmtime(filepath)) if os.path.exists(filepath) else 0
    except Exception:
        mtime = 0
    h = hashlib.md5(
        f"{filepath}|{mtime}|{THUMB_MAX_SIDE}|{THUMB_QUALITY}".encode("utf-8")
    ).hexdigest()
    return f"{h}.jpg"


def build_thumbnail(filepath: str,
                    base_folder: Optional[str],
                    zip_loader: Optional[Callable[[str], Optional[io.BytesIO]]] = None) -> Optional[bytes]:
    """从原图生成 JPEG 缩略图字节，命中缓存则直接读盘。

    `base_folder` 决定缓存目录位置；通常传 session.output_folder。
    `zip_loader` 用于处理 zip:// 路径，传 session._get_image_data 即可。
    """
    cache_dir = thumb_cache_dir(base_folder)
    cache_path = None
    if cache_dir:
        cache_path = os.path.join(cache_dir, _thumb_key(filepath))
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as f:
                    return f.read()
            except Exception:
                pass

    img_data = None
    if filepath.startswith("zip://") and zip_loader is not None:
        img_data = zip_loader(filepath)

    loaded = None
    try:
        if img_data is not None:
            loaded = Image.open(img_data)
        else:
            if not os.path.exists(filepath):
                return None
            loaded = Image.open(filepath)

        img = loaded
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            if img.mode in ("RGBA", "LA"):
                bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        data = buf.getvalue()
        if cache_path:
            try:
                tmp = cache_path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, cache_path)
            except Exception as e:
                print(f"⚠️ 缩略图缓存写失败 {cache_path}: {e}")
        return data
    except Exception as e:
        print(f"Thumb error {filepath}: {e}")
        return None
    finally:
        if loaded is not None:
            try:
                loaded.close()
            except Exception:
                pass


def media_type_for(filepath: str) -> str:
    ext = os.path.splitext(filepath.split("::")[-1] if "::" in filepath else filepath)[1].lower()
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")
