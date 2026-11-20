# -*- coding: utf-8 -*-
"""判废规则设置弹窗：勾选因素 + 拉动严格程度。

界面原则
--------
· 每个因素一行：勾选框 + 滑动条 + 当前档位文字（低/偏低/中/偏高/高）
· 不显示裸数值，用户不需要理解拉普拉斯方差之类的概念
· 说明文字精简到一行，只说「调高会怎样」
"""
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea, QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider,
    QVBoxLayout, QWidget,
)

from mixcut.verdict_config import (
    ANALYSIS_MODE_ORDER, ANALYSIS_MODE_SAMPLES, ANALYSIS_MODE_TEXT,
    DEFAULT_MIN_SCORE, DIMENSIONS, GROUPS, LEVELS, LEVEL_COUNT,
    VerdictConfig,
)

# 差分占比档位的显示
RATIO_LABELS = ["80%", "65%", "50%", "35%", "20%"]

# 质量分档位
SCORE_VALUES = [10, 18, 25, 33, 45]


class _SafeSlider(QSlider):
    """只响应左键的滑块；中键与滚轮都转交给外层滚动区。

    为什么需要
    ----------
    QSlider 默认行为对「滚动页面」很不友好：
      · 按住**中键**拖动会改变滑块值（实测 2 → 1）
      · **滚轮**滚动页面时如果光标划过滑块，页面不动、值却被改了（实测 0 → 3）

    用户习惯是用中键拖动页面、用滚轮翻页，这两种都被误改成参数。
    本类把中键与非左键的按下事件直接忽略并转发给滚动祖先，
    滚轮也转发，只有左键才能真正拖动。
    """

    def _forward_to_scroll(self, event):
        """把事件转给最近的 QAbstractScrollArea 的 viewport，让页面正常滚动。"""
        node = self.parentWidget()
        while node is not None:
            if isinstance(node, QAbstractScrollArea):
                vp = node.viewport()
                pos = vp.mapFromGlobal(event.globalPosition().toPoint())
                if event.type() == QEvent.Type.Wheel:
                    new_ev = QWheelEvent(
                        QPointF(pos), event.globalPosition(),
                        event.pixelDelta(), event.angleDelta(),
                        event.buttons(), event.modifiers(),
                        event.phase(), event.inverted())
                else:
                    new_ev = QMouseEvent(
                        event.type(), QPointF(pos), event.globalPosition(),
                        event.button(), event.buttons(), event.modifiers())
                QApplication.sendEvent(vp, new_ev)
                return True
            node = node.parentWidget()
        return False

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            # 中键/右键按下：不改变滑块，转为页面拖动
            if self._forward_to_scroll(event):
                event.accept()
                return
            event.ignore()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            if self._forward_to_scroll(event):
                event.accept()
                return
            event.ignore()
            return
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        # 滚轮一律转给页面，不改值
        if self._forward_to_scroll(event):
            event.accept()
            return
        event.ignore()


class _LevelSlider(QWidget):
    """勾选框 + 滑动条 + 档位文字的一行。

    交互规则（关键）
    ----------------
    弹窗内容是可滚动的，用户习惯「按住中键拖动页面」或「滚轮翻页」。
    如果滑块响应中键或滚轮，滚动页面时参数就会被误改。因此：

      · **中键**：忽略，并把事件转给滚动区，让页面正常拖动
      · **滚轮**：忽略，同样转发给滚动区
      · **左键**：正常拖动改档位

    与项目里 ``WheelGuard``（数字框/下拉框必须点过才吃滚轮）保持同一思路。

    ``on_change`` 在用户拖动与程序设置（``set_value``）时都会回调，
    这样预设按钮改档位时控制器也能拿到新值。
    """

    def __init__(self, label, levels_text, value, parent=None, on_change=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.slider = _SafeSlider(Qt.Horizontal)
        self.slider.setRange(0, len(levels_text) - 1)
        self.slider.setValue(value)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.setMinimumWidth(180)
        lay.addWidget(self.slider, 1)

        self.value_label = QLabel(levels_text[value])
        self.value_label.setFixedWidth(44)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setStyleSheet(
            "color:#8AB4F8;font-size:12px;font-weight:600;")
        lay.addWidget(self.value_label)

        self._texts = levels_text
        self._on_change_cb = on_change
        self.slider.valueChanged.connect(self._on_change)

    def _on_change(self, v):
        idx = max(0, min(len(self._texts) - 1, v))
        self.value_label.setText(self._texts[idx])
        if callable(self._on_change_cb):
            self._on_change_cb(idx)

    def value(self):
        return self.slider.value()

    def set_value(self, v):
        idx = max(0, min(len(self._texts) - 1, int(v)))
        self.slider.blockSignals(True)
        self.slider.setValue(idx)
        self.slider.blockSignals(False)
        self.value_label.setText(self._texts[idx])
        # 程序设置也要通知控制器，否则配置不会跟着变
        if callable(self._on_change_cb):
            self._on_change_cb(idx)


class VerdictDialog(QDialog):
    """判废规则设置。

    用法：
        dlg = VerdictDialog(cfg, parent, sample_count=6)
        if dlg.exec() == QDialog.Accepted:
            cfg = dlg.result_config()
    """

    def __init__(self, config=None, parent=None, sample_count=0):
        super().__init__(parent)
        self._cfg = VerdictConfig.from_dict(
            config.to_dict() if isinstance(config, VerdictConfig) else config)
        self._sample_count = sample_count
        self._rows = {}

        self.setWindowTitle("分析设置")
        self.setMinimumWidth(520)
        self.setMinimumHeight(520)
        self.setStyleSheet("QDialog{background:#16181C;}")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(10)

        # ---- 分析模式（一站式：先选模式，再选判废规则）----
        mode_bar = QHBoxLayout()
        mode_bar.setSpacing(8)
        mode_lab = QLabel("分析模式")
        mode_lab.setFixedWidth(64)
        mode_lab.setStyleSheet("color:#C8CCD2;font-size:12px;")
        mode_bar.addWidget(mode_lab)

        self._mode_btns = {}
        for key in ANALYSIS_MODE_ORDER:
            text, tip = ANALYSIS_MODE_TEXT[key]
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setChecked(key == self._cfg.analysis_mode)
            btn.clicked.connect(lambda _=False, k=key: self._on_mode(k))
            btn.setStyleSheet(
                "QPushButton{background:transparent;color:#C8CCD2;"
                "border:1px solid #35383D;border-radius:4px;"
                "padding:4px 16px;font-size:12px;}"
                "QPushButton:checked{background:#8AB4F8;color:#101113;"
                "border-color:#8AB4F8;font-weight:600;}"
                "QPushButton:hover{background:#2A2D31;}"
                "QPushButton:checked:hover{background:#A8C8FF;}")
            mode_bar.addWidget(btn)
            self._mode_btns[key] = btn
        mode_bar.addStretch(1)
        root.addLayout(mode_bar)

        # 采样点提示：不同模式的采样数量差异很大，直接影响判废灵敏度
        self._mode_note = QLabel("")
        self._mode_note.setStyleSheet("color:#5F6368;font-size:11px;"
                                      "padding-left:72px;")
        root.addWidget(self._mode_note)
        self._update_mode_note()

        # ---- 画面分析（可选）----
        # 放在预设按钮之上：它是下方「预设 + 因素 + 整段判定」这一整块的标题，
        # 与上面的分析模式（必选）区分开。
        pane_title = QLabel("画面分析（可选）")
        pane_title.setStyleSheet(
            "color:#E8EAED;font-size:13px;font-weight:600;"
            "padding-top:6px;")
        root.addWidget(pane_title)

        # ---- 预设 ----
        preset_bar = QHBoxLayout()
        preset_bar.setSpacing(6)
        for key, label, tip in (
            ("loose", "宽松", "只拦全黑全白"),
            ("balanced", "均衡", "推荐，基础画质 + 空镜/发灰"),
            ("strict", "严格", "全部维度都抓，可能误伤风格化素材"),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:transparent;color:#C8CCD2;"
                "border:1px solid #35383D;border-radius:4px;"
                "padding:3px 12px;font-size:12px;}"
                "QPushButton:hover{background:#2A2D31;}")
            btn.clicked.connect(lambda _=False, k=key: self._apply_preset(k))
            preset_bar.addWidget(btn)
        preset_bar.addStretch(1)
        root.addLayout(preset_bar)

        # ---- 因素列表 ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 6, 0)
        box.setSpacing(4)

        for group_name, keys in GROUPS:
            cap = QLabel(group_name)
            cap.setStyleSheet(
                "color:#8A8F98;font-size:11px;font-weight:600;"
                "padding:8px 0 2px 2px;")
            box.addWidget(cap)
            for key in keys:
                spec = DIMENSIONS.get(key)
                if spec is not None:
                    box.addWidget(self._build_row(key, spec))
        box.addStretch(1)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        # ---- 片段级 ----
        root.addWidget(self._build_segment_box())

        # ---- 按钮 ----
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = btns.button(QDialogButtonBox.Ok)
        ok.setText("开始分析")
        ok.setStyleSheet(
            "QPushButton{background:#8AB4F8;color:#101113;border:none;"
            "border-radius:5px;padding:7px 20px;font-weight:600;}"
            "QPushButton:hover{background:#FFFFFF;}")
        cancel = btns.button(QDialogButtonBox.Cancel)
        cancel.setText("取消")
        cancel.setStyleSheet(
            "QPushButton{background:transparent;color:#C8CCD2;"
            "border:1px solid #35383D;border-radius:5px;padding:7px 16px;}"
            "QPushButton:hover{background:#2A2D31;}")
        root.addWidget(btns)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        self._sync_enabled()

    # ---- 单行 ----

    def _build_row(self, key, spec):
        row = QFrame()
        row.setStyleSheet(
            "QFrame{background:#1C1E22;border:none;border-radius:5px;}")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(11, 8, 11, 8)
        lay.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(10)

        chk = QCheckBox(spec["label"])
        chk.setCursor(Qt.PointingHandCursor)
        chk.setFixedWidth(72)
        chk.setStyleSheet(
            "QCheckBox{color:#E8EAED;font-size:13px;font-weight:600;}"
            "QCheckBox::indicator{width:14px;height:14px;}")
        chk.setChecked(self._cfg.is_on(key))
        chk.toggled.connect(lambda checked, k=key: self._on_toggle(k, checked))
        head.addWidget(chk)

        slider = _LevelSlider(
            spec["label"], LEVELS, self._cfg.level(key),
            on_change=lambda v, k=key: self._on_level(k, v))
        head.addWidget(slider, 1)

        lay.addLayout(head)

        # 说明只留一行，且只说调高会怎样
        desc = QLabel(spec["desc"])
        desc.setWordWrap(False)
        desc.setStyleSheet("color:#5F6368;font-size:11px;")
        lay.addWidget(desc)

        self._rows[key] = {"chk": chk, "slider": slider, "desc": desc}

        # 手抖在采样太少时给出提示
        if key == "shake" and self._sample_count and self._sample_count < 4:
            warn = QLabel("当前只有 %d 个采样点，参考价值有限" % self._sample_count)
            warn.setStyleSheet("color:#E7B06B;font-size:11px;")
            lay.addWidget(warn)
            self._rows[key]["warn"] = warn

        return row

    def _build_segment_box(self):
        box = QFrame()
        box.setStyleSheet(
            "QFrame{background:#1C1E22;border:none;border-radius:5px;}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(11, 8, 11, 8)
        lay.setSpacing(6)

        cap = QLabel("整段判定")
        cap.setStyleSheet("color:#E8EAED;font-size:13px;font-weight:600;")
        lay.addWidget(cap)

        # 差帧比例
        r1 = QHBoxLayout()
        r1.setSpacing(10)
        l1 = QLabel("差帧占比")
        l1.setFixedWidth(72)
        l1.setStyleSheet("color:#C8CCD2;font-size:12px;")
        r1.addWidget(l1)

        self.ratio_slider = _LevelSlider(
            "差帧占比", RATIO_LABELS, self._cfg.bad_ratio_level(),
            on_change=self._on_ratio_level)
        r1.addWidget(self.ratio_slider, 1)
        lay.addLayout(r1)

        # 质量分
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        l2 = QLabel("整体质量")
        l2.setFixedWidth(72)
        l2.setStyleSheet("color:#C8CCD2;font-size:12px;")
        r2.addWidget(l2)

        self.score_slider = _LevelSlider(
            "整体质量", LEVELS, self._score_to_level(self._cfg.min_score),
            on_change=self._on_score_level)
        r2.addWidget(self.score_slider, 1)
        lay.addLayout(r2)

        note = QLabel("单个采样点出问题不会直接判废整段")
        note.setStyleSheet("color:#5F6368;font-size:11px;")
        lay.addWidget(note)

        return box

    # ---- 换算 ----

    @staticmethod
    def _score_to_level(score):
        best, best_diff = 2, None
        for i, v in enumerate(SCORE_VALUES):
            diff = abs(float(v) - float(score))
            if best_diff is None or diff < best_diff:
                best, best_diff = i, diff
        return best

    # ---- 交互 ----

    def _on_toggle(self, key, checked):
        self._cfg.enabled[key] = bool(checked)
        self._sync_enabled()

    def _on_mode(self, key):
        """切换分析模式（单选，三个按钮互斥）。"""
        self._cfg.analysis_mode = key
        for k, btn in self._mode_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == key)
            btn.blockSignals(False)
        self._update_mode_note()

    def _update_mode_note(self):
        """根据模式显示一句提示。

        说明里已经包含采样点数量，这里只说对判废的影响，
        因为采样点越少，坏帧越可能正好没被采到。
        """
        n = ANALYSIS_MODE_SAMPLES.get(self._cfg.analysis_mode, 6)
        if n <= 3:
            extra = "采样点较少，判废可能漏检，建议先粗筛"
        elif n >= 12:
            extra = "采样点密集，判废更准，但分析耗时最长"
        else:
            extra = "采样点适中，判废与速度平衡"
        self._mode_note.setText(extra)
        self._sample_count = n

    def _on_level(self, key, value):
        self._cfg.levels[key] = int(value)

    def _on_ratio_level(self, value):
        self._cfg.set_bad_ratio_level(int(value))

    def _on_score_level(self, value):
        self._cfg.min_score = float(SCORE_VALUES[
            max(0, min(len(SCORE_VALUES) - 1, int(value)))])

    def _sync_enabled(self):
        for key, row in self._rows.items():
            on = self._cfg.is_on(key)
            row["slider"].setEnabled(on)
            row["desc"].setStyleSheet(
                "color:%s;font-size:11px;"
                % ("#5F6368" if on else "#3A3D42"))

    def _apply_preset(self, name):
        self._cfg = VerdictConfig.preset(name)
        for key, row in self._rows.items():
            row["chk"].blockSignals(True)
            row["chk"].setChecked(self._cfg.is_on(key))
            row["chk"].blockSignals(False)
            row["slider"].set_value(self._cfg.level(key))
        self.ratio_slider.set_value(self._cfg.bad_ratio_level())
        self.score_slider.set_value(self._score_to_level(self._cfg.min_score))
        self._sync_enabled()

    # ---- 结果 ----

    def result_config(self):
        return VerdictConfig.from_dict(self._cfg.to_dict())


def ask_verdict_config(parent=None, config=None, sample_count=0):
    """便捷入口：弹窗并返回配置；用户取消返回 None。"""
    dlg = VerdictDialog(config, parent, sample_count=sample_count)
    if dlg.exec() == QDialog.Accepted:
        return dlg.result_config()
    return None
