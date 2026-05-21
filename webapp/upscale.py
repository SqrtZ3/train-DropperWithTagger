# webapp/upscale.py — RealESRGAN 上采样
#
# 仅在裁切区域比目标桶尺寸小时触发（cw < tgt_w or ch < tgt_h），避免有效像素损失。
# 权重文件按 UPSCALE_MODEL 关键字自动选模型；找不到权重就回退到 PIL Lanczos。
#
# 关闭超分：把 ENABLE_UPSCALING 改成 False，或者把 .pth 文件移走。
#
# 抽自 drop.py — drop.py 不再被 server.py 当作模块导入。

from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


# ============== 可调常量 ==============
# 主开关：False = 永远不做超分（即使裁切区比目标桶小也直接 LANCZOS 下采样）
ENABLE_UPSCALING: bool = True

# 模型选择，决定网络结构和权重文件名：
#   含 "anime" + "video" → realesr-animevideov3.pth        (SRVGGNetCompact)
#   含 "anime"            → RealESRGAN_x4plus_anime_6B.pth (RRDBNet 6 block)
#   其他                  → RealESRGAN_x4plus.pth          (RRDBNet 23 block)
UPSCALE_MODEL: str = "RealESRGAN_x4plus_anime_6B"

# 最终输出相对原图的放大倍率（模型本身始终 4x，这里是 outscale 参数）
UPSCALE_FACTOR: int = 2


# ============== 实现 ==============
class RealESRGANUpscaler:
    def __init__(self, model_name: str = UPSCALE_MODEL, scale: int = UPSCALE_FACTOR):
        self.model_name = model_name
        self.scale = scale
        self.model = None
        self._initialize_model()

    def _initialize_model(self):
        try:
            from basicsr.archs.rrdbnet_arch import RRDBNet
            from realesrgan import RealESRGANer
            from realesrgan.archs.srvgg_arch import SRVGGNetCompact

            # 权重文件搜索路径：项目根目录 → 当前工作目录 → ~/.cache/realesrgan/
            script_dir = Path(__file__).parent.parent  # webapp/ 的上一层 = 项目根
            model_scale = 4
            if 'anime' in self.model_name.lower():
                if 'video' in self.model_name.lower():
                    model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64,
                                            num_conv=16, upscale=model_scale, act_type='prelu')
                    model_filename = 'realesr-animevideov3.pth'
                else:
                    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                                    num_block=6, num_grow_ch=32, scale=model_scale)
                    model_filename = 'RealESRGAN_x4plus_anime_6B.pth'
            else:
                model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                                num_block=23, num_grow_ch=32, scale=model_scale)
                model_filename = 'RealESRGAN_x4plus.pth'

            possible_paths = [
                script_dir / model_filename,
                Path.cwd() / model_filename,
                Path.home() / '.cache' / 'realesrgan' / model_filename,
            ]
            model_path = next((str(p) for p in possible_paths if p.exists()), None)
            if model_path is None:
                # 没找到权重 → 上层 upscale() 会自动走 LANCZOS 回退
                self.model = None
                return

            gpu_id = 0 if self._has_cuda() else None
            self.model = RealESRGANer(
                scale=model_scale, model_path=model_path, model=model,
                tile=256, tile_pad=10, pre_pad=0,
                half=self._has_cuda(), gpu_id=gpu_id,
            )
        except Exception:
            # 缺 torch / basicsr / realesrgan / 权重 → 全部回退到 LANCZOS
            self.model = None

    def _has_cuda(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def upscale(self, img: Image.Image) -> Image.Image:
        if self.model is None:
            return self._fallback_upscale(img)
        try:
            img_np = np.array(img)
            output, _ = self.model.enhance(img_np, outscale=self.scale)
            return Image.fromarray(output)
        except Exception:
            return self._fallback_upscale(img)

    def _fallback_upscale(self, img: Image.Image) -> Image.Image:
        new_size = (img.width * self.scale, img.height * self.scale)
        return img.resize(new_size, Image.Resampling.LANCZOS)


_upscaler: Optional[RealESRGANUpscaler] = None


def get_upscaler() -> RealESRGANUpscaler:
    """单例 lazy init。第一次拿的时候才真正去加载权重。"""
    global _upscaler
    if _upscaler is None:
        _upscaler = RealESRGANUpscaler(UPSCALE_MODEL, UPSCALE_FACTOR)
    return _upscaler


def maybe_upscale(cropped: Image.Image, target_w: int, target_h: int,
                  filename_hint: str = "") -> Image.Image:
    """裁切区比目标桶小时调用 RealESRGAN，否则原样返回。失败容忍。"""
    if not ENABLE_UPSCALING:
        return cropped
    if cropped.width >= target_w and cropped.height >= target_h:
        return cropped
    try:
        return get_upscaler().upscale(cropped)
    except Exception as e:
        print(f"⚠️ 超分失败 {filename_hint}: {e}")
        return cropped
