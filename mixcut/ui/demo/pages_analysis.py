# -*- coding: utf-8 -*-
"""分析中心：镜头质量 / 画面分析 / OpenCLIP / 语义覆盖 / 相似检测 / 混剪参数。"""

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QSlider, QVBoxLayout, QWidget,
)

from . import mock, theme as T
from .ui_charts import MetricRow, ScoreRow, ShotTimeline, SimilarRow, WaveBars
from .ui_kit import Card, SoftButton, Thumb, thumb_pixmap


class ParamSlider(QWidget):
    def __init__(self, name: str, value: int, lo: int, hi: int, suffix: str = "", parent=None):
        super().__init__(parent)
        self.suffix = suffix
        self.setFixedHeight(38)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)
        top = QHBoxLayout()
        name_l = QLabel(name)
        name_l.setFont(T.font(11))
        name_l.setStyleSheet("color:%s;" % T.TEXT_2)
        self.value_l = QLabel("%d%s" % (value, suffix))
        self.value_l.setFont(T.font(11, QFont.Weight.DemiBold, mono=True))
        self.value_l.setStyleSheet("color:%s;" % T.TEXT)
        top.addWidget(name_l, 1)
        top.addWidget(self.value_l)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.slider.setFixedHeight(12)
        self.slider.valueChanged.connect(lambda v: self.value_l.setText("%d%s" % (v, self.suffix)))
        lay.addWidget(self.slider)


class CoverageBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parts = [0.0, 0.0, 0.0, 0.0, 0.0]
        self.colors = ["#E8EAF0", "#A78BFA", "#79A9FF", "#63D6A0", "#E7B06B"]
        self.labels = ["开场", "特写", "中景", "空镜", "过渡"]
        self.setFixedHeight(28)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        x = r.left()
        gap = 3
        for i, part in enumerate(self.parts):
            w = (r.width() - gap * (len(self.parts) - 1)) * part
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.colors[i]))
            p.drawRoundedRect(QRectF(x, 6, w, 12), 4, 4)
            x += w + gap
        p.end()


class MixPoolDialog(QDialog):
    """已分析素材的混剪池管理：逐条加入/移出，按文件夹整组启用。"""

    def __init__(self, page, parent=None):
        super().__init__(parent or page)
        self.page = page
        self._loading = False
        self.setWindowTitle("混剪池管理")
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        tip = QLabel(
            "逐条勾选决定素材是否进入混剪池；文件夹可整组启用/取消。"
            "取消文件夹后，里面的素材不会进入混剪。")
        tip.setWordWrap(True)
        tip.setFont(T.font(11))
        tip.setStyleSheet("color:%s;" % T.TEXT_3)
        lay.addWidget(tip)

        lay.addWidget(self._title("已分析素材", "Ctrl/Shift 可多选"))
        self.source_list = QListWidget()
        self.source_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.source_list.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.source_list, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_add = SoftButton("加入选中", "secondary", height=30)
        self.btn_remove = SoftButton("移出选中", "ghost", height=30)
        self.btn_assign = SoftButton("移入文件夹", "ghost", height=30)
        self.btn_new = SoftButton("新建文件夹", "ghost", height=30)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_remove)
        row.addWidget(self.btn_assign)
        row.addWidget(self.btn_new)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(self._title("文件夹", "取消勾选 = 整组不参与混剪"))
        self.folder_lay = QVBoxLayout()
        self.folder_lay.setSpacing(4)
        lay.addLayout(self.folder_lay)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("移入到："))
        self.folder_combo = QComboBox()
        self.folder_combo.setMinimumWidth(180)
        row2.addWidget(self.folder_combo)
        row2.addStretch(1)
        self.btn_close = SoftButton("关闭", "ghost", height=30)
        row2.addWidget(self.btn_close)
        lay.addLayout(row2)

        self.btn_add.clicked.connect(self._add_selected)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_assign.clicked.connect(self._assign_selected)
        self.btn_new.clicked.connect(self._new_folder)
        self.btn_close.clicked.connect(self.accept)
        self._refresh()

    def _title(self, text, sub):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(2)
        a = QLabel(text)
        a.setFont(T.font(12, QFont.Weight.DemiBold))
        b = QLabel(sub)
        b.setFont(T.font(10))
        b.setStyleSheet("color:%s;" % T.TEXT_3)
        lay.addWidget(a)
        lay.addWidget(b)
        return box

    def _payload(self):
        return self.page._pool_payload()

    def _paths(self, items):
        return [it.data(Qt.ItemDataRole.UserRole) for it in items]

    def _selected(self):
        return self._paths(self.source_list.selectedItems())

    def _refresh(self):
        payload = self._payload()
        items = payload.get("items") or []
        folders = payload.get("folders") or []
        self._loading = True
        self.source_list.clear()
        for it in items:
            name = it.get("folder_name") or "未分类"
            text = "%s  ·  %d 段  ·  %.1fs  ·  %s" % (
                it.get("name", ""), int(it.get("segments", 0)),
                float(it.get("duration", 0)), name)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, it.get("path"))
            item.setToolTip(it.get("path", ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if it.get("in_pool") else
                Qt.CheckState.Unchecked)
            self.source_list.addItem(item)

        while self.folder_lay.count():
            child = self.folder_lay.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.folder_combo.clear()
        self.folder_combo.addItem("未分类", "")
        for folder in folders:
            checked = bool(folder.get("checked", True))
            box = QCheckBox("%s（%d 条）" % (
                folder.get("name", ""), int(folder.get("count", 0))))
            box.setChecked(checked)
            box.toggled.connect(
                lambda on, fid=folder.get("id"): self._toggle_folder(fid, on))
            self.folder_lay.addWidget(box)
            self.folder_combo.addItem(folder.get("name", ""), folder.get("id"))
        self._loading = False
        self.page._update_scope()

    def _on_item_changed(self, item):
        if self._loading:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        ok = item.checkState() == Qt.CheckState.Checked
        self.page.win.backend.pool_set_member(path, ok)
        self.page._update_scope()

    def _toggle_folder(self, folder_id, checked):
        if self._loading:
            return
        self.page.win.backend.pool_set_folder_checked(folder_id, checked)
        self._refresh()

    def _add_selected(self):
        paths = self._selected()
        if not paths:
            QMessageBox.information(self, "混剪池", "请先在列表里选择素材。")
            return
        for path in paths:
            self.page.win.backend.pool_set_member(path, True)
        self._refresh()

    def _remove_selected(self):
        paths = self._selected()
        if not paths:
            QMessageBox.information(self, "混剪池", "请先在列表里选择素材。")
            return
        for path in paths:
            self.page.win.backend.pool_set_member(path, False)
        self._refresh()

    def _new_folder(self):
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not ok or not name.strip():
            return
        self.page.win.backend.pool_create_folder(name.strip())
        self._refresh()

    def _assign_selected(self):
        paths = self._selected()
        if not paths:
            QMessageBox.information(self, "混剪池", "请先在列表里选择素材。")
            return
        folder_id = self.folder_combo.currentData() or ""
        if not folder_id:
            QMessageBox.information(self, "混剪池", "请先选择要移入的文件夹。")
            return
        self.page.win.backend.pool_assign_folder(paths, folder_id)
        self._refresh()


class AnalysisPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.real_mode = False
        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAGE_PAD, 14, T.PAGE_PAD, 14)
        root.setSpacing(14)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        h = QLabel("分析中心")
        h.setFont(T.font(30, QFont.Weight.DemiBold))
        self.sub = QLabel("尚未分析 · 导入素材后可开始分析")
        self.sub.setFont(T.font(12))
        self.sub.setStyleSheet("color:%s;" % T.TEXT_3)
        titles.addWidget(h)
        titles.addWidget(self.sub)
        self.scope = QLabel("本轮素材：0 条")
        self.scope.setFont(T.font(11, mono=True))
        self.scope.setStyleSheet("color:%s;" % T.TEXT_3)
        titles.addWidget(self.scope)
        head.addLayout(titles, 1)
        self.btn = SoftButton("重新分析", "secondary", "spark")
        head.addWidget(self.btn)
        root.addLayout(head)

        ctl = QHBoxLayout()
        ctl.setSpacing(8)
        lab = QLabel("分析模式")
        lab.setFont(T.font(11))
        lab.setStyleSheet("color:%s;" % T.TEXT_3)
        ctl.addWidget(lab)
        self.mode = QComboBox()
        self.mode.addItem("快速（镜头边界）", 0)
        self.mode.addItem("标准（画面 + 语义）", 1)
        self.mode.addItem("精细（全量质检）", 2)
        self.mode.setCurrentIndex(1)
        self.mode.setFixedWidth(176)
        self.mode.setFixedHeight(32)
        ctl.addWidget(self.mode)
        self.btn_start = SoftButton("开始分析", "secondary", "play", height=32)
        self.btn_stop = SoftButton("停止", "ghost", height=32)
        self.btn_stop.setEnabled(False)
        self.btn_cancel = SoftButton("取消", "ghost", height=32)
        self.btn_pool = SoftButton("混剪池", "secondary", height=32)
        self.btn_mix = SoftButton("开始混剪", "primary", "flow", height=32)
        self.btn_fs = SoftButton("全屏", "ghost", "expand", height=32)
        for b in (self.btn_start, self.btn_stop, self.btn_cancel,
                  self.btn_pool, self.btn_mix, self.btn_fs):
            ctl.addWidget(b)
        ctl.addStretch(1)
        root.addLayout(ctl)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid, 1)

        # 1 镜头质量
        qcard = Card(padding=18, spacing=6)
        qcard.setMinimumHeight(252)
        grid.addWidget(qcard, 0, 0)
        qcard.lay.addWidget(self._title("镜头质量", "清晰度、运动与稳定性"))
        self.metrics = {}
        for key, name, suffix, fmt in (("quality", "片段清晰度", "%", "{:.0f}"),
                                        ("bad", "坏帧比例", "%", "{:.1f}"),
                                        ("stable", "稳定率", "%", "{:.1f}"),
                                        ("valid", "可用率", "%", "{:.0f}")):
            row = MetricRow(name, 0, suffix, fmt)
            self.metrics[key] = row
            qcard.lay.addWidget(row)
        qcard.lay.addStretch(1)

        # 2 画面分析
        scard = Card(padding=18, spacing=10)
        scard.setMinimumHeight(274)
        grid.addWidget(scard, 0, 1)
        scard.lay.addWidget(self._title("画面分析", "等待素材分析结果"))
        self.timeline = ShotTimeline()
        scard.lay.addWidget(self.timeline)
        row = QHBoxLayout()
        row.setSpacing(8)
        for i, (name, label) in enumerate(((1, "00:04"), (0, "00:12"), (5, "00:28"))):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(5)
            th = Thumb(mock.THUMBS[name], radius=6)
            th.setFixedHeight(56)
            bl.addWidget(th)
            cap = QLabel("关键片段 %s" % label)
            cap.setFont(T.font(10, mono=True))
            cap.setStyleSheet("color:%s;" % T.TEXT_3)
            bl.addWidget(cap)
            row.addWidget(box)
        scard.lay.addLayout(row)
        self.shot_note = QLabel("等待镜头边界分析")
        self.shot_note.setFont(T.font(11))
        self.shot_note.setStyleSheet("color:%s;" % T.TEXT_3)
        scard.lay.addWidget(self.shot_note)

        # 3 OpenCLIP
        ocard = Card(padding=18, spacing=4)
        ocard.setMinimumHeight(286)
        grid.addWidget(ocard, 1, 0)
        orow = QHBoxLayout()
        ot = QVBoxLayout()
        ot.setSpacing(3)
        ot.addWidget(self._title("OpenCLIP 匹配", "语义标签与候选片段"))
        self.clip_score = QLabel("最佳候选 —")
        self.clip_score.setFont(T.font(11, mono=True))
        self.clip_score.setStyleSheet("color:%s;" % T.TEXT_3)
        ot.addWidget(self.clip_score)
        orow.addLayout(ot, 1)
        self.cand = Thumb(mock.THUMBS[0], radius=7)
        self.cand.setFixedSize(84, 48)
        orow.addWidget(self.cand)
        ocard.lay.addLayout(orow)
        self.scores = {}
        for name, score in (("开场", 0.0), ("特写", 0.0), ("空镜", 0.0), ("过渡", 0.0)):
            row = ScoreRow(name, score)
            self.scores[name] = row
            ocard.lay.addWidget(row)
        ocard.lay.addStretch(1)

        # 4 语义覆盖
        ccard = Card(padding=18, spacing=10)
        ccard.setMinimumHeight(286)
        grid.addWidget(ccard, 1, 1)
        ccard.lay.addWidget(self._title("语义覆盖", "片段语义分布 · 越均衡越好"))
        self.cov = CoverageBar()
        ccard.lay.addWidget(self.cov)
        legend = QGridLayout()
        legend.setHorizontalSpacing(14)
        legend.setVerticalSpacing(6)
        for i, (lab, col) in enumerate(zip(self.cov.labels, self.cov.colors)):
            item = QWidget()
            hl = QHBoxLayout(item)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            d = QWidget()
            d.setFixedSize(8, 8)
            d.setStyleSheet("background:%s;border-radius:4px;" % col)
            t = QLabel(lab)
            t.setFont(T.font(11))
            t.setStyleSheet("color:%s;" % T.TEXT_2)
            hl.addWidget(d)
            hl.addWidget(t, 1)
            legend.addWidget(item, i // 3, i % 3)
        ccard.lay.addLayout(legend)
        self.covwave = WaveBars()
        ccard.lay.addWidget(self.covwave)
        self.cov_note = QLabel("等待分析 · 暂无覆盖率数据")
        self.cov_note.setFont(T.font(11))
        self.cov_note.setStyleSheet("color:%s;" % T.TEXT_3)
        ccard.lay.addWidget(self.cov_note)

        # 5 相似素材（跨两列，参数统一放到「参数设置」页）
        sim = Card(padding=18, spacing=8)
        grid.addWidget(sim, 2, 0, 1, 2)
        sim.lay.addWidget(self._title("相似素材检测", "跨片段指纹比对"))
        self.simrow = SimilarRow(mock.THUMBS[0], mock.THUMBS[0], 0.0)
        sim.lay.addWidget(self.simrow)
        self.sim_note = QLabel("等待分析 · 暂无相似片段")
        self.sim_note.setFont(T.font(11))
        self.sim_note.setStyleSheet("color:%s;" % T.TEXT_3)
        sim.lay.addWidget(self.sim_note)
        self.simrow2 = SimilarRow(mock.THUMBS[2], mock.THUMBS[2], 0.0)
        sim.lay.addWidget(self.simrow2)
        self.sim_note2 = QLabel("等待分析 · 暂无相似片段")
        self.sim_note2.setFont(T.font(11))
        self.sim_note2.setStyleSheet("color:%s;" % T.TEXT_3)
        sim.lay.addWidget(self.sim_note2)
        sim.lay.addStretch(1)
        self.sim_foot = QLabel("指纹算法 · pHash + OpenCLIP 向量 · 等待分析")
        self.sim_foot.setFont(T.font(10))
        self.sim_foot.setStyleSheet("color:%s;" % T.TEXT_3)
        sim.lay.addWidget(self.sim_foot)

        # 6 混剪参数已迁移到独立「参数设置」页

        self.btn.clicked.connect(self._start)
        self.btn_start.clicked.connect(self._start)
        self.btn_mix.clicked.connect(self._mix)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_cancel.clicked.connect(self._stop)
        self.btn_pool.clicked.connect(self._manage_pool)
        self.btn_fs.clicked.connect(lambda: win.go_page("work"))
        win.controller.stateChanged.connect(self.refresh)
        self._real_timer = QTimer(self)
        self._real_timer.setInterval(600)
        self._real_timer.timeout.connect(self._poll_real)
        self._real_summary = {}
        self._pool_state = {"items": [], "folders": []}

    def _start(self):
        if not self.win.active_paths():
            self.real_mode = False
            self.win.controller.log("分析中心 · 素材库为空，请先导入素材")
            return
        self.real_mode = True
        self._real_summary = {}
        ok = self.win.start_real_analysis()
        if ok:
            self.sub.setText("正在分析 · 0% · 后台准备中")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self._real_timer.start()

    def _mix(self):
        paths = self.mix_pool_paths()
        if not paths:
            self.win.controller.log(
                "分析中心 · 混剪池为空，请先在「混剪池」里加入已分析素材")
            return
        self.win.start_real_mix(paths)

    def _stop(self):
        b = self.win.backend
        if self.real_mode and b.ready:
            b.stop_analysis()
            self.win.controller.log("分析中心 · 已向后台分析任务发送停止")
            self.btn_start.setEnabled(True)
            self._real_timer.stop()
            return
        self.win.controller.log("分析中心 · 当前没有正在运行的后台分析")

    def _poll_real(self):
        if not self.real_mode:
            self._real_timer.stop()
            return
        b = self.win.backend
        state = b.analysis_state()
        if not state:
            return
        self._pool_state = state.get("pool") or {"items": [], "folders": []}
        c = self.win.controller
        pct = int(state.get("progress", 0))
        c.progress = pct / 100.0
        c.active_stage = 1 if state.get("running") else -1
        c.status_text = "分析中" if state.get("running") else (
            "已分析" if state.get("results") else "就绪")
        self._apply_real_state(state)
        if state.get("running"):
            self.sub.setText("正在分析 · %d%% · %s" % (pct, state.get("label", "")))
        else:
            self._real_timer.stop()
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            c.analyzed = bool(state.get("results"))
            c.analyzed_all = bool(state.get("results"))
            label = state.get("label", "")
            if not state.get("results") and "错误" in label:
                c.status_text = "分析失败"
                self.sub.setText(label)
            else:
                c.status_text = "已分析" if state.get("results") else "就绪"
                self.sub.setText("分析完成 · %d 个片段" % int(state.get("results", 0)))
            c.stateChanged.emit()

    def _apply_real_state(self, state):
        summary = state.get("summary") or {}
        self._real_summary = summary
        if summary:
            self.metrics["quality"].set_value(float(summary.get("quality", 0)))
            self.metrics["bad"].set_value(float(summary.get("bad_ratio", 0)))
            self.metrics["stable"].set_value(float(summary.get("stable", 0)))
            self.metrics["valid"].set_value(float(summary.get("valid", 0)))
            segs = state.get("segments") or []
            durations = [max(0.1, s.get("end", 0) - s.get("start", 0)) for s in segs]
            self.timeline.segments = durations
            self.timeline.set_lit(sum(1 for s in segs if s.get("included")))
            self.timeline.update()
            bad = sum(1 for s in segs if s.get("bad"))
            self.shot_note.setText(
                "已分析 %d 段 · 可用 %d 段 · 疑似废片 %d 段" % (
                    int(summary.get("segments", 0)),
                    int(round(int(summary.get("segments", 0)) *
                              float(summary.get("valid", 0)) / 100.0)),
                    bad))
            self.clip_score.setText(
                "分析完成 · %d 条素材 / %d 个片段" % (
                    int(summary.get("paths", 0)), int(summary.get("segments", 0))))
        else:
            for key in ("quality", "bad", "stable", "valid"):
                self.metrics[key].set_value(0)
            self.timeline.segments = []
            self.timeline.set_lit(0)
            self.shot_note.setText("等待镜头边界分析")
            self.clip_score.setText("最佳候选 —")

    def _title(self, t, sub):
        box = QWidget()
        box.setFixedHeight(42)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        a = QLabel(t)
        a.setFont(T.font(14, QFont.Weight.DemiBold))
        b = QLabel(sub)
        b.setFont(T.font(11))
        b.setStyleSheet("color:%s;" % T.TEXT_3)
        lay.addWidget(a)
        lay.addWidget(b)
        return box

    def _pool_payload(self, refresh=False):
        if refresh:
            try:
                state = self.win.backend.analysis_state()
                self._pool_state = state.get("pool") or {
                    "items": [], "folders": []}
            except Exception:
                self._pool_state = {"items": [], "folders": []}
        return self._pool_state

    def mix_pool_paths(self):
        if not self._pool_state.get("items") and self._real_summary:
            self._pool_payload(refresh=True)
        items = self._pool_state.get("items") or []
        return [str(it["path"]) for it in items
                if it.get("in_pool") and it.get("path")]

    def _manage_pool(self):
        self._pool_payload(refresh=True)
        dlg = MixPoolDialog(self, self)
        dlg.exec()
        self._pool_payload(refresh=True)
        self._update_scope()

    def _update_scope(self):
        items = self._pool_state.get("items") or []
        paths = self.mix_pool_paths()
        if paths:
            self.scope.setText(
                "混剪池：%d 条 · 已分析 %d 条 · 可逐条/按文件夹启停" %
                (len(paths), len(items)))
            self.scope.setStyleSheet("color:%s;" % T.TEXT_3)
        elif items:
            self.scope.setText(
                "混剪池：0 条 · 已分析 %d 条，请打开「混剪池」加入" % len(items))
            self.scope.setStyleSheet("color:%s;" % T.WARN)
        else:
            self.scope.setText("本轮素材：0 条 · 请先在素材库选择并分析")
            self.scope.setStyleSheet("color:%s;" % T.TEXT_3)

    def refresh(self):
        c = self.win.controller
        self._update_scope()
        cur = c.current_clip()
        self.sub.setText("当前素材 %s · %s" % (cur["name"], "已分析" if c.analyzed else "尚未分析"))
        if self._real_summary:
            for key in ("quality", "bad", "stable", "valid"):
                self.metrics[key].set_value(float(self._real_summary.get(
                    {"quality": "quality", "bad": "bad_ratio",
                     "stable": "stable", "valid": "valid"}[key], 0)))
        else:
            for key in ("quality", "bad", "stable", "valid"):
                self.metrics[key].set_value(0)
        self.timeline.set_playhead(c.progress)
        self.shot_score_update = 1.0 if c.analyzed else 0.0
        for row in self.scores.values():
            row.set_score(0)
        if self._real_summary:
            self.clip_score.setText(
                "分析完成 · %d 条素材 / %d 个片段" % (
                    int(self._real_summary.get("paths", 0)),
                    int(self._real_summary.get("segments", 0))))
        else:
            self.clip_score.setText("最佳候选 —")
        self.cand.name = mock.THUMBS[cur["thumb"]]
        self.cand.update()
