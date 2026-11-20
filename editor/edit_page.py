"""剪辑页：媒体池 | 源监视器 + 节目监视器 | Inspector，下方时间线。

严格只做剪辑：这里不出现任何调色控件（调色在独立页面）。
布局按达芬奇 Edit 页组织，媒体池和源监视器都是常驻面板，不弹窗。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import ui_theme as T
from .icons import icon

TIMELINE_MIN_H = 190


class EditPage(QWidget):
    """剪辑页容器：只负责摆放，不持有业务逻辑。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PageRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.body = QSplitter(Qt.Horizontal)
        self.body.setChildrenCollapsible(False)
        self.body.setHandleWidth(2)
        root.addWidget(self.body, 1)

        # 左：媒体池占位
        self.media_slot = QWidget()
        self.media_slot.setObjectName("PanelRoot")
        self.media_slot.setMinimumWidth(T.PANEL_LEFT_MIN)
        self.media_slot.setMaximumWidth(T.MEDIA_PANEL_WIDTH)
        media_layout = QVBoxLayout(self.media_slot)
        media_layout.setContentsMargins(0, 0, 0, 0)
        media_layout.setSpacing(0)
        self._media_layout = media_layout
        self.body.addWidget(self.media_slot)

        # 中：上双监视器 / 下时间线
        center = QWidget()
        center.setObjectName("PageRoot")
        self.center_split = QSplitter(Qt.Vertical)
        self.center_split.setChildrenCollapsible(False)
        self.center_split.setHandleWidth(2)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.center_split, 1)
        self.body.addWidget(center)

        viewers = QWidget()
        viewers.setObjectName("PageRoot")
        viewers_layout = QHBoxLayout(viewers)
        viewers_layout.setContentsMargins(0, 0, 0, 0)
        viewers_layout.setSpacing(2)
        self._source_layout = QHBoxLayout()
        self._source_host = QWidget()
        self._source_host.setObjectName("PageRoot")
        self._source_host.setLayout(self._source_layout)
        self._source_layout.setContentsMargins(0, 0, 0, 0)
        self._source_layout.setSpacing(0)
        self._program_host = QWidget()
        self._program_host.setObjectName("PageRoot")
        self._program_layout = QVBoxLayout(self._program_host)
        self._program_layout.setContentsMargins(0, 0, 0, 0)
        self._program_layout.setSpacing(0)
        viewers_layout.addWidget(self._source_host, 1)
        viewers_layout.addWidget(self._program_host, 1)
        self.center_split.addWidget(viewers)

        timeline_host = QWidget()
        timeline_host.setObjectName("PageRoot")
        timeline_layout = QVBoxLayout(timeline_host)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(0)
        timeline_layout.addWidget(self._build_timeline_toolbar())
        self._timeline_slot = QVBoxLayout()
        self._timeline_slot.setContentsMargins(0, 0, 0, 0)
        self._timeline_slot.setSpacing(0)
        timeline_layout.addLayout(self._timeline_slot, 1)
        timeline_host.setMinimumHeight(TIMELINE_MIN_H)
        self.center_split.addWidget(timeline_host)
        self.center_split.setSizes([560, 340])

        # 右：Inspector 占位
        self.inspector_slot = QWidget()
        self.inspector_slot.setObjectName("PanelRoot")
        self.inspector_slot.setMinimumWidth(T.PANEL_RIGHT_MIN)
        self.inspector_slot.setMaximumWidth(T.INSPECTOR_WIDTH)
        self._inspector_layout = QVBoxLayout(self.inspector_slot)
        self._inspector_layout.setContentsMargins(0, 0, 0, 0)
        self._inspector_layout.setSpacing(0)
        self.body.addWidget(self.inspector_slot)

        self.body.setSizes([T.MEDIA_PANEL_WIDTH, 1020, T.INSPECTOR_WIDTH])

    # ------------------------------------------------------------------
    def _build_timeline_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TimelineToolbar")
        bar.setFixedHeight(34)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        def chip(text: str, tip: str, kind: str | None = None, checkable: bool = False) -> QPushButton:
            button = QPushButton(text)
            button.setObjectName("Chip")
            button.setToolTip(tip)
            button.setCheckable(checkable)
            if kind:
                button.setIcon(icon(kind, 13, T.TEXT_DIM))
                button.setIconSize(QSize(13, 13))
            layout.addWidget(button)
            return button

        self.btn_tool_select = chip("选择", "选择/移动片段（V）", "pointer", True)
        self.btn_tool_select.setChecked(True)
        self.btn_tool_razor = chip("刀片", "刀片切割（B）", "razor", True)
        self.btn_snap = chip("吸附", "吸附到片段边缘与播放头（S）", "magnet", True)
        self.btn_snap.setChecked(True)

        layout.addSpacing(8)
        self.btn_blade = chip("切割", "在播放头切割（Ctrl+K）", "razor")
        self.btn_delete = chip("删除", "删除选中片段（Del）")
        self.btn_ripple = chip("波纹删除", "删除并前移（Shift+Del）")
        self.btn_marker = chip("标记", "在播放头加标记（M）", "marker")

        layout.addStretch(1)

        self.btn_track_add = chip("+ 视频轨", "新增一条视频轨")
        self.btn_track_add_audio = chip("+ 音频轨", "新增一条音频轨")

        layout.addSpacing(8)
        self.btn_zoom_out = chip("", "缩小时间线", "minus")
        self.btn_zoom_fit = chip("适应", "时间线自适应")
        self.btn_zoom_in = chip("", "放大时间线", "plus")
        for button in (self.btn_zoom_out, self.btn_zoom_in):
            button.setFixedWidth(26)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("Muted")
        self.zoom_label.setFixedWidth(46)
        self.zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.zoom_label)
        return bar

    # ------------------------------------------------------------------
    # 插槽
    # ------------------------------------------------------------------
    def set_media(self, widget: QWidget | None) -> None:
        self._swap(self._media_layout, widget)

    def set_source(self, widget: QWidget | None) -> None:
        self._swap(self._source_layout, widget)

    def set_program(self, widget: QWidget | None) -> None:
        self._swap(self._program_layout, widget)

    def set_inspector(self, widget: QWidget | None) -> None:
        self._swap(self._inspector_layout, widget)

    def set_timeline(self, widget: QWidget | None) -> None:
        self._swap(self._timeline_slot, widget)

    @staticmethod
    def _swap(layout, widget: QWidget | None) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
        if widget is not None:
            layout.addWidget(widget)
