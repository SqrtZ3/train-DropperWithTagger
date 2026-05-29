# webapp/routes_crop.py — 裁切相关：save/goto/undo/process_batch/auto_bucket_all

import os
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from PIL import Image

from webapp.buckets import (
    adjust_crop_box,
    calculate_output_size,
    downsample_preserving_detail,
    plan_arb_export,
    smart_crop_box,
    snap_to_texture_bucket,
)
from webapp.schemas import AutoBucketRequest, BatchProcessRequest, SaveCropRequest
from webapp.session_manager import session


router = APIRouter()


def _bucket_options(req):
    return {
        "base_resos": req.bucket_base_resos,
        "min_base_reso": req.bucket_min_base_reso or 0,
        "max_base_reso": req.bucket_max_base_reso or 0,
        "output_max_base_reso": req.output_max_base_reso or 0,
        "base_reso_step": req.bucket_base_reso_steps or 256,
        "min_reso": req.min_bucket_reso or 512,
        "max_reso": req.max_bucket_reso or 2048,
        "step": req.step or session.step,
        "no_upscale": True,
    }


def _export_crop(cropped: Image.Image, target_w: int, target_h: int,
                 req) -> tuple[Image.Image, str, tuple[int, int]]:
    strategy = (req.export_strategy or "resize").lower()
    if strategy in ("resize", "bucket_resize", "legacy"):
        return (
            cropped.resize((target_w, target_h), Image.Resampling.LANCZOS),
            "resize",
            (target_w, target_h),
        )

    plan = plan_arb_export(cropped.width, cropped.height, **_bucket_options(req))
    cx, cy, cw, ch = plan.crop_box
    final = cropped.crop((cx, cy, cx + cw, cy + ch))
    if plan.needs_resize:
        final = downsample_preserving_detail(final, plan.output_size)
    return final, plan.action, plan.bucket_size


def _flatten_to_rgb(img: Image.Image) -> Image.Image:
    """透明图(RGBA/LA/带透明的 P)拍平到白底 RGB，其余转 RGB。"""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[3])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _save_png(img: Image.Image, path: str) -> None:
    """PNG 无损存盘。compress_level 只影响体积/速度、不动像素
    （修原 `quality=95` 误用——PNG 无 quality 参数）。"""
    img.save(path, "PNG", compress_level=6)


def _copy_caption(src_filepath: str, out_path: str) -> None:
    """把非 zip 源图旁的同名 .txt 复制到输出旁。"""
    if src_filepath.startswith("zip://"):
        return
    src_txt = Path(src_filepath).with_suffix(".txt")
    if src_txt.exists():
        try:
            shutil.copy2(src_txt, Path(out_path).with_suffix(".txt"))
        except Exception:
            pass


def _export_texture(img: Image.Image, item: Dict, target_area: int,
                    step: int, max_ar: float):
    """纹理框：后端用 snap 重新校验框（不信任前端坐标），原生裁切、零重采样。
    返回 (cropped, (bw, bh))；放不下任何桶或尺寸不符则返回 None。"""
    snap = snap_to_texture_bucket(
        item["x"], item["y"], item["w"], item["h"],
        img.width, img.height, target_area=target_area, step=step, max_ar=max_ar,
    )
    if snap is None:
        return None
    nx, ny, bw, bh = snap
    cropped = img.crop((nx, ny, nx + bw, ny + bh))
    if cropped.size != (bw, bh):
        return None
    return cropped, (bw, bh)


@router.post("/api/save")
async def save_crop(data: SaveCropRequest):
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)

    key = data.filepath
    if not key:
        for f in session.files:
            if os.path.basename(f) == data.filename or f.endswith(data.filename):
                key = f
                break

    if not key:
        return JSONResponse(content={"error": "找不到文件"}, status_code=404)

    return session.save_crop(key, data.crops, data.status)


@router.post("/api/goto/{index}")
async def goto_image(index: int):
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)
    return session.go_to_image(index)


@router.post("/api/undo")
async def undo_last():
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)
    return session.undo_last()


@router.post("/api/process_batch")
async def process_batch(req: BatchProcessRequest = BatchProcessRequest()):
    """执行批处理。普通框重采样贴桶；纹理框原生裁切零重采样写入 texture 子目录。"""
    if not session.queue:
        return {"status": "empty", "message": "队列为空"}

    session.set_target(target_mp=req.target_mp, step=req.step)
    target_area = session.target_area
    bucket_step = session.step
    max_ar = req.texture_max_ar or 2.0

    # 按 kind 分流：纹理框不参与桶合并
    full_items = [it for it in session.queue if it.get("kind", "full") != "texture"]
    texture_items = [it for it in session.queue if it.get("kind", "full") == "texture"]

    min_size = req.min_bucket_size if (req.min_bucket_size and req.min_bucket_size > 0) else 1
    print(f"\n🚀 开始批处理：普通 {len(full_items)} · 纹理 {len(texture_items)} "
          f"(最小成桶 {min_size} · 目标 {session.target_mp} MP · 步长 {bucket_step} px)...")

    # 1~3. 普通框分桶 + 合并孤儿桶。只决定「裁向哪个桶」，不回写 item（修原地改写 bug）。
    final_tasks: List[Dict] = []   # {item, target_res}
    merged_count = 0
    viable_buckets: set = set()
    if full_items:
        buckets: Dict = {}
        for item in full_items:
            target_res = calculate_output_size(item["w"], item["h"],
                                               target_area=target_area, step=bucket_step)
            buckets.setdefault(target_res, []).append(item)

        print("📊 初始分布:")
        for res, items in sorted(buckets.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"   - {res}: {len(items)} 张")

        viable_buckets = {res for res, items in buckets.items() if len(items) >= min_size}
        if not viable_buckets:
            main_res = max(buckets.items(), key=lambda x: len(x[1]))[0]
            viable_buckets = {main_res}
            print(f"⚠️ 无合格桶，降级主桶 {main_res} 为唯一目标")

        viable_list = list(viable_buckets)
        for res, items in buckets.items():
            if res in viable_buckets:
                for item in items:
                    final_tasks.append({"item": item, "target_res": res})
                continue
            # 孤儿桶并入宽高比最接近的合格桶；第 4 步按目标桶 adjust_crop_box（安全夹边）
            target_res = min(viable_list, key=lambda vr: abs((vr[0] / vr[1]) - (res[0] / res[1])))
            for item in items:
                final_tasks.append({"item": item, "target_res": target_res})
                merged_count += 1

        print(f"🔄 优化完成: {merged_count} 张重新归类，{len(viable_buckets)} 个主桶")

    if not os.path.exists(session.output_folder):
        os.makedirs(session.output_folder)

    # 4a. 普通框导出（重采样贴桶）。export_summary 记录真实落盘尺寸 → 诚实统计。
    processed_count = 0
    session.save_counter = 0
    digits = max(4, len(str(max(1, len(final_tasks)))))
    export_summary: Dict = defaultdict(int)

    for task in final_tasks:
        item = task["item"]
        target_w, target_h = task["target_res"]
        out_path = os.path.join(session.output_folder, f"{str(session.save_counter).zfill(digits)}.png")
        try:
            img_data = session._get_image_data(item["filepath"])
            if not img_data:
                continue
            with Image.open(img_data) as img:
                img_w, img_h = img.size
                adj_x, adj_y, adj_w, adj_h = adjust_crop_box(
                    item["x"], item["y"], item["w"], item["h"],
                    target_w, target_h, img_w, img_h,
                )
                img = _flatten_to_rgb(img)
                cropped = img.crop((adj_x, adj_y, adj_x + adj_w, adj_y + adj_h))
                final, _, _ = _export_crop(cropped, target_w, target_h, req)
                _save_png(final, out_path)
                export_summary[(final.width, final.height)] += 1
                _copy_caption(item["filepath"], out_path)
                session.notify_artifact_saved(out_path)
                processed_count += 1
                session.save_counter += 1
        except Exception as e:
            print(f"❌ 处理失败 {item['filename']}: {e}")

    # 4b. 纹理框导出（原生裁切、零重采样）→ texture 子目录
    texture_processed = 0
    texture_skipped = 0
    texture_out_root = ""
    texture_summary: Dict = defaultdict(int)
    if texture_items:
        sub = (req.texture_subdir or "texture").strip() or "texture"
        texture_out_root = os.path.join(session.output_folder, sub)
        os.makedirs(texture_out_root, exist_ok=True)
        tdigits = max(4, len(str(len(texture_items))))
        tcounter = 0
        for item in texture_items:
            try:
                img_data = session._get_image_data(item["filepath"])
                if not img_data:
                    texture_skipped += 1
                    continue
                with Image.open(img_data) as img:
                    img = _flatten_to_rgb(img)
                    result = _export_texture(img, item, target_area, bucket_step, max_ar)
                    if result is None:
                        texture_skipped += 1
                        print(f"   ! 纹理放不下任何桶，跳过: {item['filename']}")
                        continue
                    cropped, (bw, bh) = result
                    out_path = os.path.join(texture_out_root,
                                            f"tex_{str(tcounter).zfill(tdigits)}.png")
                    _save_png(cropped, out_path)
                    texture_summary[(bw, bh)] += 1
                    if req.texture_copy_caption:
                        _copy_caption(item["filepath"], out_path)
                    session.notify_artifact_saved(out_path)
                    texture_processed += 1
                    tcounter += 1
            except Exception as e:
                texture_skipped += 1
                print(f"❌ 纹理处理失败 {item['filename']}: {e}")

    # 导出完只清空队列；items / current_index / ignored_files 保留，支持分批续作。
    session.queue = []
    session.op_history = []
    session._save_state()

    return {
        "status": "completed",
        "processed": processed_count,
        "merged_count": merged_count,
        "bucket_count": len(viable_buckets),
        "output_folder": session.output_folder,
        # 诚实统计：真实落盘尺寸分布（而非合并前的等面积桶）
        "buckets": [{"w": w, "h": h, "count": c}
                    for (w, h), c in sorted(export_summary.items(), key=lambda kv: -kv[1])],
        "texture_processed": texture_processed,
        "texture_skipped": texture_skipped,
        "texture_output_folder": texture_out_root,
        "texture_sizes": [{"w": w, "h": h, "count": c}
                          for (w, h), c in sorted(texture_summary.items(), key=lambda kv: -kv[1])],
    }


@router.post("/api/auto_bucket_all")
async def auto_bucket_all(req: AutoBucketRequest = AutoBucketRequest()):
    """「一键包囊最大分辨率 / 最近邻桶 / 最小裁切」"""
    if not session.is_initialized:
        return JSONResponse(content={"error": "未初始化"}, status_code=400)

    session.set_target(target_mp=req.target_mp, step=req.step)
    target_area = session.target_area
    bucket_step = session.step
    if not session.files:
        return {"status": "empty", "processed": 0, "skipped": 0, "errors": [],
                "output_folder": session.output_folder}

    out_root = session.output_folder
    if req.output_subdir:
        out_root = os.path.join(out_root, req.output_subdir.strip())
    os.makedirs(out_root, exist_ok=True)

    sources: List[str] = []
    for fp in session.files:
        if (not req.include_processed) and session.items.get(fp, {}).get("status") in ("cropped", "skipped"):
            continue
        sources.append(fp)

    if not sources:
        return {"status": "empty", "processed": 0, "skipped": 0, "errors": [],
                "output_folder": out_root,
                "message": "没有可处理的源图（可能已全部处理）"}

    processed = 0
    skipped = 0
    errors: List[Dict[str, str]] = []
    bucket_summary: Dict[tuple, int] = defaultdict(int)
    export_summary: Dict[tuple, int] = defaultdict(int)
    action_summary: Dict[str, int] = defaultdict(int)

    name_tmpl = req.name_template or "auto_{idx:05d}.png"
    counter = 0

    print(f"\n🪄 一键全自动 ({len(sources)} 张, 目标 {session.target_mp} MP · "
          f"步长 {bucket_step}) → {out_root}")

    for src_path in sources:
        try:
            img_data = session._get_image_data(src_path)
            if img_data is None:
                errors.append({"file": src_path, "error": "无法读取源图"})
                skipped += 1
                continue
            with Image.open(img_data) as img:
                img_w, img_h = img.size
                tgt_w, tgt_h = calculate_output_size(img_w, img_h,
                                                    target_area=target_area, step=bucket_step)
                bucket_summary[(tgt_w, tgt_h)] += 1

                cx, cy, cw, ch = smart_crop_box(img_w, img_h, tgt_w, tgt_h)

                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    bg = Image.new("RGB", (img_w, img_h), (255, 255, 255))
                    if img.mode != "RGBA":
                        img = img.convert("RGBA")
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                cropped = img.crop((cx, cy, cx + cw, cy + ch))
                final, action, export_bucket = _export_crop(cropped, tgt_w, tgt_h, req)
                export_summary[(final.width, final.height)] += 1
                action_summary[action] += 1

                out_name = name_tmpl.format(
                    idx=counter, w=final.width, h=final.height,
                    basename=os.path.splitext(os.path.basename(
                        src_path.split("::")[-1] if "::" in src_path else src_path
                    ))[0],
                )
                out_path = os.path.join(out_root, out_name)
                base, ext = os.path.splitext(out_path)
                dup = 1
                while os.path.exists(out_path):
                    dup += 1
                    out_path = f"{base}_{dup}{ext}"
                _save_png(final, out_path)

                if not src_path.startswith("zip://"):
                    src_txt = Path(src_path).with_suffix(".txt")
                    if src_txt.exists():
                        try:
                            shutil.copy2(src_txt, Path(out_path).with_suffix(".txt"))
                        except Exception:
                            pass

                session.notify_artifact_saved(out_path)

                session.items[src_path] = {
                    "status": "cropped",
                    "crop_params": [{"x": cx, "y": cy, "w": cw, "h": ch, "auto": True}],
                    "timestamp": datetime.now().isoformat(),
                }
                processed += 1
                counter += 1

        except Exception as e:
            errors.append({"file": src_path, "error": str(e)})
            skipped += 1

    session._save_state()

    summary = sorted(bucket_summary.items(), key=lambda kv: -kv[1])
    print(f"   ✅ 完成 {processed}, 跳过 {skipped}, 错误 {len(errors)}")
    for (bw, bh), c in summary[:10]:
        print(f"   - {bw}×{bh}: {c}")

    return {
        "status": "completed",
        "processed": processed,
        "skipped": skipped,
        "errors": errors[:20],
        "error_count": len(errors),
        "output_folder": out_root,
        "buckets": [{"w": bw, "h": bh, "count": c} for (bw, bh), c in summary],
        "export_sizes": [
            {"w": bw, "h": bh, "count": c}
            for (bw, bh), c in sorted(export_summary.items(), key=lambda kv: -kv[1])
        ],
        "actions": dict(action_summary),
        "target_mp": session.target_mp,
        "step": session.step,
    }
