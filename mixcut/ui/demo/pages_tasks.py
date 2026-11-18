# -*- coding: utf-8 -*-
"""混剪任务：流水线、任务队列、运行日志、输出。设置页也在此文件。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget,
)

from . import mock, theme as T
from .ui_charts import ComposeBar, PipelineStrip, ThinBar
from .ui_kit import Card, Dot, SoftButton


class TaskRow(QWidget):
    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self.setFixedHeight(58)
        self.bar = ThinBar(0, "#DDE0E6", 3)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel("%s  %s" % (task["id"], task["name"]))
        self.name.setFont(T.font(12, QFont.Weight.DemiBold))
        top.addWidget(self.name, 1)
        self.state = QLabel(task["state"])
        self.state.setFont(T.font(10, QFont.Weight.Medium))
        self.state.setStyleSheet("color:%s;" % T.TEXT_3)
        top.addWidget(self.state)
        lay.addLayout(top)
        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        self.meta = QLabel(task["meta"])
        self.meta.setFont(T.font(10))
        self.meta.setStyleSheet("color:%s;" % T.TEXT_3)
        meta.addWidget(self.meta, 1)
        self.pct = QLabel("0%")
        self.pct.setFont(T.font(10, mono=True))
        self.pct.setStyleSheet("color:%s;" % T.TEXT_3)
        meta.addWidget(self.pct)
        lay.addLayout(meta)
        lay.addWidget(self.bar)

    def set_state(self, text, pct, color=T.TEXT_3, animate=True):
        self.state.setText(text)
        self.state.setStyleSheet("color:%s;" % color)
        self.bar.set_value(pct / 100.0, animate)
        self.pct.setText("%d%%" % pct)


class TasksPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAGE_PAD, 18, T.PAGE_PAD, 22)
        root.setSpacing(16)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        h = QLabel("混剪任务")
        h.setFont(T.font(30, QFont.Weight.DemiBold))
        self.state_label = QLabel("待启动 · 0 / 6 阶段")
        self.state_label.setFont(T.font(12))
        self.state_label.setStyleSheet("color:%s;" % T.TEXT_3)
        titles.addWidget(h)
        titles.addWidget(self.state_label)
        head.addLayout(titles, 1)
        self.btn_pause = SoftButton("暂停", "secondary", "pause", min_width=88)
        self.btn_cancel = SoftButton("取消", "ghost", min_width=74)
        self.btn_output = SoftButton("打开输出文件夹", "ghost", min_width=132, height=36)
        self.btn_run = SoftButton("开始混剪", "primary", "play", min_width=108)
        head.addWidget(self.btn_pause)
        head.addWidget(self.btn_cancel)
        head.addWidget(self.btn_output)
        head.addWidget(self.btn_run)
        root.addLayout(head)

        pipe_card = Card(padding=20, spacing=6)
        pipe_card.setFixedHeight(196)
        root.addWidget(pipe_card)
        self.pipeline = PipelineStrip(mock.STAGES)
        pipe_card.lay.addWidget(self.pipeline, 1)
        self.stage_note = QLabel("素材导入 → 镜头检测 → 语义分析 → 无效过滤 → 混剪组合 → 草稿生成")
        self.stage_note.setFont(T.font(11))
        self.stage_note.setStyleSheet("color:%s;" % T.TEXT_3)
        pipe_card.lay.addWidget(self.stage_note)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        root.addLayout(bottom, 1)

        left = QVBoxLayout()
        left.setSpacing(16)
        bottom.addLayout(left, 1)
        tasks_card = Card(padding=18, spacing=10)
        self.tasks_card = tasks_card
        left.addWidget(tasks_card)
        th = QLabel("任务队列")
        th.setFont(T.font(13, QFont.Weight.DemiBold))
        tasks_card.lay.addWidget(th)
        self.rows = []
        for task in mock.TASKS:
            row = TaskRow(task)
            tasks_card.lay.addWidget(row)
            self.rows.append(row)

        comp = Card(padding=16, spacing=8)
        left.addWidget(comp)
        ch = QLabel("成片结构 · 尚未生成")
        ch.setFont(T.font(12, QFont.Weight.DemiBold))
        comp.lay.addWidget(ch)
        comp.lay.addWidget(ComposeBar())

        out = Card(padding=16, spacing=8)
        left.addWidget(out)
        oh = QHBoxLayout()
        t = QLabel("输出")
        t.setFont(T.font(12, QFont.Weight.DemiBold))
        oh.addWidget(t)
        oh.addStretch(1)
        self.out_state = QLabel("待生成")
        self.out_state.setFont(T.font(10))
        self.out_state.setStyleSheet("color:%s;" % T.TEXT_3)
        oh.addWidget(self.out_state)
        out.lay.addLayout(oh)
        self.out_path = QLabel("尚未生成")
        self.out_path.setFont(T.font(11, mono=True))
        self.out_path.setStyleSheet("color:%s;" % T.TEXT_2)
        out.lay.addWidget(self.out_path)
        self.out_meta = QLabel("剪映草稿 · 等待后台生成")
        self.out_meta.setFont(T.font(10))
        self.out_meta.setStyleSheet("color:%s;" % T.TEXT_3)
        out.lay.addWidget(self.out_meta)
        left.addStretch(1)

        logs = Card(padding=16, spacing=8, fill=T.INSET)
        bottom.addWidget(logs, 1)
        lh = QHBoxLayout()
        lt = QLabel("运行日志")
        lt.setFont(T.font(12, QFont.Weight.DemiBold))
        lh.addWidget(lt)
        lh.addStretch(1)
        self.log_count = QLabel("0 条")
        self.log_count.setFont(T.font(10))
        self.log_count.setStyleSheet("color:%s;" % T.TEXT_3)
        lh.addWidget(self.log_count)
        self.btn_autoscroll = SoftButton("自动滚动：开", "ghost", height=26, radius=7)
        self.btn_copy = SoftButton("复制", "ghost", height=26, radius=7)
        self.btn_clear_log = SoftButton("清空", "ghost", height=26, radius=7)
        for b in (self.btn_autoscroll, self.btn_copy, self.btn_clear_log):
            lh.addWidget(b)
        logs.lay.addLayout(lh)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.log.setStyleSheet("""
            QPlainTextEdit { background: transparent; border: none;
                color: %s; font-family: "Cascadia Mono"; font-size: 11px;
                selection-background-color: #33363E; padding: 2px 0; }
        """ % T.TEXT_2)
        logs.lay.addWidget(self.log, 1)

        self.autoscroll = True
        self.btn_run.clicked.connect(self._run)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_output.clicked.connect(self._open_output)
        self.btn_autoscroll.clicked.connect(self._toggle_autoscroll)
        self.btn_copy.clicked.connect(self._copy_log)
        self.btn_clear_log.clicked.connect(self.log.clear)
        win.controller.stateChanged.connect(self.refresh)
        win.controller.logAdded.connect(self.append_log)

    def start_task(self, clip_count, target_sec):
        task = {"id": "#%02d" % (len(self.rows) + 1),
                "name": "混剪任务 · %d 秒" % int(target_sec),
                "meta": "%d 素材 · 后台分析结果" % int(clip_count),
                "state": "运行中"}
        row = TaskRow(task)
        self.tasks_card.lay.addWidget(row)
        self.rows.append(row)

    def _run(self):
        if self.win.active_paths() and not self.win.backend.load_error:
            self.win.start_real_mix()
            return
        self.win.controller.log("任务 · 素材库为空，请先在素材库导入视频")

    def _pause(self):
        b = self.win.backend
        if b.ready and b.toggle_pause():
            self.win.controller.log("任务 · 已切换后台混剪暂停状态")
            return
        self.win.controller.log("任务 · 后台混剪未运行，无法暂停")

    def _cancel(self):
        c = self.win.controller
        b = self.win.backend
        if b.ready:
            b.cancel_mix()
            c.status_text = "已取消"
            c.log("任务 · 已向后台混剪任务发送取消")
            c.stateChanged.emit()
            return
        c.log("任务 · 后台混剪未运行，无需取消")

    def _open_output(self):
        b = self.win.backend
        if b.ready and b.open_output():
            return
        self.win.controller.log("输出 · 尚无已生成的草稿目录")

    def _toggle_autoscroll(self):
        self.autoscroll = not self.autoscroll
        self.btn_autoscroll.setText("自动滚动：开" if self.autoscroll else "自动滚动：关")

    def _copy_log(self):
        QApplication.clipboard().setText(self.log.toPlainText())
        self.win.controller.log("运行日志 · 已复制到剪贴板")

    def append_log(self, line):
        self.log.appendPlainText(line)
        if self.autoscroll:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log_count.setText("%d 条" % self.win.controller.log_count())

    def refresh(self):
        c = self.win.controller
        self.pipeline.set_state(c.progress, c.active_stage, c.done_stages)
        self.state_label.setText("%s · %d / 6 阶段" % (c.status_text, c.done_stages))
        self.stage_note.setText(c.stage_hint())
        if c.done_stages >= 6:
            self.btn_pause.setText("已完成")
            self.btn_pause.icon = "check"
            self.btn_pause.setEnabled(False)
            self.btn_pause.update()
            for row in self.rows:
                row.set_state("已完成", 100, T.OK)
            for row in self.rows[1:]:
                row.meta.setText("已生成")
            self.out_state.setText("已生成")
            self.out_state.setStyleSheet("color:%s;" % T.OK)
        elif c.active_stage >= 0 and c.status_text == "运行中":
            pct = int(c.progress * 100)
            if self.rows:
                self.rows[-1].set_state("运行中", pct, T.BLUE)
            self.out_state.setText("生成中" if c.drafted else "等待")
            self.out_state.setStyleSheet("color:%s;" % (T.BLUE if c.drafted else T.TEXT_3))
        elif c.status_text in ("已取消", "未完成"):
            pct = int(c.progress * 100)
            if self.rows:
                self.rows[-1].set_state(c.status_text, pct,
                                          T.ERR if c.status_text == "未完成" else T.TEXT_3, False)
            self.out_state.setText(c.status_text)
            self.out_state.setStyleSheet("color:%s;" % (T.ERR if c.status_text == "未完成" else T.TEXT_3))
        if c.done_stages < 6 and self.rows and c.status_text not in ("已取消", "未完成"):
            self.btn_pause.setEnabled(True)
            self.btn_pause.setText("继续" if c.paused else "暂停")
            self.btn_pause.icon = "play" if c.paused else "pause"
            self.btn_pause.update()
