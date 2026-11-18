# -*- coding: utf-8 -*-
"""
MixCut · 超级混剪工作区（第三阶段）

布局：
  顶部状态条：选中素材数 / 分析状态 / 开始·取消
  基础设置（重要参数，展开）
  素材选择（独立卡片）
  混剪节奏（重要参数，展开）
  高级设置（折叠：智能混剪次要项）
  输出设置（独立卡片）

所有控件仍是主窗口已有参数的“外壳”，不保存假数据，不改算法。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QPushButton,
)

from .components import SectionCard, LabeledField, make_button, StatusBadge


class MixPage(QWidget):
    """把主窗口的混剪参数按 DaVinci 风格重组。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        # ---- 顶部状态条 ----
        top = QHBoxLayout()
        title = QLabel("超级混剪")
        title.setObjectName("Hero")
        top.addWidget(title)

        self.count_badge = StatusBadge("素材 0", "idle")
        top.addWidget(self.count_badge)
        self.analyze_badge = StatusBadge("未分析", "idle")
        top.addWidget(self.analyze_badge)

        top.addStretch(1)

        self.start_btn = QPushButton("▶  开始分析")
        self.start_btn.setMinimumSize(140, 40)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet(
            "QPushButton { background:#FFFFFF; color:#101113; font-weight:700;"
            "font-size:14px; border:none; border-radius:4px; padding:8px 24px; }"
            "QPushButton:hover { background:#E6E7E9; }")
        if getattr(win, "go_analyze", None) is not None:
            self.start_btn.clicked.connect(win.go_analyze)
        top.addWidget(self.start_btn)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # ---- 1 基础设置（重要参数）----
        basic = SectionCard("基础设置", "输出规格与工程数量")
        if getattr(win, "mix_clip_count", None) is not None:
            basic.add_row(LabeledField(
                "生成工程数", win.mix_clip_count,
                "一次生成 N 个独立剪映工程"))
        if getattr(win, "mix_target", None) is not None:
            basic.add_row(LabeledField(
                "单条目标时长", win.mix_target, "成片是大概值，落在 ±浮动 内即停"))
        if getattr(win, "mix_tolerance", None) is not None:
            basic.add_row(LabeledField("时长浮动 ±", win.mix_tolerance))
        if getattr(win, "mix_resolution", None) is not None:
            basic.add_row(LabeledField("输出分辨率", win.mix_resolution))
        if getattr(win, "mix_fps", None) is not None:
            basic.add_row(LabeledField("帧率", win.mix_fps))
        if getattr(win, "info_label", None) is not None:
            basic.add_row(win.info_label)
        col.addWidget(basic)

        # ---- 2 素材选择（独立卡片）----
        rules = SectionCard("素材选择", "素材来源与使用规则")
        if getattr(win, "mix_selector", None) is not None:
            self.selector_holder = QVBoxLayout()
            self.selector_holder.addWidget(win.mix_selector)
            rules.add_layout(self.selector_holder)
        col.addWidget(rules)

        # ---- 3 混剪节奏（重要参数）----
        rhythm = SectionCard("混剪节奏", "片段长度与切换频率")
        if getattr(win, "mix_clip_min", None) is not None:
            rhythm.add_row(LabeledField("最短片段", win.mix_clip_min,
                                        "任何片段都不会短于这个值"))
        if getattr(win, "mix_clip_max", None) is not None:
            rhythm.add_row(LabeledField("最长片段", win.mix_clip_max))
        if getattr(win, "mix_rhythm", None) is not None:
            rhythm.add_row(LabeledField("时长策略", win.mix_rhythm,
                                        "均衡 / 长短交替 / 快速切换 / 舒缓节奏"))
        if getattr(win, "mix_beat_step", None) is not None:
            rhythm.add_row(LabeledField("卡点密度", win.mix_beat_step,
                                        "按每 1/2/4 拍切换"))
        col.addWidget(rhythm)

        # ---- 4 高级设置（折叠，次要参数）----
        advanced = SectionCard("高级设置", "复用 / 排列 / 智能检测（默认即可）")
        if getattr(win, "mix_reuse", None) is not None:
            advanced.add_row(LabeledField("素材复用", win.mix_reuse,
                                          "不勾选时不会重复使用同一段画面"))
        if getattr(win, "mix_shuffle", None) is not None:
            advanced.add_row(LabeledField("排列方式", win.mix_shuffle,
                                          "勾选=随机排列；不勾选=按导入顺序"))
        if getattr(win, "mix_diversity", None) is not None:
            advanced.add_row(LabeledField("重复控制", win.mix_diversity,
                                          "严格会避开同素材紧邻"))
        if getattr(win, "mix_version", None) is not None:
            advanced.add_row(LabeledField("版本策略", win.mix_version,
                                          "多工程时轮换算法倾向"))
        if getattr(win, "mix_video_detect", None) is not None:
            advanced.add_row(LabeledField("画面检测", win.mix_video_detect))
        if getattr(win, "mix_audio_detect", None) is not None:
            advanced.add_row(LabeledField("静音检测", win.mix_audio_detect))
        if getattr(win, "mix_ducking", None) is not None:
            advanced.add_row(LabeledField("音频闪避", win.mix_ducking,
                                          "人声出现时压低 BGM"))
        if getattr(win, "mix_beat_sync", None) is not None:
            advanced.add_row(LabeledField("卡点", win.mix_beat_sync))
        if getattr(win, "mix_weak_chance", None) is not None:
            advanced.add_row(LabeledField("弱拍补切概率", win.mix_weak_chance,
                                          "越低越只在强拍切，节奏更稳"))
        if getattr(win, "mix_beat_tolerance", None) is not None:
            advanced.add_row(LabeledField("卡点容差", win.mix_beat_tolerance,
                                          "切点对齐鼓点允许的时间偏差"))
        advanced.collapse()  # 默认折叠
        col.addWidget(advanced)

        # ---- 4.5 AI 增强（CUDA 引擎，来自 Super Director）----
        ai = SectionCard("AI 增强", "整句切割 / 指纹查重（需要 CUDA 引擎）")
        if getattr(win, "mix_sentence_snap", None) is not None:
            ai.add_row(LabeledField(
                "整句切割", win.mix_sentence_snap,
                "切点对齐语音句尾，避免半句话被切断"))
        if getattr(win, "mix_audio_dedup", None) is not None:
            ai.add_row(LabeledField(
                "指纹查重", win.mix_audio_dedup,
                "跳过重复素材，同组只留最长一条"))
        if getattr(win, "mix_openclip", None) is not None:
            ai.add_row(LabeledField(
                "OpenCLIP", win.mix_openclip,
                "理解候选画面的语义，降低连续相似镜头的优先级"))
        engine_btn = make_button("查看引擎状态", kind="ghost")
        engine_btn.clicked.connect(lambda: _select(win, "engine"))
        ai.add_row(engine_btn)
        col.addWidget(ai)

        # ---- 5 输出设置（独立卡片）----
        output = SectionCard("输出设置", "配乐与转场")
        if getattr(win, "music_mode", None) is not None:
            output.add_row(LabeledField("配乐方式", win.music_mode))
        if getattr(win, "mix_bgm", None) is not None:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            rl.addWidget(win.mix_bgm, 1)
            pick = make_button("选择音频", kind="ghost")
            pick.clicked.connect(lambda: _call(win, "choose_bgm"))
            rl.addWidget(pick)
            output.add_row(LabeledField("BGM 文件", row,
                                        "“手动选择 BGM”模式下生效"))
        if getattr(win, "music_folder", None) is not None:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            rl.addWidget(win.music_folder, 1)
            pick = make_button("选择文件夹", kind="ghost")
            pick.clicked.connect(lambda: _call(win, "choose_music_folder"))
            rl.addWidget(pick)
            output.add_row(LabeledField("音乐文件夹", row,
                                        "“本地随机配乐”模式下从这里随机选"))
        if getattr(win, "mix_blacklist", None) is not None:
            output.add_row(LabeledField("转场黑名单", win.mix_blacklist,
                                        "可手动输入，也可直接点下方关键词，命中只硬切"))
        if getattr(win, "blacklist_picker", None) is not None:
            output.add_row(win.blacklist_picker)
        if getattr(win, "mix_dissolve_dur", None) is not None:
            output.add_row(LabeledField("叠化时长", win.mix_dissolve_dur))
        col.addWidget(output)

        col.addStretch(1)

        # 按钮状态同步
        start_btn = getattr(win, "mix_start_btn", None)
        if start_btn is not None:
            self._sync_buttons()
            start_btn.clicked.connect(lambda *_: self._sync_buttons())

        # 定时刷新素材数/分析状态
        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start()

    def _sync_buttons(self):
        # 开始按钮总是可点，由主窗口内部判断素材是否就绪
        pass

    def _target_enabled(self, name):
        btn = getattr(self.win, name, None)
        return bool(btn is not None and btn.isEnabled())

    def _refresh_status(self):
        """从主窗口真实状态读素材数和分析进度。"""
        win = self.win
        # 素材数：topbar 的计数来自 win._material_paths
        paths = getattr(win, "_material_paths", [])
        n = len(paths) if paths else 0
        self.count_badge.setText(f"素材 {n}")
        # 分析状态：看是否在跑任务
        running = bool(getattr(win, "_mix_running", False)) or \
                  bool(getattr(win, "_analyzing", False))
        if running:
            self.analyze_badge.setText("分析中")
            self.analyze_badge.set_size("running")
        elif n > 0:
            self.analyze_badge.setText("就绪")
            self.analyze_badge.set_size("success")
        else:
            self.analyze_badge.setText("未分析")
            self.analyze_badge.set_size("idle")

    def on_shown(self):
        self._sync_buttons()
        self._refresh_status()


def _call(win, method, *args):
    fn = getattr(win, method, None)
    if callable(fn):
        return fn(*args)
    return None


def _select(win, key):
    """跳到左侧导航的某个页面（引擎能力页在 shell 里注册）。"""
    shell = getattr(win, "shell", None)
    nav = getattr(shell, "nav", None)
    if nav is not None:
        nav.select(key)
