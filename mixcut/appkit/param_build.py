# -*- coding: utf-8 -*-
"""界面构建：主界面骨架、参数面板、过滤器面板、混剪参数页。

从 main.py 原样搬出 9 个方法，其中 ``_build_mix_tab`` 约 459 行、
``_build_config_tab`` 约 263 行，是本文件最大的一块。

拆分方式说明
------------
Mixin：这批方法在 ``__init__`` 里通过 ``self._build_ui()`` 调用，
执行时会给 MainWindow 挂上 60 余个控件属性（``self.mix_resolution``、
``self.tree``、``self.tabs`` 等）。用 Mixin 保证 ``self.xxx = 控件``
的赋值语义完全不变——其它 Mixin 里的方法靠这些属性工作。

⚠️ 搬迁风险提示
    本批代码几乎全是「创建控件并挂到 self 上」的长串语句。
    一旦某个控件没被创建，使用它的方法会在运行时抛 AttributeError，
    且不一定在启动时就暴露。因此搬迁后必须做属性清单比对，
    确认实例属性一个不少（见 tests/_attr_baseline.json 基线）。

宿主契约
--------
这批方法主要**创建**属性，依赖的宿主状态很少：
    self.use_new_ui（决定走新界面还是旧界面）
    self.shell / self._workbench_* 等界面骨架相关
其余全部是 PySide6 控件与 appkit 常量。

依赖方向：导入 PySide6、标准库与 mixcut 的少量非 UI 模块；
方法内对 mixcut.ui 的导入沿用原写法（延迟导入）。
"""
import os
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox,
    QGroupBox, QFormLayout, QScrollArea, QFrame, QRadioButton,
    QButtonGroup, QTabWidget, QSplitter, QPlainTextEdit, QProgressBar,
    QTreeWidget, QAbstractSpinBox, QApplication, QMessageBox,
)
from PySide6.QtMultimediaWidgets import QVideoWidget

from mixcut import filter_engine as fe
from mixcut import ffmpeg_tools as ft
from mixcut import analysis as analysis_core
from mixcut import mixbuilder as mixbuilder_module
from mixcut.widgets import MaterialSelector
from mixcut.ui.components import TagMultiSelect

from .constants import (
    MIX_RESOLUTION_PRESETS, MIX_DEFAULT_RESOLUTION_INDEX,
    MIX_FPS_OPTIONS, MIX_DEFAULT_FPS_INDEX,
    VIDEO_EXTS, _human_size, _fmt_ms,
)
from .widgets import AspectRatioWidget, UndoableTreeWidget


class ParamBuildMixin:
    """界面与参数面板构建。"""

    def _build_ui(self):

        if getattr(self, "use_new_ui", False):
            # 新界面：旧页面照常构建（控件都保留，供新页面复用），
            # 只是不挂到窗口上，改为四区布局。
            self.tabs = QTabWidget()
            self._build_config_tab()
            self._build_mix_tab()
            self._build_workstation_tab()
            self.tabs.currentChanged.connect(self.refresh_selectors)
            self._build_new_shell()
            from mixcut.ui import theme as _ui_theme
            _ui_theme.apply(QApplication.instance())
            # 可见外壳和四页使用 demo 的原始 QSS；避免旧主题覆盖 demo 的字体像素尺寸。
            from mixcut.ui.demo import theme as _demo_theme
            QApplication.instance().setStyleSheet(_demo_theme.qss())
            return

        central = QWidget()

        self.setCentralWidget(central)

        layout = QVBoxLayout(central)

        self.tabs = QTabWidget()

        layout.addWidget(
            self.tabs
        )

        self._build_config_tab()
        self._build_mix_tab()

        # 工作站入口暂时保留为占位页；当前正式工作流为智能混剪。
        self._build_workstation_tab()

        # 切换页面时刷新智能混剪素材选择框架
        self.tabs.currentChanged.connect(self.refresh_selectors)

        self.setStyleSheet("""
            QMainWindow, QWidget {
                background:#171b24;
                color:#e9edf5;
                font-size:13px;
            }

            QGroupBox {
                border:1px solid #343d4d;
                border-radius:8px;
                margin-top:10px;
                padding:10px;
                font-weight:600;
            }

            QGroupBox::title {
                subcontrol-origin:margin;
                left:10px;
                padding:0 5px;
            }

            QPushButton {
                background:#30394a;
                border:0;
                padding:9px 13px;
                border-radius:5px;
            }

            QPushButton:hover {
                background:#43506a;
            }

            QPushButton:disabled {
                background:#252a35;
                color:#666;
            }

            QLineEdit, QSpinBox, QDoubleSpinBox,
            QComboBox, QTreeWidget,
            QPlainTextEdit, QListWidget {

                background:#222936;
                border:1px solid #394457;
                border-radius:4px;
                padding:6px;
            }

            QHeaderView::section {
                background:#30394a;
                padding:5px;
                border:0;
            }

            QProgressBar {
                border:1px solid #394457;
                border-radius:4px;
                text-align:center;
            }

            QProgressBar::chunk {
                background:#FFFFFF;
                border-radius:2px;
            }

            QTabBar::tab {
                background:#181A1D;
                padding:11px 22px;
                margin-right:2px;
                color:#92969E;
            }

            QTabBar::tab:selected {
                background:#22252A;
                color:#FFFFFF;
                font-weight:bold;
                border-bottom:2px solid #FFFFFF;
            }
        """)

    # 内容固定宽度（网页式居中，超出高度可上下滚动）
    PAGE_CONTENT_WIDTH = 1180

    def _wrap_scroll(self, content):
        """把页面内容包进 QScrollArea：固定宽度居中，垂直滚动，全屏不拉伸。"""
        content.setMaximumWidth(self.PAGE_CONTENT_WIDTH)

        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)
        h.addWidget(content)
        h.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setWidget(container)
        return scroll

    # ---------------- 引擎能力页 ----------------

    def _build_config_tab(self):

        self.tab_config = QWidget()

        tab_layout = QVBoxLayout(
            self.tab_config
        )

        splitter = QSplitter(
            Qt.Horizontal
        )

        tab_layout.addWidget(
            splitter
        )

        # 左侧

        left = QWidget()

        left_layout = QVBoxLayout(left)

        material_group = QGroupBox(
            "素材管理"
        )

        material_layout = QVBoxLayout(
            material_group
        )

        buttons = QHBoxLayout()

        add_files = QPushButton(
            "添加视频"
        )

        add_folder = QPushButton(
            "扫描文件夹"
        )

        add_category = QPushButton("新建分类")

        remove = QPushButton(
            "移除选中"
        )

        buttons.addWidget(add_files)

        buttons.addWidget(add_folder)

        buttons.addWidget(add_category)

        buttons.addWidget(remove)

        material_layout.addLayout(
            buttons
        )

        confirm_row = QHBoxLayout()

        self.check_btn = QPushButton(
            "检查素材"
        )

        self.confirm_btn = QPushButton(
            "确定使用"
        )

        self.confirm_btn.setStyleSheet(
            "background:#d48a38;color:white;font-weight:bold;"
        )

        confirm_row.addWidget(
            self.check_btn
        )

        confirm_row.addWidget(
            self.confirm_btn
        )

        material_layout.addLayout(
            confirm_row
        )

        self.tree = UndoableTreeWidget()
        # 新 UI 下旧素材库页被摘出主窗口控件树，parentWidget() 链到不了 MainWindow，
        # 这里显式记住宿主，撤销时才能回刷可见界面。
        self.tree.owner_window = self

        self.tree.setHeaderLabels([
            "分类 / 素材名称",
            "文件路径"
        ])

        self.tree.setColumnWidth(
            0,
            230
        )

        self.tree.itemClicked.connect(
            self.on_tree_item_clicked
        )

        material_layout.addWidget(
            self.tree,
            1
        )

        self.count_label = QLabel(
            "0 个素材"
        )

        material_layout.addWidget(
            self.count_label
        )

        left_layout.addWidget(
            material_group,
            3
        )

        # 播放器

        player_group = QGroupBox(
            "视频预览"
        )

        player_layout = QVBoxLayout(
            player_group
        )

        self.video_widget = QVideoWidget()

        self.video_widget.setMinimumHeight(
            260
        )

        self.video_widget.setStyleSheet(
            "background:black;"
        )

        player_layout.addWidget(
            self.video_widget,
            1
        )

        player_controls = QHBoxLayout()

        self.play_btn = QPushButton(
            "播放"
        )

        self.pause_btn = QPushButton(
            "暂停"
        )

        self.stop_btn = QPushButton(
            "停止"
        )

        player_controls.addWidget(
            self.play_btn
        )

        player_controls.addWidget(
            self.pause_btn
        )

        player_controls.addWidget(
            self.stop_btn
        )

        player_layout.addLayout(
            player_controls
        )

        left_layout.addWidget(
            player_group,
            2
        )

        splitter.addWidget(
            left
        )

        # 右侧参数

        right = QWidget()

        right_layout = QVBoxLayout(
            right
        )

        self.info_label = QLabel()

        self.info_label.setStyleSheet(
            "color:#8491a5;line-height:2;"
        )

        self.info_label.setWordWrap(
            True
        )

        right_layout.addWidget(
            self.info_label
        )

        # 详细参数面板：预览哪个视频就显示哪个视频的参数
        detail_group = QGroupBox("详细参数")
        detail_layout = QVBoxLayout(detail_group)
        self.detail_box = QPlainTextEdit()
        self.detail_box.setReadOnly(True)
        self.detail_box.setMinimumHeight(190)
        self.detail_box.setPlainText("点击左侧素材即可查看该视频的详细参数")
        detail_layout.addWidget(self.detail_box)
        right_layout.addWidget(detail_group)

        self.update_info_label()

        right_layout.addStretch()

        splitter.addWidget(
            right
        )

        splitter.setSizes([
            850,
            500
        ])

        self.tabs.addTab(
            self._wrap_scroll(self.tab_config),
            "素材与参数"
        )

        # 信号

        add_files.clicked.connect(
            self.add_files
        )

        add_folder.clicked.connect(
            self.add_folder
        )

        add_category.clicked.connect(self.create_virtual_folder)

        remove.clicked.connect(
            self.remove_selected
        )

        self.check_btn.clicked.connect(
            self.check_materials
        )

        self.confirm_btn.clicked.connect(
            self.confirm_materials
        )

    # =================================================
    # 智能粗剪筛选引擎面板
    # =================================================

    def _has_nvenc(self):
        """ffmpeg 是否带 NVENC 编码器（作为 CUDA 可用的判据）。"""
        ffmpeg = ft.find_ffmpeg()
        if not ffmpeg:
            return False
        try:
            import subprocess
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(ft, "NO_WINDOW_FLAGS", 0),
            )
            return proc.returncode == 0 and "nvenc" in proc.stdout
        except Exception:
            return False

    def _build_filter_panel(self, parent_layout):
        avail = fe.self_check()
        group = QGroupBox("智能粗剪筛选引擎")
        vbox = QVBoxLayout(group)

        # 顶部：一键预设
        preset_row = QHBoxLayout()
        btn_fast = QPushButton("快速批量")
        btn_fine = QPushButton("精细质检")
        preset_row.addWidget(btn_fast)
        preset_row.addWidget(btn_fine)
        vbox.addLayout(preset_row)

        # 强制自检按钮
        btn_check = QPushButton("强制自检")
        vbox.addWidget(btn_check)

        # 硬件加速单选
        hw_row = QHBoxLayout()
        hw_row.addWidget(QLabel("硬件加速："))
        self.filter_hw_group = QButtonGroup(self)
        self.filter_cpu_radio = QRadioButton("CPU")
        self.filter_gpu_radio = QRadioButton("GPU")
        self.filter_cpu_radio.setChecked(True)
        self.filter_hw_group.addButton(self.filter_cpu_radio)
        self.filter_hw_group.addButton(self.filter_gpu_radio)
        hw_row.addWidget(self.filter_cpu_radio)
        hw_row.addWidget(self.filter_gpu_radio)
        hw_row.addStretch()
        vbox.addLayout(hw_row)

        if not self._has_nvenc():
            self.filter_gpu_radio.setEnabled(False)
            self.filter_gpu_radio.setText("GPU（无 CUDA）")

        # 检测组件勾选
        self.filter_checks = {}
        checkbox_spec = [
            ("amverge", "amverge 静止空镜检测", True),
            ("scenedetect", "PySceneDetect 自适应镜头校验", True),
            ("videoscope", "VideoScope 画质缺陷检测", False),
            ("ffmpeg_audio_black", "FFmpeg 静音+黑屏检测", True),
            ("transnetv2", "TransNetV2 高阶镜头切变", False),
        ]
        for key, label, default_on in checkbox_spec:
            cb = QCheckBox(label)
            dep_ok = avail["ffmpeg"] if key == "ffmpeg_audio_black" else avail.get(key, False)
            cb.setChecked(bool(default_on and dep_ok))
            if key == "amverge":
                # 核心基础模块，强制开启不可关
                cb.setEnabled(False)
                cb.setChecked(True)
            elif not dep_ok:
                cb.setEnabled(False)
                cb.setText(label + "（未安装）")
            self.filter_checks[key] = cb
            vbox.addWidget(cb)

        # 状态（不显示进度条）
        self.filter_status = QLabel("就绪")
        self.filter_status.setStyleSheet("color:#8491a5;")
        vbox.addWidget(self.filter_status)

        parent_layout.addWidget(group)

        btn_fast.clicked.connect(
            lambda: self._apply_filter_preset(fe.preset_fast_batch())
        )
        btn_fine.clicked.connect(
            lambda: self._apply_filter_preset(fe.preset_fine_qc())
        )
        btn_check.clicked.connect(self.run_force_self_check)

    def run_force_self_check(self, show_dialog=True):
        """强制自检：重新导入全部组件，刷新勾选/单选状态，返回是否可继续。"""
        avail, report = fe.force_self_check()

        # 刷新 GPU 单选
        has_gpu = self._has_nvenc()
        self.filter_gpu_radio.setEnabled(has_gpu)
        self.filter_gpu_radio.setText("GPU" if has_gpu else "GPU（无 CUDA）")
        if not has_gpu and self.filter_gpu_radio.isChecked():
            self.filter_cpu_radio.setChecked(True)

        # 刷新各组件勾选框
        for key, cb in self.filter_checks.items():
            if key == "amverge":
                continue
            dep_ok = avail["ffmpeg"] if key == "ffmpeg_audio_black" else avail.get(key, False)
            if not dep_ok:
                # 依赖缺失：禁用并取消勾选
                cb.setEnabled(False)
                if "（未安装）" not in cb.text():
                    cb.setText(cb.text() + "（未安装）")
                cb.setChecked(False)
            else:
                cb.setEnabled(True)
                cb.setText(cb.text().replace("（未安装）", ""))

        self.filter_status.setText("强制自检完成")
        if show_dialog:
            QMessageBox.information(
                self, "强制自检结果", "\n".join(report)
            )
        # ffmpeg 缺失属于致命问题，不允许启动
        return bool(avail["ffmpeg"])

    def _apply_filter_preset(self, preset):
        mapping = {
            "amverge": "amverge_enable",
            "scenedetect": "scenedetect_enable",
            "videoscope": "videoscope_enable",
            "ffmpeg_audio_black": "ffmpeg_audio_black_enable",
            "transnetv2": "transnetv2_enable",
        }
        for key, cfg_key in mapping.items():
            cb = self.filter_checks[key]
            if cb.isEnabled():
                cb.setChecked(bool(preset.get(cfg_key, False)))
        want_cpu = bool(preset.get("cpu_mode", True))
        if want_cpu or not self.filter_gpu_radio.isEnabled():
            self.filter_cpu_radio.setChecked(True)
        else:
            self.filter_gpu_radio.setChecked(True)
        self.filter_status.setText("已应用预设")

    def _collect_filter_config(self):
        """汇总本次要用的筛选配置（所有会改变检测结果的参数）。

        这份配置同时作为缓存键的一部分，所以必须包含：组件开关、CPU/GPU、
        视频/静音检测开关以及它们的阈值。
        """
        video_on = self.mix_video_detect.isChecked()
        audio_on = self.mix_audio_detect.isChecked()
        thresholds = {
            key: float(self.detect_threshold_boxes[key].value())
            for key in self.detect_threshold_boxes
        }
        return {
            **fe.DEFAULT_FILTER_CONFIG,
            "cpu_mode": self.filter_cpu_radio.isChecked(),
            "amverge_enable": True,
            "scenedetect_enable": self.filter_checks["scenedetect"].isChecked(),
            "videoscope_enable": self.filter_checks["videoscope"].isChecked(),
            "ffmpeg_audio_black_enable":
                self.filter_checks["ffmpeg_audio_black"].isChecked(),
            "transnetv2_enable": self.filter_checks["transnetv2"].isChecked(),
            # 把混剪页画面/静音开关与检测阈值传给引擎，真正参与检测
            "ffmpeg_options": {
                **thresholds,
                "video_detect": video_on,
                "audio_detect": audio_on,
                "detect_black": video_on,
                "detect_freeze": video_on,
                # CUDA 引擎能力开关：语音整句切割 / 指纹查重
                "sentence_snap": bool(
                    getattr(self, "mix_sentence_snap", None)
                    and self.mix_sentence_snap.isChecked()
                ),
                "audio_dedup": bool(
                    getattr(self, "mix_audio_dedup", None)
                    and self.mix_audio_dedup.isChecked()
                ),
                "asr_model": "small",
                "asr_language": "zh",
            },
        }


    # =================================================
    # 智能混剪界面（第二个界面：随机混剪 + 配乐卡点 + 转场）
    # =================================================

    def _build_mix_tab(self):

        self.mix_worker = None
        self.tab_mix = QWidget()
        root = QVBoxLayout(self.tab_mix)

        # 顶部左右两栏：左=素材选择，右=素材库（参数配置）
        top_row = QHBoxLayout()

        # 素材选择框架：只处理勾选的视频
        self.mix_selector = MaterialSelector(
            "素材选择（勾选要混剪的视频）"
        )
        top_row.addWidget(self.mix_selector, 3)

        # —— 素材库（混剪基础参数移到右侧） ——
        base_group = QGroupBox("素材库 · 参数配置")
        bform = QFormLayout(base_group)
        self.mix_target = QDoubleSpinBox()
        self.mix_target.setRange(3, 3600)
        self.mix_target.setValue(30)
        self.mix_target.setSuffix(" 秒")
        self.mix_tolerance = QDoubleSpinBox()
        self.mix_tolerance.setRange(0, 60)
        self.mix_tolerance.setValue(2)
        self.mix_tolerance.setSuffix(" 秒")
        self.mix_tolerance.setToolTip(
            "目标时长为大概值：成片落在 目标±浮动 范围内即停止，"
            "末段保持自然长度，不再硬凑到精确秒数。"
        )
        self.mix_clip_min = QDoubleSpinBox()
        self.mix_clip_min.setRange(0.5, 60)
        self.mix_clip_min.setValue(2)
        self.mix_clip_min.setSuffix(" 秒")
        self.mix_clip_max = QDoubleSpinBox()
        self.mix_clip_max.setRange(0.5, 120)
        self.mix_clip_max.setValue(5)
        self.mix_clip_max.setSuffix(" 秒")
        self.mix_clip_count = QSpinBox()
        self.mix_clip_count.setRange(1, 100)
        self.mix_clip_count.setValue(1)
        self.mix_clip_count.setToolTip(
            "一次生成 N 个独立的剪映工程草稿，每个工程随机排列不同，"
            "各自按目标时长/片段长短装配。"
        )
        # 清晰度 / 帧率：决定草稿画布尺寸与工程帧率
        self.mix_resolution = QComboBox()
        for label, _w, _h in MIX_RESOLUTION_PRESETS:
            self.mix_resolution.addItem(label)
        self.mix_resolution.setCurrentIndex(MIX_DEFAULT_RESOLUTION_INDEX)
        self.mix_resolution.setToolTip(
            "画布尺寸决定剪映导出的清晰度；素材本身分辨率不足时，"
            "选再高也不会增加细节。"
        )
        self.mix_fps = QComboBox()
        for f in MIX_FPS_OPTIONS:
            self.mix_fps.addItem(f"{f} fps", f)
        self.mix_fps.setCurrentIndex(MIX_DEFAULT_FPS_INDEX)
        self.mix_fps.setToolTip("工程帧率，切点按秒换算到对应帧。")
        self.mix_resolution.currentIndexChanged.connect(self.update_info_label)
        self.mix_fps.currentIndexChanged.connect(self.update_info_label)
        self.update_info_label()
        self.mix_shuffle = QCheckBox("随机排列素材")
        self.mix_shuffle.setChecked(True)
        self.mix_reuse = QCheckBox("允许素材重复使用")
        self.mix_reuse.setChecked(True)
        # —— 语音整句切割 / 音频指纹查重（接 CUDA 引擎，默认关闭） ——
        self.mix_sentence_snap = QCheckBox("整句切割（切点对齐语音句尾）")
        self.mix_sentence_snap.setChecked(False)
        self.mix_sentence_snap.setToolTip(
            "对有人声的素材先做语音转写，把切点吸附到整句结尾，"
            "避免成片里半句话被切断。\n"
            "需要 CUDA 引擎（sd_asr.exe）；首次转写较慢，结果按素材缓存，"
            "同一条素材不会重复转写。"
        )
        self.mix_audio_dedup = QCheckBox("指纹查重（跳过重复素材）")
        self.mix_audio_dedup.setChecked(False)
        self.mix_audio_dedup.setToolTip(
            "用 CUDA 指纹（画面哈希 + 色彩直方图）比对素材，"
            "同一段片子的重复拷贝只保留最长的一条，避免成片来回重复。"
        )
        self.mix_openclip = QCheckBox("OpenCLIP 语义多样性评分")
        self.mix_openclip.setChecked(True)
        self.mix_openclip.setToolTip(
            "对分析页确认保留的候选片段提取中心画面，使用 OpenCLIP 语义向量"
            "降低连续相似画面的优先级。只影响候选排序，不会自动删除片段。"
            "需要 OpenCLIP 独立环境与 ViT-L-14 权重。"
        )
        # 直达「引擎能力」页（标签栏在窄窗口下会折叠，留一个稳的入口）
        self.mix_engine_link = QPushButton("查看「MIX引擎」页")
        self.mix_engine_link.setToolTip(
            "打开「MIX引擎」页：查看引擎是否在位、转写/指纹缓存有多少。"
        )
        self.mix_engine_link.clicked.connect(self.goto_engine_page)
        self.mix_diversity = QComboBox()
        self.mix_diversity.addItem("适中（推荐）", "medium")
        self.mix_diversity.addItem("严格（尽量不重复）", "strict")
        self.mix_diversity.addItem("宽松（保留随机）", "loose")
        self.mix_diversity.setToolTip(
            "相邻重复控制强度：同一素材或同一分类紧挨着出现时，"
            "严格会尽量避开，宽松只做轻微约束。"
        )
        # —— 算法策略（阶段 2 / 3 / 4） ——
        self.mix_rhythm = QComboBox()
        self.mix_rhythm.addItem("均衡时长", "balance")
        self.mix_rhythm.addItem("长短交替", "dynamic")
        self.mix_rhythm.addItem("快速切换", "fast")
        self.mix_rhythm.addItem("舒缓节奏", "slow")
        self.mix_rhythm.addItem("脉冲节奏（短长强对比）", "pulse")
        self.mix_rhythm.addItem("断奏（极短密集）", "staccato")
        self.mix_rhythm.addItem("电影感（长镜头为主）", "cinematic")
        self.mix_rhythm.setToolTip(
            "片段时长策略（受最短/最长片段约束）：均衡=长度集中，"
            "长短交替=长短穿插，快速=偏短，舒缓=偏长。"
        )
        self.mix_version = QComboBox()
        for key, preset in mixbuilder_module.VERSION_STYLES.items():
            if key == "balanced":
                self.mix_version.addItem("均衡混剪", key)
        self.mix_version.addItem("多版本轮换（快节奏/多样性/长短交替/音乐优先）", "rotate")
        self.mix_version.setToolTip(
            "生成多个工程时：选“均衡混剪”则每个工程算法一致、只换随机种子；"
            "选轮换则依次使用快节奏、多样性优先、长短交替、音乐节奏优先等策略。"
        )
        self.mix_beat_step = QComboBox()
        self.mix_beat_step.addItem("自动（按 BPM）", 0)
        self.mix_beat_step.addItem("每 1 拍切换", 1)
        self.mix_beat_step.addItem("每 2 拍切换", 2)
        self.mix_beat_step.addItem("每 4 拍切换", 4)
        self.mix_beat_step.setToolTip("卡点密度：音乐节奏优先版本会在强拍切，其余按此密度。")

        # 高级：弱拍补切概率（避免每个鼓点都机械切换）
        self.mix_weak_chance = QDoubleSpinBox()
        self.mix_weak_chance.setRange(0.0, 1.0)
        self.mix_weak_chance.setSingleStep(0.05)
        self.mix_weak_chance.setDecimals(2)
        self.mix_weak_chance.setValue(0.25)
        self.mix_weak_chance.setToolTip(
            "卡点时允许落在弱拍的概率，越低越只在强拍切，节奏更稳；"
            "越高切点越密。")

        # 高级：卡点容差（切点与鼓点允许的偏差秒数）
        self.mix_beat_tolerance = QDoubleSpinBox()
        self.mix_beat_tolerance.setRange(0.02, 0.5)
        self.mix_beat_tolerance.setSingleStep(0.02)
        self.mix_beat_tolerance.setDecimals(2)
        self.mix_beat_tolerance.setValue(0.12)
        self.mix_beat_tolerance.setToolTip(
            "切点对齐鼓点时允许的时间偏差，越大越容易贴合，"
            "越小越精确但可能找不到落点。")
        bform.addRow("清晰度", self.mix_resolution)
        bform.addRow("帧率", self.mix_fps)
        bform.addRow("目标时长", self.mix_target)
        bform.addRow("时长浮动 ±", self.mix_tolerance)
        bform.addRow("生成工程数", self.mix_clip_count)
        bform.addRow("最短片段", self.mix_clip_min)
        bform.addRow("最长片段", self.mix_clip_max)
        bform.addRow("重复控制", self.mix_diversity)
        bform.addRow("时长策略", self.mix_rhythm)
        bform.addRow("版本策略", self.mix_version)
        bform.addRow("卡点密度", self.mix_beat_step)
        bform.addRow("", self.mix_shuffle)
        bform.addRow("", self.mix_reuse)
        bform.addRow("", self.mix_sentence_snap)
        bform.addRow("", self.mix_audio_dedup)
        bform.addRow("", self.mix_engine_link)

        # —— 草稿位置（可自定义输出文件夹，方便打包传给别人） ——
        loc_group = QGroupBox("草稿位置")
        loc_lay = QVBoxLayout(loc_group)
        loc_tip = QLabel("选择剪映工程路径")
        loc_tip.setWordWrap(True)
        loc_lay.addWidget(loc_tip)
        loc_row = QHBoxLayout()
        self.mix_draft_dir = QLineEdit()
        self.mix_draft_dir.setPlaceholderText("默认剪映草稿目录（点击右侧选择自定义位置）")
        loc_btn = QPushButton("选择位置")
        loc_reset = QPushButton("恢复默认")
        loc_row.addWidget(self.mix_draft_dir)
        loc_row.addWidget(loc_btn)
        loc_row.addWidget(loc_reset)
        loc_lay.addLayout(loc_row)

        # 中栏：智能粗剪筛选引擎（放在素材库旁边）
        filter_col = QWidget()
        filter_lay = QVBoxLayout(filter_col)
        filter_lay.setContentsMargins(0, 0, 0, 0)
        self._build_filter_panel(filter_lay)

        # 右栏：参数配置 + 草稿位置
        right_col = QWidget()
        right_lay = QVBoxLayout(right_col)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(base_group)
        right_lay.addWidget(loc_group)
        right_lay.addStretch()

        top_row.addWidget(filter_col, 2)
        top_row.addWidget(right_col, 2)
        # 整体左移：收紧左右外边距
        top_row.setContentsMargins(2, 2, 18, 2)
        root.addLayout(top_row)

        # —— 废镜头检测（混剪同样先剔除） ——
        mdet_group = QGroupBox("废镜头检测")
        mform = QFormLayout(mdet_group)
        self.mix_video_detect = QCheckBox("画面检测")
        self.mix_video_detect.setChecked(True)
        self.mix_audio_detect = QCheckBox("静音检测")
        self.mix_audio_detect.setChecked(True)
        mform.addRow(self.mix_video_detect)
        mform.addRow(self.mix_audio_detect)

        # 检测阈值：真正传进引擎，也参与分析缓存键
        self.detect_threshold_boxes = {}
        for key, label, lo, hi, step, default, tip in (
            ("black_min_duration", "黑屏最短时长", 0.1, 10.0, 0.1,
             analysis_core.DEFAULT_OPTIONS["black_min_duration"],
             "连续黑屏超过该时长才判为废镜头"),
            ("freeze_min_duration", "静止最短时长", 0.1, 20.0, 0.1,
             analysis_core.DEFAULT_OPTIONS["freeze_min_duration"],
             "画面连续静止超过该时长才判为废镜头"),
            ("silence_min_duration", "静音最短时长", 0.1, 20.0, 0.1,
             analysis_core.DEFAULT_OPTIONS["silence_min_duration"],
             "连续无声超过该时长才判为废镜头"),
        ):
            box = QDoubleSpinBox()
            box.setRange(lo, hi)
            box.setSingleStep(step)
            box.setDecimals(1)
            box.setValue(float(default))
            box.setSuffix(" 秒")
            box.setToolTip(tip)
            self.detect_threshold_boxes[key] = box
            mform.addRow(label, box)

        root.addWidget(mdet_group)

        # —— 配乐卡点 ——
        music_group = QGroupBox("自动配乐 / 卡点 / 闪避")
        mlay = QVBoxLayout(music_group)

        # 配乐方式：手动 / 本地随机 / 云端（占位）
        self.music_mode = QComboBox()
        self.music_mode.addItems(
            ["手动选择 BGM", "本地随机配乐", "云端随机（暂未开放）"]
        )
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("配乐方式"))
        mode_row.addWidget(self.music_mode)
        mlay.addLayout(mode_row)

        # 手动 BGM
        bgm_row = QHBoxLayout()
        self.mix_bgm = QLineEdit()
        self.mix_bgm.setPlaceholderText("选择音频文件")
        bgm_btn = QPushButton("选择音频")
        bgm_row.addWidget(self.mix_bgm)
        bgm_row.addWidget(bgm_btn)
        mlay.addLayout(bgm_row)

        # 本地随机文件夹
        folder_row = QHBoxLayout()
        self.music_folder = QLineEdit()
        self.music_folder.setPlaceholderText("本地音乐文件夹")
        music_folder_btn = QPushButton("选择文件夹")
        folder_row.addWidget(self.music_folder)
        folder_row.addWidget(music_folder_btn)
        mlay.addLayout(folder_row)

        opt_row = QHBoxLayout()
        self.mix_beat_sync = QCheckBox("卡点")
        self.mix_beat_sync.setChecked(True)
        self.mix_ducking = QCheckBox("音频闪避")
        self.mix_ducking.setChecked(True)
        opt_row.addWidget(self.mix_beat_sync)
        opt_row.addWidget(self.mix_ducking)
        opt_row.addStretch()
        mlay.addLayout(opt_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("正常音量"))
        self.mix_base_vol = QDoubleSpinBox()
        self.mix_base_vol.setRange(0, 1)
        self.mix_base_vol.setSingleStep(0.05)
        self.mix_base_vol.setValue(1.0)
        vol_row.addWidget(self.mix_base_vol)
        vol_row.addWidget(QLabel("闪避音量"))
        self.mix_duck_vol = QDoubleSpinBox()
        self.mix_duck_vol.setRange(0, 1)
        self.mix_duck_vol.setSingleStep(0.05)
        self.mix_duck_vol.setValue(0.35)
        vol_row.addWidget(self.mix_duck_vol)
        vol_row.addStretch()
        mlay.addLayout(vol_row)
        root.addWidget(music_group)

        # —— 转场规则 ——
        tr_group = QGroupBox("自动转场 / 黑名单")
        tform = QFormLayout(tr_group)
        self.mix_trans_enable = QCheckBox("启用规则转场")
        self.mix_trans_enable.setChecked(True)
        self.mix_short = QDoubleSpinBox()
        self.mix_short.setRange(0.2, 10)
        self.mix_short.setValue(2.0)
        self.mix_short.setSuffix(" 秒")
        self.mix_long = QDoubleSpinBox()
        self.mix_long.setRange(0.5, 30)
        self.mix_long.setValue(4.0)
        self.mix_long.setSuffix(" 秒")
        self.mix_dissolve_dur = QDoubleSpinBox()
        self.mix_dissolve_dur.setRange(0.1, 3)
        self.mix_dissolve_dur.setValue(0.4)
        self.mix_dissolve_dur.setSuffix(" 秒")
        self.mix_blacklist = QLineEdit("")
        self.mix_blacklist.setToolTip(
            "逗号分隔的分类名，命中的片段之间只硬切。"
        )

        # 转场黑名单：可多选的关键词，与输入框双向同步（后端仍读输入框，无需改算法）
        from mixcut.ui.components import TagMultiSelect
        blacklist_keywords = [
            "产品特写", "特写", "近景", "中景", "远景",
            "走路", "转身", "站立", "坐着",
            "模糊", "过曝", "过暗", "手抖", "黑场",
            "人物", "空镜", "街景",
        ]
        self.blacklist_picker = TagMultiSelect(blacklist_keywords, columns=4)

        def _sync_picker_to_line(kws):
            self.mix_blacklist.setText(",".join(kws))

        def _sync_line_to_picker():
            kws = [x.strip() for x in
                   self.mix_blacklist.text().split(",") if x.strip()]
            self.blacklist_picker.set_selected(kws)

        self.blacklist_picker.selection_changed.connect(
            _sync_picker_to_line)
        self.mix_blacklist.textChanged.connect(_sync_line_to_picker)
        tform.addRow(self.mix_trans_enable)
        tform.addRow("短镜头阈值（硬切）", self.mix_short)
        tform.addRow("长镜头阈值（叠化）", self.mix_long)
        tform.addRow("叠化时长", self.mix_dissolve_dur)
        tform.addRow("黑名单（逗号分隔）", self.mix_blacklist)
        root.addWidget(tr_group)

        # —— 操作 ——
        action_row = QHBoxLayout()
        self.mix_start_btn = QPushButton("开始智能混剪")
        self.mix_start_btn.setMinimumHeight(48)
        self.mix_start_btn.setStyleSheet(
            "background:#FFFFFF;color:#101113;font-size:16px;font-weight:600;"
            "font-weight:700;border-radius:7px;"
        )
        self.mix_cancel_btn = QPushButton("取消")
        self.mix_cancel_btn.setEnabled(False)
        action_row.addWidget(self.mix_start_btn, 4)
        action_row.addWidget(self.mix_cancel_btn, 1)
        root.addLayout(action_row)

        self.mix_progress = QProgressBar()
        root.addWidget(self.mix_progress)

        self.mix_log = QPlainTextEdit()
        self.mix_log.setReadOnly(True)
        self.mix_log.setMaximumBlockCount(500)
        self.mix_log.setStyleSheet(
            "background:#0D0E10;color:#92969E;font-family:Consolas;"
        )
        root.addWidget(self.mix_log, 1)

        self.tabs.addTab(
            self._wrap_scroll(self.tab_mix), "智能混剪"
        )

        # —— 引擎能力页：Super Director 移植过来的三项能力 ——
        engine_page = QWidget()
        eroot = QVBoxLayout(engine_page)
        ehead = QLabel("MIX引擎")
        ehead.setStyleSheet("font-size:18px;font-weight:600;")
        eroot.addWidget(ehead)
        esub = QLabel(
            "三项能力从 Super Director 移植而来，开关在「智能混剪」页底部："
            "「整句切割（切点对齐语音句尾）」和「指纹查重（跳过重复素材）」。"
            "这一页显示引擎是否在位、缓存有多少，以及每项能力负责什么。"
        )
        esub.setWordWrap(True)
        eroot.addWidget(esub)

        st_group = QGroupBox("运行状态")
        st_lay = QVBoxLayout(st_group)
        self.engine_status = QLabel("尚未检查")
        self.engine_status.setWordWrap(True)
        self.engine_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.engine_status.setStyleSheet(
            "font-family:Consolas;font-size:13px;"
        )
        st_lay.addWidget(self.engine_status)
        est_row = QHBoxLayout()
        erefresh_btn = QPushButton("刷新状态")
        edoc_btn = QPushButton("打开说明页")
        ecache_btn = QPushButton("打开缓存目录")
        est_row.addWidget(erefresh_btn)
        est_row.addWidget(edoc_btn)
        est_row.addWidget(ecache_btn)
        est_row.addStretch(1)
        st_lay.addLayout(est_row)
        eroot.addWidget(st_group)

        cap_group = QGroupBox("三项能力")
        cap_lay = QVBoxLayout(cap_group)
        for cap_title, cap_text in (
            ("① 整句切割（切点对齐语音句尾）",
             "对有人声的素材先做语音转写，拿到整句时间轴，再把混剪算出的切点吸附到"
             "最近的句尾——只在最短/最长片段允许的区间内寻找，不打乱节奏。"
             "实测同素材同种子下，切点离句尾的最大偏差从 1.37 秒降到 0.11 秒。"
             "首次需转写，结果按素材缓存，同一条素材不会重复转写。"),
            ("② 指纹查重（跳过重复素材）",
             "用 CUDA 引擎为每条素材提取指纹（关键帧 phash + 色彩直方图），"
             "在本地比对，综合相似度 ≥ 0.92 判为重复；同一组里保留时长最长的一条，"
             "其余跳过并在日志里点名。实测同一文件副本 1.000，不同素材 0.000。"
             "时长比超过 1.4 的两条直接判为不同片子，省掉比对开销。"),
            ("③ CUDA 引擎桥（进程内 sd_fp.dll + 外置 sd_asr.exe）",
             "MixCut 用的是 pip 版 OpenCV，cv2.cuda 恒为 0 个设备，Python 侧拿不到 GPU 算子。"
             "因此 GPU 计算交给随包的引擎：指纹提取由 sd_fp.dll 在 MixCut 进程内直接调用"
             "（CUDA 13.5 + OpenCV CUDA 编译），不再为每条素材起一次 sd_dedup.exe；"
             "语音转写仍由 sd_asr.exe 完成。DLL 缺失或 CUDA 初始化失败时自动回退到外置 "
             "sd_dedup.exe，当前实际后端显示在引擎页状态区。"),
        ):
            ctitle = QLabel(cap_title)
            ctitle.setStyleSheet("font-weight:600;")
            ctext = QLabel(cap_text)
            ctext.setWordWrap(True)
            ctext.setStyleSheet("color:#8B909A;")
            cap_lay.addWidget(ctitle)
            cap_lay.addWidget(ctext)
        eroot.addWidget(cap_group)
        eroot.addStretch(1)

        erefresh_btn.clicked.connect(self.refresh_engine_status)
        edoc_btn.clicked.connect(self.open_engine_doc)
        ecache_btn.clicked.connect(self.open_engine_cache)
        self.tabs.addTab(self._wrap_scroll(engine_page), "MIX引擎")
        self.engine_page = self.tabs.widget(self.tabs.count() - 1)
        self.refresh_engine_status()

        bgm_btn.clicked.connect(self.choose_bgm)
        music_folder_btn.clicked.connect(self.choose_music_folder)
        self.music_mode.currentIndexChanged.connect(self._on_music_mode)
        self._on_music_mode()
        self.mix_start_btn.clicked.connect(self.start_mix)
        self.mix_cancel_btn.clicked.connect(self.cancel_mix)
        loc_btn.clicked.connect(self.choose_draft_dir)
        loc_reset.clicked.connect(
            lambda: self.mix_draft_dir.clear()
        )

