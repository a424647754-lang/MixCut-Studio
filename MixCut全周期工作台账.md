# MixCut 全周期工作台账

- 统计范围：项目第一个任务 ～ 2026-11-17（含 Phase 1 镜头边界检测专项提速、四个链路 BUG 修复、彻底移除 Qwen3-VL 本地大模型链路、2026-11-16 新 demo UI 迁移与工程 v3 修复、2026-11-17 收尾清理）
- 主语言/框架：Python 3.14 + PySide6
- 状态标记：
  - **【已完成】** 功能做完，有测试或真机验证
  - **【待审核】** 代码完成，但 offscreen 测不了真实画面/声音，需用户真机确认
  - **【未完成】** 明确没做或未开始
  - **【待完善】** 已做但有已知局限/坑

---

# 第一阶段：基础框架（UI 重构）

| 编号 | 任务 | 对应文件 | 状态 | 说明 |
|---|---|---|---|---|
| 1 | 全局主题系统 | `mixcut/ui/theme.py` | 已完成 | DaVinci 黑白灰色板，含 bg/panel/panel_alt/border/text/muted/faint/accent 等键 |
| 2 | 主窗口三栏布局 | `mixcut/ui/shell.py`、`nav.py`、`topbar.py` | 已完成 | 左导航+中央工作区+右属性面板，QSplitter 分栏 |
| 3 | 素材库页面 | `mixcut/ui/library_page.py` | 已完成 | Lightroom 风格浏览、文件夹导航、勾选、多选、右键、拖拽、双击预览；已修卡片/列表切换、滚轮误选、Ctrl 不播放等 |
| 4 | 超级混剪配置页 | `mixcut/ui/mix_page.py` | 已完成 | 最长片段、时长策略、卡点密度参数，含"开始混剪"按钮 |
| 5 | 任务中心 | `mixcut/ui/task_page.py` | 已完成 | Premiere 队列风格，运行/等待/完成/失败任务，带暂停取消 |
| 6 | 分析页（初版骨架） | `mixcut/ui/analyze_page.py` | 已完成（后被第三阶段重做） | 初期仅统计+文件列表，showEvent 自动刷新 |
| 7 | 输出页 | `mixcut/ui/output_page.py` | 已完成 | 导出记录列表+打开文件夹 |
| 8 | 启动界面 | `mixcut/ui/splash.py` | 已完成 | 深色背景+圆角，无进度条，逐行加载状态 |
| 9 | 工程保存 | `mixcut/project_builder.py` | 已完成 | `.mixcut`（JSON），存素材路径+混剪参数，支持打开/保存 |

---

# 第二阶段：性能与媒体底层

| 编号 | 任务 | 对应文件 | 状态 | 说明 |
|---|---|---|---|---|
| 10 | P1 性能优化 | `main.py`、`workers.py` | 待完善 | NVDEC 硬件解码、素材并发 2 路、ffprobe 合并探测；pip 版 OpenCV 无 CUDA，实际硬解能力受限 |
| 11 | P2 场景识别 | `mixcut/scene_analyzer.py` | 已完成 | 三轮自适应采样检测 |
| 12 | P3 代理文件 | `mixcut/ffmpeg_tools.py`、`workers.py` | 已完成 | 代理机制+视觉特征提取，NVENC 硬件编码 |
| 13 | 废镜头检测（OpenCV 规则层） | `mixcut/bad_shot_detector.py` | 已完成 | 模糊/虚焦、过曝欠曝、黑场白场、手抖/运动模糊、噪点；阈值已在实际 MOV 上校准 |
| 14 | AI 助手页面 | 原 `mixcut/ui/ai_page.py` | 已删除 | 2026-11-11 用户拍板下线：文件、`main.py` 注册、`nav.py` 导航项一并移除，冒烟测试确认无残留 |

### 早期已交付的成片能力（README 记录，当前仍保留相关模块）
| 编号 | 任务 | 对应文件 | 状态 | 说明 |
|---|---|---|---|---|
| 14a | 顺序粗剪（黑屏/静止/静音检测） | `mixcut/analysis.py`、`filter_engine.py` | 已完成 | blackdetect/freezedetect/silencedetect，审核表逐条勾选 |
| 14b | 智能混剪（随机+配乐+转场+闪避） | `mixcut/mixbuilder.py`、`musicstudio.py`、`transitions.py` | 已完成 | 随机取片、librosa 卡点、音频闪避、规则转场、黑名单 |
| 14c | 剪映工程导出 | `mixcut/timeline.py`、`project_builder.py` | 已完成 | 剪映草稿+MixCut 时间轴 JSON，可二次编辑 |
| 14d | Remotion 全自动成片 | `remotion_engine/`、`splash-anim/` | 待完善 | 一键出 mp4，用户最终放弃该启动方案，代码保留 |

---

# 第三阶段：AI 能力接入与分析深化（本轮重点）

| 编号 | 任务 | 对应文件 | 状态 | 说明 |
|---|---|---|---|---|
| 15 | 长视频多重画面分析 | `analyze_page.py` | 已完成 | 短视频(≤5s)不切分；长视频按真实镜头切多子片段，每段独立 OpenCV 采样+VL，一段出一条 |
| 16 | Qwen3-VL 真实链路接入 | `analyze_page.py`（`_ask_vl`、`_parse_vl_content`） | 已完成 | 修复漏 import json；兼容 markdown/混杂文本提取 JSON；真机返回评分/动作/景别/服装/标签 |
| 17 | 镜头检测提速 | `analyze_page.py`（`_fast_scene_detect`） | 已完成 | ① ffmpeg 管道替代 OpenCV 逐帧解码，12 秒→2.5 秒；② Phase 1 专项（2026-11-11）：双缓冲零拷贝 + L1/L2 两级检测（L2 仅 8.1% 帧）+ 黑场兜底 + MIN_LEN 时长化，ffmpeg 9.0 下 3.04s→2.20s，5 素材零漏切零多切；默认运行时已切项目内 ffmpeg 9.0.2，详见 `Phase1_镜头边界检测提速报告.md` |
| 18 | 独立缓存层+动态模式切换 | `analyze_page.py`（`AnalysisCache`、`AnalysisWorker`） | 已完成 | 帧级缓存不随 worker 销毁，分析中可切模式，已分析片段复用不重算 |
| 19 | 停止反馈 | `analyze_page.py`（`_stop_analysis`、worker 检查点） | 已完成 | 原 `_StopDialog` 常驻弹窗已删除；点停止立即提示“正在取消…”、无弹窗，worker 在 `_process`/VL 循环/批量读帧多处检查点快速退出，结束显示“已取消” |
| 20 | 预览补全 | `analyze_page.py`（`_PreviewVideo`、`eventFilter`） | 待审核 | 双击播放/再双击暂停、补音频输出、独立全屏、Esc 应用级监听退出；单击仍定位静态帧 |

---

# 第四阶段：分析/混剪链路 BUG 修复（2026-11-11）

| BUG | 问题 | 根源 | 修复 | 验证 |
|---|---|---|---|---|
| 1 | 点停止后“正在停止中”弹窗永久卡中央、切界面关不掉 | 弹窗关闭只靠 worker 跑到末尾发 `worker_stopped`；`_process` 长任务（VL 单帧 7～22s、HTTP timeout 120）全程无停止检查点，阻塞时信号迟迟不发 | 删 `_StopDialog`；点停止立即“正在取消…”反馈；worker 在 `_process` 开头、两处 VL 循环每帧间、`_read_batch` grab 循环加 `_stop` 检查点；退出后显示“已取消，已分析片段保留” | 冒烟测试通过；真机手感待确认 |
| 2（致命） | 分析完无法开始任务、不能自动剪辑生成剪映草稿 | 唯一按钮文本“开始任务”但实际是 `clear_cache=True` 重新分析，职责重叠；自动混剪依赖 `material_confirmed`/`mix_selector` 同步，链路易断 | 拆成两个按钮：“开始分析”（只分析、复用缓存）与“开始混剪”（有结果才启用，直接 `refresh_selectors` + `start_mix` 生成剪映草稿，不重新分析） | 冒烟测试通过；真机出草稿待确认 |
| 3 | 同素材同片段再次分析仍重新推理 | `_launch(clear_cache=True)` 每轮 `self._cache = AnalysisCache()` 重建，帧级缓存被清空 | 去掉重建逻辑，页面持有持久 `AnalysisCache`，worker 命中 cv/vl cache 即跳过；仅复用已有实例 | 冒烟测试确认缓存实例不被替换 |
| 4 | AI 助手页面要彻底删除 | 注册点 `main.py`、导航项 `nav.py`、文件 `ai_page.py` | 三处全部移除，确认无其他代码引用 | 冒烟测试确认页面/导航无 ai |
| 5（真机复现） | 点“开始混剪”后卡在“智能筛选引擎检测中”，十分钟无响应、不确定能否出草稿 | 两层：① `scene_analyzer` 的 ffmpeg 用 `stderr=PIPE` 却从不读取，ffmpeg 写满约 4KB 管道缓冲后阻塞，Python 只等 stdout，**管道死锁**；② 原 cuda 全管道（`hwaccel_output_format cuda`+`scale_cuda`）在该批素材实测 `hwaccel initialisation returned error`，硬解混合方案又无速度收益 | ① 两处 Popen 的 stderr 改 `DEVNULL` 直接丢弃，死锁解除；② 场景分析默认走 CPU 软解（`analyze` 默认 gpu=False、`filter_engine` 传 gpu=False）；③ 粗采/精采各加 120s/30s 看门狗，ffmpeg 异常挂起即强杀，不再永久卡 | 死锁复现 120s 挂起 → 修复后单文件 3s、两素材完整 `run_filter` 12.6s 无错误；旧死锁进程已清理 |

说明：正在进行的单个 VL HTTP 请求无法被 Python 线程硬中断，点停止后会等当前这一帧推理结束（实测 7～22s）即停，不会再出现弹窗永久卡死；停止反馈与弹窗生命周期已与 worker 线程退出解耦。

补充：全项目排查其余 `stderr=PIPE`，均为 `subprocess.run`/communicate 自动抽干、或 `detect_all` 有专门抽 stderr 线程、或 `-loglevel error` 正常无输出，无同类死锁。

---

# 第五阶段：彻底移除 Qwen3-VL 本地大模型（2026-11-11）

用户拍板：分析不再调用本地 Qwen3-VL。理由是逐片段推理慢、GPU 占用高，且打包分发还要求用户额外安装 Ollama 和数 GB 模型。判废完全由本地 OpenCV 规则层承担。

| 改动点 | 内容 |
|---|---|
| 删除依赖 | `import requests`、`import base64`、`import json`（删后已无引用）；常量 `OLLAMA_URL`、`MODEL_NAME` |
| 删除方法 | `_ask_vl`、`_parse_vl_content`、`_encode_frame` |
| 删除缓存 | `AnalysisCache.vl_cache`；`_required_frames` 不再产出 VL 帧，只返回 OpenCV 采样帧 |
| 判废改造 | `_process` 去掉两段 VL 循环，只做 OpenCV 客观评估；cv 帧额外存 `penalty`（`BadShotDetector._bad_score`）；新增 `_cv_quality` 用客观罚分合成 0-100 画质分顶替原 AI 评分；判废依据为差帧比例 `bad_ratio` + 具体客观原因，不再有“AI 评分偏低” |
| 配置精简 | 三档模式删掉 `vl_positions`、`vl_confirm_band`、`score_cut`，保留 `opencv_samples`、`bad_ratio`，三档差异仍在 |
| UI 精简 | 列表行只显示客观画质分，去掉动作/景别/标签；预览信息去掉语义描述兜底 |
| 孤立副本 | 删除 `mixcut/ui/fonts/analyze_page.py`（无任何 import；fonts 目录保留，`msyh.ttf` 仍被 theme.py 使用） |
| 测试 | 删除 VL 专用 `tests/test_think_balanced.py`；全量 py_compile 通过，冒烟、镜头检测回归（零漏切零多切、最大偏差 0.003s）通过，worker 端到端跑通、缓存复用正常 |

实测性能（基准 4K 竖屏 30s 素材、11 片段）：删 VL 后 fast 19.4s / medium 41.4s / strict 71.7s。剖析显示瓶颈已不是模型，而是 4K 全分辨率帧上的 OpenCV 滤波算子（Laplacian、medianBlur 约占 78%，单帧约 84ms）与解码（约 22%）。

后续可提速方向（待用户拍板）：在缩小帧（宽 320/480）上做 OpenCV 评估，单帧算子从 84ms 降到 1.7～5.5ms（约 15～50 倍）。亮度类阈值小图与全图一致、可直接复用；但拉普拉斯（小图反增约 44 倍）、噪点、运动阈值随分辨率变化，必须在真实素材上重新校准、保证判废结论一致，不做无差别减帧。

---

# 第六阶段：删除镜头边界检测，改用固定窗口分段（2026-11-11）

用户拍板：完全删除“镜头边界检测”阶段。真机实测该阶段在长视频上既慢又无效；判废仍逐段进行，只是分段方式从“按真实镜头”改为“按固定时长”。

真机实测（C2915.MP4，105.6s，4K）：
- 原 `_fast_scene_detect`：耗时 66.79s，却只切出 1 段（固定机位连续素材，阈值不触发、无切点），时间纯浪费。
- 固定窗口 `_fixed_windows`：0.01ms，切出 22 段，每段 5 秒，末段 (105.0,105.6055) 强制收尾到结尾，完整覆盖、不重不漏。

| 改动点 | 内容 |
|---|---|
| 分段逻辑 | `_split_segments` 去掉 `_fast_scene_detect` 调用，长视频直接走 `_fixed_windows`（每 5 秒一段、收尾到 duration）；短视频(≤5s)仍单段 |
| 删除方法 | `_fast_scene_detect`、`_l1_diff`、`_l2_metrics`、`_hist_hs`、`_pixel_dist` |
| 删除常量 | `SCENE_WIDTH/SCENE_THRESHOLD/SCENE_MIN_LEN/SCENE_MIN_DURATION`、`L1_THRESHOLD/L2_MOTION_CHANGED/L2_MOTION_HIST`、`BLACK_BRIGHTNESS/BLACK_HIST_DIFF/FLASH_BRIGHTNESS_TOL` |
| 删除字段 | worker `_last_scene_stats` |
| 清理 import | 不再使用 `subprocess`、`time`、`find_ffmpeg`、`NO_WINDOW_FLAGS`（cv2/numpy 及 probe_* 保留） |
| 文案 | 进度提示由“正在检测镜头边界”改为“正在分段/准备素材分段” |
| 测试/脚本 | 删除镜头检测专属 `tests/test_phase12_pipe.py`、`tests/test_phase1_regression.py` 及基准脚本 `bench_scene_detect.py`、`calibrate_scene.py`、`bench_resolution.py`（`bench_results/` 历史数据保留） |

验证：全量 compileall 通过；`test_bugfix_smoke.py` 冒烟通过；C2915 端到端验证 22 段、末段覆盖结尾，首段与末段完整判废（画质分 100、included=True）。

已知权衡：固定窗口不再对齐真实镜头，可能在一个 5 秒窗口内跨镜头、或把同一镜头切到相邻两段，判废颗粒度变为固定 5 秒；但保证长视频从头到尾每段都被分析，不会再丢后面内容。临时诊断/验证脚本与备份已全部清理。

---

# 第七阶段：修复暂停/取消后计时器仍走、关闭软件被拦截（2026-11-11）

两个症状：① 点暂停或取消后，任务卡计时器继续走；② 关闭软件弹“任务仍在运行”拦截窗、无法退出。

根因：
- 计时器：`TaskRow.set_paused` 只改了按钮和状态，没有停止/冻结 `QTimer`，`_tick` 继续按 `time.time()` 累加。
- 关闭拦截：`_scenedetect_cuts` 调 `scene_analyzer.analyze` 时没透传 `cancel_check`，取消/关闭时线程阻塞在 ffmpeg 管道上，只能等 30/120 秒看门狗，超过 `closeEvent` 的 7 秒等待。

| 改动点 | 内容 |
|---|---|
| 计时器冻结（`task_page.py`） | 新增 `_pause_started`；暂停时记录时刻、`timer.stop()` 冻结显示；继续时把暂停时长补偿到 `started` 再 `timer.start()`，耗时只统计实际运行时间 |
| 打通取消链路（`filter_engine.py`） | `_scenedetect_cuts` 增加 `cancel_check` 参数并透传给 `sa.analyze`；`run_filter` 在 scenedetect/videoscope/ffmpeg 各检测阶段前检查取消，已取消即 `break`、不再启动后续子进程 |

验证（C2915 长视频，offscreen）：
- 暂停后计时冻结：暂停 2.5 秒耗时显示不变，继续后恢复。
- 场景分析取消：从原来等 30～120 秒降到 **1.2 秒**内退出。
- 模拟关闭：在 1.5/3/5 秒三个不同时机发取消，线程分别在 **0.16/0.77/0.05 秒**退出，远小于 7 秒阈值，不再弹拦截窗。
- 全量 compileall、`test_bugfix_smoke.py` 冒烟通过；临时验证脚本已全部清理。

---

# 第八阶段：专业 Windows 安装包（2026-11-11）

目标：把 PyInstaller 文件夹封装成带向导的单个 Setup.exe，可分发、可卸载。

工具与配置：
- 安装 **Inno Setup 7.1.1**（官网 jrsoftware.org，本机 GitHub 直连不通，经 gh-proxy 镜像下载，静默安装）。
- 修正路径：`MixCutStudio.spec` 的 binaries 与 `build_windows.bat` 的 FFDIR 由老 `ffmpeg-20200831` 改指 `ffmpeg\bin`（9.0）。
- 新增 `MixCutStudio.iss`：固定 AppId、64 位、现代向导、lzma2 固实压缩、简体中文+英文、开始菜单与可选桌面快捷方式、完整卸载项、安装后可选启动。
- 自定义图标：用户提供的黑底白 M 图（`Desktop\1.png`，RGBA）居中裁成正方形，用 Pillow 生成多尺寸 `assets\app.ico`（16/24/32/48/64/128/256）；spec 的 EXE 加 `icon`、iss 加 `SetupIconFile`，软件与安装包统一使用该图标。

构建与真机验证（真实数据）：
- PyInstaller 构建成功，`dist\MixCutStudio` 共 1492MB；`check_frozen_build` 通过；ffmpeg/ffprobe 落在 `_internal`，运行时经 `sys._MEIPASS` 定位。
- GUI 真机导入 `_short_video.mp4`，正确解析“2s · 720x1280”，确认 ffprobe 调用正常。
- ISCC 编译耗时 245 秒，产物 `installer_output\MixCutStudio_Setup_v1.0.0.exe`，**413.5MB**；已复制一份到桌面。
- 静默安装验证：安装目录 1497.6MB，exe、ffmpeg、开始菜单快捷方式、卸载注册表项（DisplayName “MixCut Studio 1.0.0”）齐全；安装版启动正常（410MB）。
- 静默卸载验证：安装目录、开始菜单、注册表全部清除、无残留。
- 图标重打包：删除旧安装包后重新构建，PyInstaller 日志确认“Copying icon to EXE”，ISCC 重编译 241 秒，新产物 **413.6MB**；提取 exe/Setup 图标核对为黑底白 M，静默安装后软件与快捷方式图标均正确，再次卸载无残留；新包已复制到桌面。

已知项：未做代码签名（无证书），用户首次运行可能遇到 SmartScreen“未知发布者”提示；自定义图标已配置完成。

---

# 第九阶段：接入整句切割 / 指纹查重 / CUDA 引擎桥（2026-11-13）

来源：把 Super Director（`D:\超级导演`）拆出来，只取与本项目互补的三件事，其余（Electron 客户端、4K 全分辨率镜头检测）不接。
原则：全部默认关闭，勾了才跑；引擎缺失时静默降级，不改变既有出片流程。

| 编号 | 任务 | 对应文件 | 状态 | 说明 |
|---|---|---|---|---|
| 21 | CUDA 引擎桥 | `mixcut/gpu_engine.py`（新增） | 已完成 | 定位 `sd_asr.exe` / `sd_dedup.exe`（CUDA 13.5 + OpenCV CUDA 编译）；查找顺序 环境变量 `MIXCUT_GPU_ENGINE` → 项目 `engine/bin` → `D:\超级导演\cpp\build\bin`；缓存按「素材名 + 路径摘要」命名，防不同文件夹同名素材撞车；兼容旧缓存文件名 |
| 22 | 整句切割 | `mixcut/sentence_cut.py`（新增）、`mixbuilder.py` | 已完成 | 用转写句尾作为合法切点，切点吸附限制在 [cursor+lower, cursor+upper] 内，不破坏最短/最长片段与剩余时长；起点做轻量吸附（最多让 0.75s） |
| 23 | 指纹查重 | `mixcut/audio_dedup.py`（新增） | 已完成 | CUDA 提 phash + 色彩直方图，Python 侧算相似度（不起 N² 次进程）；阈值 0.92，同组保留最长一条；时长比 >1.4 直接判不同片子 |
| 24 | 流程接线 | `mixcut/workers.py`、`main.py` | 已完成 | 分析完成后插入「查重 → 转写」两步，带进度与取消检查点（`_gpu_enrich`）；混剪参数区新增两个开关，状态经 `_mix_detect_options` / `mix_options` 传入 |
| 25 | 功能说明页 | `功能说明/新增三项能力.html` | 已完成 | 单页文档：切片条示意图 + 三项能力 + 改动范围 + 已知边界 |

实测（本机真实素材）：
- 转写：28 秒素材 3.5 秒出结果（whisper small / CPU），SRT 落 `%LOCALAPPDATA%\MixCut\cache\asr`
- 查重：同一文件副本相似度 1.000、不同素材 0.000，分组与「保留最长」判定正确
- 整句吸附：同素材同种子下，切点离句尾最大偏差由 1.37s 降到 0.11s（6 段全部对齐）
- 回归：`python -m compileall` 通过；`tests/test_bugfix_smoke.py` 冒烟通过；离屏实例化主窗口，两个开关默认关、勾选后选项正确传递

不在本轮范围：OpenCLIP 上 GPU（`torch 2.15.0+cpu`，需换 cu 版轮子并确认权重结构）

---

# 第十阶段：新 demo UI 迁移、工程 v3 与深度审计修复（2026-11-16 ～ 2026-11-17）

来源：旧后台 + 新 demo UI 合体后，按用户逐条验收意见改造界面，再做工程保存/恢复专项排查、全量深度审计和打包；最后清理 D 盘旧残留并收口台账。
原则：不重做工程系统、不大规模重构；在现有 `main.py` / `mixcut/ui/demo` / 旧后台基础上修，保证真实后台数据继续参与分析和混剪。

## 10.1 界面与交互

| 编号 | 任务 | 对应文件 | 状态 | 说明 |
|---|---|---|---|---|
| 26 | 工作区页面重排 | `mixcut/ui/demo/window.py` 等 | 已完成 | 「工作台」移到「素材库」下面并改名「素材预览」；「参数设置」与「AI 分析」位置对调；顶部导航顺序固定为 素材库 → 素材预览 → AI 分析 → 参数设置 → 任务中心 |
| 27 | 「混剪任务」改「任务中心」 | `pages_tasks_real.py`、导航 | 已完成 | 更名；参考 demo 的任务队列/输出结构重做布局，顶部新增全局完成进度条（不照搬 demo 的进度样式）；整体按专业剪辑软件导出页组织：队列、当前任务、输出目录、日志/结果 |
| 28 | 分析页按钮收敛 | `pages_analyze*.py`、`main.py` | 已完成 | 删除分析页重复的「开始分析」（素材库已有唯一入口）；把原来的白色「开始分析」改为「暂停」，接后台暂停/继续；删除分析页里的「开始混剪」 |
| 29 | 素材预览页按钮收敛 | `pages_preview*.py` | 已完成 | 删除素材预览里的「开始混剪」，混剪入口只保留参数页一处 |
| 30 | 参数页开始混剪门禁 | `pages_params.py`、`main.py` | 已完成 | 未完成分析时「开始智能混剪」始终灰色；分析完成信号 `analysisFinished` 到达后解灰；打开已分析工程时直接恢复可用状态 |
| 31 | 预览窗口缩放/全屏 | 预览相关页面 | 已完成 | 修复所有预览窗口「快捷键放大后不能再按快捷键关闭」；关闭后固定预览窗口不再消失；素材预览预览器右下角新增全屏按钮，快捷键与全屏行为统一 |
| 32 | 无边框窗口与顶栏 | `window.py`、`main.py` | 已完成 | 隐藏 Python 原生标题栏；修掉左上角残留白点；补窗口拖拽/缩放区域，解决「只能放大不能缩小」；顶栏与自定义标题栏不再互相压盖 |
| 33 | 字体统一 | `theme.py` / demo QSS | 已完成 | 全界面统一使用 demo 字体，不再混用系统默认字体 |
| 34 | 启动页与图标 | `mixcut/ui/splash.py`、`assets/` | 已完成 | 源码启动页换未来感品牌图并在下方增加真实加载进度；打包版使用用户给的 3 张图随机启动（`splash_build.png` / `splash_a.jpg` / `splash_b.jpg`）；应用图标、任务栏图标、安装包 SetupIcon 全部换成新 M 标 |
| 35 | 空态文案 | 分析页 | 已完成 | 改为「还没有分析结果，请先分析喵～(∠・ω< )⌒★」 |
| 36 | 设置页落地 | `pages_settings.py` | 已完成 | 运行环境自动读取本机 GPU / CUDA / 预览分辨率 / 色彩空间；草稿目录自动读取剪映草稿目录；移除「关于 MixCut Studio」；缓存与草稿不再由后台统计，改存源文件夹/安装目录本地；清理策略可调，最大 14 天 |

## 10.2 功能修复

| 编号 | 任务 | 对应文件 | 状态 | 说明 |
|---|---|---|---|---|
| 37 | 全删素材联动清空 | `pages_library.py`、`main.py` | 已完成 | 删除全部原素材和文件夹后，同时清空已勾选、确认名单、分析结果与已移除记录，不再残留旧片段 |
| 38 | 只分析勾选素材 | `main.py`、分析 worker | 已完成 | 素材库勾选后进分析页只分析勾选项，不再退回分析整个文件夹；勾选状态跨刷新保留 |
| 39 | 代理改为全局可选 | `pages_params.py`、预览/分析链路 | 已完成 | 不再一条视频一条视频单独选代理；参数页统一选「不代理 / 720 / 1280」，选代理后所有素材统一走代理，不选则用原视频 |
| 40 | 快捷键补全 | `main.py`、各页面 | 已完成 | `Ctrl+S` 保存工程（已保存过则直接覆盖更新）；`Ctrl+Z` 撤回上一步；`Ctrl+Y` 还原撤回；`Ctrl+N` 新建工程；预览全屏/缩放快捷键统一可用 |
| 41 | 参数恢复默认补全 | `pages_params.py` | 已完成 | 「恢复默认」同时恢复黑名单 chips、组件勾选、BGM、音乐目录、CPU/GPU，不再只重置 combo/spin/toggle/text |
| 42 | 模型重载 | `backend_bridge.py` | 已完成 | 「重新加载模型」改为真正调用 `openclip_embed.load_model(force=True)`，不再只探测一条素材 |
| 43 | OpenCLIP 收拢 | 源文件夹 | 已完成 | OpenCLIP 模型与 venv 收进 `OpenCLIP/` 一个文件夹，权重 `open_clip_pytorch_model.bin` 1.71GB，源码路径同步 |

## 10.3 工程保存/打开专项修复（v3）

保存侧审计：旧 `save_project()` 只写 `version:2 / materials / params`，新 UI 的素材树、分类、勾选、分析、筛选、新页参数均未写入。
打开侧审计：旧 `open_project()` 只回填旧参数和旧页勾选，不重建素材树、不刷新新 UI、不恢复分析；启动时默认 `reload_library()` 会把界面重置成空态。

修复后 v3 字段：
`version=3 / library{tree,root_name} / materials / materials_meta / selected / removed / removed_items / current_clip / analysis{mode,finished,items} / params / params_v3 / filters{folder_name,folder_path,chip,search,sort,min_dur,view} / ui{page,scroll} / player{aspect,position,volume} / task{rows,status,progress,output} / output.proxy_width / saved_at`

统一加载顺序：
读取工程文件 → JSON/结构/版本校验 → `backend.ensure()` 初始化工程上下文 → 还原素材树 → 刷新新 UI → 恢复筛选 → 恢复勾选/已移除/当前片段/确认名单 → `load_results()` 恢复分析 → 恢复新页参数并 `sync_params()` → 恢复代理宽度 → 恢复任务中心/工作台 → 恢复页面与滚动/播放器状态 → 刷新顶栏 → 标记已加载并计算脏检查哈希。

关键点：
- 打开工程时明确 `reload_library(preserve_selection=False)`，避免默认初始化覆盖工程数据。
- 已完成分析时补发 `analysisFinished`，参数页「开始智能混剪」直接解灰。
- 素材路径按原路径保存/读取，不转换相对路径；素材元数据 `materials_meta` 覆盖刷新后的探测值，标签/缩略图/时长/编码不丢。
- `library.tree is None` 时拒绝保存；分析 worker 运行中保存先弹确认，避免存半成品。
- 保存改为同目录临时文件 + `flush/fsync` + 旧工程 `.bak` + `os.replace` 原子替换；失败清理临时文件并提示。
- 坏工程/非法 JSON/错误 tree/错误字段统一 `QMessageBox` 提示并返回 False；后台桥不可用时打开直接拒绝，不再假成功。
- `_project_state_hash()` 只哈希工程内容，排除滚动位置、播放进度、异步探测元数据；保存/打开后 `dirty=False`；换工程/关闭软件有 保存/放弃/取消 三选。
- 新增 `new_project()` / `Ctrl+N` / 设置页「新建工程」：清空旧树、勾选、确认名单、分析、removed_clips，参数回默认，回素材库。
- 撤回历史扩展 `removed_items / analysis / params_v3 / filters / page / current_path`；撤回时先恢复树/已移除，再恢复勾选/确认/参数/分析/筛选/页面，删除素材后撤回也能恢复分析结果。

验收：
- 跨进程 A 存 B 开：素材 2、分析 2、勾选 2、确认 2、分类「精选」、搜索/筛选/排序、target/fps/黑名单全部恢复；恢复后真实混剪产出 `draft_content.json` 14,617 B，3 个轨道片段，总时长 9.69 秒。
- 设置页打开工程：素材 2、勾选 2、`selpaths=2`、分析 1、target 17、黑名单「模糊」，打开后 `dirty=False`。
- 新建工程：素材 0、勾选 0；删除后撤回：分析 1 条、标签 `['测试标签']` 保留、`removed_clips=0`。
- 原子保存二次写生成 `.bak`；坏工程返回 False 并提示「打开失败」；任务历史打开后任务行 1、状态「已完成」。

## 10.4 深度审计（21 条）与修复

P0：
1. 删除文件夹会把内部素材一起从工程素材库移走，但确认框写「素材回到全部素材」→ 改为按路径找目录，先把子树视频移回顶层再删文件夹；空子目录不再被误判成视频。
2. 设置页打开工程后多调一次 `reload_library()`，勾选被清空 → 去掉多余刷新；实测打开后 selected 由 0 恢复为 2。
3. 撤回不恢复分析结果 → `_history_state()` 增加 analysis；删除素材时不再丢结果，`Ctrl+Z` 恢复树同时恢复分析卡片。
4. 保存非原子 → 临时文件 + `.bak` + `os.replace`。

P1：
5. 任何刷新都清空勾选 → `apply_backend_snapshot(preserve_selection=True)` 按路径恢复；`main.reload_library()` 默认保留。
6. 嵌套子目录被压平成顶层名 → `snapshot()` 递归，根容器 `folder_path=""`、子目录 `p:<相对路径>`、clip 带 `folder_path`；文件夹卡片按 `parent/depth`，筛选按路径。
7. 「已移除」筛选永远为空 → `controller.removed_clips` 存完整快照，刷新时补回 `mock.CLIPS` 并标 `_removed`；`active_paths/_probe_paths` 排除 removed。
8. 坏工程直接抛原生异常 → JSON/结构/版本校验，统一提示并返回 False。
9. 后台桥不可用时打开仍假成功 → `_load_project_data()` 先 `backend.ensure()`，不可用拒绝并提示；`open_project_from()` 返回真实 bool。
10. 顶栏工程名写死「未命名项目」→ `_refresh_topbar()` 用 `Path(_project_path).stem`，无工程才显示未命名。
11. 没有「新建工程」入口 → `new_project()` + `Ctrl+N` + 设置页按钮。
12. 同名文件夹合并/选错 → 目录身份改相对路径 `p:<相对路径>`，folder id 不再按顶层顺序 `r1..rN`。

P2：
13. 分析中可保存半成品 → 保存前确认，避免 `finished=false` 入库。
14. 「恢复默认」不恢复黑名单/组件/BGM/音乐目录 → 保存默认值并全量重置。
15. 「云端随机（暂未开放）」死选项 → 用户明确要求保留，不修。
16. 「重新加载模型」只探一条素材 → 改为真正加载 OpenCLIP。
17. 打开/关闭无未保存提示 → 脏检查 + `closeEvent` 保存/放弃/取消；换工程前提示。
18. 工作区只存页面/当前片段/筛选 → v3 增加滚动位置、画幅、音量、播放位置。
19. 设置页保存工程空默认路径 → 预填当前 `_project_path`。
20. 标签无模型、元数据/缩略图重开重探、任务/输出历史只在内存 → 增加素材标签右键编辑、`materials_meta` 持久化、`TasksPage.capture_state/restore_state` 持久化任务行/状态/进度/输出路径。
21. 打包版过期 → 重新 PyInstaller + Inno Setup 打包。

回归结果：
- `py_compile`：`main.py`、`backend_bridge.py`、`pages_library.py`、`pages_settings.py`、`pages_params.py`、`pages_tasks_real.py` 全通过。
- 嵌套目录：folders 为 `全部素材 / mixcut_nested4(root) / 子目录A / 更深`；根目录可见 3，子目录可见 2；clip `folder_path` 为 ``、`子目录A`、`子目录A/更深`。
- 删除「子目录A」后 3 条素材全部回到全部素材，磁盘文件不删；勾选保留 1。
- 移除 1 条后「已移除」筛选可见 1；`controller.removed` 记录 1。
- 参数默认：target 30、黑名单回默认两项、BGM/音乐目录空、组件勾选回默认。
- `tests/check_frozen_build.py` 返回 `[PASS]`；打包版离屏启动 10 秒仍存活（PID 32068）。

## 10.5 预览性能排查与代理

- 排查 Qt Multimedia / FFmpeg 解码链路：4K HEVC 10bit 4:2:2 原片实时解码压力大，预览出现慢放/卡顿；`QT_MEDIA_BACKEND=windows` 预览无画面，`-hwaccel d3d11va` 对 4:2:2 10bit 无提升，均判死路。
- 落地可选代理：参数页统一选「不代理 / 720 / 1280」，不选就是原视频；选代理后所有素材统一转代理，不再逐条选择。
- 全屏快捷键与右下角全屏按钮同步修复，全屏退出后固定预览窗口保留。
- 已知边界：4K HEVC 10bit 4:2:2 不选代理仍可能卡，属源素材解码压力，不是工程数据问题。

## 10.6 打包与安装包

- PyInstaller：`dist\MixCutStudio\MixCutStudio.exe`，57,599,234 B，2026-11-16 17:21:31；`_internal` 内含 ffmpeg/ffprobe，目标机器无需另装 Python/Qt/FFmpeg。
- Inno Setup：`installer_output\MixCutStudio_Setup_v1.0.0.exe`，429,444,186 B，2026-11-16 17:26:59；旧 429,410,207 B 包删除后重打。
- 启动图：打包版 `_internal\assets` 内三张齐全，`splash.py` 仅 `sys.frozen` 时随机；源码运行仍用 `splash_main.png`，未改源文件启动图。
- 图标：exe 与安装包 SetupIcon 均为新 M 标；`ExtractAssociatedIcon` 返回 32×32；任务栏图标已修。
- 加密：PyInstaller `optimize=2`，不释放明文 `.py`，仅字节码归档；未使用 pyarmor trial（批量混淆会 `out of license`）。
- 安装包无需额外环境；当前注册安装为 `D:\111111111111\MixCutStudio`（DisplayName `MixCut Studio 1.0.0`）。

## 10.7 D 盘清理与台账收口（2026-11-17）

- 旧源码目录 12 份 `.md` 先归档到 `D:\mixcut源文件\归档_旧文档_20261116`，再删除旧目录。
- 旧 `MixCut_Skillbox_Test` 目录已确认无残留；不再关联的旧任务随新 UI 验收收口，不再单独保留。
- 已删除：`D:\MixCutInstallTest` 1,440.8 MB、`D:\mixcut_models` 1,631.9 MB、`D:\_gpt_check_20261115` 6.3 MB、`D:\MixCut_源码与功能说明_20261115` 1.3 MB、同名 `.7z` 0.3 MB；合计释放 3,080.6 MB。
- 保留：`D:\111111111111`（当前注册安装）、`D:\MixCut_Drafts`（默认草稿目录）、`D:\mixcut安装包`、`D:\mixcut源文件`。
- 台账事故：追加 1.9.24 时 `迁移进度台账.md` 被截断为 745 B；已按会话记录重建为 9,984 B，桌面与工程根同步，1.9.21 之后完整、1.9.20 以前仅保留摘要。此后新增本第十阶段，作为项目全周期主台账。

## 10.8 本阶段已知限制

1. 「云端随机（暂未开放）」按要求保留，不实现。
2. 源文件内启动图不改；只有打包版随机三张。
3. 打包版是 PyInstaller 字节码归档，不是商业级强混淆；需要更强保护要另上 pyarmor 商业授权。
4. 素材缩略图只存路径，系统清缓存后重开需按原素材重新抽帧；素材/分析数据不丢。
5. 任务/输出历史持久化的是当前任务中心行与输出路径，不是完整历史任务列表。
6. `closeEvent` 的保存分支在无工程路径时会弹保存对话框；离屏自动测试需 patch `QMessageBox`，真实窗口关闭流程仍建议人工过一遍。
7. 根文件夹 `folder_path=""`，调用方要用 snapshot 返回的 folder_path，不要自己拼根名。
8. `delete_folder` 只把视频节点移回顶层，空子目录随文件夹删除；若要保留空目录结构需另行定义。

---

# 第十一阶段：分析页文件夹 / 素材库预览 BUG 修复（2026-11-18）

起因：用户反馈“豆包做的版本全有 bug”，逐项列出 11 条问题并要求逐项修复。
全部改动在源码中完成，并补充了离屏回归测试；用户已重启真机验证通过。

## 11.1 问题清单与修复

| # | 用户报告 | 根因 | 修复 |
|---|---|---|---|
| 1 | 分析页把片段拖进文件夹后，在「全部素材」里还能看到 | `_rows_in_current_folder()` 在 `__all__` 时直接返回全部片段，未排除已归类片段 | 「全部素材」= 未归类片段；已归类只出现在各自文件夹；标题显示“另有 N 个在文件夹里” |
| 2 | 拖入后自动打开目标文件夹 | 拖入路径里顺带设置了 `_active_folder_id` 并刷新视图 | 拖入/右键归类保持当前视图；只有显式点「移入文件夹」才跳转 |
| 3 | 新建文件夹、移入、进入、返回 | `_pool_new_folder` 会把当前视图切到新文件夹 | 新建只建壳，不抢占视图；进入/返回/左侧栏点击已可正常切换 |
| 4 | 拖入片段不置顶、顺序乱 | 片段只有分析顺序，没有“最近归类”概念 | 新增 `_frag_recency/_frag_seq`，刚归类的排最前，同批按拖入顺序 |
| 5 | 空文件夹莫名出现在底部 | 文件夹卡片是独立横条，片段占满后它掉到下方 | 文件夹卡片与片段卡片**共用同一网格**，文件夹固定顶部整行 |
| 6 | 右键文件夹没有删除按钮 | — | 已有「删除文件夹」；补齐菜单入口并保留 |
| 7 | 多选后拖动只能带走一个片段 | 拖动时按行号实时取选区，可能已塌成单选 | `_drag_ids_for_row` 保留整组；拖动开始时不清空选区 |
| 8 | 移除混剪池只对鼠标点过的片段有效 | 移出时按“当前选中行”处理 | 移出只作用于点选片段，不移除整组 |
| 9 | 80 个分析片段，混剪池只有 32 个 | 池里存的是文件路径而非片段 id | 池改为片段级 `frag_id`；工程恢复时把旧路径展开成该文件全部分析片段 |
| 10 | 已经有「全部素材」，为什么还多一个「未分类」 | `pages_analysis.py` 的演示用混剪池弹窗用「未分类」兜底 | 该弹窗未在实际主界面使用；正式界面统一为「全部素材」 |
| 11 | 删除文件夹提示文案 | — | 改为「片段会回到已分析库，不会删除分析结果。」（带片段数） |

## 11.2 追加修复（同轮用户反馈）

1. **文件夹置顶排序**：文件夹按创建顺序固定，空文件夹永不沉底；新拖入片段置顶到该文件夹最前。
2. **文件夹卡片 UI 与片段一致**：由写死 `236×198` 改为 `固定高 198 + Expanding 宽`，与片段卡片同为 `234×198`；图标/字号随之放大。
3. **新建文件夹“闪一下”**：
   - `_refresh_folder_bar()` 原为 `clear()` 整表重建 → 改为按 id 增量同步（`_sync_folder_list_items`），实测新建时 `clear()` 调用 0 次；
   - `_pool_new_folder()` 原为无条件重建全部片段卡片 → 仅在“停在全部素材且确有选中片段被移入”时重建，否则只刷新文件夹相关控件。
4. **无法取消选择**：新增工具栏「取消选择」按钮（无选择时置灰）+ 点击网格空白处取消（事件过滤器判定落点不在任何卡片上）。两种方式均已实现。
5. **在文件夹里新建会嵌套**：所有文件夹平级（无父子层级）；同名直接复用并给状态栏提示，不弹模态框；在文件夹内新建后仍停在原文件夹。
6. **素材库单击素材会在后台出声播放**：`_prime_first_frame` 原为 `play()` + 180ms 定时器暂停，解码慢或换素材时定时器错过收尾 → 改为到期**无条件**暂停并回 0 帧、保持静音；预览页不可见时 `_user_play()` 拒绝出声；页面隐藏/切页主动停止；`_user_pause` 同步清除播放意图。
7. **素材库“移进文件夹还在外面看到”**：`_clip_in_folder` 统一文件夹归属判断，「全部素材」只显示根目录下未归类素材；旧树拖放、`_on_folder_dropped`、`_move_selected_to` 移动后均调用 `reload_library` 同步可见界面；新建同名文件夹自动加后缀（`_unique_folder_name`）。
8. **`_init_player` 初始化隐患**：`seek_slider` 在播放器卡片创建前被访问，加 `getattr` 保护。

## 11.3 改动文件

| 文件 | 改动 |
|---|---|
| `mixcut/ui/analyze_page.py` | 主要改动：文件夹视图、池片段级、多选/拖动、取消选择、置顶排序、卡片布局 |
| `mixcut/ui/demo/pages_library.py` | 文件夹归属判断、移动后同步、空素材显示 |
| `mixcut/ui/demo/pages_work.py` | 素材预览页可见性判断、hideEvent 停播、点选素材静音停住 |
| `mixcut/ui/demo/backend_bridge.py` | 新建文件夹同名自动改名 |
| `mixcut/ui/library_page.py` | 旧素材库拖放/移动后同步可见界面 |
| `mixcut/main.py` | 首帧预热强制暂停、静音控制、`stop_preview_playback`、切页停播、`seek_slider` 保护、旧树拖放后 reload |

**备份**（改动前原文件，可回滚）：

- `mixcut/ui/analyze_page.py.bak-20261118-analyze-folder-fix`
- `mixcut/ui/demo/pages_library.py.bak-20261118-analyze-folder-fix`
- `mixcut/ui/demo/backend_bridge.py.bak-20261118-analyze-folder-fix`
- `mixcut/ui/demo/pages_analysis.py.bak-20261118-analyze-folder-fix`

## 11.4 新增回归测试（离屏）

| 测试 | 断言数 | 覆盖 |
|---|---|---|
| `tests/test_analyze_folders.py` | 65 | 拖入/右键归类、不自动打开、从全部素材消失、置顶排序、文件夹固定顺序、文件夹卡片第一行、多选与整组拖动、移出只影响选中、右键菜单项、删除提示与不删结果、取消选择（按钮+空白点击）、在文件夹内新建不嵌套同名 |
| `tests/test_library_preview.py` | 16 | 真实 MainWindow + 真实后台桥 + 真实旧树：归类后全部素材只剩未归类、单击素材 500ms 后不播放、预热后保持静音、非预览页播放无效、切页停止 |

运行方式：`py -3.14 tests\test_analyze_folders.py` / `py -3.14 tests\test_library_preview.py`（需 `QT_QPA_PLATFORM=offscreen`，脚本内已默认设置）。

## 11.5 本阶段注意

1. **不是热更新**：改源码后必须完全退出进程重新启动；打包版 `dist\MixCutStudio\MixCutStudio.exe` 与 `installer_output` 安装包仍是旧代码，需重新打包。
2. 离屏测试用假路径构造结构数据，**不验证真实解码与听觉效果**；“没有声音”依据播放器状态与静音状态判定，最终以用户真机为准（2026-11-18 用户重启真机验证通过）。
3. 「全部素材」现在语义为“未归类片段”，与左侧文件夹栏的计数一致；已归类片段只在对应文件夹内出现。
4. 暂停/停止后播放器保持静音，点「播放」会重新解除静音（`_user_play` 内处理）。

---

# 待审核清单（需用户真机确认，offscreen 无法验证）

1. 分析页预览**双击播放/暂停**的真实手感
2. 预览**音频输出**是否正常出声
3. 定位静态帧是否还有**黑屏/闪烁**
4. 分析页**独立全屏**画面是否正常、Esc 退出是否顺畅
5. 真机完整分析全流程是否稳定（offscreen 下的崩溃已确认是测试环境伪问题，真机需最终确认）
6. 点停止后是否快速取消、显示“已取消”，且无任何弹窗残留
7. 分析完成后点“开始混剪”自动剪辑并生成剪映草稿（已真机验证通过，#10）
8. 同素材再次分析是否命中缓存、不再重新推理
9. 暂停后任务卡耗时是否冻结、继续后只统计实际运行时间；关闭软件是否不再弹拦截窗、能正常退出（offscreen 已验证，真机手感待确认，#11）

第十阶段（2026-11-16/17 新 UI）新增待真机确认：
10. 无边框窗口四边/四角拖拽缩放是否顺手，「只能放大不能缩小」是否彻底解决
11. 所有预览窗口「快捷键放大 → 快捷键关闭」循环是否稳定，关闭后固定预览窗口是否保留
12. 素材预览预览器右下角全屏按钮与快捷键是否一致，退出全屏是否回到原布局
13. 全界面字体是否统一为 demo 字体，中文无方块/缺字
14. 打包版三张启动图随机是否生效，源码启动图是否仍为原图
15. 任务中心顶部完成进度条在真实混剪过程中的推进是否平滑
16. 4K HEVC 10bit 4:2:2 素材在「不代理」下的实际卡顿程度，以及 720/1280 代理的观感
17. 设置页运行环境读取到的 GPU/CUDA/剪映草稿目录是否与本机实际一致

第十一阶段（2026-11-18 文件夹/预览修复）新增待真机确认：
18. 分析页拖拽片段到文件夹的真实手感（离屏测试仅模拟事件）
19. 素材库单击素材是否彻底无声音、切页后是否立即静音（用户 2026-11-18 重启验证通过）
20. 新建文件夹时的闪烁是否完全消失、文件夹卡片与片段卡片宽度是否对齐
21. 点击网格空白处取消选择是否会误触发（点在卡片上不应清空）

---

# 未完成清单（明确没做 / 未开始）

1. ~~**OpenCLIP 语义检索**~~：已完成接入（见第十阶段）；权重在 `D:\mixcut源文件\混剪副本（测试）源文件\OpenCLIP\open_clip_pytorch_model.bin`（1.71GB），模型与 venv 已收拢到 `OpenCLIP/`，分析页可选 OpenCLIP 组件，设置页「重新加载模型」真实加载
2. **faster-whisper 语音转写**：已用 CUDA 引擎的 whisper（`sd_asr.exe`）替代实现，见第九阶段；未引入 `faster-whisper` 依赖
3. ~~**Inno Setup 打包**~~：已完成（见第八阶段），历史三个坑在本次构建中未复现，自定义图标已配置；待办仅剩可选的代码签名
4. **剪辑工作站**：用户说"完善后再开始"，未开工
5. **P4 构图模型**：用户说"都做"但建议暂缓
6. **镜头排版连续性方案**：先准备，暂不开发
7. **OpenFilm 动态包装**：暂缓

---

# 待完善 / 已知局限清单

1. **`mixcut/ui/fonts/analyze_page.py` 孤立旧副本已删除**（2026-11-11）：fonts 目录保留，仅存放被 `theme.py` 使用的 `msyh.ttf`
2. **黑名单命中局限**：VL 标签不进 clip tags，仅自建分类名一致才命中
3. **P1 硬解受限**：pip 版 OpenCV 不含 CUDA（设备数 0），NVDEC 实际作用有限
4. **镜头边界检测已整体删除**（2026-11-11，详见第六阶段）：长视频改按固定 5 秒窗口分段，不再对齐真实镜头（可能跨镜头/拆镜头），但完整覆盖到结尾、不再丢后面片段；历史 Phase 1 切点零偏差的优化与测试随之归档
5. **Qwen3-VL 链路已整体移除**（2026-11-11，详见第五阶段）：历史上 `think=false`/`/no_think` 无法关闭思考（thinking-only 构建、关思考反慢 12%）的结论随之失效，数据保留在 `bench_results/think_balanced_*.json` 仅作存档；当前判废纯靠 OpenCV，新瓶颈是 4K 全分辨率 OpenCV 算子
6. **Remotion 方案**：用户已放弃，相关代码保留待清理或复用
7. **ffmpeg 运行时已切 9.0.2**（2026-11-11 拍板）：项目内 `ffmpeg/bin/` 自包含 ffmpeg/ffprobe（各约 228MB），默认定位优先于老 static 目录；老 `ffmpeg-20200831-...` 目录保留作回退；未来打包需把 `ffmpeg/bin/` 纳入安装包
8. **4K HEVC 10bit 4:2:2 原片预览**：不选代理时仍可能慢放/卡顿，属源素材解码压力；已提供 720/1280 全局代理规避
9. **素材缩略图缓存**：工程只保存 `thumb_path`，未把缩略图二进制打进工程；系统清缓存后重开需按原素材重新抽帧，素材/分析数据不丢
10. **任务/输出历史**：持久化当前任务中心行、状态、进度与输出路径，不是完整历史任务列表
11. **打包加密**：PyInstaller `optimize=2` 仅字节码归档，不是商业级强混淆；要更强保护需 pyarmor 商业授权
12. **台账说明**：2026-11-16 原 `迁移进度台账.md` 在清理时被截断，已按会话记录重建；1.9.21 之后完整，1.9.20 以前仅摘要；本全周期台账已补第十阶段，作为 11/16–11/17 的主记录

---

# 关键速查

### 路径
- 项目源码：`D:\mixcut源文件\混剪副本（测试）源文件`
- Python：`C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe`
- 启动：`python main.py`
- 默认 FFmpeg：项目内 `ffmpeg/bin/`（9.0.2 full build，自包含）；老版本 `ffmpeg-20200831-4a11a6f-win64-static/` 保留回退
- 测试素材：`D:\素材\10月26日.mp4`（30.1s，2160×3840 竖屏，h264，902 帧）；本轮嵌套目录/代理回归用 `D:\素材\11.12 柯馨\棕\C2991.MP4`、`C2992.MP4`
- 本地大模型：已移除（原 Ollama `http://localhost:11434`、`qwen3-vl:4b-thinking` 不再需要，软件分发无需附带）
- OpenCLIP：`D:\mixcut源文件\混剪副本（测试）源文件\OpenCLIP\open_clip_pytorch_model.bin`（1.71GB），venv 同目录
- 打包 exe：`D:\mixcut源文件\混剪副本（测试）源文件\dist\MixCutStudio\MixCutStudio.exe`
- 安装包：`D:\mixcut源文件\混剪副本（测试）源文件\installer_output\MixCutStudio_Setup_v1.0.0.exe`
- 当前注册安装：`D:\111111111111\MixCutStudio`（MixCut Studio 1.0.0）
- 默认草稿目录：`D:\MixCut_Drafts`

### 三档分析模式（纯 OpenCV，差异在采样帧数与差帧比例门槛）
- 快速 fast：3 采样、差帧比例门槛 0.66
- 中度 medium：6 采样、差帧比例门槛 0.5
- 严格 strict：12 采样、差帧比例门槛 0.34

### 主要常量
- `SEGMENT_THRESHOLD=5.0`、`FALLBACK_WINDOW=5.0`、`GRID_SIZE=12`
- 长视频按固定窗口（每 `FALLBACK_WINDOW` 秒一段）切分，末段强制收尾到结尾；镜头检测相关常量已全部删除
- 模块 logger `mixcut.scene` 定义保留（当前无调用，便于以后加日志）

### 测试文件
- 历史 `_test_*.py` 共 12 个，全部保留；部分因代码演进方法改名而过时，非本轮引入
- 镜头检测专属测试/基准脚本（`test_phase12_pipe.py`、`test_phase1_regression.py`、`bench_scene_detect.py`、`calibrate_scene.py`、`bench_resolution.py`）已随功能删除；原始数据保留在 `bench_results/`
- 链路 BUG 修复：`tests/test_bugfix_smoke.py`（offscreen 冒烟，验证 AI 移除/按钮分离/缓存持久/停止弹窗已删，通过）
- 第十一阶段新增（2026-11-18，均需 `QT_QPA_PLATFORM=offscreen`，脚本内已默认）：
  - `tests/test_analyze_folders.py`：分析页文件夹/多选/混剪池/取消选择回归，65 项断言，全部 PASS
  - `tests/test_library_preview.py`：素材库归类可见性 + 预览器不出声回归（真实 MainWindow + 真实后台桥 + 旧树），16 项断言，全部 PASS
- 本轮诊断/验证临时文件与备份已全部清理
