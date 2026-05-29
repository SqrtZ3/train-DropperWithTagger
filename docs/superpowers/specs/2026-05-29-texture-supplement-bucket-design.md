# 原始+细化裁切 / 纹理补充桶 — 设计文档

> 状态:已与用户确认,待进入 writing-plans。
> 日期:2026-05-29
> 关联:`docs/superpowers/plans/2026-05-28-arb-crop-first-export.md`(ARB 裁切优先,本设计在其基础上演进)

## 1. 背景与目标

高分辨率源图被降采样到训练基准分辨率(~1MP)时,皮肤/布料/发丝/金属等高频纹理会被重采样抹掉。本功能为训练集增加一类 **native-resolution 细节裁切(纹理补充桶)**:在源图上框出一块**尺寸严格等于某个桶**的区域,像素 **1:1 原样输出、全程零重采样**,作为独立的"纹理补充集",让模型学到不被上/下采样扰动的真实纹理。

一张图因此可产出两类输出,互补:

- **全图集(普通框,已有)**:整图框选后重采样贴合最近的桶,教整体构图。对小于桶的裁切**上采样**、大于桶的**下采样**——输出永远是干净桶尺寸,保证"完整全图能被输入训练"。
- **纹理补充集(纹理框,新增)**:原生裁切、零重采样。全图集对小图上采样会插值出"假细节",纹理集恰好补上真实原生细节。

## 2. 范围

**做(本期)**
- Crop 屏:每个裁切框一个"纹理"开关,普通框与纹理框可在同一张图混用。
- 纹理框:自由画 → 松手吸附到合法桶 → 原生裁切零重采样 → 写入独立子目录。
- 顺手收敛桶系统统计不一致(原 bug #1),并把无效的 PNG `quality=95` 改为 `compress_level`(原 bug #3)。

**不做(YAGNI,留后续迭代)**
- 批处理"一键全自动"自动切纹理块(纹理本期仅走手动框)。
- 纹理块多分辨率 / 超分补纹理 / 纹理 caption 智能裁剪。

## 3. 核心决策(含理由)

| 决策点 | 结论 | 理由 |
| --- | --- | --- |
| 落点 | Crop 屏手动框,批处理自动化延后 | 匹配"勾勒",构图控制力最强;先把可控的做扎实 |
| 纹理框定尺寸 | 自由画 + 松手吸附到合法桶 | 圈选随手,松手再吸附保证零重采样前提 |
| 普通 vs 纹理共存 | 每框一个 `kind` 开关,可混合 | 数据模型最干净,最贴"原始+细化" |
| 纹理桶基准 | **跟随首页 MP 设定**(= `session.target_area`),非固定 1024 | 用户首页设什么基准,纹理就用同族桶;反而统一了桶定义 |
| 非纹理框默认 | **总是贴桶(含上采样)** | 输出永远干净桶尺寸;与纹理集形成"假细节 / 真细节"互补 |
| 输出存放 | 独立子目录 `output/texture/` | 训练脚本(如 kohya 按文件夹 repeat)可分别配权重 |
| 实现范围 | Route B:功能 + 修桶系统统计 bug | 保留 crop-first 意图,只让合并/统计诚实 |

## 4. 桶 family 统一(根除原 bug #1)

全程只保留**一族桶**:`get_standard_buckets(target_area, step)`,其中 `target_area = session.target_area`(由首页 MP 决定的等面积桶)。它服务于:

1. Crop 屏 "桶预测" 预览(前端 `calcBucket`,本就是这族);
2. 非纹理框的 resize 目标桶;
3. 纹理框吸附目标(再按 `AR ≤ texture_max_ar` 过滤);
4. `process_batch` 的孤儿桶合并(`min_bucket_size`)与日志统计。

**原 bug #1 的本质**:`process_batch` 用等面积桶(系统①)做合并/统计,但默认 `arb_crop` 策略下 `_export_crop` 用 `plan_arb_export`(系统②)从已裁切尺寸**重新**定桶 → "每桶≥N 张"的保证在真实数据集上不成立,日志 `bucket_count`/`merged_count` 也对不上实际落盘。

**修法(保留 crop-first)**:
- 默认策略改为 `resize`(总是贴桶),此路径下输出 == 所选桶,合并/统计天然对齐,#1 由构造消失。
- `arb_crop`(crop-first)**保留为可选项**;修正它的统计诚实性:抽出 `plan_arb_export` 的"只选桶"子步骤,导出前先算出每张图**真实会输出的桶**,据此分组合并与上报。crop-first 的裁切/降采样策略本身**不动**。

## 5. 吸附函数(纯函数,前后端共用规则)

`snap_to_texture_bucket(drawn_w, drawn_h, src_w, src_h, target_area, step, max_ar=2.0) -> (x, y, w, h) | None`

1. 候选桶 = `get_standard_buckets(target_area, step)` 过滤 `AR ≤ max_ar`。
2. 可行桶 = `{ b | b.w ≤ drawn_w 且 b.h ≤ drawn_h 且 b.w ≤ src_w 且 b.h ≤ src_h }`(既塞进画框,又不超源图)。
3. 选 `b.w * b.h` 最大者;面积相差 ≤2% 视为并列,取 AR 最接近画框者。理由:纹理目标是"尽量多的原生像素"。
4. 以画框中心居中放置该桶,夹进 `[0, src - b]`,返回整数 `(x, y, w, h)`。
5. 无可行桶(如源图最小边小于最小桶最短边,或画框太小)→ 返回 `None`。

后端导出时**始终**用本函数对客户端传来的框重新校验/重算,不信任前端坐标。

## 6. 数据模型与持久化

- 裁切框对象新增 `kind: 'full' | 'texture'`,缺省 `'full'`(向后兼容旧会话/旧队列)。
- 链路:前端 `crops[]` → `POST /api/save` 的 `crops[]` dict(本就是自由 dict,schema 形状不变)→ `session.save_crop` 存入 `crop_params` → `session.queue` item → `process_batch`。
- **待核实(实现阶段)**:`webapp/session_manager.py` 的 `save_crop()` 与 queue item 构造需透传 `kind`;确认 queue item 与 `crop_params` 的字段结构。
- `webapp/schemas.py`:`ExportBucketOptions` 新增 `texture_subdir: str = "texture"`、`texture_copy_caption: bool = True`、`texture_max_ar: float = 2.0`;`export_strategy` 默认由 `"arb_crop"` 改为 `"resize"`。

## 7. 前端 — Crop 屏(`screens/crop.jsx`)

- **每框"纹理"开关**:active 框上(或右侧 inspector)一个切换钮;切到纹理时立即对当前框跑一次吸附。
- **纹理框行为**:自由画 / 拖角保持自由,但 `mouseup` 时调用吸附(前端镜像 `snap_to_texture_bucket` 规则,即时预览);移动不改尺寸(仅夹边)。比例锁 UI 对纹理框失效。
- **视觉区分**:纹理框琥珀色虚线 + 角标"纹" + 框下显示锁定桶,如 `纹理 1216×832 · 原生`。
- **塞不下**:吸附返回 `None` → toast「此区域放不下任何桶(源图太小)」,该框退回 / 不切换为纹理。
- **`commit('accept')`** 的 payload 为每个 crop 带上 `kind`。
- 前后端各持一份吸附规则(沿用现有 `calcBucket` 同样的权衡);后端导出为准。

## 8. 后端 — 导出数据流(`webapp/routes_crop.py`)

- `process_batch` 把队列**按 `kind` 分流**:
  - `full` 项:走现有流程;默认 `resize` 策略下 resize 到所选桶(含上采样);合并/统计用统一桶 family(#1 修正)。
  - `texture` 项:逐张 → 后端用 `snap_to_texture_bucket` 重新校验框 → `img.crop(精确桶区域)` → **无任何 resize / 超分** → 写入 `<output>/<texture_subdir>/` → 按 `texture_copy_caption` 复制源 `.txt` → `notify_artifact_saved`(若开了联动打标,纹理块会被重新打标,缓解"整图 caption 不贴细节块")。
- 纹理块存盘:`PNG`,`compress_level`(顺带修原 bug #3 的无效 `quality=95`);代码内 `assert out.size == bucket` 守住零重采样。
- `auto_bucket_all`(一键全自动)本期**不接**纹理;仅受 #1 统计修正影响。
- `process_batch` 返回体新增 `texture_processed`、`texture_output_folder`。

## 9. 前端 — 导出屏(`screens/reels.jsx`)

- `export_strategy` 默认改为 `resize`;策略标签:`resize` → "贴桶(上下采样)",`arb_crop` → "裁切优先"(去掉"旧版"误导)。
- **localStorage 注意**:用户若已存 `drop.exportStrategy = 'arb_crop'`,改代码默认不会覆盖既有值——届时在导出屏点一下切到"贴桶"即可(不做自动迁移)。
- 结果区(`result` 面板)新增 `纹理` Stat + 纹理输出路径。

## 10. 边界与错误处理

- 源图最小边 < 最小桶最短边 → 该图纹理开关禁用(前端)+ 后端跳过并记 error。
- 纹理框贴边:居中后夹进 `[0, src - bucket]`,绝不越界(原生不能补边)。
- 旧会话 / 旧队列无 `kind` → 视为 `full`。
- 透明图(RGBA/LA/P)拍平逻辑复用现有实现。
- 后端不信任前端框坐标,导出前一律用吸附函数重算。

## 11. 测试(`tests/test_bucket_export.py` 扩展)

1. `get_standard_buckets(1024², step)` 过滤 AR≤2 的桶集合形状:含 1024×1024、最扁桶 AR≤2、有序。
2. `snap_to_texture_bucket()`:宽画框→宽桶、方画框→1024²、最大面积优先、居中夹边、塞不下返回 `None`、源图太小返回 `None`。
3. 纹理导出:输出尺寸**精确等于**桶;输出与 `PIL.Image.crop(同框)` **逐像素一致**(证明零重采样)。
4. **#1 回归**:`arb_crop` 策略下 `process_batch` 合并/统计的桶 == 实际输出桶;`resize` 策略下输出 == 所选桶且 `min_bucket_size` 保证在真实输出上成立。

## 12. 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `webapp/buckets.py` | + `snap_to_texture_bucket()`;抽出 `plan_arb_export` 的"只选桶"子步骤供统计复用 |
| `webapp/schemas.py` | + 3 个 texture 字段;`export_strategy` 默认改 `resize` |
| `webapp/routes_crop.py` | 队列按 `kind` 分流;新增纹理导出(无损);#1 统计修正;#3 `compress_level` |
| `webapp/session_manager.py` | 透传 `kind`(待核实结构) |
| `screens/crop.jsx` | 每框纹理开关、松手吸附、纹理框样式、inspector 锁定桶、commit 带 `kind` |
| `screens/reels.jsx` | 默认策略 `resize`、标签重命名、结果区纹理统计 |
| `lib/api.jsx` | 吸附规则 JS 镜像(即时预览) |
| `tests/test_bucket_export.py` | + 4 组测试 |

## 13. 开放/待核实

- `session_manager.py` 的 `save_crop` / queue item 字段结构(透传 `kind` 的具体落点)。
- 会话开始前 `crop.jsx / reels.jsx / buckets.py / routes_crop.py / schemas.py` 已有未提交改动:实现前先读清,叠加而非覆盖。
