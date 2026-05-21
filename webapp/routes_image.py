# webapp/routes_image.py — 图片/缩略图/snapshot URL

import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

from webapp.session_manager import session
from webapp.snapshots import snapshot_filepath
from webapp.thumbnails import build_thumbnail, media_type_for


router = APIRouter()


def _thumb(filepath: str):
    return build_thumbnail(filepath, session.output_folder, session._get_image_data)


@router.get("/api/image/{index}")
async def get_image_info(index: int):
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)
    info = session.get_image_info(index)
    if info is None:
        return JSONResponse(content={"error": "索引超出范围或文件不存在"}, status_code=404)
    return info


@router.get("/api/file/{index}")
async def get_image_file(index: int):
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)
    if index < 0 or index >= len(session.files):
        return JSONResponse(content={"error": "Not found"}, status_code=404)

    filepath = session.files[index]

    try:
        if filepath.startswith("zip://"):
            data_io = session._get_image_data(filepath)
            if data_io is None:
                return JSONResponse(content={"error": "Read error"}, status_code=500)
            return Response(content=data_io.getvalue(), media_type=media_type_for(filepath))
        if not os.path.exists(filepath):
            return JSONResponse(content={"error": f"文件不存在: {filepath}"}, status_code=404)
        return FileResponse(filepath)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/thumb/source/{index}")
async def get_source_thumb(index: int):
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)
    if index < 0 or index >= len(session.files):
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    data = _thumb(session.files[index])
    if not data:
        return JSONResponse(content={"error": "Thumb fail"}, status_code=500)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/thumb/scan/{index}")
async def get_scan_thumb(index: int):
    if index < 0 or index >= len(session.scan_targets):
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    fp = session.scan_targets[index]
    if not session.is_path_under_scan_root(fp):
        return JSONResponse(content={"error": "Forbidden"}, status_code=403)
    data = _thumb(fp)
    if not data:
        return JSONResponse(content={"error": "Thumb fail"}, status_code=500)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/snap/{snap_id}/thumb/{idx}")
async def get_snap_thumb(snap_id: str, idx: int):
    fp = snapshot_filepath(snap_id, idx)
    if fp is None:
        return JSONResponse(content={"error": "Snapshot expired"}, status_code=410)
    data = _thumb(fp)
    if not data:
        return JSONResponse(content={"error": "Thumb fail"}, status_code=500)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800, immutable"})


@router.get("/api/snap/{snap_id}/file/{idx}")
async def get_snap_file(snap_id: str, idx: int):
    fp = snapshot_filepath(snap_id, idx)
    if fp is None:
        return JSONResponse(content={"error": "Snapshot expired"}, status_code=410)

    try:
        if fp.startswith("zip://"):
            data_io = session._get_image_data(fp)
            if data_io is None:
                return JSONResponse(content={"error": "Read error"}, status_code=500)
            return Response(content=data_io.getvalue(),
                            media_type=media_type_for(fp),
                            headers={"Cache-Control": "public, max-age=604800, immutable"})
        if not os.path.exists(fp):
            return JSONResponse(content={"error": "File missing"}, status_code=404)
        return FileResponse(fp, media_type=media_type_for(fp),
                            headers={"Cache-Control": "public, max-age=604800, immutable"})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/scan_file/{index}")
async def get_scan_file(index: int):
    """提供 scan_targets 中的文件，仅允许 scan_root 之内的路径，避免任意路径读。"""
    if index < 0 or index >= len(session.scan_targets):
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    filepath = session.scan_targets[index]
    if not session.is_path_under_scan_root(filepath):
        return JSONResponse(content={"error": "Forbidden"}, status_code=403)
    if not os.path.exists(filepath):
        return JSONResponse(content={"error": "File missing"}, status_code=404)
    return FileResponse(filepath)
