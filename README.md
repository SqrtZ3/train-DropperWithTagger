# Drop Studio · 训练数据管线 WebUI

本地运行的一站式 LoRA 训练数据预处理工具。一个 FastAPI 服务 + 一个 React 单页 WebUI，包含 6 个工作屏：

| 工作屏 | 用途 |
| --- | --- |
| **主页 / Home** | 选择输入目录、输出目录、目标分桶分辨率；从最近会话直接续作 |
| **裁切 / Crop** | 逐张精修：自动裁切建议 + 多框 + 比例锁 + 键盘快捷键，支持断点续作 |
| **批处理 / Export** | 「一键全自动」（最近邻分桶 + 居中最小裁切）或「队列导出」（导出 Crop 屏加入的项） |
| **查重 / Sim** | pHash + dHash + 颜色直方图三层校验 + 严格 clique 聚类 |
| **抽帧 / Frames** | 视频两步式关键帧选取：先 probe 候选，再勾选落盘 |
| **反推 / Tagger** | ONNX 标签反推：WD v3 / Camie v2 / DeepDanbooru / 本地自定义；支持「写完一张自动打标」联动 |

服务监听 `0.0.0.0:6008`，启动时打印局域网 IP，手机走同 Wi-Fi 也能直接打开整套 WebUI。

---

## 1 · 安装

需要 Python 3.10+。

```powershell
# 1) 建议用 venv / uv 隔离
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 基础依赖
pip install fastapi uvicorn python-multipart pillow imagehash opencv-python numpy

# 3) 可选依赖
#    超分裁切（drop.py 用 RealESRGAN）
pip install torch torchvision realesrgan basicsr

#    反推标签（ONNX 推理 + HuggingFace 下载）
pip install onnxruntime-gpu huggingface_hub
#    或纯 CPU：pip install onnxruntime
#    DeepDanbooru（可选）：pip install tensorflow deepdanbooru
```

服务启动时如果检测到 `fastapi/uvicorn/python-multipart/pillow/imagehash` 缺失，会尝试用当前解释器自动 `pip install`；PEP 668 环境下会失败，请先建 venv。

`drop.py`（超分裁切）和 `get_pictures.py`（视频抽帧）作为可选业务模块加载：导入失败只是禁用对应功能，不阻塞 server 启动。

---

## 2 · 启动

```powershell
python server.py
# 或自定义端口 / 主机
python server.py --port 6008 --host 0.0.0.0
```

启动后访问 `http://localhost:6008`。控制台会打印局域网 IP，手机连同一 Wi-Fi 可直接访问。

---

## 3 · 模型放置

### 3.1 反推标签模型（Tagger）

模型根目录：**`tagger/models/`**（已被 `.gitignore` 忽略）。Drop Studio 会按下面四种约定扫这个目录，并把识别到的模型显示在反推屏的左侧 sidebar。

#### 方式 A · HuggingFace 自动下载（最省事）

在反推屏 sidebar 选一个 `hf:...` 模型 → 点「加载」按钮 → 后端首次会自动下载到本地，之后直接复用。

预定义的 HF 模型（见 [`tagger/utils.py:PREDEFINED_HF_MODELS`](tagger/utils.py)）：

| 模型 id | 大小 | 备注 |
| --- | --- | --- |
| `hf:Camais03/camie-tagger-v2` | ~788 MB | **2026-01 推荐**，70k 标签 |
| `hf:deepghs/pixai-tagger-v0.9-onnx` | ~520 MB | 2025-01，13k 标签 |
| `hf:SmilingWolf/wd-eva02-large-tagger-v3` | ~430 MB | WD v3（legacy） |
| `hf:SmilingWolf/wd-vit-large-tagger-v3` | ~430 MB | WD v3 |
| `hf:SmilingWolf/wd-vit-tagger-v3` | ~280 MB | WD v3 轻量 |
| `hf:SmilingWolf/wd-swinv2-tagger-v3` | ~285 MB | WD v3 |
| `hf:SmilingWolf/wd-convnext-tagger-v3` | ~330 MB | WD v3 |

下载完成后会在 `tagger/models/<owner>_<repo>/` 下生成：

```
tagger/models/Camais03_camie-tagger-v2/
├── model.onnx                  # 后端会把远端文件名规范化为这两个名字
└── metadata.json
```

国内访问 HuggingFace 不通时，**启动 server 之前**设置镜像环境变量：

```powershell
# PowerShell
$env:HF_ENDPOINT = 'https://hf-mirror.com'
python server.py

# CMD
set HF_ENDPOINT=https://hf-mirror.com && python server.py
```

#### 方式 B · 本地 ONNX 模型（推荐 · 完全离线）

自己下载 `model.onnx` + 标签映射文件，放到 `tagger/models/<任意文件夹名>/` 下，sidebar 会自动列为 `local:<文件夹名>`。

```
tagger/models/cl_tagger_1_01/
├── model.onnx                  # 或 model_optimized.onnx，或任意 *.onnx（取第一个）
└── tag_mapping.json            # 或 selected_tags.csv / tags.json / tags.csv

tagger/models/Camais03_camie-tagger-v2/    # 也可以这种形式手动放，效果等同于 hf: 下载完
├── model.onnx
└── metadata.json               # 有 metadata.json 后端会按 Camie 推理路径走（sigmoid + ImageNet 归一化）
```

标签映射文件格式（任选其一即可，后端用 [`_read_tag_list`](tagger/interrogator.py) 自动识别）：

- JSON 列表：`["1girl", "solo", ...]` 或 `[{"name": "1girl", "category": "general"}, ...]`
- JSON 字典：`{"0": "1girl", "1": "solo", ...}` 或 `{"0": {"name": "1girl", "category": "general"}, ...}`
- CSV 带表头：`name,category` 列（WD v3 的 `selected_tags.csv` 即是）
- CSV 无表头：单列纯标签名

#### 方式 C · DeepDanbooru 项目

整个 DeepDanbooru 项目目录（含 `project.json`）放到 `tagger/models/deepdanbooru/<name>/`，sidebar 会列为 `deepdanbooru:<name>`。需要安装 `tensorflow + deepdanbooru` 才能用。

#### 方式 D · 自定义任意绝对路径

不想放到 `tagger/models/` 下也可以，通过 API 把 model id 设为 `custom:<绝对路径>`，文件夹结构同方式 B。

#### 模型 id 速查

| 形式 | 含义 |
| --- | --- |
| `hf:<owner>/<repo>` | HuggingFace 自动下载到 `tagger/models/<owner>_<repo>/` |
| `local:<folder>` | `tagger/models/<folder>/` |
| `onnx:<folder>` | legacy：`tagger/models/onnx/<folder>/` |
| `deepdanbooru:<folder>` | `tagger/models/deepdanbooru/<folder>/`，含 `project.json` |
| `custom:<abs_path>` | 任意绝对路径 |

### 3.2 RealESRGAN 超分模型（裁切时自动上采样）

实现在 [`webapp/upscale.py`](webapp/upscale.py)。

**触发时机**：仅当裁切区域比目标桶尺寸**小**（`cropped.w < target_w 或 cropped.h < target_h`）才走 RealESRGAN，否则直接 LANCZOS 下采样到目标尺寸 —— 避免对足够大的源图做无意义的"先放大再缩小"。

**权重文件放置**：在 [`webapp/upscale.py`](webapp/upscale.py) 顶部三个常量决定走哪个分支：

| `UPSCALE_MODEL` 包含 | 权重文件名 | 用途 |
| --- | --- | --- |
| `anime` + `video` | `realesr-animevideov3.pth`（约 2.4 MB） | 动画 / 二次元视频帧 |
| `anime`（不含 video） | `RealESRGAN_x4plus_anime_6B.pth`（约 18 MB） | **默认**，动画 / 二次元静帧 |
| 其他 | `RealESRGAN_x4plus.pth`（约 64 MB） | 真实照片 |

权重文件按顺序搜索这几个位置（任一存在即可）：

```
<项目根>/RealESRGAN_x4plus_anime_6B.pth        ← 推荐放这里
<当前 cwd>/RealESRGAN_x4plus_anime_6B.pth
~/.cache/realesrgan/RealESRGAN_x4plus_anime_6B.pth
```

**下载地址**（任选其一）：
- 官方 release：https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0
- HuggingFace：搜 `RealESRGAN_x4plus_anime_6B`
- 国内镜像：`https://ghproxy.com/<release URL>`

**关掉超分的三种方式**（任选）：
1. 改 [`webapp/upscale.py`](webapp/upscale.py) 顶部 `ENABLE_UPSCALING = False`，永远不调 RealESRGAN。
2. 不放权重文件 → `_initialize_model()` 找不到 .pth 时 `self.model = None`，后续 `upscale()` 调用自动回退到 PIL LANCZOS。
3. 不装 `torch + realesrgan + basicsr` → import 阶段就 catch 掉，全程走 LANCZOS。

**`UPSCALE_FACTOR`**：实际输出相对原图的放大倍率，默认 `2`。注意 RealESRGAN 模型本身始终是 4× 训练的，这里改 `UPSCALE_FACTOR` 改的是 `outscale` 参数（最终输出尺寸由 `RealESRGANer.enhance` 二次缩放给出）。

**切换模型**：想换 `realesr-animevideov3` 或 `RealESRGAN_x4plus`：

```python
# webapp/upscale.py 顶部
UPSCALE_MODEL = "realesr-animevideov3"   # 或 "RealESRGAN_x4plus"
```

把对应的 `.pth` 权重也放到项目根目录即可。

**装坑提醒**：
- `realesrgan + basicsr` 拉的 `torchvision.transforms.functional_tensor` 在新版 torchvision 里被移除了。如果 `pip install realesrgan basicsr` 后 import 报这个错，手动编辑 `basicsr/data/degradations.py` 把 `from torchvision.transforms.functional_tensor import rgb_to_grayscale` 改成 `from torchvision.transforms.functional import rgb_to_grayscale`。原始 drop.py 顶部有兼容补丁，但 webapp/upscale.py 不再依赖那个补丁；遇到问题直接改 basicsr 源即可。

---

## 4 · 典型工作流

### 4.1 主流程：原始素材 → 清洗 → 分桶导出 → 反推标签

```
            ┌───────────────────────────────────────────┐
            │   1. 主页 · 选输入/输出目录 + 目标 MP/步长 │
            └───────────────────────────────────────────┘
                              │
                              ▼
            ┌───────────────────────────────────────────┐
            │   2. 查重 (Sim) · 删/忽略重复的源图        │
            └───────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
  ┌───────────────────┐         ┌────────────────────────────┐
  │ 3a. 裁切 (Crop)    │         │ 3b. 批处理 · 一键全自动     │
  │     逐张精修        │         │     居中最小裁切，跳过精修  │
  │     ⇒ 加入队列      │         └────────────────────────────┘
  └───────────────────┘                       │
              │                               │
              ▼                               │
  ┌───────────────────────────┐               │
  │ 3a'. 批处理 · 导出队列     │               │
  └───────────────────────────┘               │
              │                               │
              └───────────────┬───────────────┘
                              ▼
            ┌───────────────────────────────────────────┐
            │   4. 反推 (Tagger) · 给输出 PNG 打 .txt    │
            │       可在 Crop 屏开「自动联动」一边写一边打 │
            └───────────────────────────────────────────┘
```

### 4.2 视频素材

```
抽帧 (Frames) → 选关键帧落盘 → 把输出目录作为下一轮 Drop Studio 的输入
```

### 4.3 详细步骤

**Step 1 · 初始化会话（主页屏）**

- 输入目录支持 子目录递归 + `.zip` 压缩包内图（zip:// URI 直读，无需解压）。
- 输出目录留空 → 默认 `<输入>/output/`。
- 目标分桶 1MP（1024² 等效）/ 64 px 步长是默认；新底模可以放到 1.25–2 MP，老 SD 1.5 用 0.5 MP。
- 同一输出目录第二次初始化时，会自动从 `.drop_cache/<hash>/session_state.json` 恢复断点。

**Step 2 · 查重（可选，但推荐放在精修前）**

- scope 选 `source` 扫源目录、`output` 扫已输出目录、`custom` 扫任意路径。
- 预设：「严格」连拍专用、「平衡」推荐、「宽松」同场景多角度。
- 在每组里勾选要删除的（默认保留最大分辨率），点删除：本地文件会被 `os.remove`，同名 `.txt` 也一并删，源 zip 内的文件改为软忽略。

**Step 3 · 裁切**

- **裁切屏（Crop）**：逐张精修，自动给一个智能裁切建议（drop.py 的边缘+显著性算法）。多框可以从一张图同时拿好几个不同构图。
  - 快捷键：`Space` 加入队列 · `S` 跳过 · `A` 新增框 · `Z` 撤销 · `1-9` 选中第 N 个框 · `← →` 切图 · 鼠标拖拽空白区域可直接框出新裁切框。
  - 比例锁：自由 / 目标桶 / 1:1 / 4:3 / 3:4 / 16:9 / 9:16。
- **批处理屏（Export）· 一键全自动**：跳过逐张精修，对源目录每张图按最近邻桶 + 居中最小裁切直出。新底模放宽分桶要求后这是最常用模式。
- **批处理屏 · 导出队列**：把 Crop 屏加入队列的项按 `min_bucket_size` 合并孤儿桶后写出 PNG。

**Step 4 · 反推标签**

- 反推屏有三种模式：
  - **批量目录**：指一个目录，给所有图写 .txt，最常用。
  - **联动队列**：开启后 Drop Studio 每写一张输出 PNG 就自动入队打标。在 Crop 屏边精修边产出可直接用的训练数据。
  - **测试单图**：上传一张图看每个标签的置信度，调试参数用。
- 预设面板（左侧）可保存阈值 / 排除词 / 权重映射 / token 上限等成一套，下次一键加载。

---

## 5 · 环境变量

| 变量 | 作用 |
| --- | --- |
| `DROP_PORT` / `DROP_HOST` | 默认端口/主机（CLI 覆盖优先） |
| `HF_ENDPOINT` | HuggingFace 镜像，国内常用 `https://hf-mirror.com` |
| `HF_TOKEN` | gated 模型授权 |
| `TAGGER_USE_CPU=1` | 强制 ONNX 走 CPU |
| `TAGGER_DEVICE_ID` | GPU id |
| `TAGGER_DEFAULT_MODEL` | 默认 tagger 模型 id，例如 `hf:Camais03/camie-tagger-v2` |
| `TAGGER_BASE_PATH` | 反推模型/预设根目录，默认 `tagger/` |
| `TAGGER_ORT_OPT_LEVEL` | ONNX 图优化级别 `all`/`extended`/`basic`/`disable`；Camie v2 在 CPU 上首次加载若卡 5–10 分钟，可降到 `extended` |

---

## 6 · 目录结构

```
1_droptools/
├── server.py                  # FastAPI 入口（轻薄，~220 行）
├── app.jsx · index.html       # 前端根
├── lib/                       # 前端共享：api/icons/shell/tokens
├── screens/                   # 前端各工作屏
│   └── tagger/                # 反推屏拆 7 个子文件（服务端拼成单 bundle 加载）
├── webapp/                    # 后端 FastAPI 包（按职责分文件）
│   ├── config.py              # 路径/分桶/缩略图常量
│   ├── state.py               # 进程级共享单例
│   ├── session_manager.py     # SessionManager + 断点续作
│   ├── buckets.py             # 分桶数学 + 智能裁切框
│   ├── similarity.py          # 哈希 + 严格 clique 聚类
│   ├── thumbnails.py          # .thumb_cache 维护
│   ├── snapshots.py           # 相似度结果快照（snap_id）
│   ├── vendor.py              # React/Babel CDN 本地镜像
│   ├── history.py · lan.py    # 最近会话 / 局域网 IP 探测
│   ├── schemas.py             # 全部 Pydantic 请求体
│   └── routes_*.py            # 分域 APIRouter
├── webapp/
│   ├── auto_crop.py           # 智能裁切建议（边缘+显著性融合，旧 drop.py 抽出来的）
│   ├── upscale.py             # RealESRGAN 上采样 + LANCZOS 回退（旧 drop.py 抽出来的）
│   ├── video.py               # 两步式视频关键帧抽取（旧 get_pictures.py 抽出来的）
│   └── ...                    # 见上
├── tagger/                    # ONNX 反推标签子包
│   ├── api_router.py          # /api/tagger/* 全部路由
│   ├── interrogator.py        # WD / Camie / DeepDanbooru / 本地 ONNX
│   ├── queue_worker.py        # 后台联动队列（daemon thread + queue）
│   ├── utils.py · preset.py   # 批处理 + 预设
│   ├── config.py · format.py · dbimutils.py
│   ├── models/                # ⚠️ 本地模型根目录（git 忽略，需要自己放）
│   └── presets/               # 反推预设 JSON（自动生成）
├── legacy/                    # 旧版命令行工具（drop.py / get_pictures.py），WebUI 不再依赖
├── vendor/                    # React/ReactDOM/Babel 本地镜像（启动时自动下载）
├── RealESRGAN_x4plus_anime_6B.pth   # ⚠️ 超分模型权重（git 忽略，需要自己放）
└── .drop_cache/<hash(output)>/      # 按输出目录哈希的会话缓存
    ├── session_state.json
    ├── .tagger_pending.json
    └── .thumb_cache/
```

---

## 7 · 常用 API（可写脚本调用）

```
POST /api/init                 {input_dir, output_dir?, target_mp?, step?}
POST /api/save                 {filepath, crops:[{x,y,w,h}], status}
POST /api/process_batch        {target_mp?, step?, min_bucket_size?}
POST /api/auto_bucket_all      {target_mp?, step?, scope?, include_processed?, output_subdir?, name_template?}
POST /api/analyze_similarity   {scope:'source'|'output'|'custom', folder?, threshold, method, ar_tolerance, require_dual_hash}
POST /api/delete_files         {filepaths[], snap_id?}
POST /api/video/probe          {video_path, scene_threshold?, max_candidates?, sample_rate_hz?}
POST /api/video/save           {job_id, indices[], output_dir?, name_prefix?}

POST /api/tagger/preload       {model}                # 预加载模型（避免首张图阻塞）
POST /api/tagger/interrogate   multipart {image, model, threshold, ...}
POST /api/tagger/batch         {input_path, output_path?, recursive, ...}  → 返回 job_id
GET  /api/tagger/batch/{id}    # 轮询进度
POST /api/tagger/queue/config  # 配置联动队列的后处理参数
GET  /api/tagger/queue         # 队列状态快照
```

---

## 8 · 常见问题

**Q: Camie v2 加载卡死 5–10 分钟？**
A: 这是 ONNX Runtime 在 CPU 上对 788 MB 模型做图优化的正常耗时。把 `TAGGER_ORT_OPT_LEVEL=extended` 设上能砍掉大半 ——

```powershell
$env:TAGGER_ORT_OPT_LEVEL = 'extended'
python server.py
```

或者直接用 GPU（装 `onnxruntime-gpu` + 正确的 CUDA），几十秒就加载完。

**Q: HuggingFace 下载失败？**
A: 设镜像 `$env:HF_ENDPOINT='https://hf-mirror.com'`。或者按上面方式 B 自己手动下到 `tagger/models/<name>/`。

**Q: 显存不足 OOM？**
A: 反推屏右上角「卸载」可释放模型显存。批处理屏 `batch_size` 默认 8，需要时降到 4 / 2。

**Q: 一键全自动 vs 队列导出区别？**
A:
- **一键全自动**：对源目录每张图直接最近邻桶 + 居中最小裁切，**不进 Crop 队列**，不影响会话状态。日常推荐。
- **队列导出**：导出 Crop 屏手动加入队列的项。导出完只清空 queue + 撤销栈，`session.items`（哪些已 cropped / skipped）和 `current_index` 都保留，可以分批导出后回来继续做剩下的图。要彻底清掉会话状态用主页 sidebar 的「关闭当前会话」按钮（= `POST /api/reset`）。

**Q: zip 压缩包内图也能识别吗？**
A: 能。源目录里的 .zip 会被打开扫内部图片，用 `zip://<zip_path>::<inner_path>` 这种 URI 表示。注意 zip 内图不能直接被「删除」，只会被加入忽略列表。

**Q: 缩略图缓存怎么清？**
A: 删 `.drop_cache/<hash(输出目录)>/.thumb_cache/` 整个目录即可。退出 server / 关闭会话时会自动清理。

---

## 9 · ⚠️ 安全提示

**当前服务监听 `0.0.0.0` 且没有任何鉴权，CORS 全开**。`/api/init` 可以接受**任意磁盘路径**作为输入目录，扫到的图片会被收进 `session.files`，进而通过 `/api/file/{idx}` 暴露。建议：

- 仅在受信任的本地/局域网使用；
- 真正需要远程访问时，挂到反向代理后面做 BasicAuth / 内网隧道；
- 启动时改 `--host 127.0.0.1`，手机访问就走 SSH 端口转发或 Tailscale 等。

---

## 10 · 已知限制

- 前端 Babel 在浏览器端转译，无构建步骤，首屏稍慢；这是刻意保持「无 node_modules」选择。
- `.thumb_cache` 对 zip 内文件 mtime 永远算 0：源 zip 改动后缩略图不会自动失效，删除 `.drop_cache/<hash>/.thumb_cache/` 可强制重算。
- Camie v2 在 4 核 CPU 上首次 ONNX 图优化可能花 5–10 分钟（见上面 FAQ）。
