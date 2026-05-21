# tagger/queue_worker.py - Background tagging worker for Drop Studio integration.
#
# 一个 daemon 线程，从 queue.Queue 拉路径 → 凑成 batch_size 张 → 调 interrogator.interrogate_batch →
# 把标签写到 PNG 旁边的 .txt。默认 skip-if-exists（不覆盖源图自带的 caption）。
#
# 持久化：每次 enqueue 都把当前 pending 列表写到 .tagger_pending.json，崩溃后启动恢复用。

import json
import queue
import threading
import traceback
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List, Set
from collections import deque

from PIL import Image

from tagger.config import config
from tagger.utils import (
    parse_tag_list,
    format_tags_output,
    _open_rgb,
)


PENDING_FILENAME = ".tagger_pending.json"


def _now() -> float:
    import time
    return time.time()


class TaggerQueue:
    """单 worker 线程 + queue.Queue + model_lock。

    使用流程：
      tq = TaggerQueue(get_interrogator_fn, default_settings_fn, ...)
      tq.start()
      tq.enqueue("/path/to/foo.png")
      tq.snapshot()  # {pending, processing, done, failed, recent}
      tq.stop()
    """

    def __init__(
        self,
        get_interrogator: Callable[[str], Any],
        get_settings: Callable[[], Dict[str, Any]],
        model_lock: threading.Lock,
        batch_size: int = 8,
        drain_timeout: float = 2.0,
        max_recent: int = 50,
    ):
        self._get_interrogator = get_interrogator
        self._get_settings = get_settings
        self._model_lock = model_lock
        self._batch_size = batch_size
        self._drain_timeout = drain_timeout

        self._q: queue.Queue = queue.Queue()
        self._pending_set: Set[str] = set()    # 用于去重，与 q 中保持一致
        self._lock = threading.Lock()           # 守 _pending_set / _status / _recent

        self._status = {
            "pending": 0,
            "processing": 0,
            "done": 0,
            "failed": 0,
            "skipped": 0,
            "last_error": "",
            "current_file": "",
            "active": False,
        }
        self._recent: deque = deque(maxlen=max_recent)
        self._pending_file: Optional[Path] = None    # 由 set_pending_file 设定
        self._worker: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._current_model: Optional[str] = None

    # ============== 持久化 ==============
    def set_pending_file(self, path: Optional[Path]):
        """切换持久化文件（通常等于 session.output_folder/.tagger_pending.json）。"""
        with self._lock:
            self._pending_file = Path(path) if path else None
            self._save_pending_unsafe()

    def _save_pending_unsafe(self):
        if not self._pending_file:
            return
        try:
            self._pending_file.parent.mkdir(parents=True, exist_ok=True)
            data = {"pending": sorted(self._pending_set)}
            tmp = self._pending_file.with_suffix(self._pending_file.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self._pending_file)
        except Exception as e:
            print(f"[tagger.queue] save pending failed: {e}")

    def restore_from_pending_file(self):
        """启动时把上次崩溃留下的 pending 重新入队。"""
        if not self._pending_file or not self._pending_file.exists():
            return 0
        try:
            data = json.loads(self._pending_file.read_text(encoding='utf-8'))
            paths = data.get("pending") if isinstance(data, dict) else None
            if not paths:
                return 0
            added = 0
            for p in paths:
                if Path(p).exists():
                    self.enqueue(p, _from_restore=True)
                    added += 1
            print(f"[tagger.queue] Restored {added} pending paths from {self._pending_file}")
            return added
        except Exception as e:
            print(f"[tagger.queue] restore failed: {e}")
            return 0

    # ============== 队列操作 ==============
    def enqueue(self, path: str, _from_restore: bool = False) -> bool:
        path = str(path)
        with self._lock:
            if path in self._pending_set:
                return False
            self._pending_set.add(path)
            self._status["pending"] = len(self._pending_set)
            if not _from_restore:
                self._save_pending_unsafe()
        self._q.put(path)
        return True

    def enqueue_many(self, paths: List[str]) -> int:
        added = 0
        for p in paths:
            if self.enqueue(p):
                added += 1
        return added

    def clear(self):
        """清空 pending + 重置 done/skipped/failed/recent 计数。

        旧行为只清 pending，但 UI 上「完成 / 跳过 / 失败」三个 Stat 卡片永远只增不
        减，用户点了「清空」之后看到这些数字纹丝不动会以为没清掉。已经写盘的 .txt
        不受影响（这部分提示在前端 confirm 文案里）。"""
        with self._lock:
            self._pending_set.clear()
            self._status["pending"] = 0
            self._status["done"] = 0
            self._status["skipped"] = 0
            self._status["failed"] = 0
            self._status["last_error"] = ""
            self._status["current_file"] = ""
            self._recent.clear()
            self._save_pending_unsafe()
        # 排空 queue
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            snap = dict(self._status)
            snap["pending"] = len(self._pending_set)
            snap["recent"] = list(self._recent)
        return snap

    # ============== 主循环 ==============
    def start(self):
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._loop, name="tagger-queue", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 2.0):
        self._stop_event.set()
        # 投一个空标记唤醒
        try:
            self._q.put_nowait("__STOP__")
        except Exception:
            pass
        if self._worker:
            self._worker.join(timeout=timeout)

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                first = self._q.get(timeout=self._drain_timeout)
            except queue.Empty:
                # 一轮 batch 之间也清掉 current_file/active：上一批的最后一张文件名
                # 会一直留着，UI 看起来像还在跑（即使 pending=processing=0）。
                with self._lock:
                    if self._status["active"] or self._status["current_file"]:
                        self._status["active"] = False
                        self._status["current_file"] = ""
                continue
            if first == "__STOP__":
                break

            # opportunistic batch drain
            batch: List[str] = [first]
            while len(batch) < self._batch_size:
                try:
                    p = self._q.get_nowait()
                except queue.Empty:
                    break
                if p == "__STOP__":
                    self._stop_event.set()
                    break
                batch.append(p)

            if not batch:
                continue

            self._process_batch(batch)

        # 退出循环时把 active 状态清掉
        with self._lock:
            self._status["active"] = False
            self._status["current_file"] = ""

    def _process_batch(self, batch: List[str]):
        settings = self._get_settings() or {}
        model_name = settings.get('model') or config.default_model
        overwrite = bool(settings.get('overwrite_existing_txt', False))

        with self._lock:
            self._status["processing"] = len(batch)
            self._status["active"] = True

        # skip-if-exists pre-filter
        kept: List[str] = []
        for p in batch:
            txt = Path(p).with_suffix('.txt')
            if not overwrite and txt.exists() and txt.stat().st_size > 0:
                self._mark_done(p, skipped=True, reason="caption already exists")
                continue
            kept.append(p)

        if not kept:
            with self._lock:
                self._status["processing"] = 0
            return

        try:
            with self._model_lock:
                interrogator = self._get_interrogator(model_name)
                if interrogator is None:
                    raise RuntimeError(f"interrogator unavailable: {model_name}")
                images = []
                valid_paths = []
                for p in kept:
                    try:
                        images.append(_open_rgb(Path(p)))
                        valid_paths.append(p)
                    except Exception as e:
                        self._mark_failed(p, f"open: {e}")

                if not images:
                    return

                results = interrogator.interrogate_batch(images)
        except Exception as e:
            tb = traceback.format_exc()
            for p in kept:
                self._mark_failed(p, f"inference: {e}")
            print(f"[tagger.queue] batch failed: {e}\n{tb}")
            return

        # 后处理 + 写盘
        from tagger.interrogator import Interrogator as _I
        replace_underscore_excludes = parse_tag_list(
            settings.get('replace_underscore_excludes', ''))
        excluded_categories = parse_tag_list(settings.get('excluded_categories', ''))
        tag_category_map = interrogator.get_tag_category_map() if excluded_categories else {}
        for path, (ratings, tags, meta) in zip(valid_paths, results):
            try:
                processed = _I.postprocess_tags(
                    tags=tags,
                    threshold=float(settings.get('threshold', 0.35)),
                    additional_tags=parse_tag_list(settings.get('additional_tags', '')),
                    exclude_tags=parse_tag_list(settings.get('exclude_tags', '')),
                    excluded_categories=excluded_categories,
                    tag_category_map=tag_category_map,
                    replace_underscore=bool(settings.get('replace_underscore', True)),
                    replace_underscore_excludes=replace_underscore_excludes,
                    escape_tag=bool(settings.get('escape_tag', False)),
                    sort_by_alphabetical_order=bool(settings.get('sort_alphabetical', False)),
                    core_tags=parse_tag_list(settings.get('core_tags', '')),
                    core_tags_weight=float(settings.get('core_tags_weight', 1.0)),
                    add_confident_as_weight=bool(settings.get('add_confident_as_weight', False)),
                    use_weight_mapping=bool(settings.get('use_weight_mapping', False)),
                    weight_mapping_exponent=float(settings.get('weight_mapping_exponent', 1.0)),
                    weight_min=float(settings.get('weight_min', 0.9)),
                    weight_max=float(settings.get('weight_max', 1.1)),
                    max_tags=int(settings.get('max_tags', 0)),
                    token_limit=int(settings.get('token_limit', 75)),
                    tag_confidence_precision=int(settings.get('tag_precision', 2)),
                )
                txt_path = Path(path).with_suffix('.txt')
                txt_path.write_text(format_tags_output(processed), encoding='utf-8')
                self._mark_done(path, tag_count=len(processed))
            except Exception as e:
                self._mark_failed(path, f"postprocess/write: {e}")

        with self._lock:
            self._status["processing"] = 0

    # ============== 状态更新 ==============
    def _mark_done(self, path: str, skipped: bool = False, reason: str = "", tag_count: int = 0):
        with self._lock:
            self._pending_set.discard(path)
            if skipped:
                self._status["skipped"] += 1
            else:
                self._status["done"] += 1
            self._status["current_file"] = Path(path).name
            self._recent.appendleft({
                "path": path,
                "name": Path(path).name,
                "status": "skip" if skipped else "done",
                "reason": reason or (f"{tag_count} tags" if tag_count else "ok"),
                "ts": _now(),
            })
            self._save_pending_unsafe()

    def _mark_failed(self, path: str, error: str):
        with self._lock:
            self._pending_set.discard(path)
            self._status["failed"] += 1
            self._status["last_error"] = error[:200]
            self._status["current_file"] = Path(path).name
            self._recent.appendleft({
                "path": path,
                "name": Path(path).name,
                "status": "fail",
                "reason": error[:200],
                "ts": _now(),
            })
            self._save_pending_unsafe()
