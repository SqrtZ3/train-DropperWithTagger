# tagger/__init__.py - CUDA path setup for ONNX Runtime
import sys


def _safe_print(msg: str) -> None:
    """打印一行，但永远不要因为编码问题（Windows cp936/GBK）让 import 整个崩。
    server.py 入口会把 stdout reconfigure 成 utf-8；但开发者直接 python -c
    'import tagger.xxx' 时 stdout 仍是 GBK，'✓ → ' 这类字符会抛 UnicodeEncodeError。
    flush=True 顺带兜底 stdout 行缓冲未开的场景。"""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass


def setup_cuda_path():
    """从 torch / nvidia-* pip 包补 PATH 与 DLL 目录，让 ONNXRuntime 找到 CUDA 库。"""
    import os
    from pathlib import Path

    try:
        import torch
        torch_lib = Path(torch.__file__).parent / "lib"
        if torch_lib.exists():
            os.environ['PATH'] = str(torch_lib) + os.pathsep + os.environ.get('PATH', '')
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(str(torch_lib))
                except Exception:
                    pass
            _safe_print(f"[tagger] CUDA libs from torch -> {torch_lib}")
    except ImportError:
        pass

    found_nvidia = False
    for path in sys.path:
        if not path or not os.path.exists(path):
            continue
        nvidia_base = Path(path) / "nvidia"
        if nvidia_base.exists() and nvidia_base.is_dir():
            for subpkg in nvidia_base.iterdir():
                if not subpkg.is_dir():
                    continue
                for folder in ("bin", "lib"):
                    lib_path = subpkg / folder
                    if lib_path.exists():
                        os.environ['PATH'] = str(lib_path) + os.pathsep + os.environ.get('PATH', '')
                        if hasattr(os, "add_dll_directory"):
                            try:
                                os.add_dll_directory(str(lib_path))
                            except Exception:
                                pass
                        found_nvidia = True
            if found_nvidia:
                _safe_print(f"[tagger] CUDA libs from nvidia-* -> {nvidia_base}")
                break


setup_cuda_path()
