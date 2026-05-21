# tagger/dbimutils.py - DanBooru image utilities (copy of 2_tagger/backend/dbimutils.py)

import cv2
import numpy as np
from PIL import Image


def smart_imread(img: str, flag: int = cv2.IMREAD_UNCHANGED) -> np.ndarray:
    if img.lower().endswith(".gif"):
        pil_img = Image.open(img).convert("RGB")
        return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return cv2.imread(img, flag)


def smart_24bit(img: np.ndarray) -> np.ndarray:
    if img.dtype == np.uint16:
        img = (img / 257).astype(np.uint8)
    if len(img.shape) == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3]
        trans_mask = alpha == 0
        img[trans_mask] = [255, 255, 255, 255]
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def make_square(img: np.ndarray, target_size: int) -> np.ndarray:
    old_h, old_w = img.shape[:2]
    desired_size = max(old_h, old_w, target_size)
    delta_w = desired_size - old_w
    delta_h = desired_size - old_h
    top = delta_h // 2
    bottom = delta_h - top
    left = delta_w // 2
    right = delta_w - left
    return cv2.copyMakeBorder(img, top, bottom, left, right,
                              cv2.BORDER_CONSTANT, value=[255, 255, 255])


def smart_resize(img: np.ndarray, size: int) -> np.ndarray:
    h, w = img.shape[:2]
    if h == size and w == size:
        return img
    if h > size or w > size:
        return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_CUBIC)
