# legacy/

旧的命令行/tk-GUI 工具。**WebUI 已经不再依赖这些文件**。

## drop.py
2025-01 前的智能裁切单文件版（`python drop.py` 跑 tk 交互窗口）。WebUI 用到的两个核心能力已经抽到 `webapp/` 包：

| 旧位置 | 新位置 |
| --- | --- |
| `drop.calculate_auto_crop_box` 和它的所有 helper（v4 边缘/显著性融合） | [`webapp/auto_crop.py`](../webapp/auto_crop.py) |
| `drop.RealESRGANUpscaler / get_upscaler / ENABLE_UPSCALING / UPSCALE_MODEL / UPSCALE_FACTOR` | [`webapp/upscale.py`](../webapp/upscale.py) |

如果还想跑旧的 `python drop.py` 脚本，注意几点：
- 里面把图片路径硬编码在文件顶部（`IMAGE_FOLDER` / `OUTPUT_FOLDER`）；
- 需要 `tkinter`、`sklearn`、`tqdm`，以及 `cv2` 的 `cv2.saliency` 模块（部分 `opencv-python-headless` 不带）；
- 改了常量后两边可能漂移，新代码以 `webapp/auto_crop.py` + `webapp/upscale.py` 为准。

## get_pictures.py
2025-01 前的视频抽帧脚本（`python get_pictures.py` 批量处理一个源目录下所有视频/zip）。
WebUI 两步式抽帧（probe + save）已经抽到：

| 旧位置 | 新位置 |
| --- | --- |
| `get_pictures.probe_video_keyframes / save_selected_keyframes / is_video_path / SUPPORTED_VIDEO_EXTENSIONS` | [`webapp/video.py`](../webapp/video.py) |

旧的「一键抽帧」（`extract_frames_from_video` + 路由 `/api/extract_video`）已不再保留 ——
前端 `screens/video.jsx` 走的是 probe + save 两步式流程，旧路由没人调用。需要的话从 git 历史里捞回来。

如果还想跑旧的 `python get_pictures.py` 脚本：
- `source_dir` / `target_dir` 硬编码在顶部；
- 需要 `cv2 + tqdm + imagehash + PIL + numpy`。
