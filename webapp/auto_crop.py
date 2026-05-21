# webapp/auto_crop.py — 智能裁切建议（边缘 + 显著性 + 阈值融合）
#
# Crop 屏初次打开一张图时给一个推荐裁切框。算法来自 drop.py 的 v4 检测路径，
# 抽出来供 WebUI 用，不再依赖 drop.py 整文件（drop.py 顶部还 import tkinter）。
#
# 入口只有 calculate_auto_crop_box(img, target_aspect_ratio)；内部辅助函数都是私有。

import math
from typing import Dict, List, Tuple

import cv2
import numpy as np
from PIL import Image


# ============== 可调常量 ==============
CROP_THRESHOLD = 230
CROP_PADDING_RATIO = 0.10
MIN_CROP_PADDING = 40
MAX_CROP_PADDING = 200
MORPH_KERNEL_SIZE = 3
MORPH_ITERATIONS = 1
MIN_SUBJECT_AREA_RATIO = 0.03
COMPACT_THRESHOLD = 2.8
PRESERVE_TOP_BIAS = 1.0
HORIZONTAL_CENTER_BIAS = 1.0
VERTICAL_SAFETY_MARGIN = 0.97
AUTO_DETECT_VERTICAL_FOCUS = True
VERTICAL_FOCUS_THRESHOLD = 0.6

# 检测融合
USE_EDGE_DETECTION = True
USE_SALIENCY_DETECTION = True
EDGE_WEIGHT = 0.3
SALIENCY_WEIGHT = 0.4
CONTENT_WEIGHT = 0.3


# ============== 内部辅助 ==============
def _get_adaptive_padding(img_width: int, img_height: int) -> int:
    avg_dim = (img_width + img_height) / 2
    return max(MIN_CROP_PADDING, min(MAX_CROP_PADDING, int(avg_dim * CROP_PADDING_RATIO)))


def _get_edge_map(img: Image.Image) -> np.ndarray:
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img_np = np.array(img)
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)
    return edges


def _get_saliency_map(img: Image.Image) -> np.ndarray:
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img_np = np.array(img)
    try:
        saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
        success, saliency_map = saliency.computeSaliency(img_np)
        if success:
            saliency_map = (saliency_map * 255).astype(np.uint8)
            _, saliency_map = cv2.threshold(saliency_map, 0, 255,
                                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return saliency_map
    except Exception:
        pass
    # cv2.saliency 不可用时退化到拉普拉斯 + Otsu
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    _, saliency_map = cv2.threshold(np.uint8(np.absolute(lap)), 0, 255,
                                    cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return saliency_map


def _get_all_subjects(img: Image.Image, threshold: int,
                     kernel_size: int, iterations: int) -> List[Dict]:
    if img.mode != 'RGB':
        img = img.convert('RGB')
    open_cv_image = np.array(img)[:, :, ::-1]
    gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)

    # 用 4 个角的平均亮度判断主体在亮背景还是暗背景上
    corner_pixels = [gray[0, 0], gray[0, -1], gray[-1, 0], gray[-1, -1]]
    is_light_bg = np.mean(corner_pixels) > 128
    thresh_type = cv2.THRESH_BINARY_INV if is_light_bg else cv2.THRESH_BINARY

    _, thresh1 = cv2.threshold(gray, threshold if is_light_bg else (255 - threshold),
                               255, thresh_type)
    thresh2 = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    thresh_type, 21, 10)
    edge_contribution = _get_edge_map(img) if USE_EDGE_DETECTION else np.zeros_like(gray)
    saliency_contribution = _get_saliency_map(img) if USE_SALIENCY_DETECTION else np.zeros_like(gray)

    thresh_combined = (
        CONTENT_WEIGHT * cv2.bitwise_or(thresh1, thresh2)
        + EDGE_WEIGHT * edge_contribution
        + SALIENCY_WEIGHT * saliency_contribution
    ).astype(np.uint8)
    thresh_combined = cv2.normalize(thresh_combined, None, 0, 255, cv2.NORM_MINMAX)
    _, thresh_combined = cv2.threshold(thresh_combined, 127, 255, cv2.THRESH_BINARY)

    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    closed_thresh = cv2.morphologyEx(
        cv2.morphologyEx(thresh_combined, cv2.MORPH_OPEN, kernel, iterations=iterations),
        cv2.MORPH_CLOSE, kernel, iterations=1,
    )
    contours, _ = cv2.findContours(closed_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    subjects = []
    img_area = img.width * img.height
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < img_area * MIN_SUBJECT_AREA_RATIO:
            continue
        subjects.append({
            'x': x, 'y': y, 'w': w, 'h': h, 'area': area,
            'center_x': x + w / 2, 'center_y': y + h / 2,
            'aspect_ratio': w / h,
        })
    subjects.sort(key=lambda s: s['area'], reverse=True)
    return subjects


def _analyze_scene_type(subjects: List[Dict], img_width: int, img_height: int) -> Dict:
    if not subjects:
        return {'type': 'empty', 'focus_box': (0, 0, img_width, img_height),
                'strategy': 'preserve_all', 'confidence': 1.0, 'vertical_focus': 'center'}

    total_weighted_y = sum(s['center_y'] * s['area'] for s in subjects)
    total_area = sum(s['area'] for s in subjects)
    normalized_com_y = (total_weighted_y / total_area) / img_height if total_area > 0 else 0.5
    vertical_focus = (
        'bottom' if normalized_com_y > VERTICAL_FOCUS_THRESHOLD
        else 'top' if normalized_com_y < (1 - VERTICAL_FOCUS_THRESHOLD)
        else 'center'
    )

    if len(subjects) == 1:
        s = subjects[0]
        return {'type': 'single', 'focus_box': (s['x'], s['y'], s['w'], s['h']),
                'strategy': 'preserve_top', 'confidence': 1.0,
                'subject_count': 1, 'vertical_focus': vertical_focus}

    min_x, min_y = min(s['x'] for s in subjects), min(s['y'] for s in subjects)
    max_x = max(s['x'] + s['w'] for s in subjects)
    max_y = max(s['y'] + s['h'] for s in subjects)
    union_box_area = (max_x - min_x) * (max_y - min_y)
    total_subject_area = sum(s['area'] for s in subjects)

    avg_dist = 0
    if len(subjects) > 1:
        dists = []
        for i in range(len(subjects)):
            for j in range(i + 1, len(subjects)):
                dists.append(math.hypot(
                    subjects[i]['center_x'] - subjects[j]['center_x'],
                    subjects[i]['center_y'] - subjects[j]['center_y'],
                ))
        if dists:
            avg_dist = float(np.mean(dists))
    avg_subject_size = math.sqrt(total_subject_area / len(subjects)) if subjects else 0
    density_ratio = total_subject_area / union_box_area if union_box_area > 0 else 0
    spacing_ratio = avg_dist / avg_subject_size if avg_subject_size > 0 else 0

    if density_ratio > 0.3 and spacing_ratio < COMPACT_THRESHOLD:
        return {'type': 'compact_group',
                'focus_box': (min_x, min_y, max_x - min_x, max_y - min_y),
                'strategy': 'preserve_all', 'confidence': density_ratio,
                'vertical_focus': vertical_focus}
    largest = subjects[0]
    return {'type': 'scattered',
            'focus_box': (largest['x'], largest['y'], largest['w'], largest['h']),
            'strategy': 'focus_largest',
            'confidence': largest['area'] / total_subject_area,
            'vertical_focus': vertical_focus}


# ============== 公开入口 ==============
def calculate_auto_crop_box(img: Image.Image,
                            target_aspect_ratio: float) -> Tuple[int, int, int, int]:
    """根据 img 内容分析（边缘+显著性+阈值）给一个 (x, y, w, h) 推荐裁切框。

    target_aspect_ratio 是希望的目标桶宽高比；返回框会朝这个 AR 收。
    Crop 屏初次加载一张图时调它，把推荐框作为第一个裁切框给用户。
    """
    img_w, img_h = img.size
    padding = _get_adaptive_padding(img_w, img_h)

    subjects = _get_all_subjects(img, CROP_THRESHOLD, MORPH_KERNEL_SIZE, MORPH_ITERATIONS)
    scene_analysis = _analyze_scene_type(subjects, img_w, img_h)

    fx, fy, fw, fh = scene_analysis['focus_box']
    fx = max(0, fx - padding)
    fy = max(0, fy - padding)
    fw = min(img_w - fx, fw + 2 * padding)
    fh = min(img_h - fy, fh + 2 * padding)
    focus_ar = fw / fh if fh > 0 else 1.0

    if abs(focus_ar - target_aspect_ratio) < 0.1:
        return (fx, fy, fw, fh)

    vertical_focus = scene_analysis.get('vertical_focus', 'center')
    effective_top_bias = PRESERVE_TOP_BIAS
    if AUTO_DETECT_VERTICAL_FOCUS:
        if vertical_focus == 'bottom':
            effective_top_bias = 0.0
        elif vertical_focus == 'top':
            effective_top_bias = 1.0

    if focus_ar > target_aspect_ratio:
        final_w = int(fh * target_aspect_ratio)
        final_h = fh
        final_x = fx + int((fw - final_w) * HORIZONTAL_CENTER_BIAS / 2)
        final_y = fy
    else:
        final_h = int(fw / target_aspect_ratio)
        max_allowed_h = int(fh * VERTICAL_SAFETY_MARGIN)
        final_h = min(final_h, max_allowed_h)
        final_w = fw
        final_x = fx
        final_y = fy + int((fh - final_h) * (1 - effective_top_bias))

    rx = max(0, min(final_x, img_w - 10))
    ry = max(0, min(final_y, img_h - 10))
    rw = max(10, min(final_w, img_w - rx))
    rh = max(10, min(final_h, img_h - ry))
    return (int(rx), int(ry), int(rw), int(rh))
