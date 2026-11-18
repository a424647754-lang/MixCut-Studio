# -*- coding: utf-8 -*-
"""
MixCut · 设置页面

只展示真实可查的信息：环境自检结果（由主窗口实时执行）、草稿目录、
输出规格。自检结果来自 filter_engine 的真实导入检查，不做任何“已就绪”的假状态。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea,
    QFrame, QGridLayout, QPushButton,
)

from . import theme
from .components import Card, SectionCard, StatusBadge, make_button


class SettingsPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        title = QLabel("设置")
        title.setObjectName("Hero")
        lay.addWidget(title)

        # 环境自检
        env = SectionCard("环境自检", "检测组件是否真实可用", collapsible=False)
        self.check_btn = make_button("重新检测", kind="ghost")
        self.check_btn.clicked.connect(self.run_check)
        row = QHBoxLayout()
        row.addWidget(self.check_btn)
        row.addStretch(1)
        self.check_state = StatusBadge("未检测", "idle")
        row.addWidget(self.check_state)
        env.add_layout(row)
        self.check_text = QPlainTextEdit()
        self.check_text.setReadOnly(True)
        self.check_text.setMinimumHeight(150)
        self.check_text.setPlainText(
            "点击“重新检测”后显示各检测组件的真实可用性。")
        env.add_row(self.check_text)
        lay.addWidget(env)

        # 草稿目录
        draft = SectionCard("草稿输出", "剪映工程保存位置", collapsible=False)
        if getattr(win, "mix_draft_dir", None) is not None:
            row = QHBoxLayout()
            row.addWidget(win.mix_draft_dir, 1)
            draft.add_layout(row)
        self.default_dir_label = QLabel("")
        self.default_dir_label.setObjectName("Faint")
        draft.add_row(self.default_dir_label)
        lay.addWidget(draft)

        # 关于
        about = SectionCard("关于", "", collapsible=False)
        self.about_label = QLabel(
            "MixCut Studio · 智能混剪工作台\n"
            "输出为剪映可编辑草稿（项目结构未改动）。")
        self.about_label.setObjectName("Muted")
        self.about_label.setWordWrap(True)
        about.add_row(self.about_label)
        lay.addWidget(about)

        lay.addStretch(1)
        self.run_check(show_dialog=False)

    def run_check(self, show_dialog=False):
        """真实执行主窗口的环境自检（与开始任务前用的是同一个函数）。"""
        fn = getattr(self.win, "run_force_self_check", None)
        if not callable(fn):
            self.check_text.setPlainText("主窗口未提供自检接口。")
            self.check_state.setText("不可用")
            self.check_state.set_size("error")
            return
        ok = fn(show_dialog)
        from mixcut import filter_engine as fe
        avail, report = fe.force_self_check()
        lines = list(report)
        lines.append("")
        lines.append("ffmpeg 可用：是" if avail.get("ffmpeg") else "ffmpeg 可用：否")
        if hasattr(self.win, "_jianying_dir"):
            pass
        self.check_text.setPlainText("\n".join(lines))
        self.check_state.setText("可用" if ok else "环境异常")
        self.check_state.set_size("success" if ok else "error")

    def on_shown(self):
        default = getattr(self.win, "_jianying_dir", None)
        try:
            from pathlib import Path
            from mixcut import project_builder as pb
            self.default_dir_label.setText(
                f"剪映默认草稿目录：{pb._jianying_draft_dir()}")
        except Exception:
            self.default_dir_label.setText("")
