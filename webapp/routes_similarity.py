# webapp/routes_similarity.py — 相似度扫描 + 删除/忽略
# 删除/忽略历来由 Sim 页面触发（删除「重复组里的多余项」），所以一并放这里。

import os
import traceback
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from webapp import state
from webapp.schemas import (
    DeleteFilesRequest,
    IgnoreFilesRequest,
    SimilarityScanRequest,
)
from webapp.session_manager import session
from webapp.similarity import analyze_similarity_internal, disk_loader
from webapp.snapshots import get_snapshot, new_snapshot


router = APIRouter()


@router.post("/api/analyze_similarity")
async def analyze_similarity(req: SimilarityScanRequest):
    if not session.is_initialized:
        return JSONResponse(content={"error": "Project not initialized"}, status_code=400)

    if state.similarity_progress.get("running"):
        return JSONResponse(content={"error": "已有扫描在进行中"}, status_code=409)

    scope = (req.scope or "source").lower()
    try:
        if scope == "source":
            target_files = list(session.files)
            loader = session._get_image_data
            session.scan_targets = []
            session.scan_root = ""
            snap_kind = "source"
        elif scope == "output":
            folder = session.output_folder
            session.scan_folder_for_targets(folder, skip_prev_output=False)
            target_files = list(session.scan_targets)
            loader = disk_loader
            snap_kind = "scan"
        elif scope == "custom":
            folder = (req.folder or "").strip()
            if not folder:
                return JSONResponse(content={"error": "scope=custom 时必须提供 folder"},
                                    status_code=400)
            session.scan_folder_for_targets(folder, skip_prev_output=True)
            target_files = list(session.scan_targets)
            loader = disk_loader
            snap_kind = "scan"
        else:
            return JSONResponse(content={"error": f"unknown scope: {scope}"}, status_code=400)

        groups = analyze_similarity_internal(
            target_files,
            threshold=req.threshold,
            method=req.method,
            loader_func=loader,
            ar_tolerance=req.ar_tolerance if req.ar_tolerance is not None else 0.15,
            require_dual_hash=bool(req.require_dual_hash),
        )

        snap_id = new_snapshot(target_files, snap_kind)
        path_to_index = {fp: i for i, fp in enumerate(target_files)}

        def _url_for(fp, fallback_idx):
            return f"/api/snap/{snap_id}/file/{path_to_index.get(fp, fallback_idx)}"

        def _thumb_for(fp, fallback_idx):
            return f"/api/snap/{snap_id}/thumb/{path_to_index.get(fp, fallback_idx)}"

        serializable_groups = []
        for grp in groups:
            files = []
            for item in grp:
                files.append({
                    "filename": item["filename"],
                    "filepath": item["filepath"],
                    "url": _url_for(item["filepath"], item["index"]),
                    "thumb_url": _thumb_for(item["filepath"], item["index"]),
                    "width": item.get("width"),
                    "height": item.get("height"),
                })
            serializable_groups.append(files)

        return {
            "groups": serializable_groups,
            "scope": scope,
            "scanned_count": len(target_files),
            "scan_root": session.scan_root if scope != "source" else session.image_folder,
            "snap_id": snap_id,
        }

    except ValueError as ve:
        state.reset_similarity_progress()
        return JSONResponse(content={"error": str(ve)}, status_code=400)
    except Exception as e:
        traceback.print_exc()
        state.reset_similarity_progress()
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/similarity_progress")
async def similarity_progress():
    return {
        "running": state.similarity_progress.get("running", False),
        "phase": state.similarity_progress.get("phase", ""),
        "current": state.similarity_progress.get("current", 0),
        "total": state.similarity_progress.get("total", 0),
    }


@router.post("/api/cancel_similarity")
async def cancel_similarity():
    if not state.similarity_progress.get("running"):
        return {"status": "noop"}
    state.similarity_progress["cancel"] = True
    return {"status": "cancelling"}


# ============== 删除 / 软忽略 ==============
def _is_path_allowed_for_delete(fpath: str, snap_files: Optional[set]) -> bool:
    """白名单：要么属于本次快照、要么属于当前 session 已知的源/输出/扫描集合。"""
    if snap_files is not None:
        return fpath in snap_files
    if fpath in session.files:
        return True
    if fpath in session.scan_targets:
        return True
    try:
        ap = os.path.abspath(fpath)
    except Exception:
        return False
    roots = []
    if session.image_folder:
        roots.append(os.path.abspath(session.image_folder))
    if session.output_folder:
        roots.append(os.path.abspath(session.output_folder))
    if session.scan_root:
        roots.append(session.scan_root)
    for r in roots:
        try:
            if os.path.commonpath([ap, r]) == r:
                return True
        except Exception:
            continue
    return False


@router.post("/api/delete_files")
async def delete_files(req: DeleteFilesRequest):
    deleted_count = 0
    ignored_count = 0
    failed_files = []

    snap_files: Optional[set] = None
    if req.snap_id:
        snap = get_snapshot(req.snap_id)
        if snap:
            snap_files = set(snap["files"])
        else:
            return JSONResponse(
                content={"error": "Snapshot expired", "code": "snapshot_expired"},
                status_code=410,
            )

    deleted_set = set()

    for fpath in req.filepaths:
        if fpath.startswith("zip://"):
            session.ignored_files.add(fpath)
            ignored_count += 1
            continue

        if not _is_path_allowed_for_delete(fpath, snap_files):
            failed_files.append(fpath)
            print(f"Refused to delete (path not in allowed set): {fpath}")
            continue

        if os.path.exists(fpath):
            try:
                txt_path = os.path.splitext(fpath)[0] + ".txt"
                if os.path.exists(txt_path):
                    os.remove(txt_path)
                os.remove(fpath)
                session.ignored_files.add(fpath)
                deleted_set.add(fpath)
                deleted_count += 1
            except Exception as e:
                failed_files.append(fpath)
                print(f"Failed to delete {fpath}: {e}")
        else:
            session.ignored_files.add(fpath)
            deleted_set.add(fpath)
            ignored_count += 1

    if session.files:
        session.files = [f for f in session.files if f not in session.ignored_files]
    if session.scan_targets:
        session.scan_targets = [f for f in session.scan_targets if f not in deleted_set]
    # 注意：故意不修改 _SNAPSHOTS 里的列表 —— 列表索引与前端缓存的 URL 严格对应。
    if session.is_initialized:
        session._save_state()

    return {
        "status": "success",
        "deleted": deleted_count,
        "ignored": ignored_count,
        "failed": failed_files,
        "remaining_count": len(session.files),
        "remaining_scan_count": len(session.scan_targets),
    }


@router.post("/api/ignore_files")
async def ignore_files(req: IgnoreFilesRequest):
    """软忽略：把路径加入 session.ignored_files 并从 self.files 过滤，但不动磁盘。"""
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)
    if not req.filepaths:
        return {"status": "noop", "ignored": 0, "remaining_count": len(session.files)}
    before = len(session.files)
    session.add_to_ignored(req.filepaths)
    return {
        "status": "success",
        "ignored": len(req.filepaths),
        "removed_from_queue": before - len(session.files),
        "remaining_count": len(session.files),
        "current_index": session.current_index,
    }


@router.post("/api/unignore_files")
async def unignore_files(req: IgnoreFilesRequest):
    """从忽略列表移除若干路径，重扫源目录恢复 self.files。"""
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)
    if not req.filepaths:
        return {"status": "noop", "removed": 0, "remaining_count": len(session.files)}
    removed = session.remove_from_ignored(req.filepaths)
    return {
        "status": "success",
        "removed": removed,
        "remaining_count": len(session.files),
        "current_index": session.current_index,
    }
