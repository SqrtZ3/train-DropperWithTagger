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

from webapp import state
from webapp.buckets import adjust_crop_box, calculate_output_size, smart_crop_box
from webapp.schemas import AutoBucketRequest, BatchProcessRequest, SaveCropRequest
from webapp.session_manager import session


router = APIRouter()


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
    """执行批处理。"""
    if not session.queue:
        return {"status": "empty", "message": "队列为空"}

    session.set_target(target_mp=req.target_mp, step=req.step)
    target_area = session.target_area
    bucket_step = session.step

    min_size = req.min_bucket_size if (req.min_bucket_size and req.min_bucket_size > 0) else 1
    print(f"\n🚀 开始批处理 {len(session.queue)} 张图片 "
          f"(最小成桶: {min_size}, 目标 {session.target_mp} MP · 步长 {bucket_step} px)...")

    # 1. 初始分桶
    buckets: Dict = {}
    for item in session.queue:
        target_res = calculate_output_size(item["w"], item["h"],
                                           target_area=target_area, step=bucket_step)
        buckets.setdefault(target_res, []).append(item)

    print("📊 初始分布:")
    for res, items in sorted(buckets.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"   - {res}: {len(items)} 张")

    # 2. 识别合格桶
    viable_buckets = {res for res, items in buckets.items() if len(items) >= min_size}

    if not viable_buckets:
        sorted_buckets = sorted(buckets.items(), key=lambda x: len(x[1]), reverse=True)
        viable_buckets = {sorted_buckets[0][0]}
        print(f"⚠️ 无合格桶，降级主桶 {sorted_buckets[0][0]} 为唯一目标")

    # 3. 执行归并
    final_tasks: List[Dict] = []
    merged_count = 0
    viable_list = list(viable_buckets)

    for res, items in buckets.items():
        if res in viable_buckets:
            for item in items:
                final_tasks.append({"item": item, "target_res": res})
            continue

        current_ar = res[0] / res[1]
        candidates = sorted(viable_list, key=lambda vr: abs((vr[0] / vr[1]) - current_ar))

        # 同一张源图被合并候选时只读一次像素
        for item in items:
            img_data = session._get_image_data(item["filepath"])
            if not img_data:
                print(f"   ! 无法读取，保持原样: {item['filename']}")
                final_tasks.append({"item": item, "target_res": res})
                continue

            try:
                with Image.open(img_data) as img:
                    img_w, img_h = img.size
            except Exception as e:
                print(f"   ! 打开失败 {item['filename']}: {e}（保持原样）")
                final_tasks.append({"item": item, "target_res": res})
                continue

            # 真正修正 item 的裁切框使其适配目标桶比例。
            # 候选已按宽高比距离排序，第一个就是"最接近"的目标桶；只要选区在图内有效就接受。
            target_res = candidates[0]
            adj_x, adj_y, adj_w, adj_h = adjust_crop_box(
                item["x"], item["y"], item["w"], item["h"],
                target_res[0], target_res[1], img_w, img_h,
            )
            if adj_w > 0 and adj_h > 0:
                item["x"], item["y"], item["w"], item["h"] = adj_x, adj_y, adj_w, adj_h
                final_tasks.append({"item": item, "target_res": target_res})
                merged_count += 1
            else:
                print(f"   ! 无法归并: {item['filename']} (保持原样)")
                final_tasks.append({"item": item, "target_res": res})

    print(f"🔄 优化完成: {merged_count} 张图片被重新归类，生成 {len(viable_buckets)} 个主桶")

    # 4. 执行处理
    processed_count = 0
    session.save_counter = 0
    digits = max(4, len(str(len(final_tasks))))

    if not os.path.exists(session.output_folder):
        os.makedirs(session.output_folder)

    for task in final_tasks:
        item = task["item"]
        target_w, target_h = task["target_res"]

        out_filename = f"{str(session.save_counter).zfill(digits)}.png"
        out_path = os.path.join(session.output_folder, out_filename)

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

                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode != "RGBA":
                        img = img.convert("RGBA")
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                else:
                    img = img.convert("RGB")

                cropped = img.crop((adj_x, adj_y, adj_x + adj_w, adj_y + adj_h))

                if state.HAS_UPSCALE and state.upscale is not None:
                    cropped = state.upscale.maybe_upscale(
                        cropped, target_w, target_h, item.get("filename", ""),
                    )

                final = cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)
                final.save(out_path, "PNG", quality=95)

                if not item["filepath"].startswith("zip://"):
                    src_txt = Path(item["filepath"]).with_suffix(".txt")
                    if src_txt.exists():
                        shutil.copy2(src_txt, Path(out_path).with_suffix(".txt"))

                # 触发 tagger 联动（受 session.auto_tag_enabled 控制）
                session.notify_artifact_saved(out_path)

                processed_count += 1
                session.save_counter += 1

        except Exception as e:
            print(f"❌ 处理失败 {item['filename']}: {e}")

    # 导出完只清空队列、把空 queue 同步到磁盘。
    # 之前用 session.clear_session() 会把整个 session_state.json + .thumb_cache 都删掉，
    # 用户做了 600 张精修加队列、点导出之后，下次同输入/输出目录初始化就拿不回那 600 张
    # 的 cropped 标记，1000 张图全部按 pending 重扫，分批工作流被打断。
    # items / current_index / ignored_files 保留，让用户能继续做剩下的图。
    session.queue = []
    session.op_history = []
    session._save_state()

    return {
        "status": "completed",
        "processed": processed_count,
        "merged_count": merged_count,
        "bucket_count": len(viable_buckets),
        "output_folder": session.output_folder,
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

                if state.HAS_UPSCALE and state.upscale is not None:
                    cropped = state.upscale.maybe_upscale(cropped, tgt_w, tgt_h, src_path)

                final = cropped.resize((tgt_w, tgt_h), Image.Resampling.LANCZOS)

                out_name = name_tmpl.format(
                    idx=counter, w=tgt_w, h=tgt_h,
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
                final.save(out_path, "PNG", quality=95)

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
        "target_mp": session.target_mp,
        "step": session.step,
    }
