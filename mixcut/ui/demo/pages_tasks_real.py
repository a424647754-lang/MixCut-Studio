# -*- coding: utf-8 -*-
"""任务中心：导出页（专业剪辑软件的导出面板思路）。

左侧「导出设置」把本次参数摊开，中间「任务队列」显示真实混剪任务，
右侧「输出」给出剪映草稿目录；顶部是大号完成进度 + 阶段步骤；底部运行日志。
数据全部来自旧后台混剪 worker，不使用 demo 的模拟流水线。
"""

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QVBoxLayout, QWidget,
)

from . import theme as T
from .ui_charts import ThinBar
from .ui_kit import Card, Dot, SoftButton


class TaskRow(QWidget):
    """任务队列一行：名称 + 状态 + 百分比 + 细进度条。"""

    def __init__(self, name, meta, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.bar = ThinBar(0, "#DDE0E6", 3)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(name)
        self.name.setFont(T.font(12, QFont.Weight.DemiBold))
        self.name.setStyleSheet("color:%s;" % T.TEXT)
        top.addWidget(self.name, 1)
        self.state = QLabel("排队中")
        self.state.setFont(T.font(10, QFont.Weight.Medium))
        self.state.setStyleSheet("color:%s;" % T.TEXT_3)
        top.addWidget(self.state)
        lay.addLayout(top)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        self.meta = QLabel(meta)
        self.meta.setFont(T.font(10))
        self.meta.setStyleSheet("color:%s;" % T.TEXT_3)
        meta_row.addWidget(self.meta, 1)
        self.pct = QLabel("0%")
        self.pct.setFont(T.font(10, QFont.Weight.DemiBold, mono=True))
        self.pct.setStyleSheet("color:%s;" % T.TEXT_3)
        meta_row.addWidget(self.pct)
        lay.addLayout(meta_row)
        lay.addWidget(self.bar)

    def set_state(self, text, pct, color=None, animate=True):
        color = color or T.TEXT_3
        self.state.setText(text)
        self.state.setStyleSheet("color:%s;" % color)
        pct = max(0, min(100, int(pct)))
        self.bar.set_value(pct / 100.0, animate)
        self.pct.setText("%d%%" % pct)
        self.pct.setStyleSheet("color:%s;" % (color if pct else T.TEXT_3))


class StageChip(QLabel):
    """进度条下方的阶段小胶囊。"""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFont(T.font(10, QFont.Weight.Medium))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(24)
        self.set_state("idle")

    def set_state(self, state):
        if state == "done":
            self.setStyleSheet(
                "QLabel{background:%s;color:%s;border-radius:12px;padding:0 12px;}"
                % (T.INSET, T.TEXT_2))
        elif state == "active":
            self.setStyleSheet(
                "QLabel{background:%s;color:#101113;border-radius:12px;padding:0 12px;"
                "font-weight:600;}" % T.ACCENT)
        elif state == "error":
            self.setStyleSheet(
                "QLabel{background:%s;color:%s;border-radius:12px;padding:0 12px;}"
                % ("#3A2226", T.ERR))
        else:
            self.setStyleSheet(
                "QLabel{background:transparent;color:%s;border:1px solid %s;"
                "border-radius:12px;padding:0 12px;}" % (T.TEXT_3, T.LINE))


class KVRow(QWidget):
    def __init__(self, key, value, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.k = QLabel(key)
        self.k.setFont(T.font(11))
        self.k.setStyleSheet("color:%s;" % T.TEXT_3)
        lay.addWidget(self.k, 1)
        self.v = QLabel(value)
        self.v.setFont(T.font(11, QFont.Weight.Medium, mono=True))
        self.v.setStyleSheet("color:%s;" % T.TEXT_2)
        self.v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.v)

    def set_value(self, value):
        self.v.setText(str(value))


class TasksPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._rows = []
        self._log_lines = 0
        self._last_status = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAGE_PAD, 18, T.PAGE_PAD, 22)
        root.setSpacing(14)

        # ---------------- 标题行 ----------------
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        h = QLabel("任务中心")
        h.setFont(T.font(30, QFont.Weight.DemiBold))
        self.state_label = QLabel("待启动 · 尚未生成草稿")
        self.state_label.setFont(T.font(12))
        self.state_label.setStyleSheet("color:%s;" % T.TEXT_3)
        titles.addWidget(h)
        titles.addWidget(self.state_label)
        head.addLayout(titles, 1)

        self.btn_pause = SoftButton("暂停", "secondary", "pause", min_width=88)
        self.btn_cancel = SoftButton("取消", "ghost", min_width=74)
        self.btn_run = SoftButton("开始混剪", "primary", "play", min_width=108)
        for b in (self.btn_pause, self.btn_cancel, self.btn_run):
            head.addWidget(b)
        root.addLayout(head)

        # ---------------- 进度 ----------------
        prog = Card(padding=20, spacing=12)
        root.addWidget(prog)
        prow = QHBoxLayout()
        prow.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(2)
        self.pct_label = QLabel("0%")
        self.pct_label.setFont(T.font(34, QFont.Weight.DemiBold, mono=True))
        self.pct_label.setStyleSheet("color:%s;" % T.TEXT)
        cap = QLabel("完成进度")
        cap.setFont(T.font(10))
        cap.setStyleSheet("color:%s;" % T.TEXT_3)
        left.addWidget(self.pct_label)
        left.addWidget(cap)
        prow.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(9)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setStyleSheet(
            "QProgressBar{background:%s;border:none;border-radius:5px;}"
            "QProgressBar::chunk{background:%s;border-radius:5px;}"
            % (T.INSET, T.ACCENT))
        right.addWidget(self.progress_bar)

        note_row = QHBoxLayout()
        note_row.setSpacing(6)
        self.stage_dot = Dot(T.TEXT_3, 6)
        note_row.addWidget(self.stage_dot)
        self.stage_note = QLabel("等待开始：先在「AI 分析」页完成素材分析")
        self.stage_note.setFont(T.font(11))
        self.stage_note.setStyleSheet("color:%s;" % T.TEXT_3)
        note_row.addWidget(self.stage_note, 1)
        right.addLayout(note_row)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        self.chips = [StageChip(t) for t in ("准备素材", "生成时间线", "渲染片段", "写入草稿")]
        for c in self.chips:
            chip_row.addWidget(c)
        chip_row.addStretch(1)
        right.addLayout(chip_row)
        prow.addLayout(right, 1)
        prog.lay.addLayout(prow)

        # ---------------- 三列 ----------------
        mid = QHBoxLayout()
        mid.setSpacing(14)
        root.addLayout(mid, 1)

        # 导出设置
        self.settings_card = Card(padding=18, spacing=9)
        self.settings_card.setFixedWidth(330)
        st = QLabel("导出设置")
        st.setFont(T.font(13, QFont.Weight.DemiBold))
        self.settings_card.lay.addWidget(st)
        self.kv = {}
        for key in ("清晰度", "帧率", "画布", "目标时长", "片段时长", "成片数量", "输出格式", "素材数量"):
            row = KVRow(key, "—")
            self.kv[key] = row
            self.settings_card.lay.addWidget(row)
        self.settings_card.lay.addStretch(1)
        mid.addWidget(self.settings_card)

        # 任务队列
        self.tasks_card = Card(padding=18, spacing=10)
        th = QHBoxLayout()
        t = QLabel("任务队列")
        t.setFont(T.font(13, QFont.Weight.DemiBold))
        th.addWidget(t)
        th.addStretch(1)
        self.queue_count = QLabel("0 个任务")
        self.queue_count.setFont(T.font(10))
        self.queue_count.setStyleSheet("color:%s;" % T.TEXT_3)
        th.addWidget(self.queue_count)
        self.tasks_card.lay.addLayout(th)
        self.empty_hint = QLabel("暂无任务 · 在「参数设置」点「开始智能混剪」后，这里会出现任务")
        self.empty_hint.setFont(T.font(11))
        self.empty_hint.setStyleSheet("color:%s;" % T.TEXT_3)
        self.empty_hint.setWordWrap(True)
        self.tasks_card.lay.addWidget(self.empty_hint)
        self.tasks_card.lay.addStretch(1)
        mid.addWidget(self.tasks_card, 1)

        # 输出
        out = Card(padding=18, spacing=9)
        out.setFixedWidth(330)
        oh = QHBoxLayout()
        ot = QLabel("输出")
        ot.setFont(T.font(13, QFont.Weight.DemiBold))
        oh.addWidget(ot)
        oh.addStretch(1)
        self.out_state = QLabel("待生成")
        self.out_state.setFont(T.font(10, QFont.Weight.Medium))
        self.out_state.setStyleSheet("color:%s;" % T.TEXT_3)
        oh.addWidget(self.out_state)
        out.lay.addLayout(oh)

        fmt = QLabel("剪映草稿 · draft_content.json")
        fmt.setFont(T.font(10, mono=True))
        fmt.setStyleSheet("color:%s;" % T.TEXT_3)
        out.lay.addWidget(fmt)
        self.out_path = QLabel("尚未生成")
        self.out_path.setFont(T.font(11, mono=True))
        self.out_path.setStyleSheet("color:%s;" % T.TEXT_2)
        self.out_path.setWordWrap(True)
        out.lay.addWidget(self.out_path)
        self.out_meta = QLabel("完成后可直接在剪映打开继续编辑")
        self.out_meta.setFont(T.font(10))
        self.out_meta.setStyleSheet("color:%s;" % T.TEXT_3)
        self.out_meta.setWordWrap(True)
        out.lay.addWidget(self.out_meta)
        out.lay.addStretch(1)

        obtn = QHBoxLayout()
        obtn.setSpacing(8)
        self.btn_output = SoftButton("打开文件夹", "secondary", "folder", height=32, min_width=96)
        self.btn_copy_path = SoftButton("复制路径", "ghost", height=32, min_width=80)
        obtn.addWidget(self.btn_output)
        obtn.addWidget(self.btn_copy_path)
        obtn.addStretch(1)
        out.lay.addLayout(obtn)
        mid.addWidget(out)

        # ---------------- 日志 ----------------
        logs = Card(padding=16, spacing=8, fill=T.INSET)
        logs.setFixedHeight(196)
        root.addWidget(logs)
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

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.log_view.setStyleSheet("""
            QPlainTextEdit { background: transparent; border: none;
                color: %s; font-family: "%s"; font-size: 11px;
                selection-background-color: #33363E; padding: 2px 0; }
        """ % (T.TEXT_2, T.FONT_MONO))
        logs.lay.addWidget(self.log_view, 1)

        self.autoscroll = True
        self.btn_run.clicked.connect(self._run)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_output.clicked.connect(self._open_output)
        self.btn_copy_path.clicked.connect(self._copy_path)
        self.btn_autoscroll.clicked.connect(self._toggle_autoscroll)
        self.btn_copy.clicked.connect(self._copy_log)
        self.btn_clear_log.clicked.connect(self._clear_log)

        self.win.controller.stateChanged.connect(self.refresh)
        self.win.controller.logAdded.connect(self.append_log)
        self.refresh()

    # -----------------------------------------------------
    def on_shown(self):
        self._sync_settings()
        self.refresh()

    def analysis_ready(self):
        page = getattr(self.win, "page_analyze", None)
        check = getattr(page, "analysis_ready", None)
        return bool(check()) if callable(check) else False

    def _sync_settings(self):
        page = getattr(self.win, "demo_params", None)
        if page is None:
            return
        try:
            res = page.resolution.currentText()
            fps = page.fps.currentText()
            canvas = page.canvas_size()
            self.kv["清晰度"].set_value(res)
            self.kv["帧率"].set_value(fps)
            self.kv["画布"].set_value("%d × %d" % (canvas[0], canvas[1]))
            self.kv["目标时长"].set_value("%s 秒" % int(page.target.value()))
            self.kv["片段时长"].set_value(
                "%.1f – %.1f 秒" % (page.clip_min.value(), page.clip_max.value()))
            self.kv["成片数量"].set_value("%d 个" % int(page.count.value()))
            self.kv["输出格式"].set_value("剪映草稿")
            try:
                self.kv["素材数量"].set_value("%d 条" % len(self.win.get_all_video_paths()))
            except Exception:
                self.kv["素材数量"].set_value("—")
        except Exception:
            pass

    def refresh(self):
        c = self.win.controller
        pct = int(round(float(getattr(c, "progress", 0.0)) * 100))
        status = str(getattr(c, "status_text", "") or "待启动")
        pct = max(0, min(100, pct))
        if status == "已完成":
            pct = 100

        self.progress_bar.setValue(pct)
        self.pct_label.setText("%d%%" % pct)
        self.state_label.setText("%s · %s" % (
            status, "已生成草稿" if status == "已完成" else "等待后台输出"))

        color = {"运行中": T.BLUE, "已完成": T.OK,
                 "未完成": T.ERR, "已取消": T.TEXT_3}.get(status, T.TEXT_3)
        if status == "运行中":
            self.stage_note.setText(
                "后台混剪运行中%s · 进度实时同步" % ("（已暂停）" if getattr(c, "paused", False) else ""))
            self._set_chips(pct, None)
        elif status == "已完成":
            self.stage_note.setText("混剪完成，草稿已写入输出目录")
            self._set_chips(100, "done")
        elif status in ("已取消", "未完成"):
            self.stage_note.setText("任务已中止：%s" % status)
            self._set_chips(pct, "error")
        elif not self.analysis_ready():
            self.stage_note.setText("等待开始：先在「AI 分析」页完成素材分析")
            self._set_chips(0, None)
        else:
            self.stage_note.setText("分析完成，可开始混剪")
            self._set_chips(0, None)
        self.stage_note.setStyleSheet(
            "color:%s;" % (color if status != "待启动" else T.TEXT_3))
        self.stage_dot.set_color(color)

        ready = self.analysis_ready()
        self.btn_run.setEnabled(ready)
        self.btn_run.setToolTip("请先在「AI 分析」页完成素材分析" if not ready else "开始混剪")

        if status == "运行中":
            if not self._rows:
                self._add_row()
            self._rows[-1].set_state("运行中", pct, T.BLUE)
            self.btn_pause.setEnabled(True)
            paused = bool(getattr(c, "paused", False))
            self.btn_pause.setText("继续" if paused else "暂停")
            self.btn_pause.icon = "play" if paused else "pause"
            self.btn_pause.update()
            self.btn_cancel.setEnabled(True)
        else:
            self.btn_pause.setEnabled(False)
            self.btn_pause.setText("暂停")
            self.btn_pause.icon = "pause"
            self.btn_pause.update()
            self.btn_cancel.setEnabled(False)
            if self._rows and status in ("已完成", "已取消", "未完成"):
                self._rows[-1].set_state(
                    status, pct,
                    T.OK if status == "已完成" else (T.ERR if status == "未完成" else T.TEXT_3),
                    animate=False)

        if status == "已完成":
            self.out_state.setText("已生成")
            self.out_state.setStyleSheet("color:%s;font-weight:600;" % T.OK)
            self.out_meta.setText("剪映草稿 · 可导入剪映继续编辑")
        elif status == "运行中":
            self.out_state.setText("生成中")
            self.out_state.setStyleSheet("color:%s;font-weight:600;" % T.BLUE)
        else:
            self.out_state.setText(status if status in ("已取消", "未完成") else "待生成")
            self.out_state.setStyleSheet(
                "color:%s;" % (T.ERR if status == "未完成" else T.TEXT_3))

        if status != self._last_status:
            self._last_status = status
            self._sync_settings()

    def _set_chips(self, pct, override):
        marks = [0, 25, 55, 85]
        for i, chip in enumerate(self.chips):
            if override == "error":
                chip.set_state("done" if pct >= marks[i] else "error")
            elif override == "done":
                chip.set_state("done")
            elif pct >= (marks[i + 1] if i + 1 < len(marks) else 100):
                chip.set_state("done")
            elif pct >= marks[i]:
                chip.set_state("active")
            else:
                chip.set_state("idle")

    def capture_state(self):
        c = self.win.controller
        rows = []
        for row in self._rows:
            try:
                pct = int(str(row.pct.text()).rstrip("%"))
            except ValueError:
                pct = 0
            rows.append({"name": row.name.text(), "meta": row.meta.text(),
                         "state": row.state.text(), "pct": pct})
        return {"rows": rows,
                "status": str(getattr(c, "status_text", "") or ""),
                "progress": float(getattr(c, "progress", 0.0) or 0.0),
                "output": self.out_path.text() if hasattr(self, "out_path") else ""}

    def restore_state(self, state):
        if not isinstance(state, dict):
            return
        for row in list(self._rows):
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        for item in (state.get("rows") or []):
            if not isinstance(item, dict):
                continue
            row = TaskRow(item.get("name") or "混剪任务",
                          item.get("meta") or "智能混剪 · 剪映草稿")
            self.tasks_card.lay.addWidget(row)
            pct = int(item.get("pct") or 0)
            row.set_state(item.get("state") or "已完成", pct, T.TEXT_3, animate=False)
            self._rows.append(row)
        c = self.win.controller
        c.status_text = state.get("status") or "待启动"
        c.progress = float(state.get("progress") or 0.0)
        self._last_status = c.status_text
        if state.get("output") and hasattr(self, "out_path"):
            self.out_path.setText(str(state["output"]))
        elif hasattr(self, "out_path"):
            self.out_path.setText("尚未生成")
        if self._rows:
            self.empty_hint.hide()
        self.queue_count.setText("%d 个任务" % len(self._rows))

    def _add_row(self):
        page = getattr(self.win, "demo_params", None)
        target = 0
        if page is not None:
            try:
                target = int(page.target.value())
            except Exception:
                target = 0
        idx = len(self._rows) + 1
        row = TaskRow("#%02d  混剪任务 · %d 秒" % (idx, target),
                      "智能混剪 · 剪映草稿")
        self.tasks_card.lay.addWidget(row)
        self._rows.append(row)
        self.empty_hint.hide()
        self.queue_count.setText("%d 个任务" % len(self._rows))

    # -----------------------------------------------------
    def _run(self):
        if not self.analysis_ready():
            self.win.controller.log("任务中心 · 尚未完成素材分析，不能开始混剪")
            return
        if not self.win.active_paths():
            self.win.controller.log("任务中心 · 素材库为空，请先在素材库导入视频")
            return
        self._sync_settings()
        self.win.start_real_mix()

    def _pause(self):
        b = self.win.backend
        if b.ready and b.toggle_pause():
            self.win.controller.log("任务中心 · 已切换后台混剪暂停状态")
            return
        self.win.controller.log("任务中心 · 后台混剪未运行，无法暂停")

    def _cancel(self):
        b = self.win.backend
        if b.ready:
            b.cancel_mix()
            self.win.controller.log("任务中心 · 已向后台混剪任务发送取消")
            return
        self.win.controller.log("任务中心 · 后台混剪未运行，无需取消")

    def _open_output(self):
        b = self.win.backend
        if b.ready and b.open_output():
            return
        self.win.controller.log("输出 · 尚无已生成的草稿目录")

    def _copy_path(self):
        text = self.out_path.text().strip()
        if not text or text == "尚未生成":
            self.win.controller.log("输出 · 尚无草稿路径可复制")
            return
        QApplication.clipboard().setText(text)
        self.win.controller.log("输出 · 草稿路径已复制到剪贴板")

    def _toggle_autoscroll(self):
        self.autoscroll = not self.autoscroll
        self.btn_autoscroll.setText("自动滚动：开" if self.autoscroll else "自动滚动：关")

    def _copy_log(self):
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self.win.controller.log("运行日志 · 已复制到剪贴板")

    def _clear_log(self):
        self.log_view.clear()
        self._log_lines = 0
        self.log_count.setText("0 条")

    # -----------------------------------------------------
    def append_log(self, line):
        self.log_view.appendPlainText(str(line))
        self._log_lines += 1
        if self.autoscroll:
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        self.log_count.setText("%d 条" % self._log_lines)

    def log(self, message, level="info"):
        self.append_log(message)

    def set_output_path(self, path):
        self.out_path.setText(str(path) if path else "尚未生成")

    # ---- 旧任务页兼容层（旧后台仍会调用这些方法，界面全部由本页承载）----
    def start_task(self, title):
        self._add_row()
        self.out_path.setText("进行中…")
        handle = _TaskHandle(title, self)
        self._card = handle
        return handle

    def current_card(self):
        return getattr(self, "_card", None)

    def update_progress(self, value, message=""):
        """进度由 controller → refresh() 驱动，这里只兜底刷新一次。"""
        self.refresh()

    def set_paused(self, paused, message=""):
        self.refresh()

    def finish_task(self, state, text=""):
        self.refresh()

    def set_last_outputs(self, items, directory=""):
        if directory:
            self.set_output_path(directory)
        self.refresh()


class _TaskHandle(QObject):
    """旧代码在任务卡上接的三个信号；新页面按钮已直接接后台，这里留空壳兼容。"""

    pause_requested = Signal()
    resume_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = str(title)
        self.step = QLabel(self.title, parent)
        self.step.hide()
