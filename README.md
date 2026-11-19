# MixCut Studio

> Windows 桌面版**智能混剪工具**。导入素材 → 自动分析画面质量 → 剔除废镜头 → 按参数随机装配 → 生成可在剪映二次编辑的草稿工程。

![Python](https://img.shields.io/badge/Python-3.14-blue)
![PySide6](https://img.shields.io/badge/PySide6-6.12-green)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey)

> ### ⚠️ 关于 10bit 4:2:2 素材
>
> **NVIDIA GeForce 消费级显卡无法硬解 10bit 4:2:2**，这类素材分析时只能走 CPU 软解，速度明显偏慢。
>
> 索尼 A7M4 / A7S3 / FX3 的默认记录格式就是 4:2:2。
> **建议在相机里改成 4:2:0**，详见 [硬件加速](#硬件加速)。
>
> 若必须硬解 4:2:2，需 **Blackwell 架构（RTX 50 系）** 显卡；
> RTX 40 系及更早、包括专业卡在内的所有旧架构均不支持。
>
> 程序不会因此报错，会自动回退软解，只是慢。

---

## 目录

- [它能做什么](#它能做什么)
- [核心流程](#核心流程)
- [功能详解](#功能详解)
- [技术栈](#技术栈)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [性能特性](#性能特性)
  - [⚠️ 重要限制：10bit 4:2:2 无法 GPU 硬解](#️-重要限制10bit-422-无法-gpu-硬解)
- [回归测试](#回归测试)
- [常见问题](#常见问题)
- [已知限制](#已知限制)

---

## 它能做什么

给一批拍摄素材，MixCut 会自动完成这些事：

1. **分析每一条素材的画面质量** —— 检测黑场、白场、模糊、过曝、欠曝、噪点
2. **按 5 秒窗口切分片段**，每个片段独立打分
3. **标记废片段**，混剪时自动排除
4. **随机装配成片** —— 按目标时长、片段长短、是否允许重复等参数
5. **配乐卡点** —— 检测 BGM 的 BPM 与鼓点，把切点对齐到节拍
6. **人声闪避** —— 有人声时自动压低 BGM
7. **规则化转场** —— 短镜头硬切、长镜头叠化
8. **输出剪映草稿** —— 打开就能继续精修

**它不渲染视频**，只生成工程文件。真正的导出在剪映里完成。

---

## 核心流程

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ 素材库  │ →  │ AI 分析 │ →  │ 参数设置│ →  │ 任务中心│
│  导入   │    │ 打分/分段│    │ 混剪参数│    │ 生成草稿│
└─────────┘    └─────────┘    └─────────┘    └─────────┘
                     ↓                              ↓
              混剪池（片段级）              剪映草稿工程
```

---

## 功能详解

### 一、素材库

**导入方式**
- 点击「添加视频」单条导入
- 点击「扫描文件夹」递归导入整个目录

**素材管理**
- 卡片网格浏览，显示缩略图、时长、分析状态
- 按文件夹分类，支持拖拽移入、多选批量操作
- 左侧文件夹栏固定，文件夹按创建顺序排列

**选择素材**
- 单击勾选 / 取消
- `Shift` 连选、`Ctrl` 点选多选
- 多选后拖动可整组移动
- 右键「移动到文件夹」对整组生效

### 二、素材预览

- 播放 / 暂停 / 进度拖动
- 上一段 / 下一段切换
- **预览代理**：默认关闭，可选 720p / 960p / 1280p
  - 4K HEVC 素材直接播放会卡，开启代理后流畅很多
  - 代理文件后台生成，只做一次

### 三、分析

**三档分析模式**

| 模式 | 采样帧数 | 判废门槛 | 适用场景 |
|------|---------|---------|---------|
| 快速分析 | 3 帧/段 | 差帧比例 ≥ 0.66 | 粗筛，速度快 |
| 中度分析 | 6 帧/段 | 差帧比例 ≥ 0.50 | 日常推荐 |
| 严格分析 | 12 帧/段 | 差帧比例 ≥ 0.34 | 精筛，判废严格 |

**分析原理**
- 长视频按 **5 秒固定窗口**切分，短于 5 秒的整段处理
- 每个窗口用 OpenCV 计算客观指标：
  - **清晰度** —— 拉普拉斯方差
  - **亮度** —— 灰度均值
  - **对比度** —— 灰度标准差
  - **噪点** —— 中值滤波残差
- 完全本地计算，**不依赖网络和 AI 模型**

**混剪池（片段级）**
- 每个片段独立进出池子
- 支持按文件夹分组，整组启用/停用
- 分析完成后默认全部入池
- 取消勾选「池」即从混剪中排除

**文件夹管理**
- 新建 / 重命名 / 删除
- 拖拽片段归类
- 右键「加入文件夹」
- 删除文件夹不会删除分析结果，片段会回到「全部素材」

### 四、参数设置

**画面**
- 画幅：9:16 竖屏 / 16:9 横屏 / 1:1 / 4:3 / 21:9
- 分辨率：360p ~ 8K
- 帧率：24 / 25 / 30 / 48 / 50 / 60 / 100 / 120

**时长**
- 目标时长 + 浮动容差（成片落在范围内即停止，不硬凑）
- 片段最短 / 最长时长
- 是否允许素材重复使用

**批量生成**
- 一次生成 N 个独立草稿
- 每个草稿的随机排列不同
- 废镜头检测只做一次，N 个草稿复用

**配乐**
- 手动选择 BGM
- 本地随机（从指定文件夹随机挑一首）
- 关闭配乐
- **人声闪避**：检测到人声时压低 BGM，人声结束恢复

**转场**
- 短镜头：硬切
- 长镜头：叠化
- 黑名单：指定分类之间只硬切

**输出**
- 草稿位置：默认剪映目录，也可自定义

### 五、任务中心

- 混剪任务进度
- 暂停 / 继续 / 取消
- 输出草稿列表，可直接打开目录
- 运行日志

### 六、设置

- **运行环境检测**：GPU / CUDA / ffmpeg / 剪映草稿目录
- **OpenCLIP**：可选语义检索模型
- **预览代理**：分辨率设置

---

## 技术栈

| 组件 | 版本 | 用途 |
|------|------|------|
| PySide6 | 6.12 | 界面框架 |
| OpenCV | 5.0 | 画面质量分析 |
| pyJianYingDraft | 0.3.1 | 剪映草稿生成 |
| librosa | 1.0 | 节拍检测 |
| soundfile | 0.14 | 音频读写 |
| open_clip_torch | 4.2 | 语义检索（可选） |
| numpy / scipy / numba | - | 数值计算 |
| ffmpeg / ffprobe | 9.0.2 | 解码、抽帧、代理转码 |

---

## 环境要求

### 必需

- **Windows 10 / 11**
- **Python 3.14**
  - ⚠️ 必须用 `py -3.14`，系统 PATH 里的 `python` 可能指向其他版本
- **ffmpeg**：放在项目 `ffmpeg/bin/` 下

### 安装依赖

```bat
py -3.14 -m pip install -r requirements.txt
```

### 可选

**OpenCLIP 语义检索**
- 模型权重放到 `OpenCLIP/open_clip_pytorch_model.bin`（约 1.7 GB）
- 不放也能跑，该功能自动跳过

**GPU 硬件解码**
- 需要 NVIDIA 显卡 + 驱动
- 仅对 **8bit 4:2:0 / 10bit 4:2:0** 素材生效
- 10bit 4:2:2 在 GeForce 上无法硬解（芯片限制）

---

## 快速开始

### 1. 启动

双击 `启动MixCut.bat`，或命令行：

```bat
py -3.14 main.py
```

> ⚠️ **改代码后必须完全退出程序再重启**，本程序没有热重载。

### 2. 导入素材

素材库 → 「添加视频」或「扫描文件夹」

### 3. 勾选并分析

勾选要分析的素材 → 点「分析选中」→ 等待完成

### 4. 调整混剪池

分析页里检查片段，把不满意的移出混剪池，可按文件夹分组

### 5. 设置参数并开始混剪

参数设置 → 调整目标时长等 → 点「开始智能混剪」

### 6. 打开剪映

任务中心 → 找到生成的草稿 → 打开目录 → 用剪映打开

---

## 目录结构

```
main.py                      主窗口：启动装配、事件处理、入口
mixcut/
├─ analysis.py               素材分析入口
├─ bad_shot_detector.py      画面质量检测（OpenCV）
├─ frame_pipe.py             顺序解码管道（含 GPU 硬解、关键帧采样）
├─ filter_engine.py          废镜头过滤引擎
├─ mixbuilder.py             混剪时间轴装配
├─ project_builder.py        剪映草稿落盘
├─ timeline.py               中立时间轴中间格式
├─ musicstudio.py            BGM 分析（节拍、闪避）
├─ preview_proxy.py          预览代理调度
├─ workers.py                后台任务线程
├─ appkit/                   主窗口逻辑（Mixin 拆分，2026-11-19）
│  ├─ constants.py           常量、路径工具
│  ├─ selection.py           素材勾选状态（MaterialSelection）
│  ├─ library_ops.py         素材树增删改、确认、选择器
│  ├─ mix_task.py            混剪任务启动与取消
│  ├─ param_build.py         混剪参数页 / 配置页构建
│  ├─ preview_player.py      播放器控制、键盘过滤
│  ├─ preview_stage.py       画幅切换、全屏
│  ├─ project_state.py       工程读写、撤销重做
│  ├─ autosave.py            自动保存与崩溃恢复
│  ├─ shell_nav.py           页面导航、顶栏、日志
│  ├─ widgets.py             通用控件（比例容器、滚轮守卫、素材树）
│  └─ window_chrome.py       窗口外框与尺寸
├─ proxy/                    代理转码（编码探测、缓存、服务）
└─ ui/
   ├─ analyze_page.py        分析页（含分析 worker 与片段卡片）
   ├─ library_page.py        旧素材库页（实例已移除，模块保留）
   ├─ demo/                  主界面各页
   │  ├─ pages_library.py    素材库
   │  ├─ pages_work.py       素材预览
   │  ├─ pages_params.py     参数设置
   │  ├─ pages_tasks_real.py 任务中心
   │  ├─ pages_settings.py   设置
   │  ├─ backend_bridge.py   新旧后台适配层
   │  └─ window.py           侧边栏与顶栏
   └─ fonts/                 字体（不随仓库分发）
tools/
└─ benchmark_frames.py       解码性能基准脚本
tests/                       回归测试
ffmpeg/bin/                  ffmpeg 运行时（不进版本库）
OpenCLIP/                    可选模型与虚拟环境（不进版本库）
engine/                      自研引擎（不进版本库）
```

### 不进版本库的内容

`.gitignore` 已排除：

| 目录/文件 | 原因 |
|-----------|------|
| `OpenCLIP/` | 模型 + venv，约 4.7 GB |
| `engine/` | 自研引擎，约 2.2 GB |
| `ffmpeg/` | 运行时，约 434 MB |
| `cache/` | 分析缓存，约 2.3 GB |
| `dist/` `build/` | 打包产物 |
| `*.bak-*` | 本地备份 |
| `*.mixcut` | 工程文件 |
| `*.mp4` 等 | 素材 |
| `mixcut/ui/fonts/*.ttf` | 字体版权 |
| `_project_backups/` | 工程自动保存副本 |
| `_backup_*/` | 本地回滚点 |
| `tests/_*.py` 等 | 临时验证脚本（本地调试用） |
| `MixCut全周期工作台账.md` | 本地开发台账 |

---

## 性能特性

### 解码方式

分析时不再逐帧随机跳转，而是用 ffmpeg **顺序解码整条视频**，边解边按目标采样率抽帧并缩放。

- 避免反复 seek 造成的重复解码
- 帧在 ffmpeg 内就缩放到分析尺寸（最长边 960），Python 侧内存占用大幅降低

### 硬件加速

自动检测素材能否使用 GPU 硬件解码（NVDEC），能硬解就走 GPU，不能就自动回退 CPU 软解，不会报错。

#### ⚠️ 重要限制：10bit 4:2:2 无法 GPU 硬解

**NVIDIA GeForce 消费级显卡（RTX 40 系、30 系等）的 NVDEC 硬件解码器不支持 10bit 4:2:2。**

这是**芯片层面的硬件限制**，不是驱动问题、不是 ffmpeg 参数问题，换软件也解决不了。

| 像素格式 | 颜色采样 | 位深 | RTX 40 系硬解 |
|---------|---------|------|--------------|
| `yuv420p` | 4:2:0 | 8bit | ✅ 支持 |
| `yuv420p10le` | 4:2:0 | 10bit | ✅ 支持 |
| `yuv422p10le` | **4:2:2** | **10bit** | ❌ **不支持** |
| `yuv422p` | 4:2:2 | 8bit | ❌ 不支持 |

##### 4:2:2 硬解需要什么显卡

根据 [NVIDIA NVDEC 官方能力表](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvdec-application-note/index.html)：

| 硬件特性 | Pascal | Volta | Turing / Hopper | **Ada（RTX 40）** | **Blackwell（RTX 50）** |
|---------|--------|-------|-----------------|-------------------|-------------------------|
| H264 High10 / High422 | ❌ | ❌ | ❌ | **❌** | **✅** |
| HEVC main 422 10/12 | ❌ | ❌ | ❌ | **❌** | **✅** |

**只有 Blackwell 架构（RTX 50 系，如 5060 / 5070 / 5080 / 5090）才支持 4:2:2 硬解。**

注意几点：

- **与显卡档次无关，只与架构代际有关**。Quadro / RTX A 系列（Ampere）和 L40（Ada）同样**不支持** 4:2:2
- 所有 GeForce 产品都只有一个 NVDEC 引擎
- 为 4:2:2 硬解而换 50 系显卡成本较高，**改成 4:2:0 拍摄是零成本方案**

**常见来源**：索尼 A7M4、A7S3、FX3 等机器的默认记录格式就是 10bit 4:2:2（XAVC S 4K 422 10bit）。

#### 影响

- 4:2:2 素材分析时**只能走 CPU 软解**，速度明显慢于 4:2:0
- 4K 10bit 4:2:2 是最吃 CPU 的格式之一，会让所有核心满载
- 程序**不会报错**，会静默回退软解，只是在任务管理器里能看到 CPU 占满、GPU 几乎不动

#### 解决办法

**在相机里把记录格式改成 4:2:0。**

以索尼 A7M4 为例：

菜单 → **影像质量/记录** → **文件格式** → 选择：

| 选项 | 说明 |
|------|------|
| XAVC S HD | 1080p，4:2:0 |
| **XAVC S 4K** | 4K，4:2:0 —— **推荐** |
| XAVC S-I 4K | 4K 帧内压缩，文件极大 |
| XAVC HS 4K | 4K HEVC，体积小 |

然后在**「记录设置」**里避开带 "4:2:2 10bit" 字样的档位。

> 具体菜单路径以固件版本为准，关键是**避开 4:2:2**。

#### 什么情况应该保留 4:2:2

4:2:2 的色度采样是 4:2:0 的两倍，这些场景确实需要它：

- **绿幕 / 蓝幕抠像** —— 边缘细节更好抠
- **精细调色** —— 色度信息更多，拉色后不容易断层
- **HDR 高动态范围后期**
- **需要多次转码的中间素材**

如果只是拍人像、日常、Vlog 混剪，4:2:0 完全够用。

#### 如何查看现有素材的格式

```bat
ffmpeg\bin\ffprobe.exe -v error -select_streams v:0 ^
  -show_entries stream=pix_fmt ^
  -of default=noprint_wrappers=1 素材.mp4
```

| 输出 | 判断 |
|------|------|
| `yuv420p` | 8bit 4:2:0，可硬解 ✅ |
| `yuv420p10le` | 10bit 4:2:0，可硬解 ✅ |
| `yuv422p10le` | 10bit 4:2:2，**无法硬解** ❌ |

#### 代码里怎么判断

`frame_pipe.hwaccel_available(path)` 会返回该素材能否硬解：

```python
from mixcut import frame_pipe

if frame_pipe.hwaccel_available("素材.mp4"):
    print("可以硬解")
else:
    print("只能软解")
```

也可以看像素格式：

```python
fmt = frame_pipe.probe_pix_fmt("素材.mp4")   # 例如 "yuv422p10le"
```

### 关键帧采样

画面质量指标（清晰度、亮度、对比度、噪点）都是单帧计算的，因此默认**只解码关键帧（I 帧）**，跳过所有 B/P 帧。

```python
iter_frames(path, keyframes_only=True)   # 默认开启
```

- 大幅减少解码量
- 若素材关键帧过于稀疏，可传 `keyframes_only=False` 关闭

### 相关参数

`mixcut/frame_pipe.py` 的 `iter_frames()` 提供这些控制项：

| 参数 | 说明 |
|------|------|
| `fps` | 抽帧频率，默认 0.6（每 5 秒 3 帧） |
| `max_side` | 输出最长边像素，默认 960 |
| `start` / `duration` | 只处理指定区间，长素材可分段 |
| `use_hwaccel` | `True` 强制硬解 / `False` 强制软解 / `None` 自动 |
| `keyframes_only` | 是否只解关键帧，默认 `True` |

### 性能基准

`tools/benchmark_frames.py` 可以测自己机器上两种解码方式的耗时：

```bat
py -3.14 tools\benchmark_frames.py 你的素材.mp4
```

### 内存

- 单个片段的帧缓冲处理完即释放，不随片段数累积
- 缩略图缓存有上限（LRU 淘汰）
- 超长 BGM 自动分块分析，内存峰值与音频总时长解耦

---


## 回归测试

全部是离屏（offscreen）测试，不依赖真实视频素材：

```bat
py -3.14 tests\test_analyze_folders.py
py -3.14 tests\test_analyze_stats.py
py -3.14 tests\test_library_drag_drop.py
py -3.14 tests\test_library_preview.py
py -3.14 tests\test_draft_compat.py
py -3.14 tests\test_ui_shell.py
```

| 测试文件 | 覆盖内容 | 断言数 |
|---------|---------|--------|
| `test_analyze_folders.py` | 文件夹归类、置顶排序、多选拖动、混剪池、取消选择 | 65 |
| `test_analyze_stats.py` | 顶部统计卡按本轮口径 | 7 |
| `test_library_drag_drop.py` | 素材库拖拽进文件夹、右键多选移动 | 13 |
| `test_library_preview.py` | 归类可见性、预览器不后台出声 | 16 |
| `test_draft_compat.py` | 生成的剪映草稿字段与结构兼容性 | 34 |
| `test_ui_shell.py` | 界面骨架、页面切换、导入→卡片→分析→混剪→草稿全流程 | 26 |

**全部通过 = 界面与数据逻辑正常。**

> `test_ui_shell.py` 会真实调用 ffmpeg 合成测试素材，
> 完整跑一遍分析 + 混剪（约 1 分钟），因此是端到端级别的验证。

---

## 常见问题

### 草稿在剪映里不显示

1. 确认草稿目录内有 `draft_content.json` 和 `draft_meta_info.json`
2. 重启剪映
3. 检查剪映版本是否过旧

### 素材提示无效

支持格式：`mp4 / mov / mkv / m4v / avi / webm`

文件需非空，且 ffprobe 能读取。

### 分析到一半程序退出

**多半是内存不足。**

排查步骤：
1. 检查系统虚拟内存（页面文件）是否足够，建议 ≥ 64 GB
2. 分批分析，不要一次性加载几百 GB
3. 关掉其他占内存的程序

### 分析很慢

**按影响大小排查：**

**① 查看素材像素格式**

```bat
ffmpeg\bin\ffprobe.exe -v error -select_streams v:0 ^
  -show_entries stream=pix_fmt ^
  -of default=noprint_wrappers=1 素材.mp4
```

| 输出 | 含义 | GPU 硬解 |
|------|------|---------|
| `yuv420p` | 8bit 4:2:0 | 支持 |
| `yuv420p10le` | 10bit 4:2:0 | 支持 |
| `yuv422p10le` | 10bit 4:2:2 | 不支持，走软解 |

如果输出 `yuv422p10le`，可以在相机里把记录格式改成 4:2:0。

**② 分析模式**

| 模式 | 采样帧数 | 相对速度 |
|------|---------|---------|
| 快速 | 3 帧/段 | 最快 |
| 中度 | 6 帧/段 | 中 |
| 严格 | 12 帧/段 | 最慢 |

先用**快速模式**粗筛，需要精筛再换严格。

**③ 预览代理**

代理只影响播放流畅度，不影响分析速度。

**④ 硬盘**

顺序解码要读满整个文件，机械硬盘会明显拖慢。

**跑一次基准看解码速度：**
```bat
py -3.14 tools\benchmark_frames.py 你的素材.mp4
```

### 预览卡顿

4K HEVC 原片解码压力大，在素材预览页开启**预览代理**。

### 修改代码后没生效

**本程序没有热重载。** 必须完全退出进程（任务管理器确认）再重新启动。

---

## 已知限制

1. **不渲染视频** —— 只生成草稿，导出在剪映里做
2. **10bit 4:2:2 无法 GPU 硬解** —— NVIDIA GeForce 消费卡的硬件限制，会自动回退软解。
   建议拍摄时改用 4:2:0，详见 [性能特性](#性能特性)
3. **关键帧采样要求素材 I 帧足够密** —— 默认每 5 秒至少 3 个关键帧
4. **大素材内存占用高** —— 数千片段时卡片对象仍占内存，建议分批
5. **字体不随仓库分发** —— 程序使用系统自带「Microsoft YaHei」
6. **OpenCLIP 需单独下载模型** —— 1.7 GB，不放则功能跳过

---

## 开发说明

### 提交代码

```bat
git add .
git commit -m "类型: 中文描述"
git push
```

提交信息推荐用中文，格式：`类型: 描述`

- `feat` —— 新功能
- `fix` —— 修复 bug
- `perf` —— 性能优化
- `docs` —— 文档
- `refactor` —— 重构

### 新旧界面边界

主界面由 `mixcut/ui/demo/` 下的页面承载。改造过程中遗留的旧页面实例
已于 2026-11-19 全部移除，**下面这些属性不存在了，不要再引用**：

| 已移除 | 说明 |
|---|---|
| `page_library` | 旧素材库页实例 |
| `page_mix` / `page_settings` / `page_engine` / `page_workbench` | 其它旧页面 |

**注意两个同名类**：

```
mixcut/ui/library_page.py        → LibraryPage（旧，实例已移除）
mixcut/ui/demo/pages_library.py  → LibraryPage（新，正在使用）
```

**当前有效的对象**：

| 属性 | 职责 |
|---|---|
| `demo_library` / `demo_params` / `demo_workbench` / `demo_settings` / `demo_tasks` | 各可见页面 |
| `page_analyze` | 分析页 |
| `selection` | 素材勾选状态（`MaterialSelection`） |
| `probe_worker` | 后台探测线程 |
| `tree` | 素材树 |

素材勾选状态统一走 `self.selection`：

```python
self.selection.checked_paths()   # 读，返回副本
self.selection.set_checked(paths)
self.selection.count()
self.selection.reconcile(existing)
```

### 关键模块说明

**`frame_pipe.py`** —— 视频解码的核心
- `iter_frames()` —— 顺序产出 `(时间戳, BGR 帧)`
- `hwaccel_available()` —— 判断能否硬解
- `keyframes_only` —— 是否只解关键帧（默认开）
  - 关键帧模式下会**逐字节去重**：`fps` 滤镜遇稀疏 I 帧时会复制帧补位，
    实测约 14% 是重复帧，会让人为的「零差异」污染 motion/抖动指标
  - 若该模式下**一帧都没有**（素材只有极少数 I 帧），自动回退普通解码重跑

**`bad_shot_detector.py`** —— 画面质量评估
- `analyze_video()` —— 按窗口分析整条视频
- `_eval_frame()` —— 单帧指标计算

**`musicstudio.py`** —— BGM 分析
- `analyze_bgm()` —— 节拍检测，长音频自动分块

**`appkit/selection.py`** —— 素材勾选状态
- `MaterialSelection` —— 勾选状态的唯一持有者，挂在 `MainWindow.selection` 上
- `checked_paths()` / `set_checked()` / `add()` / `discard()` / `reconcile()`
- 旧素材库页的 `_checked_paths` 是转发到它的 property，两处语义一致

**`appkit/autosave.py`** —— 自动保存与崩溃恢复
- 60 秒间隔、仅在工程有改动时写入
- 写到 `_project_backups/<工程名>.autosave.mixcut`
- 主文件损坏时优先尝试从这里恢复

---

## 许可证

未指定。如需开源分发，请先补充 LICENSE 文件。
