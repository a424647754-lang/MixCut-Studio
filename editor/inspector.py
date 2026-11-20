"""剪辑页 Inspector：只放剪辑相关属性（时间/播放/不透明），不放调色。

调色属于独立的「调色」页面，剪辑页保持干净——这是本次改版的核心原则。
数值字段用 QLineEdit + 校验器实现（不用 SpinBox 的上下箭头），与专业
NLE 的属性面板一致，也不会出现 SpinBox 在自定义主题下文字被裁掉的问题。
"""

from __future__ import annotations

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import Clip, format_timecode
from . import ui_theme as T
from .limits import (OPACITY_MAX, OPACITY_MIN, SPEED_MAX, SPEED_MIN,
                     VOLUME_MAX, VOLUME_MIN, volume_needs_render_warning)
from .player_panel import PanelHeader


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def _dim(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    return label


class InspectorPanel(QWidget):
    """片段属性面板（剪辑页右侧）。"""

    changed = Signal(str, dict)       # 字段名, {属性: 新值}
    action_requested = Signal(str)    # split / delete / ripple_delete

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PanelRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._clip: Clip | None = None
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(PanelHeader("Inspector"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body.setObjectName("PanelRoot")
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(12, 8, 18, 12)
        self._layout.setSpacing(6)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # -- 片段 ---------------------------------------------------------
        self._layout.addWidget(_section("片段"))
        self.prop_name = QLineEdit()
        self.prop_name.setPlaceholderText("未选择片段")
        self.prop_name.editingFinished.connect(self._emit_name)
        self._layout.addWidget(self.prop_name)

        self.track_label = _dim("—")
        self.asset_label = _dim("—")
        self.asset_label.setWordWrap(False)
        self._layout.addWidget(self.track_label)
        self._layout.addWidget(self.asset_label)

        self.prop_enabled = QCheckBox("参与播放与导出")
        self.prop_enabled.setChecked(True)
        self.prop_enabled.toggled.connect(
            lambda value: self._emit("enabled", {"enabled": bool(value)}))
        self._layout.addWidget(self.prop_enabled)

        # -- 时间 ---------------------------------------------------------
        self._layout.addWidget(_section("时间"))
        self.prop_start, self.start_tc = self._number_row("开始", "start", 0.0, 86400.0,
                                                          " s", True)
        self.prop_duration, self.duration_tc = self._number_row("时长", "duration", 0.05,
                                                               86400.0, " s", True)
        self.prop_source, self.source_tc = self._number_row("源入点", "source_in", 0.0,
                                                           86400.0, " s", True)

        # -- 播放 ---------------------------------------------------------
        self._layout.addWidget(_section("播放"))
        self.prop_speed, _ = self._number_row("速度", "speed", SPEED_MIN, SPEED_MAX, "×")
        self.prop_volume, _ = self._number_row("音量", "volume", VOLUME_MIN, VOLUME_MAX, "")
        self.prop_volume.setToolTip("音量 1.0 为原始电平；超过 1.0 只在成片中生效（预览上限 100%）")
        self.volume_hint = _dim("")
        self.volume_hint.setWordWrap(True)
        self._layout.addWidget(self.volume_hint)
        self.prop_opacity, _ = self._number_row("不透明度", "opacity", OPACITY_MIN,
                                               OPACITY_MAX, "")

        # -- 操作 ---------------------------------------------------------
        self._layout.addWidget(_section("操作"))
        grid = QGridLayout()
        grid.setSpacing(4)
        for index, (text, key) in enumerate((("播放头切割", "split"), ("删除", "delete"),
                                             ("波纹删除", "ripple_delete"))):
            button = QPushButton(text)
            button.setObjectName("Chip")
            button.clicked.connect(lambda _=False, k=key: self.action_requested.emit(k))
            if index == 0:
                grid.addWidget(button, 0, 0, 1, 2)
            else:
                grid.addWidget(button, 1, index - 1)
        self._layout.addLayout(grid)

        self._layout.addStretch(1)
        self.setMinimumWidth(T.PANEL_RIGHT_MIN)
        self.clear()

    # ------------------------------------------------------------------
    def _number_row(self, title: str, key: str, lo: float, hi: float,
                    suffix: str, show_timecode: bool = False) -> tuple[QLineEdit, QLabel]:
        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("FieldLabel")
        label.setFixedWidth(52)
        row.addWidget(label)

        edit = QLineEdit()
        edit.setMinimumWidth(0)
        edit.setAlignment(Qt.AlignRight)
        validator = QDoubleValidator(lo, hi, 3, edit)
        validator.setNotation(QDoubleValidator.StandardNotation)
        locale = QLocale(QLocale.C)
        locale.setNumberOptions(QLocale.RejectGroupSeparator)
        validator.setLocale(locale)
        edit.setValidator(validator)
        edit.editingFinished.connect(lambda k=key, w=edit: self._emit_number(k, w))
        row.addWidget(edit, 1)
        if suffix:
            suffix_label = QLabel(suffix)
            suffix_label.setObjectName("Muted")
            suffix_label.setFixedWidth(12)
            row.addWidget(suffix_label)
        self._layout.addLayout(row)

        tc = _dim("00:00:00:00")
        tc.setMinimumWidth(0)
        tc.setIndent(0)
        if show_timecode:
            self._layout.addWidget(tc)
        else:
            tc.hide()
        return edit, tc

    def _emit_number(self, key: str, edit: QLineEdit) -> None:
        if self._clip is None or self._loading:
            return
        try:
            value = float(edit.text().strip())
        except ValueError:
            current = getattr(self._clip, key)
            if key in ("start", "duration", "source_in"):
                edit.setText(f"{current / 1000.0:.3f}")     # 毫秒级往返不丢精度（B-23）
            else:
                edit.setText(f"{current:.2f}")
            return
        if key in ("start", "duration", "source_in"):
            self._emit(key, {key: int(round(value * 1000))})
        else:
            self._emit(key, {key: value})

    # ------------------------------------------------------------------
    def _emit_name(self) -> None:
        if self._clip is None or self._loading:
            return
        text = self.prop_name.text().strip()
        if text and text != self._clip.name:
            self._emit("name", {"name": text})

    def _emit(self, label: str, updates: dict) -> None:
        if self._loading or self._clip is None:
            return
        self.changed.emit(label, updates)

    # ------------------------------------------------------------------
    def set_clip(self, clip: Clip | None, track_name: str = "", asset_name: str = "") -> None:
        self._clip = clip
        self._loading = True
        enabled = clip is not None
        for widget in (self.prop_name, self.prop_enabled, self.prop_start, self.prop_duration,
                       self.prop_source, self.prop_speed, self.prop_volume, self.prop_opacity):
            widget.setEnabled(enabled)

        if clip is None:
            self.prop_name.clear()
            self.track_label.setText("未选择片段")
            self.asset_label.setText("在时间线上选中片段后可编辑属性")
            self.prop_enabled.setChecked(False)
            for field in (self.prop_start, self.prop_duration, self.prop_source):
                field.setText("0.000")
            self.prop_speed.setText("1.00")
            self.prop_volume.setText("1.00")
            self.prop_opacity.setText("1.00")
            self.volume_hint.setText("")
            for tc in (self.start_tc, self.duration_tc, self.source_tc):
                tc.setText("00:00:00:00")
        else:
            self.prop_name.setText(clip.name)
            self.track_label.setText(f"轨道 {track_name or '—'}")
            self.asset_label.setText(asset_name or clip.path)
            self.prop_enabled.setChecked(clip.enabled)
            self.prop_start.setText(f"{clip.start / 1000.0:.3f}")
            self.prop_duration.setText(f"{clip.duration / 1000.0:.3f}")
            self.prop_source.setText(f"{clip.source_in / 1000.0:.3f}")
            self.prop_speed.setText(f"{clip.speed:.2f}")
            self.prop_volume.setText(f"{clip.volume:.2f}")
            self.prop_opacity.setText(f"{clip.opacity:.2f}")
            tip = self.prop_volume.toolTip()
            if volume_needs_render_warning(clip.volume):
                self.volume_hint.setText("音量 > 1.0：预览最多 100%，增益只在成片中生效")
            else:
                self.volume_hint.setText("")
            self.prop_volume.setToolTip(tip)
        self._loading = False

    def set_timecodes(self, fps) -> None:
        self._fps = fps
        clip = self._clip
        if clip is None:
            return
        self.start_tc.setText(format_timecode(clip.start, fps))
        self.duration_tc.setText(format_timecode(clip.duration, fps))
        self.source_tc.setText(format_timecode(clip.source_in, fps))

    def clear(self) -> None:
        self.set_clip(None)
