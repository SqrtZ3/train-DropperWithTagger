# webapp/vendor.py — React/Babel CDN 资源本地镜像
#
# 启动时把 React/ReactDOM/Babel 拉到本地 vendor/ 目录，
# 拉不到就退回 CDN。手机用同 Wi-Fi 也能离线打开 WebUI。

import os
import ssl
import urllib.request
from typing import Dict

from webapp.config import VENDOR_DIR, VENDOR_FILES


def vendor_path(name: str) -> str:
    return os.path.join(VENDOR_DIR, name)


def vendor_ok(name: str) -> bool:
    p = vendor_path(name)
    try:
        return os.path.isfile(p) and os.path.getsize(p) > 1024
    except OSError:
        return False


def ensure_vendor_files() -> None:
    """启动时把前端 CDN 资源拉到本地，下载失败也不阻塞启动。"""
    try:
        os.makedirs(VENDOR_DIR, exist_ok=True)
    except OSError as e:
        print(f"⚠️ 无法创建 vendor 目录: {e}")
        return

    ctx = ssl.create_default_context()

    for name, primary, fallback in VENDOR_FILES:
        if vendor_ok(name):
            continue
        for url in (primary, fallback):
            if not url:
                continue
            try:
                print(f"⏬ 缓存前端依赖 {name} ← {url}")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    data = resp.read()
                if not data or len(data) < 1024:
                    raise IOError(f"内容过小 ({len(data)} bytes)")
                with open(vendor_path(name), "wb") as f:
                    f.write(data)
                print(f"  ✅ {name} ({len(data) / 1024:.1f} KB)")
                break
            except Exception as e:
                print(f"  ⚠️ 失败: {e}")
                continue
        else:
            print(f"  ⚠️ {name} 全部源失败，将退回 CDN（手机若无外网将无法加载）")


def vendor_replacements() -> Dict[str, str]:
    """根据本地 vendor 命中情况，给 index.html 占位符填地址。"""
    return {
        "__JS_REACT__": "/vendor/react.min.js" if vendor_ok("react.min.js")
            else "https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js",
        "__JS_REACT_DOM__": "/vendor/react-dom.min.js" if vendor_ok("react-dom.min.js")
            else "https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js",
        "__JS_BABEL__": "/vendor/babel.min.js" if vendor_ok("babel.min.js")
            else "https://cdn.jsdelivr.net/npm/@babel/standalone@7/babel.min.js",
        "__JS_TAILWIND__": "/vendor/tailwind.js" if vendor_ok("tailwind.js")
            else "https://cdn.tailwindcss.com",
    }
