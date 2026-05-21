# webapp/video.py — 视频关键帧抽取（两步式工作流）
#
# 抽自 get_pictures.py。WebUI 只用两步式工作流：
#   probe_video_keyframes(...)        → 分析视频，列候选帧 + 缩略图
#   save_selected_keyframes(...)      → 把用户勾选的候选帧落盘
#
# 旧版「一键抽帧」(extract_frames_from_video / /api/extract_video) 没有保留：
# 新前端走 probe + save 流程，旧路由没有调用方。

import os
import random
from itertools import combinations
import math
from typing import Iterable, List, Optional, Tuple

import cv2
import numpy as np
import imagehash
from PIL import Image


# ============== 视频格式 ==============
VIDEO_EXTENSIONS: Tuple[str, ...] = (
    '.mp4', '.m4v', '.mov', '.qt',
    '.avi', '.mkv', '.webm',
    '.flv', '.f4v',
    '.wmv', '.asf',
    '.mpg', '.mpeg', '.m2v', '.mpe',
    '.mts', '.m2ts', '.ts', '.vob',
    '.3gp', '.3g2',
    '.ogv', '.ogg',
    '.rm', '.rmvb',
    '.divx', '.dv',
)
SUPPORTED_VIDEO_EXTENSIONS = tuple(e.lower() for e in VIDEO_EXTENSIONS)

# ============== 场景检测参数 ==============
# 直方图相关性阈值：越低越敏感，候选越多。前端可在 probe 时覆盖。
SCENE_DETECTION_THRESHOLD = 0.4
# 视频短于该时长（秒）就直接均匀采样，跳过场景检测
MIN_DURATION_FOR_SCENE_DETECTION = 5.0


def is_video_path(p: str) -> bool:
    p = (p or "").lower()
    return any(p.endswith(ext) for ext in SUPPORTED_VIDEO_EXTENSIONS)


# ============== 内部辅助 ==============
def _read_frame_at(cap, frame_idx: int):
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frame_idx)))
    ret, frame = cap.read()
    return frame if ret else None


def _calculate_min_pairwise_distance(indices, hashes) -> float:
    if len(indices) < 2:
        return float('inf')
    min_dist = float('inf')
    for i_idx, j_idx in combinations(indices, 2):
        if (0 <= i_idx < len(hashes) and 0 <= j_idx < len(hashes)
                and hashes[i_idx] is not None and hashes[j_idx] is not None):
            dist = hashes[i_idx] - hashes[j_idx]
            if dist < min_dist:
                min_dist = dist
        else:
            return 0
    return min_dist


def _select_most_dissimilar(group_indices: List[int], hashes: List, keep_count: int) -> List[int]:
    """从候选帧里挑差异最大的 keep_count 个（max-min phash 距离）。

    暴力枚举组合数过大时退化为随机采样，阈值 50000。
    """
    n = len(group_indices)
    valid_items = []
    for i in range(n):
        if i < len(hashes) and hashes[i] is not None:
            valid_items.append((group_indices[i], hashes[i]))
    n_valid = len(valid_items)
    effective_keep = min(n_valid, keep_count)

    if effective_keep <= 1:
        return [it[0] for it in random.sample(valid_items, effective_keep)]
    if effective_keep >= n_valid:
        return [it[0] for it in valid_items]

    try:
        num_combinations = math.comb(n_valid, effective_keep)
    except AttributeError:
        from math import factorial
        num_combinations = factorial(n_valid) // (
            factorial(effective_keep) * factorial(n_valid - effective_keep)
        )

    if num_combinations > 50000:
        print(f"  ⚠️ probe: 组合数 {num_combinations} 过大，从 {n_valid} 候选随机选 {effective_keep}")
        return [it[0] for it in random.sample(valid_items, effective_keep)]

    valid_hashes_only = [it[1] for it in valid_items]
    best: List[int] = []
    max_min_dist = -1
    for subset in combinations(range(n_valid), effective_keep):
        d = _calculate_min_pairwise_distance(subset, valid_hashes_only)
        if d > max_min_dist:
            max_min_dist = d
            best = [valid_items[i][0] for i in subset]

    if not best:
        return [it[0] for it in random.sample(valid_items, effective_keep)]
    return best


# ============== 公开入口 ==============
def probe_video_keyframes(
    video_path: str,
    *,
    scene_threshold: float = SCENE_DETECTION_THRESHOLD,
    max_candidates: int = 0,
    sample_rate_hz: float = 4.0,
    thumb_max_side: int = 512,
    write_thumbs_to: Optional[str] = None,
):
    """分析视频 → 返回所有候选关键帧元信息（不落盘最终结果）。

    参数:
      scene_threshold:  场景突变阈值（直方图相关性）。越低越敏感，候选越多。
      max_candidates:   候选数上限（0 = 不限）；超出时用 phash 选最差异化的 N 个。
      sample_rate_hz:   每秒抽几次做场景检测。
      thumb_max_side:   缩略图最长边像素。
      write_thumbs_to:  若给路径，把每个候选的预览 JPEG 写到这里，文件名 {idx:06d}.jpg。

    返回 dict（ok=False 时携带 error 字段）：
      { ok, video_path, total_frames, fps, duration, width, height, candidates, error }
    """
    info = {
        "ok": False, "video_path": video_path,
        "total_frames": 0, "fps": 0.0, "duration": 0.0,
        "width": 0, "height": 0, "candidates": [], "error": None,
    }

    if not os.path.exists(video_path):
        info["error"] = f"video not found: {video_path}"
        return info

    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            info["error"] = "OpenCV could not open the video (codec missing?)"
            return info

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = total_frames / fps if fps > 0 else 0
        info.update(total_frames=total_frames, fps=fps, duration=duration,
                    width=width, height=height)

        if total_frames <= 0:
            info["error"] = "video reports zero frames"
            return info

        # 1. 候选生成：场景突变检测，或短视频均匀采样
        if duration < MIN_DURATION_FOR_SCENE_DETECTION or fps <= 0:
            step = max(1, total_frames // max(1, int(max(3, max_candidates or 6) * 2)))
            candidate_indices = list(range(0, total_frames, step))
        else:
            scene_cuts = [0]
            prev_hist = None
            frame_skip = max(1, int(fps / max(0.5, sample_rate_hz)))
            for i in range(0, total_frames, frame_skip):
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                ok, frame = cap.read()
                if not ok:
                    continue
                hist = cv2.calcHist([frame], [0, 1], None, [256, 256], [0, 256, 0, 256])
                cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
                if prev_hist is not None:
                    score = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                    if score < scene_threshold:
                        scene_cuts.append(i)
                prev_hist = hist
            # 取每个场景的中间帧作为候选
            candidate_indices = []
            for j, start_frame in enumerate(scene_cuts):
                end_frame = scene_cuts[j + 1] if j + 1 < len(scene_cuts) else total_frames - 1
                mid = start_frame + (end_frame - start_frame) // 2
                candidate_indices.append(mid)

        candidate_indices = sorted(set(int(x) for x in candidate_indices if 0 <= x < total_frames))
        if not candidate_indices:
            info["error"] = "no candidate frames found"
            return info

        # 2. 超出 max_candidates 时用 phash 选最差异化的子集
        if max_candidates and len(candidate_indices) > max_candidates:
            hashes, valid = [], []
            for fi in candidate_indices:
                frame = _read_frame_at(cap, fi)
                if frame is None:
                    continue
                try:
                    pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    hashes.append(imagehash.phash(pil))
                    valid.append(fi)
                except Exception:
                    pass
            if valid and len(valid) > max_candidates:
                candidate_indices = sorted(set(_select_most_dissimilar(valid, hashes, max_candidates)))
            elif valid:
                candidate_indices = valid

        # 3. 写缩略图
        if write_thumbs_to:
            os.makedirs(write_thumbs_to, exist_ok=True)

        candidates = []
        for fi in candidate_indices:
            frame = _read_frame_at(cap, fi)
            if frame is None:
                continue
            ts = (fi / fps) if fps > 0 else 0.0

            thumb_filename = None
            if write_thumbs_to:
                h, w = frame.shape[:2]
                scale = min(1.0, thumb_max_side / max(h, w)) if max(h, w) > 0 else 1.0
                if scale < 1.0:
                    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                    thumb = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
                else:
                    thumb = frame
                thumb_filename = f"{fi:06d}.jpg"
                cv2.imwrite(
                    os.path.join(write_thumbs_to, thumb_filename),
                    thumb, [cv2.IMWRITE_JPEG_QUALITY, 82],
                )

            candidates.append({
                "idx": int(fi), "ts": float(ts),
                "thumb_filename": thumb_filename,
            })

        info["candidates"] = candidates
        info["ok"] = True
        return info

    except Exception as e:
        info["error"] = f"probe failed: {e}"
        return info
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass


def save_selected_keyframes(
    video_path: str,
    output_dir: str,
    frame_indices: Iterable[int],
    *,
    name_prefix: Optional[str] = None,
    start_counter: int = 1,
    zero_pad: int = 4,
):
    """把指定 frame_indices 的原始帧落盘到 output_dir（PNG）。

    name_prefix 留空则用 <视频文件名去扩展>。返回 (saved_count, written_paths)。
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(video_path))[0]
    prefix = name_prefix if name_prefix is not None else base

    cap = None
    written: List[str] = []
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise IOError(f"cannot open video: {video_path}")
        counter = start_counter
        for fi in sorted(set(int(x) for x in frame_indices)):
            frame = _read_frame_at(cap, fi)
            if frame is None:
                continue
            name = f"{prefix}_{str(counter).zfill(zero_pad)}.png"
            target = os.path.join(output_dir, name)
            if cv2.imwrite(target, frame):
                written.append(target)
                counter += 1
        return len(written), written
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
