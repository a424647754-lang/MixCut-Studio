# MixCut Studio

Windows 桌面版智能混剪工具（Python 3.14 + PySide6）。素材导入后自动分析画面质量，剔除废镜头，按参数随机装配片段、配乐卡点、规则化转场，最终生成**可在剪映中继续二次编辑的草稿工程**。

---

## 功能概览

### 素材库
- 添加视频 / 递归扫描文件夹导入素材
- 按文件夹分类管理，支持拖拽移动、多选批量操作
- 卡片网格浏览，显示缩略图、时长、分析状态
- 勾选素材决定参与后续分析的素材范围

### 素材预览
- 视频播放、进度拖动、上一段/下一段切换
- 预览代理（默认关闭，可选 720p / 960p / 1280p），降低 4K HEVC 素材的解码压力

### AI 分析
- 三档分析模式（采样密度与判废门槛不同）：

  | 模式 | 采样帧数 | 判废门槛 | 适用 |
  |---|---|---|---|
  | 快速分析 | 3 帧 | 差帧比例 ≥ 0.66 | 粗筛 |
  | 中度分析 | 6 帧 | 差帧比例 ≥ 0.50 | 日常推荐 |
  | 严格分析 | 12 帧 | 差帧比例 ≥ 0.34 | 精筛 |

- 判废基于本地 OpenCV 客观规则（黑场/白场、模糊、曝光、手抖），不依赖网络
- 长视频按 **5 秒固定窗口**分段，短于 5 秒的整段处理；每个子片段独立评分
- 片段级缩略图（后台抽帧）
- **混剪池**：片段级进出控制，可勾选/移出、按文件夹分组整组启停
- **文件夹管理**：新建、重命名、删除（片段回到已分析库，不删结果）、拖拽归类、右键归入
- 多选：`Shift` 连选 / `Ctrl` 点选；多选后拖动整组带走；点空白处或按钮取消选择
- 片段右键：加入/移出混剪池、加入文件夹、标记不参与

### 参数设置
- 输出画幅（横屏/竖屏/方形/4:3/21:9 等）与分辨率
- 目标时长 ± 浮动容差
- 片段最短/最长时长、是否允许重复使用素材
- 生成工程数（一次批量生成 N 个不同随机排列的草稿）
- 配乐：手动选 BGM / 本地随机 / 关闭；人声闪避
- 转场：短镜头硬切、长镜头叠化，可设黑名单分类

### 任务中心
- 混剪任务进度、暂停/继续/取消
- 输出草稿列表与打开目录
- 运行日志

### 设置
- 运行环境检测（GPU / CUDA / ffmpeg / 草稿目录）
- OpenCLIP 模型加载
- 预览代理分辨率

---

## 技术栈

| 组件 | 用途 |
|---|---|
| PySide6 6.12 | 界面 |
| OpenCV 5.0 | 画面质量分析 |
| pyJianYingDraft 0.3.1 | 剪映草稿生成 |
| librosa 1.0 + soundfile | 配乐节拍检测 / 人声闪避 |
| open_clip_torch 4.2.1 | 语义检索（可选） |
| numpy / scipy / numba | 数值计算 |
| ffmpeg / ffprobe | 抽帧、探测、代理转码 |

---

## 环境要求

- **Windows 10 / 11**
- **Python 3.14**（务必用 `py -3.14`，系统 PATH 里的 `python` 可能指向未装依赖的版本）
- 依赖见 `requirements.txt`，核心安装：

```bat
py -3.14 -m pip install -r requirements.txt
```

> OpenCLIP 语义检索为可选功能。需要时把模型权重放到 `OpenCLIP/open_clip_pytorch_model.bin`（约 1.7 GB），未放置时该功能自动跳过。

---

## 从源码启动

双击 `启动MixCut.bat`，或命令行：

```bat
py -3.14 main.py
```

> **修改源码后必须完全退出进程再重新启动**，本程序没有热重载。

---

## 目录结构

```
main.py                    主窗口与全部后端逻辑（体积较大，待拆分）
mixcut/
├─ analysis.py             素材分析入口
├─ bad_shot_detector.py    画面质量检测（OpenCV）
├─ filter_engine.py        废镜头过滤引擎
├─ mixbuilder.py           混剪时间轴装配
├─ project_builder.py      剪映草稿落盘
├─ timeline.py             中立时间轴中间格式
├─ preview_proxy.py        预览代理调度
├─ workers.py              后台任务线程
├─ proxy/                  代理转码（编码探测、缓存、服务）
└─ ui/
   ├─ analyze_page.py      AI 分析页（130 KB，待拆分）
   ├─ library_page.py      旧素材库页（后台兼容保留）
   ├─ demo/                主界面各页
   │  ├─ pages_library.py  素材库
   │  ├─ pages_work.py     素材预览
   │  ├─ pages_params.py   参数设置
   │  ├─ pages_tasks_real.py 任务中心
   │  ├─ pages_settings.py 设置
   │  ├─ backend_bridge.py 新旧后台适配层
   │  └─ window.py         侧边栏与顶栏
   └─ fonts/msyh.ttf       中文字体（必需）
tests/                     回归测试
ffmpeg/bin/                ffmpeg2 运行时（自包含）
OpenCLIP/                  可选模型与 venv
```

> **不进版本库**：`OpenCLIP/`、`ffmpeg/`、`engine/`、`cache/`、`dist/`、`build/`、`*.bak-*`、素材文件。详见 `.gitignore`。

---

## 工程输出

草稿默认写入剪映草稿目录：

```
%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft
```

可在参数页指定自定义输出目录。每个草稿包含：

- `draft_content.json` / `draft_meta_info.json` — 剪映工程
- `mixcut_timeline.json` — MixCut 时间轴，可回溯再编辑
- `draft_agency_config.json` — 剪映代理配置

---

## 打包

需要重新生成安装包时：

1. 确认 `ffmpeg/bin/` 下有 `ffmpeg.exe` 与 `ffprobe.exe`
2. 运行 `build_windows.bat`
3. 产物在 `dist\MixCutStudio\`，**分发时复制整个文件夹**，不要只复制 exe
4. 需要安装包时用 `MixCutStudio.iss`（Inno Setup）

> `build/`、`dist/`、`installer_output/` 均为产物目录，不进版本库。

---

## 开发说明

### 回归测试

测试为离屏（offscreen）模式，不依赖真实视频素材：

```bat
py -3.14 tests\test_analyze_folders.py
py -3.14 tests\test_library_drag_drop.py
py -3.14 tests\test_library_preview.py
py -3.14 tests\test_analyze_stats.py
```

| 测试 | 覆盖内容 | 断言数 |
|---|---|---|
| `test_analyze_folders.py` | 文件夹归类、置顶排序、多选拖动、混剪池、取消选择 | 65 |
| `test_library_drag_drop.py` | 素材库拖拽进文件夹、右键多选移动 | 13 |
| `test_library_preview.py` | 归类可见性、预览器不后台出声 | 16 |
| `test_analyze_stats.py` | 顶部统计卡按本轮口径 | 7 |

### 已知限制

1. **不是热更新**：改源码后必须重启进程。
2. **未提交大依赖**：`OpenCLIP/`、`ffmpeg/` 需自行放置，版本库只有代码。
3. **分析内存占用**：大素材（数十 GB）分析时内存峰值较高，建议调大系统虚拟内存。
4. **OpenCV 无 CUDA**：当前为 pip 版，GPU 仅参与 OpenCLIP 推理，画面分析走 CPU。
5. **混剪池按轮重置**：每次开始新一轮分析，混剪池会切换为仅含本轮素材，避免上一轮结果混入。

---

## 常见问题

**草稿在剪映里不显示**
确认草稿目录内有 `draft_content.json` 与 `draft_meta_info.json`，并重启剪映。

**素材提示无效**
支持 `mp4 / mov / mkv / m4v / avi / webm`，文件需非空且可被 ffprobe 读取。

**分析大素材时程序退出**
多半是内存不足。调大系统虚拟内存（页面文件），或分批分析。

**预览卡顿**
4K HEVC 原片解码压力大，在素材预览页开启预览代理（720p / 960p / 1280p）。

---

## 许可证

未指定。如需开源分发，请先补充 LICENSE。
