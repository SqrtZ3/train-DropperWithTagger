# webapp/routes_session.py — 会话生命周期：history/session/init/set_target/reset

import os
import traceback

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from webapp.history import load_history, save_to_history
from webapp.schemas import InitRequest, TargetUpdateRequest
from webapp.session_manager import session


router = APIRouter()


@router.get("/api/history")
async def get_history():
    return load_history()


@router.get("/api/session")
async def get_session():
    return session.get_session_state()


@router.post("/api/init")
async def init_app(req: InitRequest):
    session.reset()

    input_dir = (req.input_dir or "").strip()
    output_dir = (req.output_dir or "").strip()

    if not input_dir:
        return JSONResponse(content={"error": "输入目录不能为空"}, status_code=400)
    if not os.path.isdir(input_dir):
        return JSONResponse(content={"error": f"找不到目录: {input_dir}"}, status_code=400)

    # 在初始化前先把目标分辨率/步长落到 session 上
    session.set_target(target_mp=req.target_mp, step=req.step)

    try:
        result = session.initialize(input_dir, output_dir)
        save_to_history(session.image_folder, session.output_folder)

        print(f"✅ 初始化成功: {result['count']} 张图片")
        print(f"   输入: {session.image_folder}")
        print(f"   输出: {session.output_folder}")
        print(f"   目标桶: {session.target_mp} MP · 步长 {session.step} px")
        if result["recovered"]:
            print(f"   ♻️ 从断点恢复，当前位置: {result['current_index']}")

        return {
            **result,
            "target_mp": session.target_mp,
            "target_area": session.target_area,
            "step_size": session.step,
        }

    except Exception as e:
        traceback.print_exc()
        session.reset()
        return JSONResponse(content={"error": f"初始化失败: {e}"}, status_code=500)


@router.post("/api/set_target")
async def set_target(req: TargetUpdateRequest):
    """运行中切换目标分桶分辨率 / 步长。无需重新初始化会话。"""
    session.set_target(target_mp=req.target_mp, step=req.step)
    return {
        "ok": True,
        "target_mp": session.target_mp,
        "step_size": session.step,
        "target_area": session.target_area,
    }


@router.post("/api/reset")
async def reset_state():
    session.reset()
    return {"status": "ok"}
