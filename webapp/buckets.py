# webapp/buckets.py — 分桶数学

import math
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from PIL import Image

from webapp.config import BUCKET_STEP_SIZE, TARGET_PIXEL_AREA


@dataclass(frozen=True)
class ArbExportPlan:
    bucket_size: Tuple[int, int]
    crop_box: Tuple[int, int, int, int]
    output_size: Tuple[int, int]
    action: str
    needs_resize: bool


def get_standard_buckets(target_area: int = TARGET_PIXEL_AREA,
                         step: int = BUCKET_STEP_SIZE) -> List[Tuple[int, int]]:
    resolutions = set()
    root = int(math.sqrt(target_area))
    base = round(root / step) * step
    resolutions.add((base, base))

    for i in range(1, 64):
        w = base + i * step
        h = round((target_area / w) / step) * step
        if w * h > 0:
            resolutions.add((w, h))
            resolutions.add((h, w))
        if w / max(1, h) > 4.5:
            break

    return sorted(list(resolutions), key=lambda x: x[0] / x[1])


def calculate_output_size(crop_w: int, crop_h: int,
                          target_area: int = TARGET_PIXEL_AREA,
                          step: int = BUCKET_STEP_SIZE) -> Tuple[int, int]:
    if crop_w <= 0 or crop_h <= 0:
        return (step, step)
    current_ar = crop_w / crop_h
    buckets = get_standard_buckets(target_area, step)
    return min(buckets, key=lambda res: abs(res[0] / res[1] - current_ar))


def texture_buckets(target_area: int = TARGET_PIXEL_AREA,
                    step: int = BUCKET_STEP_SIZE,
                    max_ar: float = 2.0) -> List[Tuple[int, int]]:
    """纹理补充桶候选集：标准等面积桶里宽高比 ≤ max_ar 的那些。

    与 get_standard_buckets 同源（跟随 session.target_area），只是把极扁的桶
    （AR > max_ar）剔掉——纹理细节块不需要 4.5:1 这种长条。
    """
    cap = max(1.0, float(max_ar))
    return [
        (w, h) for (w, h) in get_standard_buckets(target_area, step)
        if max(w / h, h / w) <= cap + 1e-9
    ]


def snap_to_texture_bucket(x: float, y: float, w: float, h: float,
                           src_w: int, src_h: int,
                           target_area: int = TARGET_PIXEL_AREA,
                           step: int = BUCKET_STEP_SIZE,
                           max_ar: float = 2.0):
    """把用户画的框 (x,y,w,h) 向内吸附到一个合法纹理桶，返回原生裁切框
    (nx, ny, bw, bh)；放不下任何桶时返回 None。

    规则：候选 = texture_buckets；可行桶 = 两边都 ≤ 画框且 ≤ 源图；取面积最大者
    （面积相差 ≤2% 视为并列，再取宽高比最接近画框者——尽量多保留原生像素的
    同时贴合画框形状）；以画框中心居中、夹进源图内。输出尺寸 == 桶尺寸，
    调用方据此原生裁切、零重采样。
    """
    if w <= 0 or h <= 0 or src_w <= 0 or src_h <= 0:
        return None
    drawn_ar = w / h
    feasible = [
        (bw, bh) for (bw, bh) in texture_buckets(target_area, step, max_ar)
        if bw <= w and bh <= h and bw <= src_w and bh <= src_h
    ]
    if not feasible:
        return None
    best_area = max(bw * bh for bw, bh in feasible)
    near = [(bw, bh) for (bw, bh) in feasible if bw * bh >= best_area * 0.98]
    bw, bh = min(near, key=lambda b: abs((b[0] / b[1]) - drawn_ar))
    cx = x + w / 2.0
    cy = y + h / 2.0
    nx = int(round(cx - bw / 2.0))
    ny = int(round(cy - bh / 2.0))
    nx = max(0, min(nx, int(src_w) - bw))
    ny = max(0, min(ny, int(src_h) - bh))
    return (nx, ny, bw, bh)


def _normalize_base_resos(base_resos=None, min_base_reso: int = 0,
                          max_base_reso: int = 0,
                          base_reso_step: int = 256,
                          fallback_base: int = 1024,
                          min_reso: int = 512) -> List[int]:
    if base_resos:
        if isinstance(base_resos, str):
            raw_values = [v.strip() for v in base_resos.split(",")]
        else:
            raw_values = list(base_resos)
        values = []
        for value in raw_values:
            if value is None or value == "":
                continue
            ivalue = int(value)
            if ivalue > 0:
                values.append(ivalue)
        if values:
            return sorted(set(values))

    max_base = int(max_base_reso or 0)
    if max_base > 0:
        min_base = int(min_base_reso or 0) or int(min_reso or fallback_base)
        step = max(1, int(base_reso_step or 256))
        if min_base > max_base:
            min_base, max_base = max_base, min_base
        values = list(range(min_base, max_base + 1, step))
        if not values or values[-1] != max_base:
            values.append(max_base)
        return sorted(set(v for v in values if v > 0))

    return [int(fallback_base)]


def get_arb_buckets(base_resos=None, min_base_reso: int = 0,
                    max_base_reso: int = 0, base_reso_step: int = 256,
                    min_reso: int = 512, max_reso: int = 2048,
                    step: int = BUCKET_STEP_SIZE) -> List[Tuple[int, int]]:
    bases = _normalize_base_resos(
        base_resos, min_base_reso, max_base_reso, base_reso_step,
        fallback_base=int(math.sqrt(TARGET_PIXEL_AREA)), min_reso=min_reso,
    )
    buckets = []
    seen = set()
    min_r = max(1, int(min_reso))
    max_r = max(min_r, int(max_reso))
    step = max(1, int(step or BUCKET_STEP_SIZE))
    for base in bases:
        area = base * base
        for w in range(min_r, max_r + 1, step):
            for h in range(min_r, max_r + 1, step):
                if abs((w * h) - area) / max(1, area) > 0.1:
                    continue
                if max(w / h, h / w) > 2.0:
                    continue
                if (w, h) in seen:
                    continue
                seen.add((w, h))
                buckets.append((w, h))
    return sorted(buckets, key=lambda b: (b[0] * b[1], b[0], b[1]))


def _bucket_score(bucket: Tuple[int, int], w: int, h: int):
    bw, bh = bucket
    aspect_diff = abs((w / h) - (bw / bh))
    scale = max(bw / max(1, w), bh / max(1, h))
    scale_diff = abs(math.log(max(scale, 1e-8)))
    area_diff = abs((bw * bh) - (w * h)) / max(1, w * h)
    return aspect_diff, scale_diff, area_diff


def choose_arb_bucket(w: int, h: int, *, base_resos=None,
                      min_base_reso: int = 0, max_base_reso: int = 0,
                      base_reso_step: int = 256, min_reso: int = 512,
                      max_reso: int = 2048, step: int = BUCKET_STEP_SIZE,
                      no_upscale: bool = True) -> Tuple[int, int]:
    buckets = get_arb_buckets(
        base_resos=base_resos, min_base_reso=min_base_reso,
        max_base_reso=max_base_reso, base_reso_step=base_reso_step,
        min_reso=min_reso, max_reso=max_reso, step=step,
    )
    if not buckets:
        return calculate_output_size(w, h, target_area=TARGET_PIXEL_AREA, step=step)

    candidates = buckets
    if no_upscale:
        candidates = [
            (bw, bh) for bw, bh in buckets
            if bw <= max(1, w) and bh <= max(1, h)
        ]
    if not candidates:
        candidates = buckets
    return min(candidates, key=lambda b: _bucket_score(b, w, h))


def _center_crop_box_for_size(src_w: int, src_h: int,
                              crop_w: int, crop_h: int) -> Tuple[int, int, int, int]:
    crop_w = max(1, min(int(crop_w), int(src_w)))
    crop_h = max(1, min(int(crop_h), int(src_h)))
    x = max(0, (int(src_w) - crop_w) // 2)
    y = max(0, (int(src_h) - crop_h) // 2)
    return x, y, crop_w, crop_h


def _crop_to_bucket_pixels(src_w: int, src_h: int,
                           bucket_w: int, bucket_h: int) -> Tuple[int, int, int, int]:
    if bucket_w <= src_w and bucket_h <= src_h:
        return _center_crop_box_for_size(src_w, src_h, bucket_w, bucket_h)
    return smart_crop_box(src_w, src_h, bucket_w, bucket_h)


def _crop_to_bucket_aspect(src_w: int, src_h: int,
                           bucket_w: int, bucket_h: int,
                           snap_loss_threshold: float = 0.12) -> Tuple[int, int, int, int]:
    aspect_crop = smart_crop_box(src_w, src_h, bucket_w, bucket_h)
    if bucket_w > src_w or bucket_h > src_h:
        return aspect_crop

    snap_area = bucket_w * bucket_h
    aspect_area = aspect_crop[2] * aspect_crop[3]
    loss = 1.0 - (snap_area / max(1, aspect_area))
    if loss <= max(0.0, float(snap_loss_threshold)):
        return _center_crop_box_for_size(src_w, src_h, bucket_w, bucket_h)
    return aspect_crop


def _base_for_bucket(bucket: Tuple[int, int]) -> float:
    return math.sqrt(max(1, bucket[0] * bucket[1]))


def _find_output_bucket_for_limit(bucket: Tuple[int, int],
                                  max_base_reso: int,
                                  *, min_reso: int, step: int) -> Tuple[int, int]:
    max_base = int(max_base_reso or 0)
    if max_base <= 0 or _base_for_bucket(bucket) <= max_base:
        return bucket

    ar = bucket[0] / bucket[1]
    target_w = math.sqrt((max_base * max_base) * ar)
    target_h = target_w / ar
    step = max(1, int(step or BUCKET_STEP_SIZE))
    out_w = max(int(min_reso), int(round(target_w / step) * step))
    out_h = max(int(min_reso), int(round(target_h / step) * step))
    if out_w * out_h > max_base * max_base * 1.1:
        scale = math.sqrt((max_base * max_base) / (out_w * out_h))
        out_w = max(int(min_reso), int(math.floor(out_w * scale / step) * step))
        out_h = max(int(min_reso), int(math.floor(out_h * scale / step) * step))
    return max(1, out_w), max(1, out_h)


def plan_arb_export(src_w: int, src_h: int, *, base_resos=None,
                    min_base_reso: int = 0, max_base_reso: int = 0,
                    output_max_base_reso: int = 0,
                    base_reso_step: int = 256, min_reso: int = 512,
                    max_reso: int = 2048, step: int = BUCKET_STEP_SIZE,
                    no_upscale: bool = True) -> ArbExportPlan:
    bucket = choose_arb_bucket(
        src_w, src_h, base_resos=base_resos,
        min_base_reso=min_base_reso, max_base_reso=max_base_reso,
        base_reso_step=base_reso_step, min_reso=min_reso,
        max_reso=max_reso, step=step, no_upscale=no_upscale,
    )
    crop_box = _crop_to_bucket_aspect(src_w, src_h, bucket[0], bucket[1])
    if no_upscale and (bucket[0] > src_w or bucket[1] > src_h):
        return ArbExportPlan(
            bucket_size=bucket,
            crop_box=crop_box,
            output_size=(crop_box[2], crop_box[3]),
            action="keep",
            needs_resize=False,
        )
    crop_size = (crop_box[2], crop_box[3])
    if output_max_base_reso and _base_for_bucket(crop_size) > output_max_base_reso:
        output_size = _find_output_bucket_for_limit(
            crop_size, output_max_base_reso, min_reso=min_reso, step=step,
        )
    else:
        output_size = crop_size
    needs_resize = output_size != (crop_box[2], crop_box[3])
    action = "downsample" if needs_resize else "crop"
    return ArbExportPlan(
        bucket_size=bucket,
        crop_box=crop_box,
        output_size=output_size,
        action=action,
        needs_resize=needs_resize,
    )


# sRGB ↔ 线性光 转换（IEC 61966-2-1）。输入/输出都是 [0,1] 浮点。
_SRGB_THRESH = 0.04045
_LIN_THRESH = 0.0031308


def _srgb_to_linear(a: np.ndarray) -> np.ndarray:
    return np.where(a <= _SRGB_THRESH, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(a: np.ndarray) -> np.ndarray:
    a = np.clip(a, 0.0, 1.0)
    return np.where(a <= _LIN_THRESH, a * 12.92, 1.055 * (a ** (1.0 / 2.4)) - 0.055)


# 输入是 8-bit sRGB，用 256 项 LUT 把 sRGB→线性 的 pow 从「全图逐像素」降为查表，
# 大图下采样省掉 ~6 倍开销（线性→sRGB 只作用在更小的输出上，保留 pow 即可）。
_SRGB_TO_LIN_LUT = _srgb_to_linear(np.arange(256, dtype=np.float32) / 255.0).astype(np.float32)


def _resize_linear_light(img: Image.Image, size: Tuple[int, int],
                         resample=Image.Resampling.LANCZOS) -> Image.Image:
    """在线性光空间重采样（gamma-correct）。逐通道转 32-bit float 单独 resize，
    避免 8-bit 中间量化引入 banding。仅处理 RGB，返回 RGB。"""
    lin = _SRGB_TO_LIN_LUT[np.asarray(img, dtype=np.uint8)]
    chans = []
    for c in range(3):
        fch = Image.fromarray(np.ascontiguousarray(lin[:, :, c]), mode="F")
        chans.append(np.asarray(fch.resize(size, resample=resample), dtype=np.float32))
    srgb = _linear_to_srgb(np.stack(chans, axis=-1))
    out = np.clip(srgb * 255.0 + 0.5, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(out, mode="RGB")


def downsample_preserving_detail(img, target_size: Tuple[int, int]):
    """高质量下采样：在【线性光空间】用 Lanczos 一次到位。

    旧实现是「BOX 逐级减半 + 末尾 Lanczos」，且全程在 sRGB(gamma 编码)值上做
    加权平均——这正是「画面损伤太大」的两个根源：
      1) gamma 误差：sRGB 不是线性的，直接对编码值平均会让亮部 / 高频细节整体
         偏暗、对比与亮度流失（见 Brasseur「gamma error in picture scaling」、
         Pillow #1604）。正确做法是 sRGB→线性 → 重采样 → 线性→sRGB。
      2) 多余的 BOX 预缩：Pillow 的 Lanczos 缩小时已自带按比例展宽的抗锯齿预滤波，
         任意缩放比一次到位即可；逐级 BOX 只是额外软化、削弱锐度。
    中间量用 32-bit float，避免 8-bit 量化 banding。
    """
    target_w, target_h = int(target_size[0]), int(target_size[1])
    if img.size == (target_w, target_h):
        return img.copy()
    if target_w >= img.width or target_h >= img.height:
        # 放大（本管线 no_upscale=True 下基本不发生）：保持原行为
        return img.resize((target_w, target_h), resample=Image.Resampling.LANCZOS)
    if img.mode != "RGB":
        # 非 RGB（如带 alpha / 调色板）退回普通 Lanczos，避免线性化误处理通道
        return img.resize((target_w, target_h), resample=Image.Resampling.LANCZOS)
    return _resize_linear_light(img, (target_w, target_h))


def adjust_crop_box(x: float, y: float, w: float, h: float,
                    target_w: int, target_h: int,
                    img_w: int, img_h: int) -> Tuple[int, int, int, int]:
    target_ar = target_w / target_h
    center_x = x + w / 2
    center_y = y + h / 2

    new_w, new_h = w, h

    if w / h > target_ar:
        new_h = w / target_ar
        if new_h > img_h:
            new_h = img_h
            new_w = new_h * target_ar
    else:
        new_w = h * target_ar
        if new_w > img_w:
            new_w = img_w
            new_h = new_w / target_ar

    new_x = center_x - new_w / 2
    new_y = center_y - new_h / 2

    if new_x < 0: new_x = 0
    if new_y < 0: new_y = 0
    if new_x + new_w > img_w: new_x = img_w - new_w
    if new_y + new_h > img_h: new_y = img_h - new_h

    # 先把左上角 floor 成 int，再用 int 后的值算右下边界；否则浮点 new_x 截断后
    # new_w 可能多出 1 px，最终 new_x_int + new_w_int > img_w → PIL.crop 越界出黑边。
    ix = max(0, int(new_x))
    iy = max(0, int(new_y))
    iw = max(0, min(int(new_w), img_w - ix))
    ih = max(0, min(int(new_h), img_h - iy))
    return ix, iy, iw, ih


def smart_crop_box(img_w: int, img_h: int,
                   target_w: int, target_h: int) -> Tuple[int, int, int, int]:
    """给定源尺寸 + 目标桶尺寸，返回居中、最小裁切的 (x, y, w, h)。"""
    if img_w <= 0 or img_h <= 0 or target_w <= 0 or target_h <= 0:
        return 0, 0, img_w, img_h
    target_ar = target_w / target_h
    src_ar = img_w / img_h
    if src_ar > target_ar:
        new_h = img_h
        new_w = int(round(new_h * target_ar))
        new_w = min(new_w, img_w)
    else:
        new_w = img_w
        new_h = int(round(new_w / target_ar))
        new_h = min(new_h, img_h)
    x = max(0, (img_w - new_w) // 2)
    y = max(0, (img_h - new_h) // 2)
    return x, y, new_w, new_h
