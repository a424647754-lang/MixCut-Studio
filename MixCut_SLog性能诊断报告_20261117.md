# MixCut S-Log 灰片：分析与预览卡顿专项诊断报告

- 诊断日期：2026-11-17
- 诊断范围：只读诊断。**未修改任何产品代码、未重构、未调整 UI**
- 素材目录：`D:\BaiduNetdiskDownload\影视飓风素材`（10 个 `.mov`）
- 工程根：`D:\mixcut源文件\混剪副本（测试）源文件`
- 原始数据：`%TEMP%\mixcut_slog_*.json`（脚本同名 `.py` 保留，可复核）

---

## 一、结论摘要（先看这里）

1. **卡顿的根因不是 S-Log 色彩处理，而是“4K ProRes 10-bit 4:2:2 只能软解”+“同一条素材被多套解码器重复解多遍”。**
2. 单看预览就已经跑不满：源 59.94 fps，实测 **45.47 fps（0.756 倍速）**，CPU 平均 **352.9%**，GPU 仅 **38.7%** 且不参与解码（Qt 输出 `Format_P016`，解码在 CPU）。
3. 预览 + 分析并行时更差：实际分析页路径下预览掉到 **27.69 fps（约 0.46 倍速）**，进程 CPU 冲到 **1198.8%**（16 线程里占满 12 条），内存从 715 MB 涨到 **1440.7 MB**。
4. 分析慢的直接原因：`AnalysisWorker._read_batch()` 为了取 6～24 个采样帧，**顺序 grab 解码整条素材的全部帧**。10 秒 60p 素材固定 **2.53 s**（235.8 fps 解码），且 fast/medium/strict 三档耗时几乎一样（2.558 / 2.532 / 2.557 s）——**提高分析档位不会更慢，降低档位也不会更快**。
5. 混剪链路（`filter_engine`）还有第二层浪费：ProRes 是 **all-I（每帧都是关键帧）**，amverge 拿到 596 个“关键帧”、密度 59.94/s，被 `_looks_pathological()` 判定为病理状态后**回退成全片解码**（3.201 s），而纯 demux 只要 **0.448 s**，差 **7 倍**；同时产出 **198 个假切点**（合并后 99 个），10 秒素材平均每 0.1 秒一个切点。
6. **S-Log 没有触发额外色彩转换**：10 条素材的 `color_space / color_transfer / color_primaries` 全部是 **bt709/bt709/bt709**，`color_range=tv`，代码里也没有按 S-Log 分支的 LUT/转换。灰是画面本身烘焙的观感，不是元数据驱动的色彩管线。
7. **磁盘不是瓶颈**：顺序读 **2809.6 MB/s**，单条 1.06 Gbps 素材约 132 MB/s，占磁盘 5% 不到。

---

## 二、测试环境与素材真实元数据

### 2.1 机器

| 项目 | 值 |
|---|---|
| CPU | Intel i5-13490F，10 核 16 线程 |
| 内存 | 31.8 GB |
| GPU | NVIDIA RTX 4060 8 GB |
| 系统 | Windows，Python 3.14（`C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe`） |
| Qt 媒体后端 | PySide6 Qt Multimedia（FFmpeg 后端），离屏 `QVideoSink` 采样 |

### 2.2 素材（ffprobe 实测，全部一致为 ProRes 422 Standard / 3840×2160 / 10-bit 4:2:2）

| 文件 | 分辨率 | 像素格式 | 帧率 | 码率 |
|---|---|---|---|---|
| 20260628_7755_0232.mov | 3840×2160 | yuv422p10le | 59.94 | 1157.6 Mbps |
| 20260712_A1M2-5503_1122.mov | 3840×2160 | yuv422p10le | 59.94 | 1023.8 Mbps |
| 20260716_A1M2-5503_1428.mov | 3840×2160 | yuv422p10le | 59.94 | 974.5 Mbps |
| A015C0008_260609_884515.mov | 3840×2160 | yuv422p10le | 59.94 | 1059.7 Mbps |
| A015C0026_260609_884515.mov | 3840×2160 | yuv422p10le | 59.94 | 1291.7 Mbps |
| DJI_20260703065629_0007_D.mov | 3840×2160 | yuv422p10le | 59.94 | 881.3 Mbps |
| DSC_2220.mov | 3840×2160 | yuv422p10le | 59.94 | 1146.1 Mbps |
| N099_F030_0717U7_001.mov | 3840×2160 | yuv422p10le | 29.97 | 380.2 Mbps |
| N099_F041_0717SV_001.mov | 3840×2160 | yuv422p10le | 29.97 | 352.6 Mbps |
| Z904C047_260708DO.mov | 3840×2160 | yuv422p10le | 59.94 | 1065.0 Mbps |

全部素材：`codec=prores`、`profile=Standard`、`color_space=bt709`、`color_transfer=bt709`、`color_primaries=bt709`、`color_range=tv`、编码器 `Apple ProRes 422 / DaVinci Resolve`。

代表素材 A015C0008：**9.943 s、596 帧、59.94 fps、3840×2160、1.060 Gbps**，以下分环节数据默认用它。

---

## 三、三种场景实测

### 3.1 单独播放（Qt 播放器，原始 4K 文件，无代理）

| 指标 | 数值 |
|---|---|
| 12 s 墙钟走到 | `position = 9059 ms` |
| 折算倍速 | **0.756×** |
| 平均帧率 | **45.47 fps**（源 59.94） |
| CPU 平均 / 峰值 | **352.9% / 480.1%** |
| 进程 RSS 峰值 | **715.2 MB** |
| GPU 平均 / 峰值 | **38.7% / 41%** |
| 显存占用 | 约 **1073 MB** |
| Qt 收到像素格式 | `Format_P016` |

结论：**纯预览已经只有 0.76 倍速**，且 CPU 软解、GPU 不承担解码。0.76× 不是“分析拖累”，是 4K ProRes 10-bit 4:2:2 本身软解不到 60 fps。

### 3.2 单独分析（实际分析页 `AnalysisWorker`，10 条素材）

| 指标 | 数值 |
|---|---|
| 总墙钟 | **10.65 s**（素材总时长 32.03 s，约 3.0× 实时） |
| 产出片段 | 11 个（A015 拆 2 段，其余各 1 段） |
| 采样帧缓存 | 66 帧 |
| 进程 RSS 峰值 | **776.2 MB** |
| 错误 | 无 |

单条 10 秒 60p 素材的 OpenCV 解码成本：

| 路径 | 耗时 | 说明 |
|---|---|---|
| `_read_batch` fast（6 采样） | **2.558 s** | 顺序 grab 全部 596 帧 |
| `_read_batch` medium（12 采样） | **2.532 s** | 同上 |
| `_read_batch` strict（24 采样） | **2.557 s** | 同上 |
| 纯 `grab()` 596 帧 | **2.528 s（235.8 fps）** | 只解码不转 BGR |
| 纯 `read()` 596 帧（转 BGR） | **8.149 s（73.1 fps）** | 转换代价 5.6 s |

**关键：三档耗时几乎完全相同。** 采样数只影响 `retrieve()` 的次数，不影响顺序解码的帧数；`_read_batch` 的 grab 路径本身已经是最快解（比 `read` 快 3.2 倍），瓶颈在“必须把整条视频解完”。

### 3.3 播放 + 分析并行（最关键的一组）

分两种分析路径测，因为 UI 里两条链路都存在：

| 场景 | 帧率 | 倍速 | CPU 平均/峰值 | RSS 峰值 | GPU 平均/峰值 | 显存峰值 | 分析耗时 |
|---|---|---|---|---|---|---|---|
| 播放 + `AnalysisWorker`（分析页实际路径） | **27.69 fps** | **约 0.46×** | **1198.8%** | **1440.7 MB** | 17.6% / — | 1349 MB | **14.67 s**（单独时 10.65 s） |
| 播放 + `filter_engine`（智能混剪路径） | **29.86 fps** | **0.497×** | 450.2% / **862.8%** | **1021.6 MB** | 30.8% / 45% | 1072 MB | 9.679 s / 99 cuts |

- `AnalysisWorker` 并行时 CPU 冲到 **1198.8%**：OpenCV 默认吃满所有核，和 Qt 的解码线程硬抢。
- 同一条素材被 **两套独立解码器** 同时读：Qt 播放器解一遍，OpenCV 分析再解一遍。不是共享解码器，不存在锁竞争，是纯 CPU 算力竞争。
- 内存同步上涨 300～700 MB：两个解码管线各自的 4K P016/BGR 帧缓冲叠加。

---

## 四、分环节基准（A015C0008，9.943 s / 596 帧）

| 环节 | 函数 | 耗时 | 说明 |
|---|---|---|---|
| amverge 关键帧 | `amverge.core.keyframes.keyframes.generate_keyframes` | **3.801 s** | 返回 **596** 个“关键帧” |
| 场景分析（粗采） | `mixcut.scene_analyzer.analyze` / `_coarse_scan` | **2.512 s** | 整片解码后按 1 fps 取样 |
| 废帧检测 | `mixcut.bad_shot_detector.analyze_video` | **1.337 s** | 随机 seek 采样 |
| 视频+音频检测 | `mixcut.ffmpeg_tools.detect_all` | **3.790 s** | 整片解码 |
| 仅音频检测 | `mixcut.ffmpeg_tools.detect_all` | **0.075 s** | 只跑 `silencedetect` |
| cv2 随机 seek 3 次 | `cv2.VideoCapture.set(POS_FRAMES)` | **0.617 s** | 约 0.2 s/次 |
| 顺序读 30 帧 | `cv2.VideoCapture.read` | **0.489 s** | 61 fps |

另两条素材：

| 素材 | 时长/帧数 | amverge | scene | badshot | detect_all | 仅音频 | seek3 | read30 |
|---|---|---|---|---|---|---|---|---|
| Z904C047 | 3.620 s / 217 帧 | 1.487 s | 2.392 s | 0.696 s | 1.723 s | 0.081 s | 0.583 s | 0.487 s |
| N099_F041 | 3.403 s / 102 帧 | 0.555 s | 2.314 s | 0.585 s | 1.018 s | 0.080 s | 0.511 s | 0.471 s |

### 4.1 amverge 的 all-I 病理回退（已确认）

| 测试 | 结果 |
|---|---|
| 只 demux 不解码 | **0.448 s / 597 包 / 596 关键帧 / keyframe_ratio 0.998** |
| `skip_frame=NONKEY` 全解码 | **3.201 s / 596 帧**（关键帧跳过完全无效） |
| `_looks_pathological(times, 9.943)` | **True**（密度 **59.94/s**） |

ProRes 422 是 all-I，每一帧都是关键帧 → 关键帧列表等于全部帧 → 密度 59.94/s 远超阈值 → `_looks_pathological()` 判定病理 → **放弃“只取关键帧”的快速路径，回退整片解码**。demux 0.448 s 与解码 3.201 s 差 **7.1 倍**，这 2.7 秒是纯浪费。

同时 `198` 个原始切点经 `filter_engine` 0.08 s 合并容差后仍是 **99** 个：9.94 秒素材平均每 0.1 秒一个切点。这不是“慢”，是**切分结果被污染**，会让混剪片段碎成渣。

### 4.2 10 条素材完整 `filter_engine`

| 指标 | 数值 |
|---|---|
| 并发 | 2 路 |
| 总墙钟 | **26.71 s** |
| 素材总时长 | 32.03 s |
| 相对实时 | **1.2×** |
| 总切点 | **135** |
| 废段 | 每个文件各 1 个（**10/10 全部命中**） |

逐文件切点：20260628_7755_0232 = 17、A1M2-5503_1122 = 27、A1M2-5503_1428 = 13、**A015C0008 = 99**、A015C0026 = 17、DJI_0007 = 29、DSC_2220 = 26、N099_F030 = 14、N099_F041 = 25、Z904C047 = 36。

> “每个文件恰好 1 个废段”这个整齐的 10/10 分布，强烈提示是某条固定规则在系统性误判（见第六节推测项），而不是真的每条都有一段废片。

### 4.3 磁盘与显存

| 项目 | 数值 |
|---|---|
| 素材顺序读 | 1,256.1 MB / 0.447 s / **2809.6 MB/s** |
| 单条素材码率需求 | 约 **132 MB/s** |
| 显存 | 1.07～1.35 GB / 8 GB，无压力 |

---

## 五、瓶颈定位（逐环节判定）

### 5.1 解码：**已确认，第一主因**

- ProRes 422 Standard / yuv422p10le / 10-bit / 4:2:2 的硬解链路在本机不可用：`-hwaccel cuda -hwaccel_output_format cuda` + `scale_cuda` 报 `Function not implemented (-40)`，输出 **0 帧**。RTX 4060 的 NVDEC 不支持这条 10-bit 4:2:2 ProRes 组合。
- 因此预览和 `ffmpeg` 全都走 CPU 软解。单路 4K 60p 的软解上限就在 45～60 fps 之间，实测 45.47 fps。
- Qt 输出 `Format_P016` 说明解码后的 10-bit 数据仍以高位深格式交给上层，渲染/上传由 GPU 做（GPU 38.7% 是合成与纹理上传，不是解码）。

**判定：预览卡顿在“CPU 软解 4K ProRes”这一层，不在 UI 绘制、不在磁盘、不在显存。**

### 5.2 OpenCV 分析：**已确认，第二主因**

- `_read_batch()` 为了拿 6～24 个点，从当前位置一路 `grab()` 到最后一个目标帧，等于**整条素材全解码**。
- 10 秒 60p 素材固定 2.53 s；30 秒素材就是 7.5 s 以上。分析页一次选 10 条（32 s 素材）实测 10.65 s。
- 更关键的是**并行时 CPU 被 OpenCV 吃满**：分析页 + 播放实测 1198.8%，预览直接掉到 0.46×。
- `grab()` 已经比 `read()` 快 3.2 倍（235.8 fps vs 73.1 fps），说明代码作者已经优化过；**剩余成本是无法再省的“逐帧解码”**，只能靠“少解码像素”或“换更小的源”解决。

**判定：分析卡顿在“OpenCV 为了稀疏采样而全片解码”+“OpenCV 线程数默认吃满所有核，抢占预览解码”。**

### 5.3 FFmpeg 抽帧：**已确认，无效优化已存在**

| 手段 | 结果 |
|---|---|
| 原始解码 | `rtime=2.822 s`、`speed=3.52×`、`utime=35.031 s`、`maxrss=733,220 KiB` |
| `-skip_frame nokey` | 仍 **596 帧**、`rtime=2.907 s`，**零收益** |
| `scale=480:-2,fps=1` | 反而 **3.516 s / 2.84×**，因为 `fps=1` 是“先解码再丢帧” |

`scene_analyzer._decoder_cmd()` 和 `_coarse_scan()` 用的就是“整片解码 + scale + fps=1”。素材是 all-I 时，`fps=1` 一秒只留 1 帧，但前面 59 帧照样解完。这就是 scene 分析 2.5 s 的来源。

**判定：FFmpeg 抽帧环节的问题是“降采样发生在解码之后”，解码量一点没少。**

### 5.4 色彩转换：**已确认不构成瓶颈**

- 全部素材元数据 `bt709/bt709/bt709/tv`，没有 S-Log 传递函数标记。
- 代码审计未发现按 S-Log 触发的 LUT、`zscale`、`colorspace` 或 ICC 转换分支。
- `BadShotDetector._eval_frame()` 里只有 `cv2.resize → BGR2GRAY → Laplacian → medianBlur`，属于常规 8-bit 指标计算，不做色彩管理。

**判定：色彩管线不是卡顿来源。灰片观感来自素材本身烘焙，不是软件在算 LUT。**

### 5.5 并发：**已确认，放大因素**

- 预览与分析**各自独立解码同一条文件**，不是共享解码器，也没有锁竞争——是两条 CPU 解码流水线在抢同一批核。
- `filter_engine` 内部还会对同一文件做 **amverge 解码 + scene 解码 + badshot seek + detect_all 解码**，最多 4 遍；再叠加预览就是 5 遍。
- 10 文件批量 2 路并发 26.71 s，CPU 峰值 862.8%，已经接近“并发不升速、只升占用”的区间。

### 5.6 UI 主线程：**推测，不是主因**

- 已确认 `AnalysisWorker` 是 `QThread`，`_process()` 跑在 worker 线程，**不在主线程**；预览解码在 Qt 自己的线程。主线程不承担解码。
- 但 CPU 被吃到 1199% 时，主线程的事件循环、绘制、Python 槽函数都会被挤，表现为“点按钮迟钝、窗口拖动卡”。这一点**没有单独量化**，只能标为推测。
- 若后续要坐实，可在主线程挂 50 ms `QTimer` 心跳，记录最大间隔。

---

## 六、已确认 / 推测 清单

### 已确认（有数据支撑）

1. 4K ProRes 10-bit 4:2:2 在本机**只能 CPU 软解**；单路预览 45.47 fps / 0.756×，CPU 352.9%，GPU 不参与解码。
2. 播放 + 分析并行时预览掉到 **0.46～0.50×**，CPU 450%～1199%，内存 1.0～1.44 GB。
3. `_read_batch()` 为 6～24 个采样帧**全片顺序解码**；10 s 素材固定 2.53 s；fast/medium/strict 三档耗时几乎相同。
4. ProRes all-I：`-skip_frame nokey` 完全无效（596/596 帧），`fps=1` 仍是解码后再丢帧（3.516 s > 2.822 s 基线）。
5. amverge 因关键帧密度 59.94/s 判定 pathological 并回退全解码：demux 0.448 s vs 解码 3.201 s，差 7.1 倍。
6. amverge 对 all-I 产出逐帧假切点：A015 198 → 合并 99；10 文件共 135 切点 / 32 s 素材，约 4.2 个假切点/秒。
7. 全部素材色彩元数据为 bt709/bt709/bt709/tv；代码中无 S-Log 专用色彩分支。
8. 磁盘 2809.6 MB/s，单条需求约 132 MB/s，不是瓶颈。
9. `config/preview_proxy.json` 与根目录 `preview_proxy.json` **均不存在**，`preview_proxy.get_width()` 默认 0，即**代理功能实际处于关闭状态**，预览读的是原始 4K 文件。

### 推测（无直接数据，需后续验证）

1. **S-Log 低对比可能让 `BadShotDetector` 的“模糊/曝光”规则误报**：10/10 文件各命中 1 个废段，分布过于整齐，怀疑是固定规则在灰片上系统性触发。需逐条打印 `_eval_frame()` 的 penalty 明细才能坐实。
2. **真实 `QVideoWidget` 窗口可能比离屏 `QVideoSink` 更慢**：现有 0.76×/0.46× 全部来自离屏采样，实际窗口多一层 D3D 呈现，未实测。
3. **UI 主线程事件循环延迟未量化**：只确认解码不在主线程，不能确认“UI 不卡”。
4. `filter_engine` 的 4 遍解码是否全部必要，未逐遍打点（amverge/scene/badshot/detect_all 各自的输入输出是否有可复用的中间帧，未验证）。
5. OpenCV 内部线程池（TBB/OpenMP）实际开了几条、能否 `cv2.setNumThreads()` 限流，未实测。

---

## 七、最小修改方案（仅建议，本阶段不落地）

> 原则：不降低分析质量、不删分析功能、不改混剪判定逻辑。以下每条都标注“是否会改变分析结果”。

| # | 问题 | 最小改法 | 涉及源码 / 函数 | 预期效果 | 是否影响分析质量 |
|---|---|---|---|---|---|
| A | 分析为稀疏采样全片解码 4K | 分析输入改用 **960p 预览代理**（或 ffmpeg pipe 直接输出 960p 原始帧） | `mixcut/ui/analyze_page.py:_read_batch` / `_get_cap`；复用 `mixcut/preview_proxy.py:_generate` / `resolve` | 4K 解码 2.53 s → 960p 约 **0.5～0.7 s**；10 文件分析 10.65 s → **约 3 s** | **不影响**：`EvalConfig.max_side=960`，现有指标本来就在 960 内计算 |
| B | 预览读原始 4K，0.76× | 导入后后台生成 720/960/1280 代理，预览优先读代理（当前默认关闭） | `mixcut/preview_proxy.py:resolve/_generate`；`main.py:_init_player/show_clip_preview` | 960p 预览解码约 4K 的 1/10 像素 → 预览从 0.76× 提到 **≥2× 实时**，CPU 353% → 约 **80%** | 不影响：只影响预览清晰度，导出仍走原片 |
| C | OpenCV 吃满 16 线程，抢预览 | 分析 worker 内 `cv2.setNumThreads(4)`（或分析放独立低优先级进程） | `mixcut/ui/analyze_page.py:AnalysisWorker.run` | 并行时预览从 0.46× 回到 **0.7× 左右**；分析略慢 10～20% | 不影响：只改线程并发度 |
| D | all-I 导致 amverge 病理回退 + 假切点 | 先判 `keyframe_ratio > 0.9` → 直接跳过 amverge，改用 scene/detect 结果；不再回退全解码 | `amverge/core/keyframes/keyframes.py:generate_keyframes/_looks_pathological`；`mixcut/filter_engine.py:_amverge_cuts` | 每条省 **3.2 s 解码**；A015 假切点 99 → 个位数；10 文件 26.71 s → **约 15 s** | 会改变切点结果（**变正确**），但属于修 bug，不是降质 |
| E | scene `fps=1` 解码后再丢帧 | 复用 D 的输出帧，或在代理上跑；all-I 且无真实切点时直接跳过 scene | `mixcut/scene_analyzer.py:_decoder_cmd/_coarse_scan/analyze` | 每条省 **2.3～2.5 s** | 不影响（跳过的是已被证明无切点的路径） |
| F | 同一文件被解 4～5 遍 | 以“一次解码、多环节复用帧”为目标，先做帧缓存共享（分析页与混剪共用一份采样帧） | `mixcut/ui/analyze_page.py:_read_batch`、`mixcut/filter_engine.py:run_filter` | 理论再省 **30～50%** 总耗时 | 不影响 |

**优先级建议：B（预览代理）→ A（分析走 960p）→ C（限线程）→ D/E（all-I 修切点）→ F（共享帧）。**
B 和 A 是“用更小的像素做同样的事”，收益最大且零质量损失；D/E 是修正确性问题，顺带提速。

---

## 八、明确未测 / 未做

1. 真实 `QVideoWidget` 窗口（非离屏）的预览帧率与掉帧分布——未测。
2. UI 主线程事件循环延迟（心跳间隔）——未测。
3. 缩略图 worker 独立 ffmpeg 抽帧的耗时——未单独计。
4. `filter_engine` 内部 4 遍解码的逐遍耗时拆分——只测了各函数总耗时，未做帧复用验证。
5. OpenCV 实际线程数与 `setNumThreads` 的收益——未测。
6. S-Log 是否导致 `BadShotDetector` 误判——只有“10/10 各 1 废段”的现象，未打印 penalty 明细。
7. 用户机器上是否曾在设置里选过 720/960/1280 代理——本机两份 `preview_proxy.json` 都不存在，只能确认默认关闭。

---

## 九、附：原始数据索引

| 文件 | 内容 |
|---|---|
| `%TEMP%\mixcut_slog_diag.json` | 分环节基准（amverge/scene/badshot/detect_all/cv2） |
| `%TEMP%\mixcut_slog_filter.json` | amverge 切点 + `filter_engine` 端到端 |
| `%TEMP%\mixcut_slog_playback.json` | 单独播放 45.47 fps / 0.76× |
| `%TEMP%\mixcut_slog_playback_perf.json` | 修正内存采样后的播放数据（715.2 MB） |
| `%TEMP%\mixcut_slog_parallel.json` | 播放 + `filter_engine` 并行（29.86 fps / 0.497×） |
| `%TEMP%\mixcut_slog_worker.json` | 分析页 `AnalysisWorker` 全流程 10.65 s |
| `%TEMP%\mixcut_slog_worker_parallel.json` | 播放 + `AnalysisWorker` 并行（27.69 fps / 1198.8% / 1440.7 MB） |
| `%TEMP%\mixcut_slog_cv.json` | `_read_batch` 三档 2.558/2.532/2.557 s、grab 235.8 fps、read 73.1 fps |
| `%TEMP%\mixcut_slog_amverge.json` | demux 0.448 s / decode 3.201 s / pathological True / 10 文件 26.71 s |

---

**本阶段结论：诊断完成，未改任何产品代码。等待人工审核后再决定是否按第七节落地。**
