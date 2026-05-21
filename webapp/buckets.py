# webapp/buckets.py — 分桶数学

import math
from typing import List, Tuple

from webapp.config import BUCKET_STEP_SIZE, TARGET_PIXEL_AREA


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
