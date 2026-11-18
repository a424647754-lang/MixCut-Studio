# -*- coding: utf-8 -*-
"""参数设置：对齐正式源码「智能混剪」页参数，去掉素材选择，转场黑名单为下滑面板。"""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from . import theme as T
from .ui_kit import Card, Chip, Dot, IconButton, SoftButton
from ...app_paths import detect_draft_dir


BLACKLIST_KEYWORDS = [
    "产品特写", "特写", "近景", "中景", "远景",
    "走路", "转身", "站立", "坐着",
    "模糊", "过曝", "过暗", "手抖", "黑场",
    "人物", "空镜", "街景", "夜景",
]


class Toggle(QWidget):
    """轻量开关，自绘，点击切换。"""

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self._on = checked
        self.setFixedSize(38, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self):
        return self._on

    def setChecked(self, on):
        on = bool(on)
        if on != self._on:
            self._on = on
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._on = not self._on
            self.update()
            e.accept()
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(T.c("#4B3F7A" if self._on else T.LINE), 1))
        p.setBrush(T.c(T.ACCENT_BG if self._on else T.INSET))
        p.drawRoundedRect(r, 10, 10)
        cx = r.right() - 10 if self._on else r.left() + 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.c("#EAE4FF" if self._on else T.TEXT_3))
        p.drawEllipse(QRectF(cx - 6, r.center().y() - 6, 12, 12))
        p.end()


class Field(QWidget):
    def __init__(self, label: str, control: QWidget, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lab = QLabel(label)
        lab.setFont(T.font(11))
        lab.setStyleSheet("color:%s;" % T.TEXT_2)
        lab.setFixedWidth(96)
        lay.addWidget(lab)
        lay.addWidget(control, 1)
        self.setFixedHeight(36)


class ToggleLine(QWidget):
    def __init__(self, label: str, checked: bool = True, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(label)
        lab.setFont(T.font(11))
        lab.setStyleSheet("color:%s;" % T.TEXT_2)
        lay.addWidget(lab, 1)
        self.toggle = Toggle(checked)
        lay.addWidget(self.toggle)
        self.setFixedHeight(32)


def combo(items, current=0):
    c = QComboBox()
    for it in items:
        c.addItem(it)
    c.setCurrentIndex(current)
    c.setFixedHeight(30)
    return c


def spin(value, lo, hi, suffix="", decimals=0, step=1.0):
    if decimals:
        box = QDoubleSpinBox()
        box.setDecimals(decimals)
    else:
        box = QSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    box.setFixedHeight(30)
    box.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return box


class BlacklistPanel(QWidget):
    """点击黑名单行后在下方滑出关键词面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected = {"特写", "模糊"}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.header = QWidget()
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        title = QLabel("转场黑名单")
        title.setFont(T.font(12, QFont.Weight.DemiBold))
        title.setStyleSheet("color:%s;" % T.TEXT)
        self.count = QLabel("已选 %d 个关键词" % len(self.selected))
        self.count.setFont(T.font(10))
        self.count.setStyleSheet("color:%s;" % T.TEXT_3)
        self.arrow = QLabel("▸")
        self.arrow.setFont(T.font(12))
        self.arrow.setStyleSheet("color:%s;" % T.TEXT_3)
        hl.addWidget(title)
        hl.addWidget(self.count)
        hl.addStretch(1)
        hl.addWidget(self.arrow)
        lay.addWidget(self.header)
        self.header.mousePressEvent = self._toggle_click

        self.body = QWidget()
        self.body.setObjectName("BlacklistBody")
        self.body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.body.setStyleSheet(
            "QWidget#BlacklistBody { background:%s; border:1px solid %s; border-radius:10px; }"
            % (T.CARD, T.LINE_SOFT))
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(12, 12, 12, 12)
        bl.setSpacing(8)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        self.chips = {}
        for i, kw in enumerate(BLACKLIST_KEYWORDS):
            chip = Chip(kw)
            chip.setFixedHeight(28)
            chip.setFixedWidth(96)
            chip.setChecked(kw in self.selected)
            chip.clicked.connect(lambda checked, k=kw: self._on_chip(k, checked))
            self.grid.addWidget(chip, i // 6, i % 6)
            self.chips[kw] = chip
        bl.addLayout(self.grid)
        self.tip = QLabel("命中的片段之间只硬切；可多选，点击标签切换")
        self.tip.setFont(T.font(10))
        self.tip.setStyleSheet("color:%s;" % T.TEXT_3)
        bl.addWidget(self.tip)
        self.body.setMaximumHeight(0)
        self.body.setVisible(False)
        lay.addWidget(self.body)

        self.open = False
        self.anim = QPropertyAnimation(self.body, b"maximumHeight", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _toggle_click(self, _e):
        self.toggle()

    def toggle(self):
        self.open = not self.open
        self.arrow.setText("▾" if self.open else "▸")
        if self.open:
            self.body.setVisible(True)
            h = self.body.sizeHint().height()
            start, end = 0, max(90, h)
        else:
            start, end = self.body.height(), 0
        self.anim.stop()
        self.anim.setStartValue(start)
        self.anim.setEndValue(end)
        self.anim.start()
        if not self.open:
            self.anim.finished.connect(self._hide_body)

    def _hide_body(self):
        try:
            self.anim.finished.disconnect(self._hide_body)
        except Exception:
            pass
        if not self.open:
            self.body.setVisible(False)

    def _on_chip(self, kw, checked):
        if checked:
            self.selected.add(kw)
        else:
            self.selected.discard(kw)
        self.count.setText("已选 %d 个关键词" % len(self.selected))


class ParamsPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        self.scroll = scroll

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(T.PAGE_PAD, 18, T.PAGE_PAD, 24)
        root.setSpacing(16)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        h = QLabel("参数设置")
        h.setFont(T.font(30, QFont.Weight.DemiBold))
        s = QLabel("对齐正式「智能混剪」参数 · 不含素材选择 · 真实后台")
        s.setFont(T.font(12))
        s.setStyleSheet("color:%s;" % T.TEXT_3)
        titles.addWidget(h)
        titles.addWidget(s)
        head.addLayout(titles, 1)
        self.btn_engine = SoftButton("查看「MIX引擎」页", "ghost", height=34, min_width=148)
        self.btn_cancel = SoftButton("取消", "ghost", height=34, min_width=74)
        self.btn_run = SoftButton("开始智能混剪", "primary", "play", min_width=124)
        head.addWidget(self.btn_engine)
        head.addWidget(self.btn_cancel)
        head.addWidget(self.btn_run)
        root.addLayout(head)

        # 基础参数
        base = Card(padding=18, spacing=10)
        base.lay.addWidget(self._title("素材库 · 参数配置", "画布、时长与片段长度"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(2)
        for col in range(3):
            grid.setColumnStretch(col, 1)

        self.resolution = combo(["1080P 横屏", "4K 横屏", "1080P 竖屏", "4K 竖屏"], 1)
        self.fps = combo(["23.976 fps", "24 fps", "25 fps", "29.97 fps", "30 fps", "60 fps"], 3)
        self.target = spin(30, 3, 3600, " 秒")
        self.tolerance = spin(2, 0, 60, " 秒", decimals=1, step=0.5)
        self.count = spin(1, 1, 100)
        self.clip_min = spin(2, 0.5, 60, " 秒", decimals=1, step=0.5)
        self.clip_max = spin(5, 0.5, 120, " 秒", decimals=1, step=0.5)
        self.diversity = combo(["适中（推荐）", "严格（尽量不重复）", "宽松（保留随机）"])
        self.rhythm = combo(["均衡时长", "长短交替", "快速切换", "舒缓节奏",
                             "脉冲节奏（短长强对比）", "断奏（极短密集）", "电影感（长镜头为主）"])
        self.version = combo(["均衡混剪", "多版本轮换（快节奏/多样性/长短交替/音乐优先）"])
        self.beat_step = combo(["自动（按 BPM）", "每 1 拍切换", "每 2 拍切换", "每 4 拍切换"])

        fields = [
            ("清晰度", self.resolution), ("帧率", self.fps), ("目标时长", self.target),
            ("时长浮动 ±", self.tolerance), ("生成工程数", self.count), ("最短片段", self.clip_min),
            ("最长片段", self.clip_max), ("重复控制", self.diversity), ("时长策略", self.rhythm),
            ("版本策略", self.version), ("卡点密度", self.beat_step),
        ]
        for i, (label, ctrl) in enumerate(fields):
            grid.addWidget(Field(label, ctrl), i // 3, i % 3)
        base.lay.addLayout(grid)

        toggles = QHBoxLayout()
        toggles.setSpacing(28)
        self.t_shuffle = ToggleLine("随机排列素材", True)
        self.t_reuse = ToggleLine("允许素材重复使用", True)
        self.t_sentence = ToggleLine("整句切割（对齐语音句尾）", False)
        self.t_dedup = ToggleLine("指纹查重（跳过重复素材）", False)
        self.t_oclip = ToggleLine("OpenCLIP 语义多样性评分", True)
        for w in (self.t_shuffle, self.t_reuse, self.t_sentence, self.t_dedup, self.t_oclip):
            toggles.addWidget(w)
        toggles.addStretch(1)
        base.lay.addLayout(toggles)
        root.addWidget(base)

        # 智能粗剪筛选引擎（源码同名面板）
        eng = Card(padding=18, spacing=8)
        eh = QHBoxLayout()
        eh.addWidget(self._title("智能粗剪筛选引擎", "预设、硬件加速与检测组件"))
        eh.addStretch(1)
        self.btn_fast = SoftButton("快速批量", "secondary", height=30, radius=8)
        self.btn_fine = SoftButton("精细质检", "secondary", height=30, radius=8)
        self.btn_selfcheck = SoftButton("强制自检", "ghost", height=30, radius=8)
        self.btn_engine_state = SoftButton("查看引擎状态", "ghost", height=30, radius=8)
        for b in (self.btn_fast, self.btn_fine, self.btn_selfcheck, self.btn_engine_state):
            eh.addWidget(b)
        eng.lay.addLayout(eh)
        hw = QHBoxLayout()
        hw.setSpacing(10)
        lab = QLabel("硬件加速")
        lab.setFont(T.font(11))
        lab.setStyleSheet("color:%s;" % T.TEXT_3)
        hw.addWidget(lab)
        self.hw_cpu = QRadioButton("CPU")
        self.hw_gpu = QRadioButton("GPU")
        self.hw_cpu.setChecked(True)
        self.hw_group = QButtonGroup(self)
        self.hw_group.addButton(self.hw_cpu)
        self.hw_group.addButton(self.hw_gpu)
        hw.addWidget(self.hw_cpu)
        hw.addWidget(self.hw_gpu)
        self.engine_status = QLabel("就绪 · 5 个组件可用")
        self.engine_status.setFont(T.font(10))
        self.engine_status.setStyleSheet("color:%s;" % T.TEXT_3)
        hw.addStretch(1)
        hw.addWidget(self.engine_status)
        eng.lay.addLayout(hw)
        comps = QGridLayout()
        comps.setHorizontalSpacing(18)
        comps.setVerticalSpacing(4)
        self.comp_boxes = {}
        specs = [
            ("amverge", "amverge 静止空镜检测", True),
            ("scenedetect", "PySceneDetect 镜头校验", True),
            ("ffmpeg_audio_black", "FFmpeg 静音 + 黑屏", True),
            ("videoscope", "VideoScope 画质缺陷", False),
            ("transnetv2", "TransNetV2 镜头切变", False),
        ]
        for i, (key, text, on) in enumerate(specs):
            cb = QCheckBox(text)
            cb.setChecked(on)
            cb.setFont(T.font(11))
            if key == "amverge":
                cb.setEnabled(False)
            self.comp_boxes[key] = cb
            comps.addWidget(cb, i // 3, i % 3)
        eng.lay.addLayout(comps)
        root.addWidget(eng)

        mid = QHBoxLayout()
        mid.setSpacing(16)
        root.addLayout(mid)

        # 废镜头检测
        bad = Card(padding=18, spacing=6)
        bad.lay.addWidget(self._title("废镜头检测", "混剪前先剔除无效片段"))
        self.bad_pic = ToggleLine("画面检测", True)
        self.bad_audio = ToggleLine("静音检测", True)
        bad.lay.addWidget(self.bad_pic)
        bad.lay.addWidget(self.bad_audio)
        self.black_min = spin(0.5, 0.1, 10, " 秒", decimals=1, step=0.1)
        self.freeze_min = spin(1.0, 0.1, 20, " 秒", decimals=1, step=0.1)
        self.silence_min = spin(1.2, 0.1, 20, " 秒", decimals=1, step=0.1)
        bad.lay.addWidget(Field("黑屏最短", self.black_min))
        bad.lay.addWidget(Field("静止最短", self.freeze_min))
        bad.lay.addWidget(Field("静音最短", self.silence_min))
        bad.lay.addStretch(1)
        mid.addWidget(bad, 1)

        # 配乐 / 卡点
        music = Card(padding=18, spacing=6)
        music.lay.addWidget(self._title("自动配乐 / 卡点 / 闪避", "BGM 与音量策略"))
        self.bgm_mode = combo(["手动选择 BGM", "本地随机配乐", "云端随机（暂未开放）"])
        music.lay.addWidget(Field("配乐方式", self.bgm_mode))
        self.bgm = QLineEdit()
        self.bgm.setPlaceholderText("选择音频文件")
        self.bgm.setFixedHeight(30)
        brow = QHBoxLayout()
        brow.setSpacing(8)
        brow.addWidget(self.bgm, 1)
        self.btn_bgm = SoftButton("选择音频", "secondary", height=30, radius=8)
        brow.addWidget(self.btn_bgm)
        music.lay.addLayout(brow)
        self.music_dir = QLineEdit()
        self.music_dir.setPlaceholderText("本地音乐文件夹")
        self.music_dir.setFixedHeight(30)
        mdir = QHBoxLayout()
        mdir.setSpacing(8)
        mdir.addWidget(self.music_dir, 1)
        self.btn_music_dir = SoftButton("选择文件夹", "secondary", height=30, radius=8)
        mdir.addWidget(self.btn_music_dir)
        music.lay.addLayout(mdir)
        mrow = QHBoxLayout()
        mrow.setSpacing(24)
        self.t_beat = ToggleLine("卡点", True)
        self.t_duck = ToggleLine("音频闪避", True)
        mrow.addWidget(self.t_beat)
        mrow.addWidget(self.t_duck)
        mrow.addStretch(1)
        music.lay.addLayout(mrow)
        vrow = QHBoxLayout()
        vrow.setSpacing(10)
        self.vol_normal = spin(1.0, 0, 1, "", decimals=2, step=0.05)
        self.vol_duck = spin(0.35, 0, 1, "", decimals=2, step=0.05)
        vrow.addWidget(Field("正常音量", self.vol_normal))
        vrow.addWidget(Field("闪避音量", self.vol_duck))
        music.lay.addLayout(vrow)
        music.lay.addStretch(1)
        mid.addWidget(music, 1)

        # 转场规则 + 黑名单
        trans = Card(padding=18, spacing=8)
        trans.lay.addWidget(self._title("自动转场 / 黑名单", "按镜头长度决定硬切或叠化"))
        self.t_trans = ToggleLine("启用规则转场", True)
        trans.lay.addWidget(self.t_trans)
        trow = QHBoxLayout()
        trow.setSpacing(12)
        self.trans_short = spin(2.0, 0.2, 10, " 秒", decimals=1, step=0.1)
        self.trans_long = spin(4.0, 0.5, 30, " 秒", decimals=1, step=0.1)
        self.trans_dissolve = spin(0.4, 0.1, 3, " 秒", decimals=1, step=0.1)
        trow.addWidget(Field("短镜头阈值", self.trans_short))
        trow.addWidget(Field("长镜头阈值", self.trans_long))
        trow.addWidget(Field("叠化时长", self.trans_dissolve))
        trans.lay.addLayout(trow)
        self.blacklist = BlacklistPanel()
        trans.lay.addWidget(self.blacklist)
        trans.lay.addStretch(1)
        root.addWidget(trans)

        # 草稿位置
        loc = Card(padding=18, spacing=8)
        loc.lay.addWidget(self._title("草稿位置", "生成剪映工程的输出目录"))
        lrow = QHBoxLayout()
        lrow.setSpacing(10)
        _jy_draft = detect_draft_dir()
        self.draft_dir = QLineEdit(_jy_draft or "D:\\MixCut_Drafts")
        self.draft_dir.setFixedHeight(30)
        lrow.addWidget(self.draft_dir, 1)
        self.btn_loc = SoftButton("选择位置", "secondary", "folder", height=30, min_width=92)
        self.btn_reset = SoftButton("恢复默认", "ghost", height=30, min_width=80)
        lrow.addWidget(self.btn_loc)
        lrow.addWidget(self.btn_reset)
        loc.lay.addLayout(lrow)
        root.addWidget(loc)
        root.addStretch(1)

        self._defaults = []
        for w in (self.resolution, self.fps, self.diversity, self.rhythm,
                  self.version, self.beat_step):
            self._defaults.append(("combo", w, w.currentIndex()))
        for w in (self.target, self.tolerance, self.count, self.clip_min, self.clip_max):
            self._defaults.append(("spin", w, w.value()))
        for w in (self.vol_normal, self.vol_duck, self.black_min, self.freeze_min,
                  self.silence_min, self.trans_short, self.trans_long,
                  self.trans_dissolve):
            self._defaults.append(("spin", w, w.value()))
        for w in (self.t_shuffle, self.t_reuse, self.t_sentence,
                  self.t_dedup, self.t_oclip, self.t_beat, self.t_duck,
                  self.t_trans, self.bad_pic, self.bad_audio):
            self._defaults.append(("toggle", w, w.toggle.isChecked()))
        self._defaults.append(("combo", self.bgm_mode, self.bgm_mode.currentIndex()))
        self._defaults.append(("text", self.draft_dir, self.draft_dir.text()))
        self._default_blacklist = set(self.blacklist.selected)
        self._default_comp = {k: b.isChecked() for k, b in self.comp_boxes.items()}
        self._default_bgm = self.bgm.text()
        self._default_music_dir = self.music_dir.text()

        self.btn_run.clicked.connect(self._start)
        self.btn_run.setEnabled(False)
        self.btn_run.setToolTip("请先在「AI 分析」页完成素材分析")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_engine.clicked.connect(self._open_engine)
        self.btn_fast.clicked.connect(lambda: self._preset("快速批量"))
        self.btn_fine.clicked.connect(lambda: self._preset("精细质检"))
        self.btn_selfcheck.clicked.connect(self._self_check)
        self.btn_engine_state.clicked.connect(self._open_engine)
        self.btn_bgm.clicked.connect(lambda: self._choose(self.bgm, "音频文件"))
        self.btn_music_dir.clicked.connect(lambda: self._choose(self.music_dir, "音乐文件夹"))
        self.btn_loc.clicked.connect(lambda: self._choose(self.draft_dir, "草稿目录"))
        self.btn_reset.clicked.connect(self._reset_defaults)
        theme_apply = self.win.controller.stateChanged if hasattr(self.win, "controller") else None
        if theme_apply:
            theme_apply.connect(self._sync_status)

    def _title(self, t, sub):
        box = QWidget()
        box.setFixedHeight(42)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        a = QLabel(t)
        a.setFont(T.font(14, QFont.Weight.DemiBold))
        a.setStyleSheet("color:%s;" % T.TEXT)
        b = QLabel(sub)
        b.setFont(T.font(10))
        b.setStyleSheet("color:%s;" % T.TEXT_3)
        lay.addWidget(a)
        lay.addWidget(b)
        return box

    def _start(self):
        if not self.analysis_ready():
            self.win.controller.log("参数设置 · 尚未完成素材分析，不能开始混剪")
            self.refresh_run_state()
            return
        paths = self.win.active_paths()
        if not paths:
            self.win.controller.log("参数设置 · 素材库为空，无法开始混剪")
            self.win.controller.log("参数设置 · 请先在素材库导入视频")
            return
        self.win.controller.log("参数设置 · 同步参数并调用后台智能混剪")
        self.win.start_real_mix()

    def analysis_ready(self):
        """分析是否已完成：未完成时「开始智能混剪」保持灰色。"""
        page = getattr(self.win, "page_analyze", None)
        check = getattr(page, "analysis_ready", None)
        if callable(check):
            return bool(check())
        return False

    def refresh_run_state(self):
        ready = self.analysis_ready()
        self.btn_run.setEnabled(ready)
        self.btn_run.setToolTip(
            "开始混剪并生成剪映草稿" if ready
            else "请先在「AI 分析」页完成素材分析")

    def on_shown(self):
        self.refresh_run_state()

    # -----------------------------------------------------
    # 工程保存 / 恢复（新 UI 参数才是真实来源）
    # -----------------------------------------------------
    def capture(self):
        return {
            "resolution": self.resolution.currentIndex(),
            "fps": self.fps.currentIndex(),
            "target": self.target.value(),
            "tolerance": self.tolerance.value(),
            "count": self.count.value(),
            "clip_min": self.clip_min.value(),
            "clip_max": self.clip_max.value(),
            "diversity": self.diversity.currentIndex(),
            "rhythm": self.rhythm.currentIndex(),
            "version": self.version.currentIndex(),
            "beat_step": self.beat_step.currentIndex(),
            "toggles": {
                "shuffle": self.t_shuffle.toggle.isChecked(),
                "reuse": self.t_reuse.toggle.isChecked(),
                "sentence": self.t_sentence.toggle.isChecked(),
                "dedup": self.t_dedup.toggle.isChecked(),
                "openclip": self.t_oclip.toggle.isChecked(),
                "beat": self.t_beat.toggle.isChecked(),
                "ducking": self.t_duck.toggle.isChecked(),
                "transition": self.t_trans.toggle.isChecked(),
                "bad_pic": self.bad_pic.toggle.isChecked(),
                "bad_audio": self.bad_audio.toggle.isChecked(),
            },
            "vol_normal": self.vol_normal.value(),
            "vol_duck": self.vol_duck.value(),
            "black_min": self.black_min.value(),
            "freeze_min": self.freeze_min.value(),
            "silence_min": self.silence_min.value(),
            "trans_short": self.trans_short.value(),
            "trans_long": self.trans_long.value(),
            "trans_dissolve": self.trans_dissolve.value(),
            "blacklist": sorted(self.blacklist.selected),
            "comp": {k: b.isChecked() for k, b in self.comp_boxes.items()},
            "hw": "gpu" if self.hw_gpu.isChecked() else "cpu",
            "draft_dir": self.draft_dir.text().strip(),
            "bgm_mode": self.bgm_mode.currentIndex(),
            "bgm": self.bgm.text().strip(),
            "music_dir": self.music_dir.text().strip(),
        }

    def restore(self, d):
        if not d:
            return False
        def idx(widget, key, default=0):
            try:
                widget.setCurrentIndex(int(d.get(key, default)))
            except (TypeError, ValueError):
                widget.setCurrentIndex(default)

        def val(widget, key, default):
            try:
                widget.setValue(type(widget.value())(d.get(key, default)))
            except (TypeError, ValueError):
                widget.setValue(default)

        idx(self.resolution, "resolution")
        idx(self.fps, "fps")
        idx(self.diversity, "diversity")
        idx(self.rhythm, "rhythm")
        idx(self.version, "version")
        idx(self.beat_step, "beat_step")
        idx(self.bgm_mode, "bgm_mode")
        for widget, key, default in (
                (self.target, "target", 30), (self.tolerance, "tolerance", 2),
                (self.count, "count", 1), (self.clip_min, "clip_min", 2),
                (self.clip_max, "clip_max", 5), (self.vol_normal, "vol_normal", 1.0),
                (self.vol_duck, "vol_duck", 0.35), (self.black_min, "black_min", 0.5),
                (self.freeze_min, "freeze_min", 1.0), (self.silence_min, "silence_min", 1.2),
                (self.trans_short, "trans_short", 2.0), (self.trans_long, "trans_long", 4.0),
                (self.trans_dissolve, "trans_dissolve", 0.4)):
            val(widget, key, default)
        tg = d.get("toggles") or {}
        for key, line in (("shuffle", self.t_shuffle), ("reuse", self.t_reuse),
                          ("sentence", self.t_sentence), ("dedup", self.t_dedup),
                          ("openclip", self.t_oclip), ("beat", self.t_beat),
                          ("ducking", self.t_duck), ("transition", self.t_trans),
                          ("bad_pic", self.bad_pic), ("bad_audio", self.bad_audio)):
            if key in tg:
                line.toggle.setChecked(bool(tg[key]))
        words = d.get("blacklist")
        if isinstance(words, list):
            self.blacklist.selected = {w for w in words if w in self.blacklist.chips}
            for kw, chip in self.blacklist.chips.items():
                chip.setChecked(kw in self.blacklist.selected)
            self.blacklist.count.setText("已选 %d 个关键词" % len(self.blacklist.selected))
        comp = d.get("comp") or {}
        for key, box in self.comp_boxes.items():
            if key in comp:
                box.setChecked(bool(comp[key]))
        if d.get("hw") == "gpu" and self.hw_gpu.isEnabled():
            self.hw_gpu.setChecked(True)
        else:
            self.hw_cpu.setChecked(True)
        if d.get("draft_dir"):
            self.draft_dir.setText(str(d["draft_dir"]))
        if d.get("bgm") is not None:
            self.bgm.setText(str(d.get("bgm") or ""))
        if d.get("music_dir") is not None:
            self.music_dir.setText(str(d.get("music_dir") or ""))
        self.refresh_run_state()
        return True

    def _cancel(self):
        b = self.win.backend
        if b.ready:
            b.cancel_mix()
            self.win.controller.log("参数设置 · 已向后台混剪任务发送取消")
            return
        self.win.controller.log("参数设置 · 后台混剪任务未运行，无需取消")

    def _open_engine(self):
        self.win.controller.log("参数设置 · 跳转「MIX引擎」状态与缓存（设置页）")
        self.win.go_page("settings")

    _RES_CANVAS = {
        "1080P 横屏": (1920, 1080),
        "4K 横屏": (3840, 2160),
        "1080P 竖屏": (1080, 1920),
        "4K 竖屏": (2160, 3840),
    }

    def canvas_size(self):
        """清晰度/帧率 → 引擎画布 (宽, 高, 整数帧率)。

        旧后台的清晰度预设只有竖屏尺寸，横屏/4K 由这里直接交给引擎，
        避免用下拉索引硬套旧预设把画布选错。
        """
        width, height = self._RES_CANVAS.get(
            self.resolution.currentText(), (1080, 1920))
        text = self.fps.currentText().split(" ")[0].strip()
        try:
            fps = float(text)
        except ValueError:
            fps = 30.0
        return width, height, int(round(fps))

    def _preset(self, name):
        b = self.win.backend
        if b.ready or not b.load_error:
            b.filter_preset(name)
        if name == "快速批量":
            for key, cb in self.comp_boxes.items():
                if cb.isEnabled():
                    cb.setChecked(key in ("scenedetect", "ffmpeg_audio_black"))
            self.hw_cpu.setChecked(True)
            self.engine_status.setText("已应用「快速批量」预设")
        else:
            for key, cb in self.comp_boxes.items():
                if cb.isEnabled():
                    cb.setChecked(True)
            self.hw_gpu.setChecked(True)
            self.engine_status.setText("已应用「精细质检」预设")
        self.win.controller.log("参数设置 · 筛选引擎预设：%s" % name)

    def _self_check(self):
        b = self.win.backend
        if b.load_error and not b.ready:
            self.engine_status.setText("后台桥不可用 · 无法自检")
            self.win.controller.log("参数设置 · 强制自检未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        ok = b.force_self_check(show_dialog=False)
        self.engine_status.setText(
            "强制自检完成 · 组件可用" if ok else "强制自检未通过 · 请检查 ffmpeg")
        self.win.controller.log("参数设置 · 强制自检：%s" %
                                ("后台组件就绪" if ok else "后台返回失败"))

    def _choose(self, field, what):
        b = self.win.backend
        if b.load_error and not b.ready:
            self.win.controller.log("参数设置 · 选择%s未执行：%s" % (
                what, b.load_error.splitlines()[-1]))
            return
        value = ""
        if what == "音频文件":
            value, _ = QFileDialog.getOpenFileName(
                self, "选择 BGM", field.text().strip(),
                "音频 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        elif what == "音乐文件夹":
            value = QFileDialog.getExistingDirectory(
                self, "选择本地音乐文件夹", field.text().strip())
        elif what == "草稿目录":
            value = QFileDialog.getExistingDirectory(
                self, "选择草稿目录", field.text().strip())
        if value:
            field.setText(value)
            if what == "音频文件":
                b.set_bgm_path(value)
            elif what == "音乐文件夹":
                b.set_music_folder(value)
            elif what == "草稿目录":
                b.set_draft_dir(value)
            self.win.controller.log("参数设置 · 选择%s：%s" % (what, value))
            return
        self.win.controller.log("参数设置 · 未选择%s" % what)

    def _reset_defaults(self):
        for kind, w, val in self._defaults:
            if kind == "combo":
                w.setCurrentIndex(val)
            elif kind == "spin":
                w.setValue(val)
            elif kind == "toggle":
                w.toggle._on = val
                w.toggle.update()
            else:
                w.setText(val)
        # 黑名单、组件勾选、BGM/音乐目录也要一起回默认，否则“恢复默认”只恢复一半
        for kw, chip in self.blacklist.chips.items():
            chip.setChecked(kw in self._default_blacklist)
            chip.update()
        self.blacklist.selected = set(self._default_blacklist)
        self.blacklist.count.setText("已选 %d 个关键词" % len(self.blacklist.selected))
        for key, box in self.comp_boxes.items():
            box.setChecked(bool(self._default_comp.get(key, box.isChecked())))
        self.bgm.setText(self._default_bgm)
        self.music_dir.setText(self._default_music_dir)
        self.hw_cpu.setChecked(True)
        self.win.controller.log("参数设置 · 已恢复默认参数")

    def _sync_status(self):
        pass
