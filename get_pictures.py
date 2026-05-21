# -*- coding: utf-8 -*-
import os
import cv2
import shutil
import random
from PIL import Image
import imagehash
from itertools import combinations
from tqdm import tqdm
import math
import numpy as np
import zipfile
import tempfile

# --- 配置设定 ---
# 源文件夹路径
source_dir = r'D:\Datasets\chuzenji\ori'
# 目标文件夹路径
target_dir = r'D:\Datasets\chuzenji\chuzenji1\1_chvzenj1'

# 从每个视频中提取多少帧
FRAMES_PER_VIDEO = 3
# 【已废弃】对每个视频初步采样多少帧用于哈希比较 (新方法不再需要)
# VIDEO_FRAME_SAMPLE_COUNT = 384

# --- 新增：视频处理优化配置 ---
# 场景变化检测阈值。值越低，检测越灵敏，会找出更多场景。
# 对于动画类，颜色变化剧烈，建议值高一些 (e.g., 0.4-0.6)。
# 对于真人实拍，可以低一些 (e.g., 0.2-0.4)。可以根据你的数据微调。
SCENE_DETECTION_THRESHOLD = 0.4
# 视频最短时长（秒），短于此时长的视频将使用旧的均匀采样方法，因为场景检测意义不大
MIN_DURATION_FOR_SCENE_DETECTION = 5.0

# 处理模式
# PROCESSING_MODE = "zips_only"
PROCESSING_MODE = "files_and_videos"
# PROCESSING_MODE = "zips_only"

# ZIP 文件解压密码
ZIP_PASSWORD = None  # 如果不使用密码或密码为空

# 支持的文件格式
image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
# 扩展视频扩展名（覆盖 OpenCV/FFmpeg 后端常见的容器格式）。
video_extensions = [
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
]


# --- 配置结束 ---

def extract_frames_from_video(video_path, output_dir, frames_per_video=FRAMES_PER_VIDEO, start_counter=1):
    """
    供外部调用的视频抽帧函数
    :param video_path: 视频文件路径
    :param output_dir: 输出目录
    :param frames_per_video: 提取帧数
    :param start_counter: 文件名起始序号
    :return: (saved_count, next_counter)
    """
    global global_counter
    global_counter = start_counter
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        
    base_name = os.path.basename(video_path)
    
    # 临时修改全局配置以匹配参数
    original_frames = FRAMES_PER_VIDEO
    # 注意：这里修改全局变量是为了复用 process_video_with_scene_detection 内部逻辑，
    # 但 process_video_with_scene_detection 使用的是 global FRAMES_PER_VIDEO (作为默认值或常量)
    # 实际上 process_video_with_scene_detection 内部直接使用了 FRAMES_PER_VIDEO 常量。
    # 为了不修改原有逻辑的大结构，我们这里需要传递参数给 process_video_with_scene_detection
    # 或者修改 process_video_with_scene_detection 接受参数。
    # 为了最小化改动，我将修改 process_video_with_scene_detection 的签名，使其接受可选参数。
    
    process_video_with_scene_detection(video_path, base_name, output_dir, target_frames=frames_per_video)
    
    return global_counter - start_counter, global_counter


# --- 辅助函数 (哈希计算等) ---
# [这部分函数未修改，保持原样]
def calculate_min_pairwise_distance(indices, hashes):
    if len(indices) < 2:
        return float('inf')
    min_dist = float('inf')
    for i_idx, j_idx in combinations(indices, 2):
        if 0 <= i_idx < len(hashes) and 0 <= j_idx < len(hashes) and hashes[i_idx] is not None and hashes[
            j_idx] is not None:
            dist = hashes[i_idx] - hashes[j_idx]
            if dist < min_dist:
                min_dist = dist
        else:
            return 0
    return min_dist


def select_most_dissimilar(group_indices, hashes, keep_count):
    n = len(group_indices)
    valid_items = []
    for i in range(n):
        # 修正一个小bug：应该是 group_indices[i] 对应的 hashes[i]
        if i < len(hashes) and hashes[i] is not None:
            valid_items.append((group_indices[i], hashes[i]))
    n_valid = len(valid_items)
    effective_keep_count = min(n_valid, keep_count)

    if effective_keep_count <= 1:
        selected_valid_items = random.sample(valid_items, effective_keep_count)
        return [item[0] for item in selected_valid_items]
    if effective_keep_count >= n_valid:
        return [item[0] for item in valid_items]

    best_subset_original_indices = []
    max_min_dist = -1
    valid_hashes_only = [item[1] for item in valid_items]
    indices_for_combination = list(range(n_valid))

    try:
        num_combinations = math.comb(n_valid, effective_keep_count)
    except AttributeError:
        from math import factorial
        if effective_keep_count < 0 or effective_keep_count > n_valid:
            num_combinations = 0
        else:
            num_combinations = factorial(n_valid) // (
                    factorial(effective_keep_count) * factorial(n_valid - effective_keep_count))

    combination_threshold = 50000
    if num_combinations > combination_threshold:
        tqdm.write(
            f"\n  - 警告：组合数 ({num_combinations}) 过大。将从 {n_valid} 个有效帧中随机选择 {effective_keep_count} 个。" # noqa
        )
        selected_valid_items = random.sample(valid_items, effective_keep_count)
        return [item[0] for item in selected_valid_items]

    subset_indices_generator = combinations(indices_for_combination, effective_keep_count)
    for subset_indices_relative in subset_indices_generator:
        min_dist = calculate_min_pairwise_distance(subset_indices_relative, valid_hashes_only)
        if min_dist > max_min_dist:
            max_min_dist = min_dist
            best_subset_original_indices = [valid_items[i][0] for i in subset_indices_relative]

    if not best_subset_original_indices:
        tqdm.write(f"\n  - 警告：未能找到最优子集。将随机选择 {effective_keep_count} 个。")
        selected_valid_items = random.sample(valid_items, effective_keep_count)
        return [item[0] for item in selected_valid_items]
    return best_subset_original_indices


# --- 【核心修改】统一处理并保存为 PNG 的函数 ---
# [原 `convert_webp_to_png` 函数已被移除，其逻辑已合并到下面的新函数中]
def _process_and_copy_image(source_image_path, original_base_name, target_dir_for_image):
    """
    打开任何格式的图片，并统一将其保存为 PNG 格式。
    """
    global global_counter
    try:
        # 目标文件名总是 .png
        new_name = f"{global_counter:04d}.png"
        target_path = os.path.join(target_dir_for_image, new_name)

        # 使用 Pillow 打开图像文件
        with Image.open(source_image_path) as img:
            # 为了保证兼容性，处理不同的图像模式
            # 如果图像有透明度通道 (RGBA, LA) 或调色板模式(P)中带有透明度信息
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                # 转换为 RGBA 模式以保留透明度
                img = img.convert('RGBA')
            # 如果图像不是标准的 RGB 模式（例如灰度图 'L'）
            elif img.mode != 'RGB':
                # 转换为 RGB 模式
                img = img.convert('RGB')

            # 以 PNG 格式保存
            img.save(target_path, 'PNG')

        global_counter += 1
        return True

    except Exception as e:
        tqdm.write(f"\n处理图片 {original_base_name} 时出错 (已跳过): {str(e)}")
        return False


# --- 【核心优化】新的视频处理函数 ---
# [这部分函数未修改，因为它已经输出 PNG 格式]
def process_video_with_scene_detection(video_path, base_name, target_dir_for_video, target_frames=FRAMES_PER_VIDEO):
    """
    使用场景变化检测来智能提取视频帧
    """
    global global_counter
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            tqdm.write(f"\n警告：无法打开视频文件 {base_name}。已跳过。" # noqa
            )
            return

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = total_frames / fps if fps > 0 else 0
        tqdm.write(f"\n正在处理视频: {base_name} ({total_frames} 帧, {duration:.2f} 秒)")

        if total_frames < target_frames:
            tqdm.write(f"  - 视频总帧数 ({total_frames}) 少于要提取的数量，将提取所有帧。" # noqa
            )
            candidate_indices = list(range(total_frames))
        elif duration < MIN_DURATION_FOR_SCENE_DETECTION:
            tqdm.write(f"  - 视频时长短于阈值，使用均匀采样。" # noqa
            )
            # 对短视频使用简化的均匀采样
            step = max(1, total_frames // (target_frames * 5))  # 采样 target_frames*5 个点来选择
            uniform_indices = list(range(0, total_frames, step))
            # 短视频直接用这些采样点即可，或者也可以走一遍哈希
            candidate_indices = uniform_indices
        else:
            tqdm.write(f"  - 正在检测场景变化...")
            scene_cuts = [0]  # 总是从第一帧开始
            prev_hist = None
            # 为了效率，不需要逐帧比较，可以每秒比较几次
            frame_skip = max(1, int(fps / 4))  # 每秒检测4次

            for i in range(0, total_frames, frame_skip):
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                ret, frame = cap.read()
                if not ret:
                    continue

                # 计算直方图进行比较
                hist = cv2.calcHist([frame], [0, 1], None, [256, 256], [0, 256, 0, 256])
                cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

                if prev_hist is not None:
                    # 使用相关性比较，1为完全相同，-1为完全不同，0为不相关
                    score = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                    if score < SCENE_DETECTION_THRESHOLD:
                        scene_cuts.append(i)  # 记录场景切换的帧号
                prev_hist = hist

            tqdm.write(f"  - 检测到 {len(scene_cuts)} 个潜在场景。" # noqa
            )

            # 从每个场景中选择中间帧作为候选
            candidate_indices = []
            for i in range(len(scene_cuts)):
                start_frame = scene_cuts[i]
                end_frame = scene_cuts[i + 1] if i + 1 < len(scene_cuts) else total_frames - 1
                # 选取场景的中间帧
                middle_frame = start_frame + (end_frame - start_frame) // 2
                candidate_indices.append(middle_frame)

            # 去重
            candidate_indices = sorted(list(set(candidate_indices)))

        if not candidate_indices:
            tqdm.write(f"  - 错误：未能从视频 {base_name} 中找到任何候选帧。已跳过。" # noqa
            )
            return

        # 决定最终要保留的帧
        if len(candidate_indices) <= target_frames:
            selected_frame_numbers = candidate_indices
            tqdm.write(f"  - 候选帧数量 ({len(candidate_indices)}) 不足，全部保留。" # noqa
            )
        else:
            # 如果候选帧过多，使用哈希法优中选优
            tqdm.write(f"  - 从 {len(candidate_indices)} 个候选帧中筛选 {target_frames} 个差异最大的帧...")
            candidate_hashes = []
            valid_indices = []
            for frame_index in candidate_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ret, frame = cap.read()
                if ret:
                    try:
                        pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        phash = imagehash.phash(pil_img)
                        candidate_hashes.append(phash)
                        valid_indices.append(frame_index)
                    except Exception:
                        continue  # 如果哈希计算失败则跳过

            if not valid_indices:
                tqdm.write(f"  - 错误：无法哈希任何候选帧。已跳过。" # noqa
                )
                return

            selected_frame_numbers = select_most_dissimilar(
                valid_indices,
                candidate_hashes,
                target_frames
            )

        # 保存选定的帧
        frames_saved_count = 0
        for frame_number in sorted(selected_frame_numbers):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ret, frame = cap.read()
            if ret:
                new_name = f"{global_counter:04d}.png"
                target_path = os.path.join(target_dir_for_video, new_name)
                saved = cv2.imwrite(target_path, frame)
                if saved:
                    global_counter += 1
                    frames_saved_count += 1

        if frames_saved_count > 0:
            tqdm.write(f"  - 已成功从视频 {base_name} 提取 {frames_saved_count} 帧。" # noqa
            )

    except Exception as e:
        tqdm.write(f"\n处理视频 {base_name} 时发生意外错误: {str(e)}")
    finally:
        if cap is not None and cap.isOpened():
            cap.release()


# --- 主要处理函数 ---
global_counter = 1


def process_files():
    global global_counter

    if os.path.exists(target_dir):
        print(f"目标目录 {target_dir} 已存在，正在清空...")
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    print(f"已创建空的目标目录: {target_dir}\n")

    global_counter = 1

    all_files_to_process = []
    print("正在扫描源文件夹...")
    for root, _, files in os.walk(source_dir):
        for file in files:
            all_files_to_process.append(os.path.join(root, file))
    print(f"扫描完成，共找到 {len(all_files_to_process)} 个文件。\n")

    print("开始处理文件...")
    for file_path in tqdm(all_files_to_process, desc="处理文件", unit="个"):
        base_name = os.path.basename(file_path)
        _, ext = os.path.splitext(base_name)
        ext = ext.lower()

        # --- ZIP 文件处理 ---
        if ext == '.zip':
            if PROCESSING_MODE in ["zips_only", "all"]:
                tqdm.write(f"\n正在处理 ZIP 文件: {base_name}")
                temp_extract_path = None
                try:
                    # ... [ZIP 处理逻辑与原脚本完全相同，此处省略以保持简洁] ...
                    temp_extract_path = tempfile.mkdtemp(prefix="zip_extract_", dir=target_dir)
                    with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        pwd = None
                        if ZIP_PASSWORD:
                            pwd = ZIP_PASSWORD.encode('utf-8')
                        try:
                            zip_ref.extractall(path=temp_extract_path, pwd=pwd)
                        except RuntimeError as e:
                            if "password" in str(e).lower():
                                tqdm.write(f"  - 使用密码 '{ZIP_PASSWORD}' 解压 {base_name} 失败: {e}")
                                if pwd:
                                    tqdm.write(f"  - 尝试无密码解压 {base_name}...")
                                    try:
                                        zip_ref.extractall(path=temp_extract_path, pwd=None)
                                    except Exception as e_no_pwd:
                                        tqdm.write(f"  - 无密码解压 {base_name} 也失败: {e_no_pwd}")
                                        continue
                                else:
                                    tqdm.write(f"  - 无密码解压 {base_name} 失败: {e}")
                                    continue
                            else:
                                raise
                        except Exception as e_other:
                            tqdm.write(f"  - 解压 {base_name} 时发生其他错误: {e_other}")
                            continue

                    extracted_images_count = 0
                    for extracted_root, _, extracted_files in os.walk(temp_extract_path):
                        for extracted_file_name in extracted_files:
                            extracted_file_path = os.path.join(extracted_root, extracted_file_name)
                            _, extracted_ext = os.path.splitext(extracted_file_name)
                            if extracted_ext.lower() in image_extensions:
                                if _process_and_copy_image(extracted_file_path, extracted_file_name, target_dir):
                                    extracted_images_count += 1
                    if extracted_images_count > 0:
                        tqdm.write(f"  - 从 {base_name} 中提取并处理了 {extracted_images_count} 张图片。" # noqa
                        )
                except zipfile.BadZipFile:
                    tqdm.write(f"  - 错误：文件 {base_name} 不是一个有效的 ZIP 文件或已损坏。" # noqa
                    )
                except Exception as e:
                    tqdm.write(f"\n处理 ZIP 文件 {base_name} 时发生未知错误: {str(e)}")
                finally:
                    if temp_extract_path and os.path.exists(temp_extract_path):
                        shutil.rmtree(temp_extract_path)

        # --- 图像文件处理 ---
        elif ext in image_extensions:
            if PROCESSING_MODE in ["files_and_videos", "all"]:
                _process_and_copy_image(file_path, base_name, target_dir)

        # --- 视频文件处理 ---
        elif ext in video_extensions:
            if PROCESSING_MODE in ["files_and_videos", "all"]:
                # 调用新的、优化过的视频处理函数
                process_video_with_scene_detection(file_path, base_name, target_dir)

    print(f"\n处理完成！共提取和复制 {global_counter - 1} 个文件到:", os.path.abspath(target_dir))


# --- 主程序入口 ---
if __name__ == "__main__":
    print("开始执行脚本...")
    print(f"当前处理模式: {PROCESSING_MODE}")
    if ZIP_PASSWORD:
        print(f"将尝试使用密码 '{ZIP_PASSWORD[:3]}...' (如果ZIP需要密码)")
    else:
        print("将尝试无密码解压ZIP文件。" # noqa
        )
    if PROCESSING_MODE == "zips_only":
        print("将仅处理 ZIP 文件内的图像。" # noqa
        )
    elif PROCESSING_MODE == "files_and_videos":
        print("将仅处理独立的图像和视频文件。" # noqa
        )
    elif PROCESSING_MODE == "all":
        print("将处理 ZIP 文件内的图像、独立的图像和视频文件。" # noqa
        )
    else:
        print(f"警告：未知的 PROCESSING_MODE ('{PROCESSING_MODE}')。可能不会处理任何文件。" # noqa
        )
        print("有效模式为: 'zips_only', 'files_and_videos', 'all'")

    process_files()
    print("脚本执行完毕。" # noqa
    )


# ============================================================
#  WebUI helpers (two-step keyframe selection workflow)
# ============================================================

SUPPORTED_VIDEO_EXTENSIONS = tuple(e.lower() for e in video_extensions)


def is_video_path(p: str) -> bool:
    """快速判断扩展名是否在支持列表内。"""
    p = (p or "").lower()
    return any(p.endswith(ext) for ext in SUPPORTED_VIDEO_EXTENSIONS)


def _read_frame_at(cap, frame_idx):
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frame_idx)))
    ret, frame = cap.read()
    return frame if ret else None


def probe_video_keyframes(
    video_path: str,
    *,
    scene_threshold: float = SCENE_DETECTION_THRESHOLD,
    max_candidates: int = 0,
    sample_rate_hz: float = 4.0,
    thumb_max_side: int = 512,
    write_thumbs_to: str = None,
):
    """
    分析一个视频，返回所有候选关键帧的元信息（不直接落盘最终结果）。

    参数:
      scene_threshold: 直方图相关性阈值；越低越敏感，候选越多。
      max_candidates:  上限 0 = 不限；超出时用 phash 选最差异化的 N 个。
      sample_rate_hz:  每秒抽几次做场景检测。
      thumb_max_side:  缩略图最长边。
      write_thumbs_to: 若给路径，把每个候选帧的预览 JPEG 写到该目录，
                        文件名形如 "{idx:06d}.jpg"。

    返回字典:
      {
        "ok": bool,
        "video_path": str,
        "total_frames": int, "fps": float, "duration": float,
        "width": int, "height": int,
        "candidates": [
            { "idx": int, "ts": float, "thumb_filename": str | None }
        ],
        "error": str | None,
      }
    """
    info = {
        "ok": False,
        "video_path": video_path,
        "total_frames": 0, "fps": 0.0, "duration": 0.0,
        "width": 0, "height": 0,
        "candidates": [],
        "error": None,
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

        # 1. 候选生成：场景突变检测（或短视频均匀采样）
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

        # 2. 若超出 max_candidates，用 phash 选差异最大的子集
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
                chosen = select_most_dissimilar(valid, hashes, max_candidates)
                candidate_indices = sorted(set(chosen))
            elif valid:
                candidate_indices = valid

        # 3. 写缩略图（若有目标目录）
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
                    thumb,
                    [cv2.IMWRITE_JPEG_QUALITY, 82],
                )

            candidates.append({
                "idx": int(fi),
                "ts": float(ts),
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
    frame_indices,
    *,
    name_prefix: str = None,
    start_counter: int = 1,
    zero_pad: int = 4,
):
    """
    把指定 frame_indices 的原始帧落盘到 output_dir，PNG 格式。
    name_prefix 留空则使用 视频文件名（去扩展）_{counter}。
    返回 (saved_count, written_paths)。
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(video_path))[0]
    prefix = name_prefix if name_prefix is not None else base

    cap = None
    written = []
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
            ok = cv2.imwrite(target, frame)
            if ok:
                written.append(target)
                counter += 1
        return len(written), written
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
