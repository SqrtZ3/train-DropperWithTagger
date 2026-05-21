# webapp/session_manager.py — 会话状态管理，支持断点续作
#
# 与原 server.py 行为保持一致；只是把 tagger_queue / HAS_TAGGER 访问改成
# 通过 webapp.state 间接读取（避免循环导入）。

import io
import json
import os
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PIL import Image

from webapp import state
from webapp.config import (
    BUCKET_STEP_SIZE,
    DEFAULT_TARGET_MP,
    MAX_STEP,
    MAX_TARGET_MP,
    MIN_STEP,
    MIN_TARGET_MP,
    THUMB_CACHE_DIRNAME,
    get_session_cache_dir,
)
from webapp.thumbnails import remove_thumb_cache


class SessionManager:
    """管理会话状态，支持断点续传。"""

    def __init__(self):
        self.session_id = None
        self.image_folder = ""
        self.output_folder = ""
        self.files: List[str] = []
        self.queue: List[Dict[str, Any]] = []
        self.items: Dict[str, Dict[str, Any]] = {}
        # 操作历史栈：每项 = {filepath, prev_status, prev_crop_params,
        #                    queue_added_count, prev_index, prev_queue_for_path, op_type}
        self.op_history: List[Dict[str, Any]] = []
        self.current_index = 0
        self.is_initialized = False
        self.save_counter = 0
        self._state_file: Optional[str] = None
        self.ignored_files: set = set()
        # 「扫描目标」相关：可独立于 self.files，例如对输出目录或任意目录做相似度扫描
        self.scan_targets: List[str] = []
        self.scan_root: str = ""
        # 目标分桶分辨率（MP）+ 步长（px），可由前端覆盖
        self.target_mp: float = DEFAULT_TARGET_MP
        self.step: int = BUCKET_STEP_SIZE
        # Tagger 联动
        self.artifact_listeners: List[Callable[[str], None]] = []
        self.auto_tag_enabled: bool = False

    def notify_artifact_saved(self, path: str) -> None:
        """每写完一张输出 PNG 调用一次。仅当 auto_tag_enabled 时触发监听器。"""
        if not self.auto_tag_enabled:
            return
        for cb in list(self.artifact_listeners):
            try:
                cb(path)
            except Exception as e:
                print(f"⚠️ artifact listener error: {e}")

    def reset(self) -> None:
        remove_thumb_cache(self.output_folder)
        self.session_id = None
        self.image_folder = ""
        self.output_folder = ""
        self.files = []
        self.queue = []
        self.items = {}
        self.op_history = []
        self.current_index = 0
        self.is_initialized = False
        self.save_counter = 0
        self._state_file = None
        self.ignored_files = set()
        self.scan_targets = []
        self.scan_root = ""
        # 注意：target_mp / step 不重置，跨会话保留前端最近一次选择
        print("🔄 会话状态已重置")

    def set_target(self, target_mp: Optional[float] = None, step: Optional[int] = None) -> None:
        if target_mp is not None:
            try:
                v = float(target_mp)
                self.target_mp = max(MIN_TARGET_MP, min(MAX_TARGET_MP, v))
            except Exception:
                pass
        if step is not None:
            try:
                s = int(step)
                s = max(MIN_STEP, min(MAX_STEP, (s // 8) * 8 or MIN_STEP))
                self.step = s
            except Exception:
                pass

    @property
    def target_area(self) -> int:
        return int(self.target_mp * 1024 * 1024)

    def initialize(self, input_dir: str, output_dir: str) -> Dict[str, Any]:
        self.image_folder = input_dir
        self.output_folder = output_dir if output_dir else os.path.join(input_dir, "output")

        # Determine the cache directory path
        cache_dir = get_session_cache_dir(self.output_folder)
        if cache_dir:
            self._state_file = os.path.join(cache_dir, "session_state.json")
        else:
            self._state_file = os.path.join(self.output_folder, "session_state.json")

        if not os.path.exists(self.output_folder):
            os.makedirs(self.output_folder)

        recovered = False
        if not self.is_initialized:
            recovered = self._try_restore()

        self.files = self._scan_images(self.image_folder)

        if not self.files:
            raise ValueError(f"目录中没有找到图片: {input_dir}")

        if not recovered:
            self.session_id = str(uuid.uuid4())[:8]
            self.current_index = 0
            self.queue = []
            self.items = {}
            print(f"📌 创建新会话: {self.session_id}")

        self.is_initialized = True
        self._save_state()

        # Clean up legacy cache files/directories in self.output_folder if they exist
        if cache_dir and os.path.abspath(cache_dir) != os.path.abspath(self.output_folder):
            legacy_state = os.path.join(self.output_folder, "session_state.json")
            if os.path.exists(legacy_state):
                try:
                    os.remove(legacy_state)
                    print(f"🧹 已删除旧输出目录中的 session_state.json")
                except Exception as e:
                    print(f"⚠️ 无法删除旧 session_state.json: {e}")

            legacy_tagger = os.path.join(self.output_folder, ".tagger_pending.json")
            if os.path.exists(legacy_tagger):
                try:
                    os.remove(legacy_tagger)
                    print(f"🧹 已删除旧输出目录中的 .tagger_pending.json")
                except Exception as e:
                    print(f"⚠️ 无法删除旧 .tagger_pending.json: {e}")

            legacy_thumb = os.path.join(self.output_folder, THUMB_CACHE_DIRNAME)
            if os.path.exists(legacy_thumb) and os.path.isdir(legacy_thumb):
                try:
                    import shutil
                    new_thumb = os.path.join(cache_dir, THUMB_CACHE_DIRNAME)
                    if not os.path.exists(new_thumb):
                        shutil.move(legacy_thumb, new_thumb)
                        print(f"🚚 已迁移旧输出目录的缩略图缓存")
                    else:
                        shutil.rmtree(legacy_thumb)
                        print(f"🧹 已清理旧输出目录的缩略图缓存目录")
                except Exception as e:
                    print(f"⚠️ 清理/迁移旧缩略图目录失败: {e}")

        # 把 tagger 队列的持久化文件指向新的 cache_dir/.tagger_pending.json
        if state.HAS_TAGGER and state.tagger_queue is not None and self.output_folder:
            try:
                new_tagger_pending = Path(cache_dir if cache_dir else self.output_folder) / ".tagger_pending.json"
                legacy_tagger_pending = Path(self.output_folder) / ".tagger_pending.json"
                
                # If legacy exists and new one does not, move it to new
                if legacy_tagger_pending.exists() and new_tagger_pending != legacy_tagger_pending:
                    try:
                        if not new_tagger_pending.exists():
                            import shutil
                            shutil.move(str(legacy_tagger_pending), str(new_tagger_pending))
                            print(f"🚚 已迁移 tagger 待处理队列文件到缓存目录")
                        else:
                            legacy_tagger_pending.unlink(missing_ok=True)
                    except Exception as e:
                        print(f"⚠️ 迁移 tagger 待处理队列文件失败: {e}")
                
                state.tagger_queue.set_pending_file(new_tagger_pending)
                restored = state.tagger_queue.restore_from_pending_file()
                if restored:
                    print(f"♻️ tagger 队列恢复 {restored} 个未完成项")
            except Exception as e:
                print(f"⚠️ tagger 队列持久化设置失败: {e}")

        return {
            "session_id": self.session_id,
            "count": len(self.files),
            "current_index": self.current_index,
            "output_dir": self.output_folder,
            "recovered": recovered,
            "queue_len": len(self.queue),
            "processed_count": sum(
                1 for item in self.items.values()
                if item.get("status") in ("cropped", "skipped")
            ),
        }

    def _scan_images(self, folder: str) -> List[str]:
        """扫描文件夹中的图片（支持递归和压缩包）。"""
        if not os.path.isdir(folder):
            return []

        exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
        files: List[str] = []
        zip_count = 0
        zip_img_count = 0
        skipped_dirs: List[str] = []

        # 跳过输出目录（默认 input_dir/output）以及缩略图缓存目录，
        # 否则上一次处理生成的图片会被吸进来当作源图片，下次去重时混入。
        try:
            abs_output = os.path.abspath(self.output_folder) if self.output_folder else None
        except Exception:
            abs_output = None

        print(f"📂 正在扫描目录: {folder} ...")

        for root, dirs, filenames in os.walk(folder):
            pruned = []
            for d in list(dirs):
                full_d = os.path.abspath(os.path.join(root, d))
                if abs_output and full_d == abs_output:
                    pruned.append(full_d)
                    dirs.remove(d)
                elif d == THUMB_CACHE_DIRNAME:
                    pruned.append(full_d)
                    dirs.remove(d)
                else:
                    try:
                        markers = (
                            os.path.exists(os.path.join(full_d, "session_state.json")) or
                            os.path.isdir(os.path.join(full_d, THUMB_CACHE_DIRNAME))
                        )
                    except Exception:
                        markers = False
                    if markers:
                        pruned.append(full_d)
                        dirs.remove(d)
            if pruned:
                skipped_dirs.extend(pruned)

            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext in exts:
                    full_path = os.path.join(root, filename)
                    if full_path not in self.ignored_files:
                        files.append(full_path)
                elif ext == ".zip":
                    zip_count += 1
                    zip_path = os.path.join(root, filename)
                    try:
                        with zipfile.ZipFile(zip_path, "r") as zf:
                            for name in zf.namelist():
                                if name.endswith("/"):
                                    continue
                                _, inner_ext = os.path.splitext(name)
                                if inner_ext.lower() in exts:
                                    zip_uri = f"zip://{zip_path}::{name}"
                                    if zip_uri not in self.ignored_files:
                                        files.append(zip_uri)
                                        zip_img_count += 1
                    except Exception as e:
                        print(f"⚠️ 无法读取压缩包 {zip_path}: {e}")

        files = sorted(list(set(files)))
        if skipped_dirs:
            print(f"⛔ 跳过子目录（输出/缩略图缓存）: {len(skipped_dirs)} 个")
        print(
            f"✅ 扫描完成: 共 {len(files)} 张图片 "
            f"(含 {zip_count} 个压缩包内的 {zip_img_count} 张, 忽略 {len(self.ignored_files)} 张)"
        )
        return files

    def _get_image_data(self, filepath: str) -> Optional[io.BytesIO]:
        """读取图片数据（支持普通文件和 ZIP 内文件）。"""
        try:
            if filepath.startswith("zip://"):
                parts = filepath[6:].split("::", 1)
                if len(parts) != 2:
                    return None
                zip_path, inner_path = parts

                if not os.path.exists(zip_path):
                    print(f"❌ Zip file not found: {zip_path}")
                    return None

                with zipfile.ZipFile(zip_path, "r") as zf:
                    data = zf.read(inner_path)
                    return io.BytesIO(data)
            else:
                if not os.path.exists(filepath):
                    return None
                with open(filepath, "rb") as f:
                    return io.BytesIO(f.read())
        except Exception as e:
            print(f"Read error {filepath}: {e}")
            return None

    def _try_restore(self) -> bool:
        if not self._state_file:
            return False

        restore_path = self._state_file
        legacy_state = os.path.join(self.output_folder, "session_state.json")
        if not os.path.exists(restore_path) and os.path.exists(legacy_state):
            restore_path = legacy_state

        if not os.path.exists(restore_path):
            return False

        try:
            with open(restore_path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            if saved.get("image_folder") != self.image_folder:
                return False

            self.session_id = saved.get("session_id", str(uuid.uuid4())[:8])
            self.current_index = saved.get("current_index", 0)
            self.queue = saved.get("queue", [])
            self.items = saved.get("items", {})
            self.op_history = saved.get("op_history", [])
            self.ignored_files = set(saved.get("ignored_files", []))
            self.auto_tag_enabled = bool(saved.get("auto_tag_enabled", False))

            print(
                f"♻️ 恢复会话 {self.session_id}: 当前位置 {self.current_index}, "
                f"队列 {len(self.queue)} 项, 忽略 {len(self.ignored_files)} 文件"
            )
            return True

        except Exception as e:
            print(f"⚠️ 恢复会话失败: {e}")
            return False

    def _save_state(self) -> None:
        if not self._state_file:
            return

        try:
            payload = {
                "session_id": self.session_id,
                "image_folder": self.image_folder,
                "output_folder": self.output_folder,
                "current_index": self.current_index,
                "queue": self.queue,
                "items": self.items,
                "op_history": self.op_history[-200:],
                "ignored_files": list(self.ignored_files),
                "auto_tag_enabled": self.auto_tag_enabled,
                "saved_at": datetime.now().isoformat(),
            }

            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"⚠️ 保存状态失败: {e}")

    def get_image_info(self, index: int) -> Optional[Dict[str, Any]]:
        if index < 0 or index >= len(self.files):
            return None

        filepath = self.files[index]

        if filepath.startswith("zip://"):
            parts = filepath[6:].split("::", 1)
            filename = f"[ZIP] {os.path.basename(parts[0])}/{parts[1]}"
        else:
            filename = os.path.relpath(filepath, self.image_folder)

        try:
            img_data = self._get_image_data(filepath)
            if img_data is None:
                return None

            with Image.open(img_data) as img:
                width, height = img.size

        except Exception as e:
            print(f"Get info error: {e}")
            return None

        suggested_box = self._get_smart_crop_suggestion(filepath)
        if suggested_box is None:
            suggested_box = (0, 0, width, height)

        item_state = self.items.get(filepath, {})

        return {
            "index": index,
            "filename": filename,
            "filepath": filepath,
            "width": width,
            "height": height,
            "suggested_box": suggested_box,
            "url": f"/api/file/{index}",
            "status": item_state.get("status", "pending"),
            "saved_crops": item_state.get("crop_params", []),
        }

    def _get_smart_crop_suggestion(self, filepath: str):
        try:
            img_data = self._get_image_data(filepath)
            if not img_data:
                return None

            with Image.open(img_data) as img:
                img_w, img_h = img.size

                if state.HAS_AUTO_CROP and state.auto_crop is not None:
                    return state.auto_crop.calculate_auto_crop_box(img, 1.0)

                return (0, 0, img_w, img_h)
        except Exception:
            return None

    def save_crop(self, filepath: str, crops: List[Dict], status: str) -> Dict[str, Any]:
        timestamp = datetime.now().isoformat()

        prev_item = self.items.get(filepath)
        prev_status = prev_item.get("status") if prev_item else "pending"
        prev_crop_params = list(prev_item.get("crop_params", [])) if prev_item else []
        prev_index = self.current_index
        
        # Align current_index with the saved filepath index to ensure correct advancing
        if filepath in self.files:
            self.current_index = self.files.index(filepath)

        prev_queue_for_path = [q for q in self.queue if q["filepath"] == filepath]

        if status == "skip":
            self.items[filepath] = {
                "status": "skipped", "crop_params": [], "timestamp": timestamp,
            }
            self.op_history.append({
                "op_type": "skip",
                "filepath": filepath,
                "prev_status": prev_status,
                "prev_crop_params": prev_crop_params,
                "prev_queue_for_path": prev_queue_for_path,
                "queue_added_count": 0,
                "prev_index": prev_index,
            })
            print(f"⏭ 跳过: {filepath}")
        else:
            queue_added_count = 0
            for i, crop in enumerate(crops):
                self.queue.append({
                    "filepath": filepath,
                    "filename": os.path.basename(filepath) if not filepath.startswith("zip://") else filepath,
                    "x": crop["x"], "y": crop["y"], "w": crop["w"], "h": crop["h"],
                    "crop_id": i,
                })
                queue_added_count += 1

            self.items[filepath] = {
                "status": "cropped", "crop_params": crops, "timestamp": timestamp,
            }
            self.op_history.append({
                "op_type": "crop",
                "filepath": filepath,
                "prev_status": prev_status,
                "prev_crop_params": prev_crop_params,
                "prev_queue_for_path": prev_queue_for_path,
                "queue_added_count": queue_added_count,
                "prev_index": prev_index,
            })
            print(f"➕ 加入队列: {filepath} ({len(crops)} 个裁切框)")

        self._advance_to_next_pending()
        self._save_state()

        return {
            "status": "ok",
            "queue_len": len(self.queue),
            "current_index": self.current_index,
            "can_undo": bool(self.op_history),
        }

    def _advance_to_next_pending(self) -> None:
        for i in range(self.current_index + 1, len(self.files)):
            fpath = self.files[i]
            if fpath not in self.items or self.items[fpath].get("status") == "pending":
                self.current_index = i
                return
        self.current_index = len(self.files)

    def go_to_image(self, index: int) -> Dict[str, Any]:
        if 0 <= index < len(self.files):
            self.current_index = index
            self._save_state()
            return {"status": "ok", "current_index": index}
        return {"status": "error", "message": "索引超出范围"}

    def undo_last(self) -> Dict[str, Any]:
        if not self.op_history:
            return {"status": "error", "message": "没有可撤销的操作"}

        last = self.op_history.pop()
        filepath = last["filepath"]
        op_type = last.get("op_type", "crop")
        added = last.get("queue_added_count", 0)

        if added > 0 and len(self.queue) >= added:
            removed = 0
            for k in range(len(self.queue) - 1, -1, -1):
                if removed >= added:
                    break
                if self.queue[k]["filepath"] == filepath:
                    del self.queue[k]
                    removed += 1

        if last.get("prev_status", "pending") == "pending" and not last.get("prev_crop_params"):
            self.items.pop(filepath, None)
        else:
            self.items[filepath] = {
                "status": last["prev_status"],
                "crop_params": last.get("prev_crop_params", []),
                "timestamp": None,
            }

        try:
            self.current_index = self.files.index(filepath)
        except ValueError:
            self.current_index = max(0, last.get("prev_index", self.current_index))

        self._save_state()

        return {
            "status": "ok",
            "op_type": op_type,
            "undone_file": filepath,
            "queue_len": len(self.queue),
            "current_index": self.current_index,
            "can_undo": bool(self.op_history),
        }

    def get_session_state(self) -> Dict[str, Any]:
        tagger_snap = None
        if state.HAS_TAGGER and state.tagger_queue is not None:
            try:
                tagger_snap = state.tagger_queue.snapshot()
            except Exception:
                tagger_snap = None
        return {
            "session_id": self.session_id,
            "is_initialized": self.is_initialized,
            "image_folder": self.image_folder,
            "output_folder": self.output_folder,
            "total_count": len(self.files),
            "current_index": self.current_index,
            "queue_len": len(self.queue),
            "items": self.items,
            "can_undo": bool(self.op_history),
            "processed_count": sum(
                1 for item in self.items.values()
                if item.get("status") in ("cropped", "skipped")
            ),
            "pending_count": len(self.files) - sum(
                1 for item in self.items.values()
                if item.get("status") in ("cropped", "skipped")
            ),
            "target_mp": self.target_mp,
            "step_size": self.step,
            "target_area": self.target_area,
            "auto_tag_enabled": self.auto_tag_enabled,
            "tagger_queue": tagger_snap,
            "has_tagger": state.HAS_TAGGER,
        }

    def clear_session(self) -> None:
        if self._state_file and os.path.exists(self._state_file):
            try:
                os.remove(self._state_file)
                print("🗑 会话状态文件已删除")
            except Exception as e:
                print(f"⚠️ 删除会话状态文件失败: {e}")
        remove_thumb_cache(self.output_folder)

    def add_to_ignored(self, filepaths: List[str]) -> None:
        for fp in filepaths:
            self.ignored_files.add(fp)
        prev_focus = (
            self.files[self.current_index]
            if (0 <= self.current_index < len(self.files)) else None
        )
        self.files = [f for f in self.files if f not in self.ignored_files]
        if prev_focus is not None and prev_focus in self.files:
            self.current_index = self.files.index(prev_focus)
        else:
            self.current_index = min(self.current_index, max(0, len(self.files) - 1))
        self._save_state()

    def remove_from_ignored(self, filepaths: List[str]) -> int:
        removed = 0
        for fp in filepaths:
            if fp in self.ignored_files:
                self.ignored_files.discard(fp)
                removed += 1
        if removed and self.image_folder and os.path.isdir(self.image_folder):
            prev_focus = (
                self.files[self.current_index]
                if (0 <= self.current_index < len(self.files)) else None
            )
            self.files = self._scan_images(self.image_folder)
            if prev_focus is not None and prev_focus in self.files:
                self.current_index = self.files.index(prev_focus)
            else:
                self.current_index = min(self.current_index, max(0, len(self.files) - 1))
        self._save_state()
        return removed

    def scan_folder_for_targets(self, folder: str, skip_prev_output: bool = False) -> List[str]:
        """扫描任意磁盘目录（递归），返回图片路径列表，挂到 self.scan_targets。

        skip_prev_output=True 时，会跳过包含 session_state.json 的子目录。
        """
        if not folder or not os.path.isdir(folder):
            self.scan_targets = []
            self.scan_root = ""
            raise ValueError(f"目录不存在: {folder}")

        exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
        result: List[str] = []
        for root, dirs, filenames in os.walk(folder):
            if THUMB_CACHE_DIRNAME in dirs:
                dirs.remove(THUMB_CACHE_DIRNAME)
            if skip_prev_output:
                for d in list(dirs):
                    full_d = os.path.join(root, d)
                    try:
                        if os.path.exists(os.path.join(full_d, "session_state.json")):
                            dirs.remove(d)
                    except Exception:
                        pass
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in exts:
                    result.append(os.path.join(root, fn))
        result.sort()
        self.scan_targets = result
        self.scan_root = os.path.abspath(folder)
        print(f"📂 扫描目标目录 {folder}: {len(result)} 张图片")
        return result

    def is_path_under_scan_root(self, path: str) -> bool:
        if not self.scan_root:
            return False
        try:
            ap = os.path.abspath(path)
            return os.path.commonpath([ap, self.scan_root]) == self.scan_root
        except Exception:
            return False


# 全局会话单例
session = SessionManager()
# 兼容：旧代码可能 `from webapp import state; state.session` 访问
state.session = session
