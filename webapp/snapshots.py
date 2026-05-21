# webapp/snapshots.py — 相似度结果快照表
#
# 为什么要快照？前端会把扫描结果缓存到 localStorage，URL 里携带 session.files /
# scan_targets 的「索引」是不稳定的：用户重新初始化、再次扫描、删图都会让索引漂移，
# 导致前端展示的缩略图、模态框点开看到的图与文件名错位。
#
# 解决办法：每次扫描发一个唯一 snap_id，把当时的 filepath 列表快照固化下来；
# 图片/缩略图的 URL 改成 `/api/snap/{snap_id}/...`。snap_id 唯一即代表内容唯一，
# 可以走强缓存且不会串。

import secrets
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional

from webapp.config import SNAPSHOT_LIMIT


_SNAPSHOTS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def new_snapshot(files: List[str], kind: str) -> str:
    """注册一个新的相似度结果快照，返回 snap_id。kind: 'source' | 'scan'"""
    snap_id = secrets.token_urlsafe(8)
    _SNAPSHOTS[snap_id] = {
        "files": list(files),
        "kind": kind,
        "created_at": datetime.now().isoformat(),
    }
    while len(_SNAPSHOTS) > SNAPSHOT_LIMIT:
        _SNAPSHOTS.popitem(last=False)
    return snap_id


def snapshot_filepath(snap_id: str, idx: int) -> Optional[str]:
    snap = _SNAPSHOTS.get(snap_id)
    if not snap:
        return None
    files = snap["files"]
    if idx < 0 or idx >= len(files):
        return None
    _SNAPSHOTS.move_to_end(snap_id)
    return files[idx]


def get_snapshot(snap_id: str) -> Optional[Dict[str, Any]]:
    return _SNAPSHOTS.get(snap_id)
