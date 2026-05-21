# -*- coding: utf-8 -*-
# 智能混合裁切处理器 V7.3 - 自由窗口交互版
#
# V7.3 更新日志:
# 1. [窗口自由化] 窗口现在可以像普通程序一样自由拖拽调整大小 (Resize)。
#    - 无论窗口被拉伸成什么样，图片都会保持比例居中显示 (Aspect Fit)。
# 2. [位置记忆] 程序只在第一次运行时自动定位窗口。
#    - 后续处理所有图片时，窗口都会保持在你上一次放置的位置和大小，不再重置。
# 3. [交互优化] 重构了鼠标映射逻辑，以适应动态变化的窗口尺寸。

import sys
import os
import shutil
import time
import math
import json
from collections import defaultdict, Counter
from pathlib import Path
from typing import Optional, Tuple, List, Dict
import tkinter as tk

import cv2
import numpy as np
from PIL import Image
from sklearn.cluster import KMeans
from tqdm import tqdm
import imagehash

# ============ TorchVision 兼容性补丁 ============ 
try:
    import torchvision.transforms.functional_tensor
except (ImportError, AttributeError):
    import torchvision.transforms.functional as F

    sys.modules['torchvision.transforms.functional_tensor'] = F

# ============ 参数配置 ============ 
INTERACTIVE_MODE = True
# 注意：V7.3 后，此参数仅作为首次启动的默认高度参考
PREVIEW_INITIAL_HEIGHT = 900

# --- 路径配置 ---
IMAGE_FOLDER = r"D:\Datasets\天凉多喝防冻液\fdy1"
OUTPUT_FOLDER = r"D:\Datasets\天凉多喝防冻液\fdy9\1_fdy"
PREFERENCE_FILE = "training_preferences.json"

# --- 超分辨率配置 ---
ENABLE_UPSCALING = True
UPSCALE_MODEL = "RealESRGAN_x4plus_anime_6B"
UPSCALE_FACTOR = 2

# --- 相似性检测 ---
SIMILARITY_THRESHOLD = 15
HASH_METHOD = 'phash'

# --- 分桶策略 ---
N_CLUSTERS = 8
TARGET_PIXEL_AREA = 1024 * 1024
BUCKET_STEP_SIZE = 64
TARGET_BATCH_SIZE = 8
MAX_SIMILAR_IN_BATCH = 2

# --- 图像处理 ---
OUTPUT_QUALITY = 95

# --- 智能裁切参数 ---
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

# --- V4.0 融合检测 ---
USE_EDGE_DETECTION = True
USE_SALIENCY_DETECTION = True
EDGE_WEIGHT = 0.3
SALIENCY_WEIGHT = 0.4
CONTENT_WEIGHT = 0.3
DEBUG_MODE = False


# ============ V6.0 偏好学习系统 ============ 

class PreferenceManager:
    def __init__(self, filepath):
        self.filepath = filepath
        self.history = []
        self.current_session_stats = {'padding_ratios': [], 'aspect_ratios': []}
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.history = data.get('history', [])
                    if self.history:
                        avg_padding = np.mean([h.get('avg_padding', 0.1) for h in self.history])
                        global CROP_PADDING_RATIO
                        CROP_PADDING_RATIO = (CROP_PADDING_RATIO + avg_padding) / 2
                        print(f"✓ [偏好系统] 已加载历史偏好，调整 Padding Ratio 为: {CROP_PADDING_RATIO:.3f}")
            except:
                pass

    def log_crop(self, img_w, img_h, crop_box):
        cx, cy, cw, ch = crop_box
        padding_x = (img_w - cw) / 2
        padding_y = (img_h - ch) / 2
        avg_dim = (img_w + img_h) / 2
        padding_ratio = max(0, (padding_x + padding_y) / 2 / avg_dim)
        self.current_session_stats['padding_ratios'].append(padding_ratio)
        self.current_session_stats['aspect_ratios'].append(cw / ch)

    def save(self):
        if not self.current_session_stats['padding_ratios']: return
        session_summary = {
            'timestamp': time.time(),
            'avg_padding': float(np.mean(self.current_session_stats['padding_ratios'])),
            'avg_ar': float(np.mean(self.current_session_stats['aspect_ratios'])),
            'count': len(self.current_session_stats['padding_ratios'])
        }
        self.history.append(session_summary)
        self.history = self.history[-10:]
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump({'history': self.history}, f, indent=2)
        except:
            pass


_prefs = PreferenceManager(PREFERENCE_FILE)


# ============ V6.0 核心逻辑：分桶辅助函数 ============ 

def find_best_bucket_for_box(box_w: int, box_h: int, target_resolutions: Dict[int, Tuple[int, int]]) -> Tuple[int, int]:
    current_ar = box_w / box_h
    best_res = None
    min_diff = float('inf')
    for _, (tw, th) in target_resolutions.items():
        target_ar = tw / th
        diff = abs(current_ar - target_ar)
        if diff < min_diff:
            min_diff = diff
            best_res = (tw, th)
    return best_res


def adjust_box_to_match_ar(box: Tuple[int, int, int, int], target_w: int, target_h: int, img_w: int, img_h: int) -> \
        Tuple[int, int, int, int]:
    """微调物理选框以匹配 AR，策略：优先外扩，越界则收缩"""
    x, y, w, h = box
    center_x = x + w / 2
    center_y = y + h / 2
    target_ar = target_w / target_h
    current_ar = w / h

    new_w, new_h = w, h
    if current_ar > target_ar:
        test_h = w / target_ar
        if test_h <= img_h:
            new_h, new_w = test_h, w
        else:
            if h < img_h:
                new_h = img_h
            else:
                new_h = h
            new_w = new_h * target_ar
            if new_w > img_w: new_w = img_w; new_h = new_w / target_ar
    else:
        test_w = h * target_ar
        if test_w <= img_w:
            new_w, new_h = test_w, h
        else:
            if w < img_w:
                new_w = img_w
            else:
                new_w = w
            new_h = new_w / target_ar
            if new_h > img_h: new_h = img_h; new_w = new_h * target_ar

    final_w, final_h = int(new_w), int(new_h)
    final_x = int(center_x - final_w / 2)
    final_y = int(center_y - final_h / 2)

    if final_x < 0: final_x = 0
    if final_y < 0: final_y = 0
    if final_x + final_w > img_w: final_x = img_w - final_w
    if final_y + final_h > img_h: final_y = img_h - final_h

    return (final_x, final_y, final_w, final_h)


# ============ V7.3 交互类 (自由窗口与状态记忆) ============ 

def get_screen_resolution():
    try:
        root = tk.Tk();
        root.withdraw()
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
        root.destroy();
        return w, h
    except:
        return 1920, 1080


class InteractiveCropper:
    def __init__(self):
        self.state = 'idle'
        self.box = (0, 0, 1, 1)
        self.drag_start_pos = (0, 0)
        self.drag_start_box = (0, 0, 1, 1)
        self.window_name = "Smart Crop Review V7.3"
        self.auto_mode_triggered = False
        self.GRAB_MARGIN = 15
        self.locked_ar = None

        # 动态视图参数 (由窗口大小决定)
        self.view_params = {
            'scale': 1.0,
            'off_x': 0,
            'off_y': 0,
            'win_w': 100,
            'win_h': 100
        }

        # 状态记忆
        self.first_launch = True
        self.screen_w, self.screen_h = get_screen_resolution()

        # UI Colors
        self.COLOR_BG = (30, 30, 30)
        self.COLOR_ACTIVE = (0, 255, 0)
        self.COLOR_WARNING = (0, 165, 255)
        self.COLOR_RESCUE = (255, 50, 255)

    def _get_real_pos(self, canvas_x, canvas_y):
        """将窗口坐标转换为图片真实坐标 - 保持浮点精度"""
        vp = self.view_params
        if vp['scale'] == 0: return (0.0, 0.0)
        real_x = (canvas_x - vp['off_x']) / vp['scale']
        real_y = (canvas_y - vp['off_y']) / vp['scale']
        return (real_x, real_y)

    def _get_canvas_pos(self, real_x, real_y):
        """将图片真实坐标转换为窗口坐标(绘图)"""
        vp = self.view_params
        # 正向映射: real * scale + offset
        canvas_x = int(real_x * vp['scale'] + vp['off_x'])
        canvas_y = int(real_y * vp['scale'] + vp['off_y'])
        return (canvas_x, canvas_y)

    def _get_handle_at_pos(self, real_x, real_y):
        x, y, w, h = self.box
        x2, y2 = x + w, y + h

        scale = self.view_params['scale'] if self.view_params['scale'] > 0 else 1
        margin = self.GRAB_MARGIN / scale

        # 角点
        if abs(real_x - x) < margin and abs(real_y - y) < margin: return 'resizing_tl'
        if abs(real_x - x2) < margin and abs(real_y - y) < margin: return 'resizing_tr'
        if abs(real_x - x) < margin and abs(real_y - y2) < margin: return 'resizing_bl'
        if abs(real_x - x2) < margin and abs(real_y - y2) < margin: return 'resizing_br'

        # 边中点
        if abs(real_x - x) < margin and (y + margin < real_y < y2 - margin): return 'resizing_l'
        if abs(real_x - x2) < margin and (y + margin < real_y < y2 - margin): return 'resizing_r'
        if abs(real_y - y) < margin and (x + margin < real_x < x2 - margin): return 'resizing_t'
        if abs(real_y - y2) < margin and (x + margin < real_x < x2 - margin): return 'resizing_b'

        if (x < real_x < x2) and (y < real_y < y2): return 'moving'
        return 'idle'

    def _mouse_callback(self, event, x, y, flags, param):
        if self.auto_mode_triggered: return

        img_h, img_w = param
        real_x, real_y = self._get_real_pos(x, y)

        if event == cv2.EVENT_LBUTTONDOWN:
            self.state = self._get_handle_at_pos(real_x, real_y)
            # 锁定比例时忽略边中点
            if self.locked_ar and self.state in ('resizing_l', 'resizing_r', 'resizing_t', 'resizing_b'):
                self.state = 'idle'
            if self.state != 'idle':
                self.drag_start_pos = (real_x, real_y)
                self.drag_start_box = self.box

        elif event == cv2.EVENT_MOUSEMOVE:
            if self.state == 'idle': return

            dx = real_x - self.drag_start_pos[0]
            dy = real_y - self.drag_start_pos[1]
            bx, by, bw, bh = self.drag_start_box
            new_x, new_y, new_w, new_h = bx, by, bw, bh

            if self.state == 'moving':
                new_x = bx + dx
                new_y = by + dy
            elif self.locked_ar:
                target_ratio = self.locked_ar
                change = dx if 'r' in self.state else -dx
                new_w = bw + change
                new_h = new_w / target_ratio

                if self.state == 'resizing_br':
                    new_x, new_y = bx, by
                elif self.state == 'resizing_bl':
                    new_x, new_y = bx + (bw - new_w), by
                elif self.state == 'resizing_tr':
                    new_x, new_y = bx, by + (bh - new_h)
                elif self.state == 'resizing_tl':
                    new_x, new_y = bx + (bw - new_w), by + (bh - new_h)
            else:
                if self.state == 'resizing_l':
                    new_x, new_w = bx + dx, bw - dx
                elif self.state == 'resizing_r':
                    new_w = bw + dx
                elif self.state == 'resizing_t':
                    new_y, new_h = by + dy, bh - dy
                elif self.state == 'resizing_b':
                    new_h = bh + dy
                elif self.state == 'resizing_tl':
                    new_x, new_y, new_w, new_h = bx + dx, by + dy, bw - dx, bh - dy
                elif self.state == 'resizing_tr':
                    new_y, new_w, new_h = by + dy, bw + dx, bh - dy
                elif self.state == 'resizing_bl':
                    new_x, new_w, new_h = bx + dx, bw - dx, bh + dy
                elif self.state == 'resizing_br':
                    new_w, new_h = bw + dx, bh + dy

            # 尺寸限制
            new_w = max(10, new_w)
            new_h = max(10, new_h)
            # 边界限制
            new_x = max(0, min(new_x, img_w - new_w))
            new_y = max(0, min(new_y, img_h - new_h))
            new_w = min(new_w, img_w - new_x)
            new_h = min(new_h, img_h - new_y)

            self.box = (int(new_x), int(new_y), int(new_w), int(new_h))

        elif event == cv2.EVENT_LBUTTONUP:
            self.state = 'idle'

    def _draw_hud(self, canvas, lines, color=(0, 255, 0)):
        x_start, y_start = 15, 20
        line_height = 25
        padding = 8
        max_w = 0
        for line in lines:
            (w, h), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
            max_w = max(max_w, w)
        bg_h = len(lines) * line_height + padding * 2
        bg_w = max_w + padding * 2
        overlay = canvas.copy()
        # HUD 位置固定在左上角，基于窗口坐标
        cv2.rectangle(overlay, (x_start - padding, y_start - padding - 15),
                      (x_start + bg_w, y_start + bg_h - 15), (40, 40, 40), -1)
        cv2.addWeighted(overlay, 0.7, canvas, 0.3, 0, canvas)
        for i, line in enumerate(lines):
            y = y_start + i * line_height
            cv2.putText(canvas, line, (x_start, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)

    def get_user_crop(self, pil_img, initial_box, mode='standard', current_res=None, rescue_res=None):
        if self.auto_mode_triggered or not INTERACTIVE_MODE:
            return initial_box, 'confirm'

        img_np = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        img_h, img_w = img_np.shape[:2]

        # 1. 创建可缩放窗口 (WINDOW_NORMAL)
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window_name, self._mouse_callback, (img_h, img_w))

        # 2. 仅在第一次启动时设置位置和大小
        if self.first_launch:
            # 默认逻辑：占屏幕高 85%
            init_h = int(self.screen_h * 0.85)
            scale_h = init_h / img_h
            init_w = int(img_w * scale_h)

            # 限制最大宽度
            if init_w > self.screen_w * 0.9:
                init_w = int(self.screen_w * 0.9)
                init_h = int(init_w / (img_w / img_h))

            # 居中
            win_x = max(0, int((self.screen_w - init_w) / 2))
            win_y = max(0, int((self.screen_h - init_h) / 2))

            cv2.resizeWindow(self.window_name, init_w, init_h)
            cv2.moveWindow(self.window_name, win_x, win_y)
            self.first_launch = False

            # 初始化 view_params 防止第一次循环前出错
            self.view_params['win_w'] = init_w
            self.view_params['win_h'] = init_h

        self.box = initial_box
        self.state = 'idle'

        has_rescue_option = (rescue_res is not None)
        active_submode = 'rescue' if has_rescue_option else 'keep'

        if mode == 'standard':
            self.locked_ar = None
        else:
            if active_submode == 'rescue':
                self.locked_ar = rescue_res[0] / rescue_res[1]
            else:
                self.locked_ar = current_res[0] / current_res[1]

        while True:
            # 3. 获取当前窗口大小 (用户可能拖动了窗口)
            try:
                win_rect = cv2.getWindowImageRect(self.window_name)
                if win_rect and win_rect[2] > 0 and win_rect[3] > 0:
                    # win_rect 格式通常是 (x, y, w, h)
                    win_w, win_h = win_rect[2], win_rect[3]
                else:
                    # Fallback if window not visible yet
                    win_w, win_h = self.view_params['win_w'], self.view_params['win_h']
            except:
                win_w, win_h = self.view_params['win_w'], self.view_params['win_h']

            # 4. 计算 Aspect Fit 缩放参数
            # 目标：在 win_w x win_h 的窗口里，完整放下 img_w x img_h
            scale_w = win_w / img_w
            scale_h = win_h / img_h
            scale = min(scale_w, scale_h)  # 保持比例，取较小值

            # 计算居中偏移量
            display_w = int(img_w * scale)
            display_h = int(img_h * scale)
            off_x = (win_w - display_w) // 2
            off_y = (win_h - display_h) // 2

            # 更新参数供 _mouse_callback 和 _draw 使用
            self.view_params = {
                'scale': scale, 'off_x': off_x, 'off_y': off_y,
                'win_w': win_w, 'win_h': win_h
            }

            # 5. 绘制画布
            # 创建黑色背景
            canvas = np.zeros((win_h, win_w, 3), dtype=np.uint8)
            canvas[:] = self.COLOR_BG

            # 缩放图片
            if display_w > 0 and display_h > 0:
                resized_img = cv2.resize(img_np, (display_w, display_h), interpolation=cv2.INTER_LINEAR)
                # 贴图到中心
                canvas[off_y:off_y + display_h, off_x:off_x + display_w] = resized_img

            # 6. UI 绘制 (基于 Canvas 坐标)
            if mode == 'standard':
                mode_lines = [f"MODE: STANDARD CROP"]
                help_text = "[Space] Confirm | [A] Auto All | [Esc] Skip"
                theme_color = self.COLOR_ACTIVE
            else:
                if active_submode == 'rescue':
                    mode_lines = [f"MODE: SMART RESCUE", f"Target: {rescue_res}"]
                    help_text = "[Space] SWITCH | [Tab] Keep | [Esc] DROP"
                    theme_color = self.COLOR_RESCUE
                else:
                    mode_lines = [f"MODE: OVERFLOW KEEP", f"Bucket: {current_res}"]
                    help_text = "[Space] KEEP | [Tab] Switch | [Esc] DROP"
                    theme_color = self.COLOR_WARNING

            self._draw_hud(canvas, mode_lines, theme_color)
            cv2.putText(canvas, help_text, (15, win_h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1,
                        cv2.LINE_AA)

            # 绘制裁切框
            cx, cy = self._get_canvas_pos(self.box[0], self.box[1])
            cw = int(self.box[2] * scale)
            ch = int(self.box[3] * scale)

            cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), theme_color, 2)
            res_text = f"{int(self.box[2])} x {int(self.box[3])}"
            # 确保文字不跑出上方边界
            text_y = cy - 10 if cy - 10 > 20 else cy + 20
            cv2.putText(canvas, res_text, (cx, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, theme_color, 1)

            # 绘制手柄
            # 绘制手柄 - 始终显示8个点
            corner_handles = [(cx, cy), (cx + cw, cy), (cx, cy + ch), (cx + cw, cy + ch)]
            edge_handles = [(cx + cw // 2, cy), (cx + cw // 2, cy + ch),
                            (cx, cy + ch // 2), (cx + cw, cy + ch // 2)]
            # 角点
            for hx, hy in corner_handles:
                cv2.circle(canvas, (hx, hy), 5, (255, 255, 255), -1)
                cv2.circle(canvas, (hx, hy), 3, theme_color, -1)
            # 边中点 - 锁定时灰色
            edge_color = (80, 80, 80) if self.locked_ar else (255, 255, 255)
            edge_inner = (50, 50, 50) if self.locked_ar else theme_color
            for hx, hy in edge_handles:
                cv2.circle(canvas, (hx, hy), 4, edge_color, -1)
                cv2.circle(canvas, (hx, hy), 2, edge_inner, -1)

            cv2.imshow(self.window_name, canvas)
            key = cv2.waitKey(15) & 0xFF

            if key == 32 or key == 13 or key == ord('k') or key == ord('K'):  # Confirm
                if mode == 'standard': return self.box, 'confirm'
                return self.box, ('switch' if active_submode == 'rescue' else 'keep')

            elif key == 27 or key == ord('d') or key == ord('D'):  # Drop
                return None, 'skip'

            elif key == 9:  # Tab
                if has_rescue_option:
                    active_submode = 'keep' if active_submode == 'rescue' else 'rescue'
                    target_res = rescue_res if active_submode == 'rescue' else current_res
                    self.locked_ar = target_res[0] / target_res[1]
                    self.box = adjust_box_to_match_ar(self.box, target_res[0], target_res[1], img_w, img_h)

            elif key == ord('a') or key == ord('A'):
                if mode == 'standard':
                    self.auto_mode_triggered = True
                    return self.box, 'confirm'

    def close(self):
        cv2.destroyAllWindows()


_interactive_cropper = InteractiveCropper()


# ============ 辅助功能 (RealESRGAN & Others) ============ 

class RealESRGANUpscaler:
    def __init__(self, model_name: str = "realesr-animevideov3", scale: int = 2):
        self.model_name = model_name
        self.scale = scale
        self.model = None
        self._initialize_model()

    def _initialize_model(self):
        try:
            from basicsr.archs.rrdbnet_arch import RRDBNet
            from realesrgan import RealESRGANer
            from realesrgan.archs.srvgg_arch import SRVGGNetCompact
            script_dir = Path(__file__).parent if '__file__' in globals() else Path.cwd()
            model_scale = 4
            if 'anime' in self.model_name.lower():
                if 'video' in self.model_name.lower():
                    model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=16, upscale=model_scale,
                                            act_type='prelu')
                    model_filename = 'realesr-animevideov3.pth'
                else:
                    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=6, num_grow_ch=32,
                                    scale=model_scale)
                    model_filename = 'RealESRGAN_x4plus_anime_6B.pth'
            else:
                model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=model_scale)
                model_filename = 'RealESRGAN_x4plus.pth'
            possible_paths = [script_dir / model_filename, Path.cwd() / model_filename,
                              Path.home() / '.cache' / 'realesrgan' / model_filename]
            model_path = next((str(p) for p in possible_paths if p.exists()), None)
            if model_path is None: self.model = None; return
            gpu_id = 0 if self._has_cuda() else None
            self.model = RealESRGANer(scale=model_scale, model_path=model_path, model=model, tile=256, tile_pad=10,
                                      pre_pad=0, half=self._has_cuda(), gpu_id=gpu_id)
        except:
            self.model = None

    def _has_cuda(self) -> bool:
        try:
            import torch;
            return torch.cuda.is_available()
        except:
            return False

    def upscale(self, img: Image.Image) -> Image.Image:
        if self.model is None: return self._fallback_upscale(img)
        try:
            img_np = np.array(img)
            output, _ = self.model.enhance(img_np, outscale=self.scale)
            return Image.fromarray(output)
        except:
            return self._fallback_upscale(img)

    def _fallback_upscale(self, img: Image.Image) -> Image.Image:
        new_size = (img.width * self.scale, img.height * self.scale)
        return img.resize(new_size, Image.Resampling.LANCZOS)


_upscaler: Optional[RealESRGANUpscaler] = None


def get_upscaler() -> RealESRGANUpscaler:
    global _upscaler
    if _upscaler is None: _upscaler = RealESRGANUpscaler(UPSCALE_MODEL, UPSCALE_FACTOR)
    return _upscaler


class UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, x):
        if self.parent[x] != x: self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        rootX, rootY = self.find(x), self.find(y)
        if rootX != rootY: self.parent[rootX] = rootY


def get_adaptive_padding(img_width: int, img_height: int) -> int:
    avg_dim = (img_width + img_height) / 2
    return max(MIN_CROP_PADDING, min(MAX_CROP_PADDING, int(avg_dim * CROP_PADDING_RATIO)))


# ... (V4 Detection Functions preserved as is) ...
def get_edge_map(img: Image.Image) -> np.ndarray:
    if img.mode != 'RGB': img = img.convert('RGB')
    img_np = np.array(img)
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)
    return edges


def get_saliency_map(img: Image.Image) -> np.ndarray:
    if img.mode != 'RGB': img = img.convert('RGB')
    img_np = np.array(img)
    try:
        saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
        success, saliency_map = saliency.computeSaliency(img_np)
        if success:
            saliency_map = (saliency_map * 255).astype(np.uint8)
            _, saliency_map = cv2.threshold(saliency_map, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return saliency_map
    except:
        pass
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    _, saliency_map = cv2.threshold(np.uint8(np.absolute(lap)), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return saliency_map


def get_all_subjects_v4(img: Image.Image, threshold: int, kernel_size: int, iterations: int) -> List[Dict]:
    if img.mode != 'RGB': img = img.convert('RGB')
    open_cv_image = np.array(img)[:, :, ::-1]
    gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)
    corner_pixels = [gray[0, 0], gray[0, -1], gray[-1, 0], gray[-1, -1]]
    is_light_bg = np.mean(corner_pixels) > 128
    thresh_type = cv2.THRESH_BINARY_INV if is_light_bg else cv2.THRESH_BINARY
    _, thresh1 = cv2.threshold(gray, threshold if is_light_bg else (255 - threshold), 255, thresh_type)
    thresh2 = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, thresh_type, 21, 10)
    edge_contribution = get_edge_map(img) if USE_EDGE_DETECTION else np.zeros_like(gray)
    saliency_contribution = get_saliency_map(img) if USE_SALIENCY_DETECTION else np.zeros_like(gray)
    thresh_combined = (CONTENT_WEIGHT * cv2.bitwise_or(thresh1,
                                                       thresh2) + EDGE_WEIGHT * edge_contribution + SALIENCY_WEIGHT * saliency_contribution).astype(
        np.uint8)
    thresh_combined = cv2.normalize(thresh_combined, None, 0, 255, cv2.NORM_MINMAX)
    _, thresh_combined = cv2.threshold(thresh_combined, 127, 255, cv2.THRESH_BINARY)
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    closed_thresh = cv2.morphologyEx(cv2.morphologyEx(thresh_combined, cv2.MORPH_OPEN, kernel, iterations=iterations),
                                     cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(closed_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours: return []
    subjects = []
    img_area = img.width * img.height
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < img_area * MIN_SUBJECT_AREA_RATIO: continue
        subjects.append({'x': x, 'y': y, 'w': w, 'h': h, 'area': area, 'center_x': x + w / 2, 'center_y': y + h / 2,
                         'aspect_ratio': w / h})
    subjects.sort(key=lambda s: s['area'], reverse=True)
    return subjects


def analyze_scene_type_v4(subjects: List[Dict], img_width: int, img_height: int) -> Dict:
    if not subjects: return {'type': 'empty', 'focus_box': (0, 0, img_width, img_height), 'strategy': 'preserve_all',
                             'confidence': 1.0, 'vertical_focus': 'center'}
    total_weighted_y = sum(s['center_y'] * s['area'] for s in subjects)
    total_area = sum(s['area'] for s in subjects)
    normalized_com_y = (total_weighted_y / total_area) / img_height if total_area > 0 else 0.5
    vertical_focus = 'bottom' if normalized_com_y > VERTICAL_FOCUS_THRESHOLD else (
        'top' if normalized_com_y < (1 - VERTICAL_FOCUS_THRESHOLD) else 'center')
    if len(subjects) == 1:
        s = subjects[0]
        return {'type': 'single', 'focus_box': (s['x'], s['y'], s['w'], s['h']), 'strategy': 'preserve_top',
                'confidence': 1.0, 'subject_count': 1, 'vertical_focus': vertical_focus}
    min_x, min_y = min(s['x'] for s in subjects), min(s['y'] for s in subjects)
    max_x, max_y = max(s['x'] + s['w'] for s in subjects), max(s['y'] + s['h'] for s in subjects)
    union_box_area = (max_x - min_x) * (max_y - min_y)
    total_subject_area = sum(s['area'] for s in subjects)
    avg_dist = 0
    if len(subjects) > 1:
        dists = []
        for i in range(len(subjects)):
            for j in range(i + 1, len(subjects)):
                dists.append(math.hypot(subjects[i]['center_x'] - subjects[j]['center_x'],
                                        subjects[i]['center_y'] - subjects[j]['center_y']))
        if dists: avg_dist = np.mean(dists)
    avg_subject_size = math.sqrt(total_subject_area / len(subjects)) if subjects else 0
    density_ratio = total_subject_area / union_box_area if union_box_area > 0 else 0
    spacing_ratio = avg_dist / avg_subject_size if avg_subject_size > 0 else 0
    if density_ratio > 0.3 and spacing_ratio < COMPACT_THRESHOLD:
        return {'type': 'compact_group', 'focus_box': (min_x, min_y, max_x - min_x, max_y - min_y),
                'strategy': 'preserve_all', 'confidence': density_ratio, 'vertical_focus': vertical_focus}
    else:
        largest = subjects[0]
        return {'type': 'scattered', 'focus_box': (largest['x'], largest['y'], largest['w'], largest['h']),
                'strategy': 'focus_largest', 'confidence': largest['area'] / total_subject_area,
                'vertical_focus': vertical_focus}


def calculate_auto_crop_box(img: Image.Image, target_aspect_ratio: float) -> Tuple[int, int, int, int]:
    img_w, img_h = img.size
    padding = get_adaptive_padding(img_w, img_h)
    subjects = get_all_subjects_v4(img, CROP_THRESHOLD, MORPH_KERNEL_SIZE, MORPH_ITERATIONS)
    scene_analysis = analyze_scene_type_v4(subjects, img_w, img_h)
    fx, fy, fw, fh = scene_analysis['focus_box']
    fx = max(0, fx - padding)
    fy = max(0, fy - padding)
    fw = min(img_w - fx, fw + 2 * padding)
    fh = min(img_h - fy, fh + 2 * padding)
    focus_ar = fw / fh
    if abs(focus_ar - target_aspect_ratio) < 0.1: return (fx, fy, fw, fh)
    strategy = scene_analysis['strategy']
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


# ============ V7.0 逻辑：执行处理 (PNG透明修正) ============ 

def execute_processing_plan(plan_items, output_dir):
    """执行最终的处理计划，统一转换为PNG，处理透明背景"""
    digits = len(str(len(plan_items)))

    for i, item in tqdm(enumerate(plan_items), total=len(plan_items), desc="最终处理"):
        try:
            img_data_obj = item['data']
            img_path = img_data_obj['filepath']

            target_w, target_h = item['target_res']
            box = item['crop_box']

            with Image.open(img_path) as img:
                # 1. 透明背景处理 (Convert RGBA/P to RGB with White BG)
                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    img = img.convert('RGBA')
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                else:
                    img = img.convert('RGB')

                # 2. 裁切
                cx, cy, cw, ch = box
                cropped_img = img.crop((cx, cy, cx + cw, cy + ch))

                # 3. 放大
                processed_img = cropped_img
                if ENABLE_UPSCALING and (cw < target_w or ch < target_h):
                    upscaler = get_upscaler()
                    processed_img = upscaler.upscale(cropped_img)

                # 4. 缩放
                if processed_img.size != (target_w, target_h):
                    final_img = processed_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                else:
                    final_img = processed_img

                # 5. 记录偏好
                _prefs.log_crop(img.width, img.height, box)

                # 6. 保存 (强制 PNG)
                new_base_name = str(i + 1).zfill(digits)
                # Save as PNG
                final_img.save(output_dir / f"{new_base_name}.png", quality=OUTPUT_QUALITY)

                # Copy tags if exist
                src_txt = img_path.with_suffix(".txt")
                if src_txt.exists():
                    shutil.copy2(src_txt, output_dir / f"{new_base_name}.txt")

        except Exception as e:
            print(f"处理出错 {img_path.name}: {e}")


def round_to_step(value, step): return round(value / step) * step


# ============ V7.0 辅助：分桶搜索 ============ 

def find_bucket_needing_one_item(buckets, current_ar, batch_size):
    """寻找一个还差1张图片就能填满Batch的桶。"""
    candidates = []
    for res, items in buckets.items():
        current_count = len(items)
        if (current_count + 1) % batch_size == 0:
            target_ar = res[0] / res[1]
            diff = abs(target_ar - current_ar)
            candidates.append((diff, res))
    if not candidates: return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


# ============ V7.4 新增：API 接口逻辑 ============ 

def analyze_images_similarity(image_folder, threshold=SIMILARITY_THRESHOLD):
    """
    分析文件夹中的图片相似度并返回分组信息
    :param image_folder: 图片文件夹路径
    :param threshold: 相似度阈值
    :return: list of groups, each group is a list of dicts {'filepath': str, 'hash': imagehash}
    """
    folder = Path(image_folder)
    if not folder.is_dir():
        return []

    # 1. 加载并计算哈希
    all_files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ('.jpg', '.png', '.webp', '.jpeg')]
    img_data = []
    
    # 简单的进度打印，因为这通常在 API 中调用
    print(f"Analyzing {len(all_files)} images for similarity...")
    
    for idx, fp in enumerate(all_files):
        try:
            with Image.open(fp) as img:
                # 统一转为 RGB 计算哈希，防止透明图带来的干扰
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                img_hash = imagehash.phash(img)
                img_data.append({
                    'index': idx,
                    'filepath': str(fp),
                    'filename': fp.name,
                    'hash': img_hash,
                    'orig_w': img.width, 
                    'orig_h': img.height,
                    'aspect_ratio': img.width / img.height
                })
        except Exception as e:
            print(f"Skipping {fp.name}: {e}")

    if not img_data:
        return []

    # 2. 并查集聚类
    uf = UnionFind(len(img_data))
    for i in range(len(img_data)):
        for j in range(i + 1, len(img_data)):
            if (img_data[i]['hash'] - img_data[j]['hash']) <= threshold:
                uf.union(i, j)

    # 3. 整理结果
    groups = defaultdict(list)
    for i, item in enumerate(img_data):
        root_id = uf.find(i)
        groups[root_id].append(item)

    # 4. 格式化输出 (只返回有多于1张图片的组，或者全部返回由调用者决定)
    # 这里我们按组大小降序排列
    sorted_groups = []
    for root_id, items in groups.items():
        if len(items) > 1: # 只关心相似组
             sorted_groups.append(items)
             
    # 按组内数量排序
    sorted_groups.sort(key=lambda x: len(x), reverse=True)
    
    return sorted_groups, img_data, uf


# ============ 主程序 ============ 

if __name__ == "__main__":
    print("=" * 70)
    print("智能混合裁切处理器 V7.3 - 自由窗口交互版")
    print("=" * 70)

    if not Path(IMAGE_FOLDER).is_dir(): exit(f"错误: {IMAGE_FOLDER}")
    Path(OUTPUT_FOLDER).mkdir(parents=True, exist_ok=True)
    if ENABLE_UPSCALING: get_upscaler()

    # --- 阶段一: 分析与初步分桶 ---
    print("\n[阶段一] 分析图片...")
    
    # 使用提取的函数进行分析
    # 注意：为了保持原有逻辑的变量名一致性，我们这里稍微适配一下
    # 原逻辑需要 img_data 包含 aspect_ratio 用于 KMeans
    
    _, img_data, uf = analyze_images_similarity(IMAGE_FOLDER, SIMILARITY_THRESHOLD)
    
    if not img_data: exit("无图片")

    # 原有逻辑继续...
    # 重构 KMeans 部分，因为 img_data 现在由函数返回
    ars = np.array([d['aspect_ratio'] for d in img_data]).reshape(-1, 1)
    kmeans = KMeans(n_clusters=min(N_CLUSTERS, len(img_data)), n_init='auto').fit(ars)

    target_resolutions = {}
    for i, center_ar in enumerate(kmeans.cluster_centers_.flatten()):
        h = math.sqrt(TARGET_PIXEL_AREA / center_ar)
        w = h * center_ar
        target_resolutions[i] = (round_to_step(w, BUCKET_STEP_SIZE), round_to_step(h, BUCKET_STEP_SIZE))

    # --- 阶段二: 初始自由交互 ---
    print("\n[阶段二] 自由裁切交互 (第一遍)...")
    pre_bucket_data = []

    for idx, item in enumerate(img_data):
        fp = item['filepath']
        best_res_id = min(target_resolutions, key=lambda k: abs(
            item['aspect_ratio'] - (target_resolutions[k][0] / target_resolutions[k][1])))
        default_res = target_resolutions[best_res_id]

        with Image.open(fp) as img:
            img = img.convert('RGB')
            # calculate_auto_crop_box 需要 PIL Image
            auto_box = calculate_auto_crop_box(img, default_res[0] / default_res[1])

            if INTERACTIVE_MODE and not _interactive_cropper.auto_mode_triggered:
                user_box, action = _interactive_cropper.get_user_crop(img, auto_box)
                if action == 'skip': continue
            else:
                user_box = auto_box

            user_w, user_h = user_box[2], user_box[3]
            best_bucket_res = find_best_bucket_for_box(user_w, user_h, target_resolutions)
            if not best_bucket_res: best_bucket_res = default_res

            snapped_box = adjust_box_to_match_ar(user_box, best_bucket_res[0], best_bucket_res[1], item['orig_w'],
                                                 item['orig_h'])

            # 这里 uf.find(idx) 依赖于 img_data 的顺序，analyze_images_similarity 保证了顺序
            # img_data 是列表，idx 是索引，uf 是基于索引的
            pre_bucket_data.append({
                'data': item,
                'crop_box': snapped_box,
                'target_res': best_bucket_res,
                'group_id': uf.find(idx) # 使用 UF 查找 Group ID
            })

    # --- 阶段三: 智能分桶平衡 (V7.1 三选一交互逻辑) ---
    print("\n[阶段三] 智能分桶平衡 (Smart Balancing)...")

    buckets = defaultdict(list)
    for item in pre_bucket_data:
        buckets[item['target_res']].append(item)

    final_plan = []
    sorted_bucket_keys = sorted(buckets.keys(), key=lambda x: x[0] * x[1])

    for res in sorted_bucket_keys:
        items = buckets[res]
        count = len(items)
        remainder = count % TARGET_BATCH_SIZE

        print(f"  桶 {res}: {count} 张 (余数: {remainder})")

        if remainder == 0:
            final_plan.extend(items)
            continue

        group_counts = Counter([x['group_id'] for x in items])
        items.sort(key=lambda x: (group_counts[x['group_id']] > 1, x['crop_box'][2] * x['crop_box'][3]), reverse=False)

        candidates_to_drop = items[:remainder]
        keep_items = items[remainder:]
        final_plan.extend(keep_items)

        for cand in candidates_to_drop:
            if group_counts[cand['group_id']] > 1:
                print(f"    - [自动丢弃] 重复图片: {cand['data']['filepath'].name}")
                continue

            rescued = False
            if INTERACTIVE_MODE:
                with Image.open(cand['data']['filepath']) as img:
                    start_box = cand['crop_box']
                    cand_ar = start_box[2] / start_box[3]

                    # 1. 搜索 "完美填坑" 目标
                    target_rescue_res = find_bucket_needing_one_item(buckets, cand_ar, TARGET_BATCH_SIZE)

                    # 初始自动吸附: 如果有填坑目标，优先吸附到填坑目标；否则吸附到原桶(Overflow)
                    if target_rescue_res:
                        init_snap_target = target_rescue_res
                    else:
                        init_snap_target = res

                    auto_snapped = adjust_box_to_match_ar(
                        start_box, init_snap_target[0], init_snap_target[1],
                        cand['data']['orig_w'], cand['data']['orig_h']
                    )

                    # 2. 唤起三合一界面
                    rescue_box, action = _interactive_cropper.get_user_crop(
                        img,
                        auto_snapped,
                        mode='rescue',
                        current_res=res,  # Option B: Stay Here
                        rescue_res=target_rescue_res  # Option A: Switch (Optional)
                    )

                    if action == 'switch' and target_rescue_res:
                        cand['crop_box'] = rescue_box
                        cand['target_res'] = target_rescue_res
                        buckets[target_rescue_res].append(cand)
                        print(f"    + [完美填坑] 移动到桶 {target_rescue_res}: {cand['data']['filepath'].name}")
                        rescued = True

                    elif action == 'keep':
                        cand['crop_box'] = rescue_box
                        final_plan.append(cand)  # Force Keep
                        print(f"    + [强制保留] 溢出保留: {cand['data']['filepath'].name}")
                        rescued = True

            if not rescued:
                print(f"    x [最终丢弃] {cand['data']['filepath'].name}")

    # --- 阶段四: 执行与记录 ---
    print(f"\n[阶段四] 最终处理 ({len(final_plan)} 张)...")
    execute_processing_plan(final_plan, Path(OUTPUT_FOLDER))

    _prefs.save()
    _interactive_cropper.close()
    print("\n✅ 全部完成!")