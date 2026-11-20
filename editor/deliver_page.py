"""导出页（Deliver）。

左侧是导出设置（格式/CRF/分辨率/音频/路径 + 体积预估），右侧是渲染
任务监视器：真实百分比、剩余时间、ffmpeg 日志、结束后的定位入口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import Project
from . import ui_theme as T
from .panels import ExportPanel
from .player_panel import PanelHeader


class DeliverPage(QWidget):
    export_requested = Signal(dict)
    cancel_requested = Signal()
    reveal_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self._output = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(2)
        root.addWidget(splitter, 1)

        # 左：设置
        left = QWidget()
        left.setObjectName("PanelRoot")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(PanelHeader("RENDER SETTINGS"))
        self.export_panel = ExportPanel()
        self.export_panel.export_requested.connect(self.export_requested.emit)
        left_layout.addWidget(self.export_panel, 1)
        splitter.addWidget(left)

        # 右：任务监视
        right = QWidget()
        right.setObjectName("PanelRoot")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(PanelHeader("导出任务 · 当前仅单任务"))

        body = QWidget()
        body.setObjectName("PanelRoot")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(10)

        self.state_label = QLabel("等待导出")
        self.state_label.setObjectName("SectionTitle")
        body_layout.addWidget(self.state_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        body_layout.addWidget(self.progress)

        self.detail_label = QLabel("尚未开始")
        self.detail_label.setObjectName("Muted")
        body_layout.addWidget(self.detail_label)

        body_layout.addSpacing(6)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_cancel = QPushButton("取消导出")
        self.btn_cancel.setObjectName("Chip")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)
        btn_row.addWidget(self.btn_cancel)

        self.btn_reveal = QPushButton("打开所在文件夹")
        self.btn_reveal.setObjectName("Chip")
        self.btn_reveal.setEnabled(False)
        self.btn_reveal.clicked.connect(self._reveal)
        btn_row.addWidget(self.btn_reveal)
        btn_row.addStretch(1)
        body_layout.addLayout(btn_row)

        body_layout.addWidget(QLabel("FFmpeg 日志"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        body_layout.addWidget(self.log, 1)

        right_layout.addWidget(body, 1)
        splitter.addWidget(right)
        splitter.setSizes([420, 760])

    # ------------------------------------------------------------------
    def set_project(self, project: Project) -> None:
        self.export_panel.set_project(project)

    def refresh(self) -> None:
        self.export_panel.refresh()

    def start(self) -> None:
        self.progress.setValue(0)
        self.state_label.setText("正在导出")
        self.btn_cancel.setEnabled(True)
        self.btn_reveal.setEnabled(False)
        self.log.clear()

    def set_progress(self, percent: int, text: str) -> None:
        self.progress.setValue(max(0, min(100, int(percent))))
        self.detail_label.setText(text)

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def finish(self, path: str) -> None:
        self.progress.setValue(100)
        self.state_label.setText("导出完成")
        self.detail_label.setText(path)
        self._output = path
        self.btn_cancel.setEnabled(False)
        self.btn_reveal.setEnabled(bool(path))

    def fail(self, message: str) -> None:
        self.state_label.setText("导出失败")
        self.detail_label.setText(message)
        self.btn_cancel.setEnabled(False)
        self.btn_reveal.setEnabled(False)

    def set_cancelling(self) -> None:
        """用户点了取消、等进程退出（与"失败"区分，B-36）"""
        self.state_label.setText("正在取消")
        self.detail_label.setText("等待 ffmpeg 退出…")
        self.btn_cancel.setEnabled(False)

    def cancelled(self) -> None:
        self.state_label.setText("已取消")
        self.detail_label.setText("已取消导出，未生成成片")
        self.progress.setValue(0)
        self.btn_cancel.setEnabled(False)
        self.btn_reveal.setEnabled(False)

    def _reveal(self) -> None:
        if self._output:
            self.reveal_requested.emit(self._output)
