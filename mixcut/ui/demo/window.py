# -*- coding: utf-8 -*-
"""主窗口：macOS 风格侧栏 + 页面 + 顶栏；真实功能由 BackendBridge 调用旧后台。"""

import os
import time

from PySide6.QtCore import (
    QEasingCurve, QObject, QPoint, QPropertyAnimation, QRectF, QTimer, QVariantAnimation,
    Qt, Signal,
)
from PySide6.QtGui import (QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen,
                           QPixmap, QShortcut)
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMainWindow, QSizeGrip,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import mock, theme as T
from .backend_bridge import BackendBridge
from .pages_analysis import AnalysisPage
from .pages_library import LibraryPage
from .pages_params import ParamsPage
from .pages_settings import SettingsPage
from .pages_tasks import TasksPage
from .pages_work import WorkbenchPage
from .ui_kit import Dot, IconButton, NavItem, SoftButton, draw_icon


class BrandMark(QWidget):
    """侧栏品牌标记：直接用应用图标（assets/app.png），与任务栏图标一致。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        path = os.path.join(root, "assets", "app.png")
        self._pm = QPixmap(path) if os.path.exists(path) else QPixmap()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 9, 9)
        p.setClipPath(path)
        if not self._pm.isNull():
            scaled = self._pm.scaled(self.size(),
                                     Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(0, 0, scaled)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ECEDF1"))
            p.drawRoundedRect(r, 9, 9)
            p.setPen(QPen(QColor("#131418"), 2))
            p.drawLine(10, 23, 13, 11)
            p.drawLine(13, 11, 17, 20)
            p.drawLine(17, 20, 21, 11)
            p.drawLine(21, 11, 24, 23)
        p.end()


class TopBar(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._drag = None
        self.setFixedHeight(T.TOPBAR_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 0, 18, 0)
        lay.setSpacing(12)

        crumb = QLabel("未命名工程")
        self.crumb = crumb
        crumb.setFont(T.font(12, QFont.Weight.DemiBold))
        crumb.setStyleSheet("color:%s;" % T.TEXT)
        lay.addWidget(crumb)
        sep = QLabel("/")
        sep.setFont(T.font(11))
        sep.setStyleSheet("color:%s;" % T.TEXT_3)
        lay.addWidget(sep)
        sub = QLabel("草稿 01")
        self.sub = sub
        sub.setFont(T.font(11))
        sub.setStyleSheet("color:%s;" % T.TEXT_3)
        lay.addWidget(sub)
        lay.addStretch(1)

        self.status_dot = Dot(T.TEXT_3, 7)
        lay.addWidget(self.status_dot)
        self.status = QLabel("就绪")
        self.status.setFont(T.font(11))
        self.status.setStyleSheet("color:%s;" % T.TEXT_2)
        lay.addWidget(self.status)
        lay.addSpacing(8)
        self.gpu = QLabel("RTX 4060 · CUDA 13.5")
        self.gpu.setFont(T.font(10, mono=True))
        self.gpu.setStyleSheet("color:%s;" % T.TEXT_3)
        lay.addWidget(self.gpu)
        lay.addSpacing(6)
        self.search = IconButton("search", 32, tooltip="搜索")
        self.settings = IconButton("sliders", 32, tooltip="设置")
        lay.addWidget(self.search)
        lay.addWidget(self.settings)

        # 工程操作必须有可见中文入口，不能只靠 Ctrl 快捷键
        self.btn_new = SoftButton("新建工程", "ghost", height=30)
        self.btn_open = SoftButton("打开工程", "ghost", height=30)
        self.btn_save = SoftButton("保存工程", "secondary", height=30)
        lay.addWidget(self.btn_new)
        lay.addWidget(self.btn_open)
        lay.addWidget(self.btn_save)
        lay.addSpacing(6)

        self.btn_import = SoftButton("导入素材", "primary", "import", height=32, min_width=100)
        lay.addWidget(self.btn_import)
        lay.addSpacing(6)
        self.btn_min = IconButton("minus", 28, tooltip="最小化")
        self.btn_max = IconButton("maximize", 28, tooltip="最大化 / 还原")
        self.btn_close = IconButton("close", 28, tooltip="关闭", danger=True)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        lay.addWidget(self.btn_close)

        self.btn_import.clicked.connect(win._topbar_import)
        self.btn_new.clicked.connect(lambda: win.new_project())
        self.btn_open.clicked.connect(lambda: win.open_project())
        self.btn_save.clicked.connect(lambda: win._shortcut_save())
        self.settings.clicked.connect(lambda: win.go_page("settings"))
        self.search.clicked.connect(lambda: win.go_page("library"))
        self.btn_min.clicked.connect(win.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close.clicked.connect(win.close)

    # 旧顶栏接口兼容
    def set_counts(self, total, selected):
        self.status.setText("素材 %d · 已选 %d" % (int(total), int(selected)))

    def set_project(self, name):
        self.crumb.setText(str(name) or "未命名工程")

    def _toggle_max(self):
        if self.win.isMaximized():
            self.win.showNormal()
            self.btn_max.icon = "maximize"
        else:
            self.win.showMaximized()
            self.btn_max.icon = "restore"
        self.btn_max.update()

    def mouseDoubleClickEvent(self, e):
        # 双击标题栏 = 最大化 / 还原（原生标题栏已隐藏）
        if e.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()
        super().mouseDoubleClickEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = (e.globalPosition().toPoint(), self.win.frameGeometry().topLeft())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag and e.buttons() & Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._drag[0]
            self.win.move(self._drag[1] + delta)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        self._toggle_max()
        super().mouseDoubleClickEvent(e)


class Sidebar(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setFixedWidth(T.SIDEBAR_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(BrandMark())
        btext = QVBoxLayout()
        btext.setSpacing(0)
        name = QLabel("MixCut")
        name.setFont(T.font(18, QFont.Weight.DemiBold))
        name.setStyleSheet("color:%s;" % T.TEXT)
        sub = QLabel("智能混剪工作台")
        sub.setFont(T.font(9))
        sub.setStyleSheet("color:%s;" % T.TEXT_3)
        btext.addWidget(name)
        btext.addWidget(sub)
        brand.addLayout(btext)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(18)

        self.items = {}
        self._group(lay, "工作区")
        for key, label, icon in (("library", "素材库", "library"),
                                  ("work", "素材预览", "home"),
                                  ("analysis", "AI 分析", "spark"),
                                  ("params", "参数设置", "sliders"),
                                  ("tasks", "任务中心", "flow")):
            item = NavItem(key, label, icon)
            item.clicked.connect(lambda _=False, k=key: win.go_page(k))
            lay.addWidget(item)
            self.items[key] = item
        lay.addStretch(1)

        foot = QWidget()
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(10, 10, 10, 10)
        fl.setSpacing(5)
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        d = Dot(T.OK, 6)
        r1.addWidget(d)
        t = QLabel("就绪 · 按需加载后台")
        self.status_label = t
        t.setFont(T.font(10, QFont.Weight.Medium))
        t.setStyleSheet("color:%s;" % T.TEXT_2)
        r1.addWidget(t)
        r1.addStretch(1)
        fl.addLayout(r1)
        t2 = QLabel("后台在首次导入素材时加载")
        self.hint_label = t2
        t2.setFont(T.font(9))
        t2.setStyleSheet("color:%s;" % T.TEXT_3)
        fl.addWidget(t2)
        foot.setStyleSheet("background:%s;border-radius:10px;" % T.CARD)
        lay.addWidget(foot)

    def _group(self, lay, text):
        cap = QLabel(text.upper())
        cap.setFont(T.font(9, QFont.Weight.DemiBold))
        cap.setStyleSheet("color:%s;letter-spacing:1px;" % T.TEXT_3)
        cap.setContentsMargins(6, 4, 0, 4)
        lay.addWidget(cap)

    def select(self, key):
        for k, item in self.items.items():
            item.set_active(k == key)
        self.win.go_page(key)

    def set_status(self, text, kind="idle"):
        # 主题里错误色叫 ERR，这里曾经写成 DANGER，混剪一启动就 AttributeError
        colors = {"success": T.OK, "warning": T.WARN,
                  "error": getattr(T, "ERR", "#E58B84")}
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            "color:%s;" % colors.get(kind, T.TEXT_2))



class DemoController(QObject):
    """全部模拟状态。API 与旧版保持一致，便于验收脚本复用。"""

    stateChanged = Signal()
    logAdded = Signal(str)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self._timers = []
        self._pending = []
        self._anims = []
        self._pending_progress = None
        self.reset(initial=True)

    # ---- 状态 ----
    def reset(self, initial=False):
        self._stop()
        if hasattr(self.win, "_scope_paths"):
            self.win._scope_paths = None
        self.selected = set()
        self.removed = set()
        self.imported = False
        self.analyzed_all = False
        self.analyzed = False
        self.openclip_done = False
        self.deduped = False
        self.filtered = False
        self.mixed = False
        self.drafted = False
        self.paused = False
        self.progress = 0.0
        self.active_stage = -1
        self.done_stages = 0
        self.status_text = "就绪"
        self.timecode = "00:00:00:00"
        self.boxes = 0.0
        self.glow = 0.0
        self._logs = []
        self._current = mock.CLIPS[0]["id"] if mock.CLIPS else ""
        if not initial:
            self.win.logs_page.log_view.clear()
        self.stateChanged.emit()

    def _stop(self):
        for rec in self._timers:
            try:
                rec["timer"].stop()
            except Exception:
                pass
        self._timers = []
        self._pending = []
        for a in self._anims:
            try:
                a.stop()
            except Exception:
                pass
        self._anims = []

    def _later(self, ms, fn):
        if self.paused:
            self._pending.append((ms, fn))
            return
        t = QTimer(self.win)
        t.setSingleShot(True)
        rec = {"timer": t, "fn": fn, "due": time.time() + ms / 1000.0}
        t.timeout.connect(lambda: self._fire(rec))
        t.start(ms)
        self._timers.append(rec)

    def _fire(self, rec):
        try:
            rec["timer"].stop()
        except Exception:
            pass
        if rec in self._timers:
            self._timers.remove(rec)
        if self.paused:
            self._pending.append((0, rec["fn"]))
            return
        rec["fn"]()

    def log(self, line):
        self._logs.append(line)
        self.logAdded.emit(line)

    def log_count(self):
        return len(self._logs)

    def visible_count(self):
        return len(mock.CLIPS) - len(self.removed)

    def usable_count(self):
        return max(0, self.visible_count() - (2 if self.filtered else 0))

    def current_clip(self):
        for c in mock.CLIPS:
            if c["id"] == self._current:
                return c
        if not mock.CLIPS:
            return {"id": "", "name": "暂无素材", "thumb": 0, "dur": "—",
                    "shots": 0, "folder": "", "quality": 0, "motion": 0,
                    "stability": 0, "tags": []}
        return mock.CLIPS[0]

    def select_clip(self, cid):
        self._current = cid
        self.stateChanged.emit()

    def toggle_selection(self, cid):
        win = getattr(self, "win", None)
        if win is not None and hasattr(win, "_push_history"):
            win._push_history()
        if cid in self.selected:
            self.selected.discard(cid)
        else:
            self.selected.add(cid)
        self._current = cid
        self.stateChanged.emit()

    def clear_selection(self):
        win = getattr(self, "win", None)
        if win is not None and hasattr(win, "_push_history"):
            win._push_history()
        self.selected.clear()
        self.stateChanged.emit()

    def stage_hint(self):
        hints = ["扫描素材目录并建立索引", "检测镜头边界与关键片段", "OpenCLIP 语义编码与匹配",
                 "移除黑场、静默与无效片段", "按时长目标编排片段顺序", "生成剪映草稿"]
        if self.drafted:
            return "任务完成 · 草稿可进入剪映精修"
        if self.active_stage >= 0:
            return "第 %d / 6 阶段 · %s" % (self.active_stage + 1, hints[self.active_stage])
        return "待启动 · 共 6 个阶段"

    def _set_progress(self, target, active, done, status, ms=520):
        if self.paused:
            self._pending_progress = (target, active, done, status, ms)
            self.active_stage = active
            self.done_stages = done
            self.status_text = status
            self.stateChanged.emit()
            return
        self.active_stage = active
        self.done_stages = done
        self.status_text = status
        anim = QVariantAnimation(self.win)
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(self.progress)
        anim.setEndValue(float(target))
        anim.valueChanged.connect(self._tick_progress)
        anim.start()
        self._anims.append(anim)

    def _tick_progress(self, v):
        self.progress = float(v)
        self.timecode = "00:00:%02d:%02d" % (int(v * 34), int((v * 34 * 25) % 25))
        self.stateChanged.emit()

    # ---- 流程 ----
    def import_clips(self):
        if self.imported:
            return
        self.imported = True
        self.progress = 0.02
        self.status_text = "导入中"
        self.log("001 · 扫描素材目录 D:\\素材\\11.12 柯馨")
        self._later(380, lambda: self._after_import())

    def _after_import(self):
        self._set_progress(0.10, 0, 1, "素材已导入")
        self.log("002 · 发现 10 个视频文件，建立素材索引 4K / HEVC")

    def _need_import(self):
        if not self.imported:
            self.import_clips()
            return True
        return False

    def analyze(self, cid=None):
        if cid:
            self._current = cid
        if not self.selected:
            self.selected.update(["c1", "c3"])
        self.analyzed = True
        self.status_text = "分析中"
        self.active_stage = 1
        self._start_scan()
        if not self.analyzed_all:
            self._set_progress(0.28, 1, 1, "镜头检测中", 700)
            self._later(760, self._finish_shots)
        else:
            self.stateChanged.emit()

    def _start_scan(self):
        # 预览窗口不再叠加扫描/识别框特效，仅保留状态与进度
        self.win.workbench.preview.set_scan(-1.0)

    def _tick_scan(self, v):
        self.win.workbench.preview.set_scan(float(v))

    def _tick_boxes(self, v):
        self.boxes = float(v)
        self.win.workbench.preview.set_boxes(self.boxes)

    def _finish_shots(self):
        self.log("003 · 镜头边界检测：47 个镜头 · 7 段疑似静止")
        self.log("004 · 关键片段评分完成，保留 3 段")
        self._set_progress(0.32, 2, 2, "语义编码中", 600)
        self._later(620, self._finish_analysis)

    def _finish_analysis(self):
        self.analyzed_all = True
        self.status_text = "已完成"
        self.win.workbench.preview.set_scan(-1.0)
        self._set_progress(0.36, -1, 2, "分析完成")
        self.stateChanged.emit()

    def analyze_selected(self):
        if self.selected:
            self.select_clip(sorted(self.selected)[0])
        self.analyze()

    def openclip(self):
        if not self.analyzed_all:
            self.analyze()
        self.openclip_done = True
        self._set_progress(0.58, 2, 3, "语义编码中", 700)
        self.log("005 · 加载 ViT-L/14 (OpenCLIP)，设备 cuda:0")
        self._later(520, lambda: self.log("006 · 编码 12 个候选片段 → 768 维向量，2.47 s/段"))
        self._later(760, self._after_openclip)

    def _after_openclip(self):
        self.log("007 · 语义多样性评分完成，平均相似度 0.58")
        self._set_progress(0.60, 3, 3, "语义匹配完成")
        self.stateChanged.emit()

    def dedupe(self):
        self.deduped = True
        self.log("008 · 发现高度相似片段 C2991 / C2996，相似度 0.93")
        self.stateChanged.emit()

    def filter_invalid(self):
        self.filtered = True
        self.log("009 · 后台分析将无效片段移出候选池")
        self._set_progress(0.70, 3, 4, "无效过滤完成")
        self.stateChanged.emit()

    def mix(self):
        self.filtered = True
        self.mixed = True
        self._set_progress(0.85, 4, 5, "混剪编排中", 700)
        self.log("011 · 目标时长 30±5 秒 · 最短 2 秒 · 允许重复使用")
        self._later(700, lambda: self.log("012 · 混剪组合完成：13 段，预计 31.7 秒"))

    def draft(self):
        self.mixed = True
        self.drafted = True
        self._set_progress(1.0, 5, 6, "已完成", 900)
        self.log("013 · 按时间轴生成剪映草稿（模拟）")
        self.log("014 · 草稿已生成：MixCut_Mix_20261115_1209（模拟路径）")
        self.log("015 · 任务完成，可进入剪映继续精修")
        self.active_stage = -1
        self.done_stages = 6
        self.status_text = "已完成"
        self.win.workbench.preview.set_scan(-1.0)
        self.stateChanged.emit()

    def _tick_glow(self, v):
        self.glow = float(v)
        self.win.workbench.preview.set_glow(self.glow)

    def run_all(self):
        self.reset()
        self._later(60, self.import_clips)
        self._later(900, self.analyze)
        self._later(2400, self.openclip)
        self._later(3400, self.dedupe)
        self._later(3900, self.filter_invalid)
        self._later(4600, self.mix)
        self._later(5600, self.draft)

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            now = time.time()
            for rec in self._timers:
                try:
                    if rec["timer"].isActive():
                        rec["timer"].stop()
                        rem = max(0, int((rec["due"] - now) * 1000))
                        self._pending.append((rem, rec["fn"]))
                except Exception:
                    pass
            self._timers = []
            for a in self._anims:
                a.pause()
            self.status_text = "已暂停"
        else:
            for a in self._anims:
                a.resume()
            self.status_text = "运行中"
            pending = self._pending
            self._pending = []
            for ms, fn in pending:
                self._later(ms, fn)
            if self._pending_progress:
                args = self._pending_progress
                self._pending_progress = None
                self._set_progress(*args)
        self.stateChanged.emit()

    stateChanged = Signal()
    logAdded = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MixCut Studio")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.resize(1600, 1000)
        self.setMinimumSize(1200, 780)
        self.setStyleSheet(T.qss())

        self.controller = DemoController(self)
        # None=按素材库选择/当前文件夹推导本轮范围；[]=显式清空。
        self._scope_paths = None
        # 旧后台按需加载；未点击真实功能按钮前不拖慢启动。
        self.backend = BackendBridge(self)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar(self)
        outer.addWidget(self.sidebar)

        main = QWidget()
        main.setObjectName("Content")
        mv = QVBoxLayout(main)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)
        outer.addWidget(main, 1)

        self.topbar = TopBar(self)
        mv.addWidget(self.topbar)

        self.stack = QStackedWidget()
        mv.addWidget(self.stack, 1)

        self.workbench = WorkbenchPage(self)
        self.library = LibraryPage(self)
        self.analysis = AnalysisPage(self)
        self.params = ParamsPage(self)
        self.tasks = TasksPage(self)
        self.settings_page = SettingsPage(self)
        self.pages = {"library": self.library, "analysis": self.analysis,
                      "params": self.params, "work": self.workbench,
                      "tasks": self.tasks, "settings": self.settings_page}
        for p in self.pages.values():
            self.stack.addWidget(p)
        self.logs_page = self.tasks
        self.current_page = "library"

        grip = QSizeGrip(root)
        grip.setFixedSize(18, 18)
        grip.move(root.width() - 18, root.height() - 18)

        self.controller.stateChanged.connect(self._sync_chrome)
        self.go_page("library", animate=False)
        self._sync_chrome()
        self._bind_shortcuts()

    def _topbar_import(self):
        self.library._add_files()

    def selected_paths(self):
        """新 UI 当前勾选素材对应的真实路径；没有路径的模拟素材返回空。"""
        out = []
        for cid in getattr(self.controller, "selected", set()):
            for clip in mock.CLIPS:
                if clip.get("id") == cid and clip.get("path"):
                    out.append(clip["path"])
                    break
        return out

    def library_paths(self):
        """素材库当前全部真实路径。"""
        paths = []
        for clip in mock.CLIPS:
            path = clip.get("path")
            if path and path not in paths:
                paths.append(path)
        return paths

    def analysis_scope_paths(self):
        """本轮分析/混剪范围：显式范围优先，其次勾选素材，最后当前文件夹可见素材。"""
        forced = getattr(self, "_scope_paths", None)
        if forced is not None:
            return list(forced)
        selected = self.selected_paths()
        if selected:
            return selected
        library = getattr(self, "library", None)
        if library is not None and hasattr(library, "visible_paths"):
            return library.visible_paths()
        return self.library_paths()

    def set_analysis_scope(self, paths):
        """None=按当前选择/文件夹自动推导；[]=显式清空本轮范围。"""
        self._scope_paths = None if paths is None else [str(p) for p in paths]

    def active_paths(self):
        return self.analysis_scope_paths()

    def reload_library(self):
        if not self.backend.ready:
            return
        snap = self.backend.snapshot()
        self.library.apply_backend_snapshot(snap)
        work = getattr(self, "workbench", None)
        if work is not None and hasattr(work, "rebuild_strip"):
            work.rebuild_strip()

    def start_real_analysis(self):
        paths = self.active_paths()
        if not paths:
            self.controller.log("AI 分析 · 后台素材库没有真实视频，请先导入素材")
            return False
        mode = self.analysis.mode.currentIndex()
        if not self.backend.ready and not self.backend.load_error:
            self.controller.status_text = "加载后台"
            self.controller.stateChanged.emit()
            QApplication.processEvents()
        ok = self.backend.start_analysis(paths, mode)
        if ok:
            self.analysis.real_mode = True
            self.controller.status_text = "分析中"
            self.controller.active_stage = 1
            self.controller.stateChanged.emit()
        else:
            self.controller.log("AI 分析 · 后台未能启动分析任务")
        return ok

    def start_real_mix(self, paths=None):
        if paths is None:
            analysis = getattr(self, "analysis", None)
            if analysis is not None and hasattr(analysis, "mix_pool_paths"):
                paths = analysis.mix_pool_paths()
            if not paths:
                paths = self.active_paths()
        paths = list(paths or [])
        if not paths:
            self.controller.log(
                "开始混剪 · 混剪池为空，请先在 AI 分析页的「混剪池」加入素材")
            return False
        if not self.backend.ready and not self.backend.load_error:
            self.controller.status_text = "加载后台"
            self.controller.stateChanged.emit()
            QApplication.processEvents()
        ok = self.backend.start_mix(paths, self.params)
        if ok:
            tasks = getattr(self, "tasks", None)
            if tasks is not None and hasattr(tasks, "start_task"):
                tasks.start_task(len(paths), self.params.target.value())
            self.controller.progress = 0.0
            self.controller.active_stage = 0
            self.controller.done_stages = 0
            self.controller.paused = False
            self.controller.status_text = "运行中"
            self.controller.stateChanged.emit()
            self.go_page("tasks")
        else:
            self.controller.log("开始混剪 · 后台未启动混剪任务")
        return ok

    def _bind_shortcuts(self):
        pages = ["library", "analysis", "params", "work", "tasks"]
        for i, key in enumerate(pages, start=1):
            sc = QShortcut(QKeySequence("Ctrl+%d" % i), self)
            sc.activated.connect(lambda k=key: self.go_page(k))
        for seq, fn in (("Ctrl+I", self._topbar_import),
                        ("Ctrl+R", self._shortcut_mix)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)

    def _shortcut_mix(self):
        if not self.start_real_mix():
            self.controller.log("快捷键 · 素材库为空，无法开始混剪")

    def closeEvent(self, e):
        try:
            self.backend.shutdown()
        except Exception:
            pass
        super().closeEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        grip = self.findChild(QSizeGrip)
        if grip:
            grip.move(self.centralWidget().width() - 18, self.centralWidget().height() - 18)

    def go_page(self, key: str, animate: bool = True):
        if key not in self.pages:
            return
        page = self.pages[key]
        if key == self.current_page and animate:
            return
        self.current_page = key
        for k, item in self.sidebar.items.items():
            item.set_active(k == key, animate=False)
        if animate:
            eff = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", page)
            anim.setDuration(180)
            anim.setStartValue(0.35)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def done():
                if page.graphicsEffect() is eff:
                    page.setGraphicsEffect(None)
                page._page_anim = None
                page.update()
            anim.finished.connect(done)
            QTimer.singleShot(200, done)
            page._page_anim = anim
            anim.start()
        self.stack.setCurrentWidget(page)

    def _sync_chrome(self):
        c = self.controller
        self.topbar.status.setText(c.status_text)
        color = {"就绪": T.TEXT_3, "已完成": T.OK, "已暂停": T.WARN}.get(c.status_text, T.BLUE)
        self.topbar.status_dot.set_color(color)
        self.topbar.btn_import.setEnabled(not c.imported)
