import sys
import os
import json
import random
import shutil
import subprocess
import tempfile
import hashlib
from pathlib import Path

from PySide6.QtCore import (
    Qt, QUrl, QObject, QEvent, QTimer, QTranslator, QLibraryInfo, QLocale,
)
from PySide6.QtGui import (
    QAction, QShortcut, QKeySequence, QIcon,
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox,
    QComboBox, QAbstractSpinBox,
    QGroupBox, QFormLayout, QProgressBar, QMessageBox,
    QSplitter, QPlainTextEdit, QTreeWidget,
    QTreeWidgetItem, QInputDialog, QAbstractItemView,
    QMenu, QTabWidget,
    QTreeWidgetItemIterator, QScrollArea, QFrame,
    QRadioButton, QButtonGroup
)

from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


# =====================================================
# 基础常量 / 工具函数 / 共用控件
# 已拆分到 mixcut/appkit/，这里导入以保持 main.py 对外名字不变：
# tests 与外部脚本用 `import main` 后访问 VIDEO_EXTS / open_folder /
# UndoableTreeWidget 等名字，语义与原实现完全一致。
# =====================================================
from mixcut.appkit import (  # noqa: F401
    VIDEO_EXTS,
    MIX_RESOLUTION_PRESETS,
    MIX_DEFAULT_RESOLUTION_INDEX,
    MIX_FPS_OPTIONS,
    MIX_DEFAULT_FPS_INDEX,
    open_folder,
    _fmt_ms,
    _human_size,
    app_base_dir as _app_base_dir_impl,
    resolve_app_icon as _resolve_app_icon_impl,
    AspectRatioWidget,
    WheelGuard,
    UndoableTreeWidget,
)
from mixcut.appkit.window_chrome import WindowChromeMixin
from mixcut.appkit.library_ops import LibraryOpsMixin

# 这两个函数原来用 `Path(__file__).resolve().parent` 定位项目根目录，
# 搬进 mixcut/appkit 后 __file__ 的语义会变。这里用薄包装把 main.py 自己的
# __file__ 传进去，调用签名与返回值与原实现完全一致。
_MAIN_FILE = __file__


def app_base_dir():
    """打包后取 exe 所在目录，源码运行取 main.py 所在目录。"""
    return _app_base_dir_impl(_MAIN_FILE)


def resolve_app_icon():
    """定位应用图标，兼容源码运行 / onedir 顶层 / _internal 布局。"""
    return _resolve_app_icon_impl(_MAIN_FILE)


# 素材检测 / 智能混剪 / 配乐 / 转场 / 剪映工程生成
from mixcut import ffmpeg_tools as ft
from mixcut import timeline as tl_mod
from mixcut import workers as mc_workers
from mixcut import filter_engine as fe
from mixcut import analysis as analysis_core
from mixcut import mixbuilder as mixbuilder_module
from mixcut import gpu_engine as gpue
from mixcut.analysis_cache import AnalysisCache
from mixcut.widgets import MaterialSelector


# =====================================================
# 智能混剪页：清晰度（360p → 8K）与帧率（24 → 120）
# 竖屏 9:16 画布，尺寸/帧率决定剪映草稿的导出规格
# =====================================================


# =====================================================
# 工具函数
# =====================================================


# =====================================================
# 素材树：支持撤销和右键
# =====================================================

# =====================================================
# 主窗口
# =====================================================

class MainWindow(WindowChromeMixin, LibraryOpsMixin, QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("MixCut Studio")

        # 旧界面默认 1280x720；新界面稍后按 demo 的 1600x1000 起步
        self._ratio_guard = False
        self.resize(1280, 720)
        self.setMinimumSize(960, 540)

        self.material_confirmed = False
        self._confirmed_paths = []

        # 素材分析内存缓存：运行期间复用，关闭软件进程结束即自动释放
        self.analysis_cache = AnalysisCache()

        # 后台混剪线程与“用户是否点过取消”的记录（finished 之后不再依赖 QThread 状态）
        self.mix_worker = None
        self._mix_canceled = False

        # 新界面默认开启；MIXCUT_LEGACY_UI=1 可退回旧界面排查
        self.use_new_ui = os.environ.get("MIXCUT_LEGACY_UI", "") not in ("1", "true")

        self._build_ui()

        # 新界面按 demo 工程的窗口尺寸起步（1600x1000，最小 1200x780），
        # 屏幕不够大时按可用区域收缩，避免布局被压扁或窗口超出屏幕。
        if self.use_new_ui:
            self._apply_demo_window_size()
            self._build_resize_zones()

        self._init_player()

        # 全局快捷键：空格=播放/暂停，Esc=退出全屏，P=切换全屏
        self.space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.space_shortcut.setContext(Qt.ApplicationShortcut)
        self.space_shortcut.activated.connect(self._toggle_play)

        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.esc_shortcut.setContext(Qt.ApplicationShortcut)
        self.esc_shortcut.activated.connect(self._escape_action)

        self.p_shortcut = QShortcut(QKeySequence(Qt.Key_P), self)
        self.p_shortcut.setContext(Qt.ApplicationShortcut)
        self.p_shortcut.activated.connect(self._toggle_fullscreen)

        # 全屏预览是独立窗口，快捷键必须挂在 QApplication 上才收得到
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _build_new_shell(self):
        """demo 布局 + 旧后台原生对象。分析/任务先保留旧页面。"""
        from mixcut.ui import library_page, mix_page, settings_page, engine_page
        from mixcut.ui.analyze_page import AnalyzePage
        from mixcut.ui.demo.backend_bridge import BackendBridge
        from mixcut.ui.demo.window import DemoController
        from mixcut.ui.demo import (pages_library, pages_params, pages_settings,
                                    pages_work, pages_tasks_real)
        from mixcut.ui.demo_shell import DemoShell

        # demo 页面通过这个桥直接驱动当前旧 MainWindow，不二次加载 main.py。
        self.backend = BackendBridge(self, legacy=self)
        self.controller = DemoController(self)
        self.controller.stateChanged.connect(self._refresh_topbar)

        # 旧页面仍然构建，旧后台方法刷新它们；可见页面由 DemoShell 承载。
        self.page_library = library_page.LibraryPage(self)
        self.page_mix = mix_page.MixPage(self)
        self.page_settings = settings_page.SettingsPage(self)
        self.page_workbench = self._build_workbench_page()
        # 旧素材库页虽然不显示，仍需要挂到 MainWindow，才能使用后台探测/缩略图线程。
        self.page_library.setParent(self)
        self.page_engine = engine_page.EnginePage(self)
        self.page_analyze = AnalyzePage(self)

        # 玩家卡片必须先建：它内部会包住 video_widget。
        # 随后 demo 工作台再把 video_widget 接到自己的预览卡片上，
        # 否则视频控件会被留在隐藏的旧素材库页里，工作台只能看到黑屏。
        self._demo_player_card = self._build_player_card()

        self.demo_library = pages_library.LibraryPage(self)
        self.demo_params = pages_params.ParamsPage(self)
        self.demo_workbench = pages_work.WorkbenchPage(self)
        self.demo_settings = pages_settings.SettingsPage(self)
        # 任务中心：demo 的队列/输出/日志布局 + 真实后台进度
        self.demo_tasks = pages_tasks_real.TasksPage(self)

        # demo 可见页；旧页只做后台刷新与兼容，不挂到主界面。
        self.shell = DemoShell(self, {
            "library": self.demo_library,
            "params": self.demo_params,
            "analysis": self.page_analyze,
            "work": self.demo_workbench,
            "tasks": self.demo_tasks,
            "settings": self.demo_settings,
        })
        self.setCentralWidget(self.shell)

        # 分析完成 → 参数页「开始智能混剪」解除灰色，任务中心同时刷新门控
        self.page_analyze.analysisFinished.connect(self._on_analysis_finished)
        self.demo_params.refresh_run_state()
        self.demo_tasks.refresh()

        # 兼容旧代码里的 page_* / logs_page 引用（旧任务页已移除，任务中心接手）
        self.logs_page = self.demo_tasks
        self.library = self.demo_library
        self.params = self.demo_params
        self.analysis = self.page_analyze
        self.tasks = self.demo_tasks
        self.settings_page = self.demo_settings
        self.page_library.hide()
        self.page_mix.hide()
        self.page_settings.hide()
        self.page_engine.hide()
        self.page_workbench.hide()
        # 旧页仍然保留做后台兼容，但不允许它们作为窗口子控件在左上角漏画碎片
        for _child in self.children():
            if isinstance(_child, QWidget) and _child is not self.shell \
                    and _child.objectName() != "ResizeZone":
                _child.hide()

        # 旧顶部/设置方法仍然使用旧页面；日志统一走任务中心。
        self._log_sink = self._new_log
        self._new_ui_ready = True
        self._ensure_probe_worker()

        if getattr(self.page_library, "preview_slot", None) is not None:
            # 旧素材库页虽然不显示，仍需要这块卡片承载进度条等旧控件；
            # video_widget 已经归 demo 工作台，这里只放进隐藏页，不影响可见界面。
            self.page_library.preview_slot.addWidget(self._demo_player_card)

        self._refresh_topbar()
        self.shell.show_page("library")
        self._refresh_materials_now()
        # 代理配置是持久化的：上次开着就继续把现有素材排队生成
        self._ensure_proxy_listener()
        self._kick_proxy_queue()
        # 快捷键统一在 QApplication 级绑定：Ctrl+I/R/S/Z/Y、Ctrl+1..5、空格、Esc、P
        self._bind_app_shortcuts()

    def _build_workbench_page(self):
        """工作台 = 素材库 + 预览窗口。

        素材库页面（文件夹树 / 素材卡片 / 筛选 / 操作条）直接作为工作台主体，
        它右侧原来的“素材详情”文字区在 LibraryPage 里换成预览槽位，
        由 _build_player_card() 填进播放器。
        """
        from PySide6.QtWidgets import QSplitter
        from mixcut.ui.components import Card

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("工作台")
        title.setObjectName("Hero")
        head.addWidget(title)
        head.addStretch(1)
        quick = QPushButton("开始混剪")
        quick.setObjectName("Primary")
        quick.clicked.connect(self._navigate_to_mix)
        head.addWidget(quick)
        lay.addLayout(head)

        # 素材库页面本身就是主体（内部已含 树 + 卡片 + 筛选 + 预览槽位）
        lay.addWidget(self.page_library, 1)
        return page

    # ---- demo 页面需要的宿主接口（直接复用旧后台） ----
    def go_page(self, key, animate=True):
        aliases = {"analysis": "analysis", "analyze": "analysis",
                   "workbench": "work", "mix": "params"}
        key = aliases.get(key, key)
        # 离开「工作台」时停掉素材预览：预览器藏在其它页面后面时不许出声。
        if key != "work":
            self.stop_preview_playback()
        if key == "settings":
            self.shell.show_page("settings")
            return
        self.shell.show_page(key)

    def _topbar_import(self):
        self.demo_library._add_files()

    def selected_paths(self):
        from mixcut.ui import demo
        return [c.get("path") for c in demo.mock.CLIPS
                if c.get("id") in getattr(self.controller, "selected", set())
                and c.get("path")]

    # =================================================
    # 快捷键（全部 ApplicationShortcut：焦点在任意子控件都能收到）
    # =================================================
    def _bind_app_shortcuts(self):
        if getattr(self, "_app_shortcuts", None):
            return
        # 旧隐藏树自己也挂了 Ctrl+Z，同一窗口内两个同键 QShortcut 会互相抢成
        # activatedAmbiguously，两边都不触发；这里禁掉旧树的，由新 UI 统一接管。
        legacy_tree = getattr(getattr(self, "page_library", None), "tree", None)
        sc = getattr(legacy_tree, "undo_shortcut", None)
        if sc is not None:
            sc.setEnabled(False)
        from PySide6.QtGui import QShortcut, QKeySequence
        self._app_shortcuts = []

        def bind(seq, fn):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(fn)
            self._app_shortcuts.append(sc)

        for i, key in enumerate(("library", "work", "analysis", "params", "tasks"), 1):
            bind("Ctrl+%d" % i, lambda k=key: self.go_page(k))
        bind("Ctrl+I", self._topbar_import)
        bind("Ctrl+R", self._shortcut_run_mix)
        bind("Ctrl+S", self._shortcut_save)
        bind("Ctrl+O", self.open_project)
        bind("Ctrl+N", self.new_project)
        bind("Ctrl+Z", self.undo_step)
        bind("Ctrl+Y", self.redo_step)
        bind("Ctrl+Shift+Z", self.redo_step)

        # 参数页改动也进历史：控件信号太碎，改成轮询检测，一次改动算一步
        try:
            self._params_seen = self._project_params()
        except Exception:
            self._params_seen = {}
        if getattr(self, "_params_watch", None) is None:
            self._params_watch = QTimer(self)
            self._params_watch.setInterval(600)
            self._params_watch.timeout.connect(self._watch_params)
            self._params_watch.start()

    def _watch_params(self):
        if getattr(self, "_history_busy", False):
            return
        try:
            cur = self._project_params()
        except Exception:
            return
        if cur == getattr(self, "_params_seen", None):
            return
        st = self._history_state()
        st["params"] = dict(getattr(self, "_params_seen", {}) or {})
        self._push_history(st)
        self._params_seen = cur

    def _shortcut_run_mix(self):
        if not self.start_real_mix():
            self.controller.log("快捷键 Ctrl+R · 当前还不能开始混剪")

    def _shortcut_save(self):
        """Ctrl+S：已保存过就覆盖原工程，没有工程文件才弹保存对话框。"""
        path = getattr(self, "_project_path", "")
        if path:
            ok = False
            try:
                ok = bool(self.backend.save_project_to(path))
            except Exception:
                ok = False
            if ok:
                self.controller.log("工程已更新：%s" % path)
            return
        self.save_project()

    # =================================================
    # 撤回 / 还原（快照式：素材结构 + 勾选 + 确认 + 参数）
    # =================================================
    def _tree_state(self):
        """旧素材树的完整快照（文本 + 路径 + 父子关系），撤回时原样还原。"""
        try:
            old = self.backend.ensure()
        except Exception:
            old = None
        tree = getattr(old, "tree", None)
        if tree is None or not hasattr(tree, "serialize_tree"):
            return None
        return tree.serialize_tree(tree.invisibleRootItem())

    def _history_state(self):
        from mixcut.ui import demo
        clips = [{"path": c.get("path") or "",
                  "folder": c.get("folder") or "全部素材",
                  "folder_path": c.get("folder_path") or ""}
                 for c in demo.mock.CLIPS if c.get("path") and not c.get("_removed")]
        folders = [{"name": f.get("name") or "",
                    "folder_path": f.get("folder_path") or "",
                    "parent": f.get("parent") or "all",
                    "depth": f.get("depth") or 1}
                   for f in getattr(self.demo_library, "folders", [])
                   if f.get("id") not in ("all",) and f.get("name")]
        selected = {c["path"] for c in demo.mock.CLIPS
                    if c.get("id") in getattr(self.controller, "selected", set())
                    and c.get("path")}
        page = getattr(self, "page_analyze", None)
        removed_items = [dict(c) for c in demo.mock.CLIPS if c.get("_removed")
                         and c.get("path")]
        return {
            "clips": clips,
            "folders": folders,
            "selected": sorted(selected),
            "confirmed": list(getattr(self, "_confirmed_paths", []) or []),
            "params": self._project_params(),
            "params_v3": (self.demo_params.capture()
                          if hasattr(self.demo_params, "capture") else {}),
            "tree": self._tree_state(),
            "removed_items": removed_items,
            "analysis": {
                "mode": getattr(page, "_mode_key", "medium"),
                "finished": bool(getattr(page, "_finished", False)),
                "items": [dict(r) for r in getattr(page, "_results", [])],
                "pool": (page.pool_state()
                         if hasattr(page, "pool_state") else {}),
            },
            "filters": self._demo_filters_state(),
            "page": getattr(getattr(self, "shell", None), "current", "library"),
            "current_path": next((c.get("path") for c in demo.mock.CLIPS
                                  if c.get("id") == getattr(self.controller, "_current", None)
                                  and c.get("path")), ""),
        }

    def _history_sig(self, st=None):
        st = st or self._history_state()
        no_root = ("全部素材", "")
        return (tuple((c["path"], c["folder"])
                      for c in st["clips"] if c["folder"] not in no_root),
                tuple(st["folders"]))

    def _push_history(self, st=None):
        if getattr(self, "_history_busy", False):
            return
        try:
            st = st or self._history_state()
        except Exception:
            return
        stack = getattr(self, "_undo_stack", None)
        if stack is None:
            self._undo_stack = []
            self._redo_stack = []
            stack = self._undo_stack
        key = repr(st)
        if stack and stack[-1].get("_key") == key:
            return
        st["_key"] = key
        stack.append(st)
        if len(stack) > 80:
            stack.pop(0)
        self._redo_stack.clear()

    def undo_step(self):
        stack = getattr(self, "_undo_stack", None) or []
        if not stack:
            self.controller.log("撤回 · 没有可撤回的操作了")
            return
        cur = self._history_state()
        target = stack.pop()
        cur["_key"] = repr(cur)
        self._redo_stack.append(cur)
        self._apply_history_state(target, "撤回")

    def redo_step(self):
        stack = getattr(self, "_redo_stack", None) or []
        if not stack:
            self.controller.log("还原 · 没有可还原的操作了")
            return
        cur = self._history_state()
        target = stack.pop()
        cur["_key"] = repr(cur)
        self._undo_stack.append(cur)
        self._apply_history_state(target, "还原")

    def _apply_history_state(self, st, label):
        from mixcut.ui import demo

        self._history_busy = True
        page = getattr(self, "page_analyze", None)
        try:
            # 已移除素材随历史一起回滚，reload 后才能重建“已移除”卡片
            self.controller.removed_clips = [dict(c) for c in
                                             (st.get("removed_items") or [])]
            tree_now = self._tree_state()
            if st.get("tree") is not None and repr(tree_now) != repr(st.get("tree")):
                old = self.backend.ensure()
                tree = getattr(old, "tree", None)
                if tree is not None:
                    tree.clear()
                    tree.deserialize_tree(st["tree"], tree.invisibleRootItem())
                    old.invalidate_materials()
                    old.update_count()
            self.reload_library(preserve_selection=False)
            sel_paths = set(st["selected"])
            self.controller.selected = {
                c["id"] for c in demo.mock.CLIPS if c.get("path") in sel_paths}
            self._confirmed_paths = list(st["confirmed"])
            self.material_confirmed = bool(self._confirmed_paths)
            self._apply_project_params(st.get("params") or {})
            cur_path = st.get("current_path") or ""
            if cur_path:
                cid = next((c["id"] for c in demo.mock.CLIPS
                            if c.get("path") == cur_path), "")
                if cid:
                    self.controller._current = cid
            # 撤回素材树时同步恢复分析结果，而不是只把素材放回来
            ana = st.get("analysis") or {}
            items = [r for r in (ana.get("items") or [])
                     if isinstance(r, dict) and r.get("path")
                     and os.path.isfile(r["path"])]
            if page is not None:
                if items:
                    page.load_results(items, finished=bool(ana.get("finished", True)),
                                      mode_key=ana.get("mode"))
                else:
                    page.clear_results()
                if ana.get("pool"):
                    page.load_pool_state(ana.get("pool"))
            if hasattr(self, "demo_params") and st.get("params_v3"):
                self.demo_params.restore(st.get("params_v3") or {})
            if st.get("filters"):
                self._restore_filters(st["filters"])
        finally:
            self._history_busy = False
        try:
            self._params_seen = self._project_params()
        except Exception:
            pass
        self._sync_material_state()
        self._refresh_topbar()
        if hasattr(self, "demo_params"):
            self.demo_params.refresh_run_state()
        if st.get("page") in getattr(getattr(self, "shell", None), "pages", {}):
            self.go_page(st["page"])
        self.controller.log("%s · 已回到上一步" % label)


    def active_paths(self):
        """当前要分析的素材：勾选优先；没勾选才退回当前文件夹（排除已移除）。"""
        from mixcut.ui import demo

        selected = self.selected_paths()
        if selected:
            return list(dict.fromkeys(selected))
        active = getattr(self.demo_library, "active_folder", "all")
        folder = next((f for f in self.demo_library.folders
                       if f["id"] == active), None)
        base = (folder or {}).get("folder_path") or ""
        if active in ("all", "root") or not base:
            paths = [c.get("path") for c in demo.mock.CLIPS
                     if c.get("path") and not c.get("_removed")
                     and c["id"] not in getattr(self.controller, "removed", set())]
        else:
            paths = [c.get("path") for c in demo.mock.CLIPS
                     if c.get("path") and not c.get("_removed")
                     and c["id"] not in getattr(self.controller, "removed", set())
                     and ((c.get("folder_path") or "") == base
                          or (c.get("folder_path") or "").startswith(base + "/"))]
        return list(dict.fromkeys(paths))

    def reload_library(self, preserve_selection=True):
        snap = self.backend.snapshot()
        self.demo_library.apply_backend_snapshot(
            snap, preserve_selection=preserve_selection)
        if hasattr(self.demo_workbench, "rebuild_strip"):
            self.demo_workbench.rebuild_strip()
        # 后台探测直接吃新 UI 素材清单的全部路径，不再依赖旧页卡片是否展开了嵌套目录。
        self._ensure_probe_worker().request(self._probe_paths())
        page = getattr(self, "page_library", None)
        if page is not None and hasattr(page, "refresh_cards"):
            page.refresh_cards()
        if hasattr(self, "demo_workbench") and getattr(self, "player", None) is not None:
            cur = self.controller.current_clip()
            if cur.get("path"):
                self.show_clip_preview(cur["path"])
        # 新导入/新出现的素材，代理开启时自动加入队列
        self._kick_proxy_queue()
        # 素材被删后，勾选与分析结果同步清掉，避免分析/混剪还引用已删除的原片
        self._sync_material_state()

    def _sync_material_state(self):
        """按现有素材裁剪勾选与分析结果；素材全删时选空、分析清空、预览复位。"""
        from mixcut.ui.demo import mock

        existing = set(self._probe_paths())
        removed = set(getattr(self, "_known_paths", set())) - existing
        self._known_paths = existing
        try:
            self.controller.selected &= {
                c["id"] for c in mock.CLIPS if c.get("path") in existing}
        except Exception:
            pass
        self._confirmed_paths = [p for p in
                                 (getattr(self, "_confirmed_paths", []) or [])
                                 if p in existing]
        page = getattr(self, "page_analyze", None)
        if page is not None and hasattr(page, "drop_paths") and removed:
            page.drop_paths(removed)
        if not existing:
            self.controller.selected.clear()
            self._confirmed_paths = []
            self.material_confirmed = False
            if page is not None and hasattr(page, "clear_results"):
                page.clear_results()
            self._preview_path = ""
            for name in ("player",):
                obj = getattr(self, name, None)
                if obj is not None:
                    try:
                        obj.stop()
                    except Exception:
                        pass
            self.controller.log("素材库已清空 · 已选素材与分析结果一并清空")
        self._refresh_topbar()
        self.demo_params.refresh_run_state() if hasattr(self, "demo_params") else None

    def _probe_paths(self):
        """需要探测的全部素材路径：新 UI 素材清单 + 旧树，排除“已移除”。"""
        from mixcut.ui.demo import mock
        removed_ids = getattr(getattr(self, "controller", None), "removed", set()) or set()
        paths = [c.get("path") for c in mock.CLIPS
                 if c.get("path") and c["id"] not in removed_ids
                 and not c.get("_removed")]
        try:
            paths.extend(self.get_all_video_paths())
        except Exception:
            pass
        return list(dict.fromkeys(p for p in paths if p))

    def _demo_clip(self, path):
        from mixcut.ui.demo import mock
        return next((c for c in mock.CLIPS if c.get("path") == path), None)

    def _update_demo_thumbnail(self, path, image_path):
        clip = self._demo_clip(path)
        if not clip:
            return
        clip["thumb_path"] = image_path or ""
        card = getattr(self.demo_library, "cards", {}).get(clip["id"])
        if card is not None:
            card.update()
        for tile in getattr(self.demo_workbench, "tiles", {}).values():
            if tile.clip["id"] == clip["id"]:
                tile.update()
        self.demo_workbench.refresh()

    def _update_demo_metadata(self, path):
        clip = self._demo_clip(path)
        info = (getattr(self, "_probe_cache", {}) or {}).get(path) or {}
        if not clip or not info:
            return
        fmt = info.get("format") or {}
        video = info.get("video") or {}
        if fmt.get("duration"):
            sec = float(fmt["duration"])
            clip["dur"] = "%02d:%02d" % (int(sec // 60), int(sec % 60))
        clip["width"] = video.get("width") or 0
        clip["height"] = video.get("height") or 0
        clip["codec"] = video.get("codec") or ""
        clip["fps"] = video.get("fps") or 0
        clip["pix_fmt"] = video.get("pix_fmt") or ""
        clip["bit_depth"] = (10 if "10" in str(clip["pix_fmt"]) else
                              12 if "12" in str(clip["pix_fmt"]) else
                              8 if clip["pix_fmt"] else "")
        clip["file_size"] = info.get("file_size") or 0
        clip["quality"] = round((clip["width"] * clip["height"] / 2073600.0) * 80
                                + float(fmt.get("bit_rate") or video.get("bit_rate") or 0)
                                / 1000000.0, 1)
        card = getattr(self.demo_library, "cards", {}).get(clip["id"])
        if card is not None:
            card.update()
        self.demo_workbench.refresh()

    def start_real_analysis(self):
        paths = self.active_paths()
        if not paths:
            self.controller.log("开始分析 · 素材库为空")
            return False
        self._confirmed_paths = list(paths)
        self.material_confirmed = True
        self._refresh_selectors() if hasattr(self, "_refresh_selectors") else None
        self.page_analyze._start_analysis()
        return True

    def start_real_mix(self):
        paths = self.active_paths()
        if not paths:
            self.controller.log("开始混剪 · 素材库为空")
            return False
        self._confirmed_paths = list(paths)
        self.material_confirmed = True
        # 走桥的启动入口：先把新 UI 参数同步进旧后台控件，再启动旧混剪线程。
        # 直接调旧 start_mix() 会绕过 sync_params，界面上改的参数不会生效。
        params = getattr(self, "demo_params", None)
        return bool(self.backend.start_mix(paths, params))


    def _build_player_card(self):
        """播放器卡片：沿用原有 QMediaPlayer 与 QVideoWidget，只换外观。"""
        from PySide6.QtWidgets import QSlider

        card = QWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.video_widget.setMinimumHeight(120)
        self.video_widget.setStyleSheet(
            "background:#0B0D12;border-radius:6px;")
        # 用比例容器包住视频，选不同比例时预览区等比居中
        self._preview_holder = AspectRatioWidget(self.video_widget, 9/16.0)
        lay.addWidget(self._preview_holder, 1)

        # 进度条（真实播放位置）
        from PySide6.QtWidgets import QSlider
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setToolTip("拖动调整播放进度")
        lay.addWidget(self.seek_slider)
        self.time_label = QLabel("--:-- / --:--")
        self.time_label.setObjectName("Faint")
        lay.addWidget(self.time_label)

        controls = QHBoxLayout()
        controls.addWidget(self.play_btn)
        controls.addWidget(self.pause_btn)
        controls.addWidget(self.stop_btn)
        controls.addStretch(1)
        # 常用预览比例下拉
        self.aspect_combo = QComboBox()
        self.aspect_combo.setObjectName("Ghost")
        self._aspect_ratios = [
            ("9:16", 9/16.0),
            ("16:9", 16/9.0),
            ("1:1", 1.0),
            ("4:3", 4/3.0),
            ("3:4", 3/4.0),
            ("21:9", 21/9.0),
        ]
        for label, _r in self._aspect_ratios:
            self.aspect_combo.addItem(label)
        self.aspect_combo.setCurrentIndex(0)
        self.aspect_combo.setToolTip("选择预览画幅比例")
        self.aspect_combo.currentIndexChanged.connect(self._on_aspect_changed)
        controls.addWidget(self.aspect_combo)
        self.fullscreen_btn = QPushButton("全屏")
        self.fullscreen_btn.setObjectName("Ghost")
        self.fullscreen_btn.clicked.connect(self._toggle_video_fullscreen)
        controls.addWidget(self.fullscreen_btn)
        lay.addLayout(controls)

        # 真实播放状态 → 进度条（位置变化时才更新，不造假进度）
        player = getattr(self, "player", None)
        if player is not None:
            player.positionChanged.connect(self._on_position)
            player.durationChanged.connect(self._on_duration)
            self.seek_slider.sliderReleased.connect(self._on_seek)

        self._preview_aspect = "9:16"
        self._apply_preview_aspect()
        return card

    def _on_aspect_changed(self, idx):
        label, ratio = self._aspect_ratios[idx]
        self._preview_aspect = label
        self._preview_holder.set_ratio(ratio)
        from PySide6.QtCore import Qt
        self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)

    def _apply_preview_aspect(self):
        """视频保持比例在预览框内显示。"""
        from PySide6.QtCore import Qt
        vw = getattr(self, "video_widget", None)
        if vw is not None:
            vw.setAspectRatioMode(Qt.KeepAspectRatio)

    def _on_position(self, ms):
        slider = getattr(self, "seek_slider", None)
        if slider is not None and not slider.isSliderDown():
            slider.setValue(int(ms))
        self._update_time_label(ms)

    def _on_duration(self, ms):
        slider = getattr(self, "seek_slider", None)
        if slider is not None:
            slider.setRange(0, max(0, int(ms)))
        self._update_time_label(self.player.position() if self.player else 0)

    def _on_seek(self):
        if self.player is not None:
            self.player.setPosition(self.seek_slider.value())

    def _update_time_label(self, ms):
        label = getattr(self, "time_label", None)
        if label is None:
            return
        dur = self.player.duration() if self.player else 0
        label.setText(f"{_fmt_ms(ms)} / {_fmt_ms(dur) if dur else '--:--'}")

    def _preview_stage(self):
        """素材预览页的视频舞台容器（新 UI 才有）。"""
        wb = getattr(self, "demo_workbench", None)
        stage = getattr(wb, "stage", None)
        if stage is not None and stage.layout() is not None:
            return stage
        return None

    def _video_home(self):
        """video_widget 的归属容器：(layout, holder, insert_index)。

        新 UI 下视频放在工作台的 PreviewStage 里，旧 UI 下是比例容器。
        """
        stage = self._preview_stage()
        if stage is not None:
            return stage.lay, stage, 0
        holder = getattr(self, "_preview_holder", None)
        if holder is not None and holder.layout() is not None:
            return holder.layout(), holder, holder.layout().count()
        return None, None, 0

    def _fullscreen_preview(self):
        """当前处于全屏的预览容器（素材预览舞台 / 分析页预览区）。"""
        stage = self._preview_stage()
        if stage is not None and stage.isFullScreen():
            return stage
        host = getattr(getattr(self, "page_analyze", None), "preview_host", None)
        if host is not None and host.isFullScreen():
            return host
        return None

    def _set_fs_button_state(self, full):
        stage = self._preview_stage()
        if stage is not None:
            stage.set_fullscreen_state(full)
        pa = getattr(self, "page_analyze", None)
        if pa is not None and hasattr(pa, "set_fullscreen_state"):
            pa.set_fullscreen_state(full)

    def _analysis_preview(self):
        """分析页的预览容器（全屏的就是这个容器，视频不搬家）。"""
        pa = getattr(self, "page_analyze", None)
        host = getattr(pa, "preview_host", None)
        if host is not None and host.layout() is not None:
            return host
        return None

    def _current_preview_target(self):
        """当前页面该全屏哪个预览容器。"""
        cur = getattr(getattr(self, "shell", None), "current", "")
        if cur == "analysis":
            return self._analysis_preview()
        if cur == "work":
            return self._preview_stage()
        return None

    def _layout_of(self, widget):
        """安全取控件的布局：Card 之类把布局放在 .lay，个别控件 .layout 是属性。"""
        if widget is None:
            return None
        lay = getattr(widget, "lay", None)
        if lay is not None and hasattr(lay, "indexOf"):
            return lay
        lay = getattr(widget, "layout", None)
        if hasattr(lay, "indexOf"):
            return lay
        try:
            return widget.layout()
        except TypeError:
            return None

    def _enter_preview_fullscreen(self, target):
        """把预览容器整体全屏。

        注意：target 原本是布局里的子控件，Qt 对子控件 showFullScreen() 不会
        真正铺满屏幕（isFullScreen 为 True 但画面不动）。所以这里先把它升成
        独立无边框窗口，再全屏；视频控件是它的子控件，跟着容器走，不搬家。
        """
        if target is None:
            return False
        parent = target.parentWidget()
        lay = self._layout_of(parent)
        idx = lay.indexOf(target) if lay is not None else -1
        self._fs_home = (parent, lay, idx)
        target.setParent(None)
        target.setWindowFlags(Qt.WindowType.Window
                              | Qt.WindowType.FramelessWindowHint)
        target.showFullScreen()
        target.raise_()
        target.setFocus()
        self._set_fs_button_state(True)
        return True

    def _exit_preview_fullscreen(self):
        target = self._fullscreen_preview()
        if target is None:
            return False
        target.showNormal()
        parent, lay, idx = getattr(self, "_fs_home", (None, None, -1))
        # 收回成普通子控件，再按原位插回布局
        target.setWindowFlags(Qt.WindowType.Widget)
        if parent is not None:
            target.setParent(parent)
            if lay is not None and lay.indexOf(target) < 0:
                lay.insertWidget(max(0, idx), target, 1)
        elif target is self._preview_stage():
            card = getattr(getattr(self, "demo_workbench", None), "preview_card", None)
            if card is not None and card.lay.indexOf(target) < 0:
                card.lay.insertWidget(0, target, 1)
        target.show()
        self._set_fs_button_state(False)
        self._fs_home = (None, None, -1)
        return True

    def _toggle_preview_fullscreen_for(self, target):
        """统一入口：按钮 / 快捷键都走这里，进得去也出得来。"""
        if target is None:
            return False
        if target.isFullScreen():
            return self._exit_preview_fullscreen()
        return self._enter_preview_fullscreen(target)

    def _toggle_video_fullscreen(self):
        full = self._fullscreen_preview()
        if full is not None:
            return self._exit_preview_fullscreen()

        target = self._current_preview_target()
        if target is not None:
            return self._enter_preview_fullscreen(target)

        if self._preview_stage() is not None:
            # 新 UI 但当前页面没有预览可全屏（比如在素材库/任务中心）
            return False

        # 旧界面兜底：video_widget 自己全屏，退出时还回原容器
        if self.video_widget.isFullScreen():
            self.video_widget.showNormal()
            lay, holder, idx = self._video_home()
            if holder is not None:
                self.video_widget.setParent(holder)
                lay.insertWidget(idx, self.video_widget, 1)
            self.video_widget.show()
        else:
            lay, _holder, _idx = self._video_home()
            if lay is not None:
                lay.removeWidget(self.video_widget)
            self.video_widget.setParent(None)
            self.video_widget.showFullScreen()
        return True


    def _back_to_library(self):
        self._navigate("workbench")

    # ---- 页面导航 / 日志 / 计数 ----
    def _navigate(self, key):
        if getattr(self, "shell", None) is not None:
            self.shell.nav.select(key)

    def _navigate_to_mix(self):
        if not self.material_confirmed:
            QMessageBox.information(
                self, "提示",
                "请先在素材库里点“确定使用”锁定素材，再开始混剪。")
            self._navigate("workbench")
            return
        self._navigate("mix")

    def go_analyze(self):
        """超级混剪页“开始分析”按钮：先弹窗选模式，再跳到分析页并自动开始分析。"""
        from PySide6.QtWidgets import QDialog
        from mixcut.ui.analyze_page import ModeSelectDialog
        shell = getattr(self, "shell", None)
        if shell is None:
            return
        page = shell._pages.get("analyze")
        current = page._mode_key if page is not None else "medium"

        dlg = ModeSelectDialog(current, self)
        if dlg.exec() != QDialog.Accepted:
            return  # 用户取消：不跳转、不分析

        mode_key = dlg.selected_mode()
        shell.nav.select("analyze")
        if page is not None:
            page.set_mode(mode_key)   # 确保下拉框/内部键与弹窗选择一致
            page._start_analysis()

    def _on_analysis_finished(self, _count=0):
        """分析跑完：同步参数页与任务中心的「开始混剪」门控。"""
        self.demo_params.refresh_run_state()
        self.demo_tasks.refresh()

    def _new_log(self, message, level="info"):
        page = getattr(self, "demo_tasks", None)
        if page is not None:
            page.log(message, level)
        # 可见的「任务中心」也要收到同一条日志
        shown = getattr(self, "demo_tasks", None)
        if shown is not None:
            shown.append_log(message)

    def _refresh_topbar(self):
        shell = getattr(self, "shell", None)
        if shell is None:
            return
        paths = self.get_all_video_paths()
        # 顶栏「已选 N」= 真正会进入混剪的素材数：
        # 已锁定 → 锁定名单；未锁定 → 素材库当前的勾选集（实时跟随勾选框）
        if getattr(self, "material_confirmed", False):
            selected = len(getattr(self, "_confirmed_paths", []) or [])
        elif hasattr(self, "controller") and hasattr(self, "demo_library"):
            selected = len(getattr(self.controller, "selected", set()))
        else:
            page = getattr(self, "page_library", None)
            checked = getattr(page, "_checked_paths", None) if page is not None else None
            if checked is not None:
                selected = len(set(checked))
            else:
                selector = getattr(self, "mix_selector", None)
                selected = selector.selected_count() if selector is not None else 0
        shell.topbar.set_counts(len(paths), selected)
        project = getattr(self, "_project_path", "") or ""
        name = Path(project).stem if project else "未命名项目"
        shell.topbar.set_project(name)


    def _refresh_library_cards(self):
        """素材变化后刷新卡片（导入 / 删除 / 撤销 / 新建分类都会走这里）。"""
        page = getattr(self, "page_library", None)
        if page is None:
            return
        try:
            page.refresh_cards()
        except Exception:
            # 单个卡片出错不应拖垮整个界面
            import traceback
            traceback.print_exc()

    def _refresh_materials_now(self):
        """顶部工具栏“刷新素材”：按磁盘现状重新统计素材（真实功能）。"""
        before = len(self.get_all_video_paths())
        self.update_count()
        page = getattr(self, "page_library", None)
        if page is not None:
            page.refresh_cards()
        after = len(self.get_all_video_paths())
        removed = before - after
        message = f"已刷新素材：当前 {after} 个"
        if removed > 0:
            message += f"（{removed} 个文件已不存在，已从列表移除）"
        self.log(message)
        if getattr(self, "_log_sink", None):
            self._log_sink(message, "info")

    def _project_params(self):
        """收集当前界面上的混剪参数，供保存工程使用。

        保存与恢复必须一一对应：这里写进去的每个键，open_project 都要能还原。
        新增参数时两边一起改，不再出现“存了不读”的字段。
        """
        def val(widget, getter, default=None):
            if widget is None:
                return default
            try:
                return getter(widget)
            except Exception:
                return default

        return {
            # 时长与片段
            "target_duration": val(getattr(self, "mix_target", None), lambda w: w.value(), 30.0),
            "tolerance": val(getattr(self, "mix_tolerance", None), lambda w: w.value(), 5.0),
            "clip_min": val(getattr(self, "mix_clip_min", None), lambda w: w.value(), 2.0),
            "clip_max": val(getattr(self, "mix_clip_max", None), lambda w: w.value(), 5.0),
            "project_count": val(getattr(self, "mix_clip_count", None), lambda w: w.value(), 1),
            # 画布
            "resolution_index": val(getattr(self, "mix_resolution", None), lambda w: w.currentIndex(), 0),
            "fps_index": val(getattr(self, "mix_fps", None), lambda w: w.currentIndex(), 0),
            # 排列
            "shuffle": val(getattr(self, "mix_shuffle", None), lambda w: w.isChecked(), True),
            "reuse": val(getattr(self, "mix_reuse", None), lambda w: w.isChecked(), True),
            # 卡点
            "beat_sync": val(getattr(self, "mix_beat_sync", None), lambda w: w.isChecked(), True),
            "beat_step": val(getattr(self, "mix_beat_step", None), lambda w: w.currentData(), 0),
            "beat_tolerance": val(getattr(self, "mix_beat_tolerance", None), lambda w: w.value(), 0.12),
            "weak_beat_chance": val(getattr(self, "mix_weak_chance", None), lambda w: w.value(), 0.25),
            # 策略
            "diversity": val(getattr(self, "mix_diversity", None), lambda w: w.currentData(), "medium"),
            "rhythm": val(getattr(self, "mix_rhythm", None), lambda w: w.currentData(), "balance"),
            "version_style": val(getattr(self, "mix_version", None), lambda w: w.currentData(), "balanced"),
            # 增强开关
            "sentence_snap": val(getattr(self, "mix_sentence_snap", None), lambda w: w.isChecked(), False),
            "audio_dedup": val(getattr(self, "mix_audio_dedup", None), lambda w: w.isChecked(), False),
            "openclip": val(getattr(self, "mix_openclip", None), lambda w: w.isChecked(), True),
            # 输出位置
            "draft_dir": val(getattr(self, "mix_draft_dir", None), lambda w: w.text(), ""),
            # 配乐
            "music_mode": val(getattr(self, "music_mode", None), lambda w: w.currentIndex(), 0),
            "music_path": val(getattr(self, "mix_bgm", None), lambda w: w.text(), ""),
            "ducking": val(getattr(self, "mix_ducking", None), lambda w: w.isChecked(), False),
            "base_volume": val(getattr(self, "mix_base_vol", None), lambda w: w.value(), 1.0),
            "duck_volume": val(getattr(self, "mix_duck_vol", None), lambda w: w.value(), 0.35),
            # 转场
            "trans_enable": val(getattr(self, "mix_trans_enable", None), lambda w: w.isChecked(), False),
            "dissolve_duration": val(getattr(self, "mix_dissolve_dur", None), lambda w: w.value(), 0.4),
            "short_threshold": val(getattr(self, "mix_short", None), lambda w: w.value(), 1.5),
            "long_threshold": val(getattr(self, "mix_long", None), lambda w: w.value(), 3.0),
            "blacklist": val(getattr(self, "mix_blacklist", None), lambda w: w.text(), ""),
        }

    def _apply_project_params(self, p):
        """把工程里的参数写回界面；缺字段一律按默认值处理，兼容旧工程。"""
        def set_num(widget, key, default, caster=float):
            if widget is None:
                return
            raw = p.get(key, default)
            try:
                if isinstance(caster, type) and caster is int:
                    widget.setValue(int(raw))
                else:
                    widget.setValue(float(raw))
            except (TypeError, ValueError):
                widget.setValue(type(default)(default) if not isinstance(default, bool) else default)

        def set_bool(widget, key, default):
            if widget is not None:
                widget.setChecked(bool(p.get(key, default)))

        def set_text(widget, key, default=""):
            if widget is not None:
                widget.setText(str(p.get(key, default) or ""))

        def set_index(widget, key, default):
            if widget is None:
                return
            try:
                widget.setCurrentIndex(int(p.get(key, default)))
            except (TypeError, ValueError):
                widget.setCurrentIndex(int(default))

        def set_data(widget, key, default):
            """按 currentData 还原（下拉框的文案可能改，data 才是稳定值）。"""
            if widget is None:
                return
            target = p.get(key, default)
            idx = widget.findData(target)
            if idx < 0:
                idx = widget.findData(default)
            if idx >= 0:
                widget.setCurrentIndex(idx)

        set_num(getattr(self, "mix_target", None), "target_duration", 30.0)
        set_num(getattr(self, "mix_tolerance", None), "tolerance", 5.0)
        set_num(getattr(self, "mix_clip_min", None), "clip_min", 2.0)
        set_num(getattr(self, "mix_clip_max", None), "clip_max", 5.0)
        set_num(getattr(self, "mix_clip_count", None), "project_count", 1, caster=int)
        set_index(getattr(self, "mix_resolution", None), "resolution_index", 0)
        set_index(getattr(self, "mix_fps", None), "fps_index", 0)
        set_bool(getattr(self, "mix_shuffle", None), "shuffle", True)
        set_bool(getattr(self, "mix_reuse", None), "reuse", True)
        set_bool(getattr(self, "mix_beat_sync", None), "beat_sync", True)
        set_data(getattr(self, "mix_beat_step", None), "beat_step", 0)
        set_num(getattr(self, "mix_beat_tolerance", None), "beat_tolerance", 0.12)
        set_num(getattr(self, "mix_weak_chance", None), "weak_beat_chance", 0.25)
        set_data(getattr(self, "mix_diversity", None), "diversity", "medium")
        set_data(getattr(self, "mix_rhythm", None), "rhythm", "balance")
        set_data(getattr(self, "mix_version", None), "version_style", "balanced")
        set_bool(getattr(self, "mix_sentence_snap", None), "sentence_snap", False)
        set_bool(getattr(self, "mix_audio_dedup", None), "audio_dedup", False)
        set_bool(getattr(self, "mix_openclip", None), "openclip", True)
        set_text(getattr(self, "mix_draft_dir", None), "draft_dir", "")
        set_index(getattr(self, "music_mode", None), "music_mode", 0)
        set_text(getattr(self, "mix_bgm", None), "music_path", "")
        set_bool(getattr(self, "mix_ducking", None), "ducking", False)
        set_num(getattr(self, "mix_base_vol", None), "base_volume", 1.0)
        set_num(getattr(self, "mix_duck_vol", None), "duck_volume", 0.35)
        set_bool(getattr(self, "mix_trans_enable", None), "trans_enable", False)
        set_num(getattr(self, "mix_dissolve_dur", None), "dissolve_duration", 0.4)
        set_num(getattr(self, "mix_short", None), "short_threshold", 1.5)
        set_num(getattr(self, "mix_long", None), "long_threshold", 3.0)
        set_text(getattr(self, "mix_blacklist", None), "blacklist", "")

    # =================================================
    # 工程 v3：素材树 / 分类 / 勾选 / 分析结果 / 参数 / 筛选 / 界面状态
    # =================================================
    def _project_tree_state(self):
        """旧素材树全量结构（分类 + 子目录 + 视频路径 + 显示名）。"""
        try:
            old = self.backend.ensure()
        except Exception:
            old = None
        tree = getattr(old, "tree", None)
        if tree is None or not hasattr(tree, "serialize_tree"):
            return None
        try:
            return tree.serialize_tree(tree.invisibleRootItem())
        except Exception:
            return None

    def _demo_filters_state(self):
        page = getattr(self, "demo_library", None)
        if page is None:
            return {}
        chip = next((n for n, c in page.chips.items() if c.isChecked()), "全部")
        folder = next((f for f in page.folders
                       if f.get("id") == getattr(page, "active_folder", "all")), None)
        return {
            "folder_name": (folder or {}).get("name", ""),
            "folder_path": (folder or {}).get("folder_path", ""),
            "chip": chip,
            "search": page.search.text(),
            "sort": page.sort.currentIndex(),
            "min_dur": page.min_dur.currentIndex(),
            "view": page.view_mode.currentIndex(),
        }

    def _project_state_v3(self):
        import datetime
        from mixcut.ui import demo

        try:
            from mixcut import preview_proxy
            proxy_width = preview_proxy.get_width()
        except Exception:
            proxy_width = 0
        page = getattr(self, "page_analyze", None)
        demo_clips = [dict(c) for c in demo.mock.CLIPS if c.get("path")]
        removed_items = [dict(c) for c in demo.mock.CLIPS
                         if c.get("_removed") and c.get("path")]
        player = getattr(self, "player", None)
        audio = getattr(self, "audio_output", None)
        return {
            "version": 3,
            "app": "MixCut",
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "library": {"tree": self._project_tree_state(), "root_name": "素材库"},
            "materials": list(getattr(self, "_confirmed_paths", []) or []),
            "materials_meta": demo_clips,
            "selected": self.selected_paths(),
            "removed": [c.get("path") for c in removed_items],
            "removed_items": removed_items,
            "current_clip": next((c.get("path") for c in demo.mock.CLIPS
                                  if c.get("id") == getattr(self.controller, "_current", None)
                                  and c.get("path")), ""),
            "analysis": {
                "mode": getattr(page, "_mode_key", "medium"),
                "finished": bool(getattr(page, "_finished", False)),
                "items": [dict(r) for r in getattr(page, "_results", [])],
                "pool": (page.pool_state()
                         if hasattr(page, "pool_state") else {}),
            },
            "params": self._project_params(),
            "params_v3": (self.demo_params.capture()
                          if hasattr(self.demo_params, "capture") else {}),
            "filters": self._demo_filters_state(),
            "ui": {
                "page": getattr(getattr(self, "shell", None), "current", "library"),
                "scroll": (self.demo_library.scroll.verticalScrollBar().value()
                           if hasattr(self, "demo_library") else 0),
            },
            "player": {
                "aspect": (self.aspect_combo.currentIndex()
                           if hasattr(self, "aspect_combo") else 0),
                "position": (player.position() if player is not None else 0),
                "volume": (audio.volume() if audio is not None else 1.0),
            },
            "task": (self.demo_tasks.capture_state()
                     if hasattr(self, "demo_tasks") and hasattr(self.demo_tasks, "capture_state")
                     else {}),
            "output": {"proxy_width": proxy_width},
        }

    def _project_state_hash(self, data=None):
        data = data or self._project_state_v3()
        # 只把“真正的工程内容”算进改动指纹；滚动、播放进度、异步探测到的
        # 时长/缩略图等浏览或缓存状态变化，不应触发“未保存”提示。
        keys = ("version", "materials", "selected", "removed", "removed_items",
                "current_clip", "analysis", "params", "params_v3", "filters")
        payload = {k: data.get(k) for k in keys}
        payload["library"] = {"tree": (data.get("library") or {}).get("tree")}
        payload["ui"] = {"page": (data.get("ui") or {}).get("page")}
        payload["output"] = data.get("output")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _project_dirty(self):
        """工程是否有未保存改动：以最近一次保存/打开的内容指纹为准。"""
        base = getattr(self, "_saved_state_hash", "")
        if not base:
            try:
                state = self._project_state_v3()
            except Exception:
                return False
            return bool(state.get("materials") or state.get("analysis", {}).get("items")
                        or self.get_all_video_paths())
        try:
            return self._project_state_hash() != base
        except Exception:
            return False

    def save_project(self, path=None):
        """保存工程：校验 → 原子写临时文件 → 备份旧工程 → 替换。

        Ctrl+S 已保存过时走 `_shortcut_save` 直接传路径；设置页仍可弹框。
        """
        from PySide6.QtWidgets import QFileDialog

        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存工程", getattr(self, "_project_path", "") or "",
                "MixCut 工程 (*.mixcut)")
            if not path:
                return False
        if not str(path).lower().endswith(".mixcut"):
            path = str(path) + ".mixcut"
        path = os.path.abspath(path)

        page = getattr(self, "page_analyze", None)
        worker = getattr(page, "_worker", None)
        if worker is not None and worker.isRunning():
            ok = QMessageBox.question(
                self, "分析还在进行",
                "AI 分析尚未完成，现在保存的是已完成的部分结果。\n"
                "建议等分析结束后再保存。仍要保存吗？")
            if ok != QMessageBox.StandardButton.Yes:
                self.log("工程保存已取消：等待分析完成")
                return False

        data = self._project_state_v3()
        if data.get("library", {}).get("tree") is None:
            QMessageBox.warning(self, "保存失败", "后台素材库尚未加载，无法保存完整工程。")
            self.log("工程保存失败：后台素材库未就绪")
            return False

        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".mixcut_save_", suffix=".tmp",
                                       dir=os.path.dirname(path) or ".")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.isfile(path):
                try:
                    shutil.copy2(path, path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, path)
            tmp = None
        except Exception as exc:
            self.log("工程保存失败：%s" % exc)
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            QMessageBox.warning(self, "保存失败", "工程写入失败：%s" % exc)
            return False

        self._project_path = path
        self._saved_state_hash = self._project_state_hash(data)
        self._refresh_topbar()
        self.log(f"工程已保存：{path}（{len(data['materials'])} 个已选素材、"
                 f"{len(data['analysis']['items'])} 个分析片段）")
        return True

    def _restore_library_tree(self, data):
        """用工程里的树结构还原旧素材树；旧格式只有名单时退化为平铺。"""
        old = self.backend.ensure()
        if old is None:
            return 0, []
        tree = getattr(old, "tree", None)
        lib = data.get("library") or {}
        tree_state = lib.get("tree")
        paths = []
        if tree_state:
            tree.clear()
            tree.deserialize_tree(tree_state, tree.invisibleRootItem())
            old.invalidate_materials()
            old.update_count()
            try:
                paths = list(old.get_all_video_paths())
            except Exception:
                paths = []
        else:
            mats = [str(x) for x in (data.get("materials") or []) if str(x).strip()]
            alive = [p for p in mats if os.path.isfile(p) and os.path.getsize(p) > 0]
            if alive:
                old.add_paths_to_tree(alive, None)
                old.invalidate_materials()
                old.update_count()
            paths = alive
        existing = {p for p in paths if os.path.isfile(p)}
        missing = [p for p in paths if p not in existing]
        return len(existing), missing

    def _restore_filters(self, f):
        page = getattr(self, "demo_library", None)
        if page is None or not f:
            return
        if f.get("search"):
            page.search.setText(str(f["search"]))
        chip = f.get("chip")
        if chip and chip in page.chips:
            page._filter(chip)
        for widget, key in ((page.sort, "sort"), (page.min_dur, "min_dur"),
                            (page.view_mode, "view")):
            try:
                widget.setCurrentIndex(int(f.get(key, widget.currentIndex())))
            except (TypeError, ValueError):
                pass
        folder_path = f.get("folder_path")
        folder_name = f.get("folder_name") or ""
        fid = ""
        if folder_path is not None:
            fid = next((x["id"] for x in page.folders
                        if x.get("folder_path") == folder_path), "")
        if not fid:
            fid = next((x["id"] for x in page.folders
                        if x.get("name") == folder_name), "all")
        page._select_folder(fid)

    def _load_project_data(self, data, path):
        from mixcut.ui import demo

        if self.backend.ensure() is None:
            self.log("打开工程失败：后台素材库未加载，工程未切换")
            QMessageBox.warning(self, "打开失败", "后台素材库未加载，无法恢复工程。")
            return False
        p = data.get("params") or {}
        applied = len(p)
        page = getattr(self, "page_analyze", None)
        if page is not None:
            page.clear_results()

        self._history_busy = True
        try:
            # 0) 已移除素材先放回 controller，reload 时才能重新生成“已移除”卡片
            removed_items = data.get("removed_items")
            if not isinstance(removed_items, list):
                removed_items = []
                for rp in (data.get("removed") or []):
                    if isinstance(rp, str) and rp:
                        removed_items.append({"path": rp, "name": Path(rp).name,
                                              "folder": "", "folder_path": "",
                                              "dur": "—", "_removed": True})
            self.controller.removed_clips = [dict(x) for x in removed_items
                                             if isinstance(x, dict) and x.get("path")]
            # 1) 旧参数先还原（v2 只有这一套；v3 随后被新 UI 参数覆盖）
            if p:
                self._apply_project_params(p)
            # 2) 素材树（分类 / 子目录 / 素材顺序）
            alive_n, missing = self._restore_library_tree(data)
            # 3) 新 UI 素材库刷新（明确换工程，不保留上一工程勾选）
            self.reload_library(preserve_selection=False)
            # 2.5) 用工程里保存的素材元数据覆盖重新探测前的占位值
            meta_by_path = {}
            for m in (data.get("materials_meta") or []):
                if isinstance(m, dict) and m.get("path"):
                    meta_by_path[m["path"]] = m
            if meta_by_path:
                fields = ("name", "dur", "width", "height", "codec", "fps",
                          "pix_fmt", "bit_depth", "file_size", "quality", "tags",
                          "thumb_path")
                for clip in demo.mock.CLIPS:
                    meta = meta_by_path.get(clip.get("path"))
                    if meta:
                        for key in fields:
                            if key in meta:
                                clip[key] = meta[key]
                if hasattr(self.demo_library, "refresh"):
                    self.demo_library.refresh()
            # 4) 筛选状态
            self._restore_filters(data.get("filters") or {})
            # 5) 勾选 / 确认名单
            sel_paths = set(data.get("selected") or [])
            self.controller.selected = {c["id"] for c in demo.mock.CLIPS
                                        if c.get("path") in sel_paths}
            removed_paths = set(data.get("removed") or [])
            try:
                self.controller.removed = {c["id"] for c in demo.mock.CLIPS
                                           if c.get("path") in removed_paths}
            except Exception:
                pass
            cur_path = data.get("current_clip") or ""
            if cur_path:
                cid = next((c["id"] for c in demo.mock.CLIPS
                            if c.get("path") == cur_path), "")
                if cid:
                    self.controller._current = cid
            confirmed = [p2 for p2 in (data.get("materials") or [])
                         if os.path.isfile(p2)]
            self._confirmed_paths = confirmed
            self.material_confirmed = bool(confirmed)
            old_page = getattr(self, "page_library", None)
            if old_page is not None and hasattr(old_page, "_checked_paths"):
                old_page._checked_paths = set(confirmed)
                for name in ("_refresh_checkboxes", "_sync_selection_labels"):
                    fn = getattr(old_page, name, None)
                    if callable(fn):
                        fn()
            self.controller.stateChanged.emit()
            # 6) 分析结果（片段级，含废镜 / 纳入状态）
            ana = data.get("analysis") or {}
            items = ana.get("items") or []
            alive_items = [r for r in items
                           if isinstance(r, dict) and r.get("path")
                           and os.path.isfile(r["path"])]
            if len(alive_items) != len(items):
                self.log("分析结果里有 %d 个片段的原素材已不存在，已跳过"
                         % (len(items) - len(alive_items)))
            if page is not None and alive_items:
                page.load_results(alive_items,
                                  finished=bool(ana.get("finished", True)),
                                  mode_key=ana.get("mode"))
            if page is not None and ana.get("pool"):
                page.load_pool_state(ana.get("pool"))
            # 7) 新 UI 参数（真正驱动混剪的一套）
            self.demo_params.restore(data.get("params_v3") or {})
            try:
                self.backend.sync_params(self.demo_params)
            except Exception:
                pass
            # 8) 代理开关（工程里记录过就沿用）
            width = (data.get("output") or {}).get("proxy_width")
            if width:
                try:
                    self.set_preview_proxy_width(width)
                except Exception:
                    pass
        finally:
            self._history_busy = False

        # 9) 收尾刷新
        self._known_paths = set(self._probe_paths())
        self._refresh_topbar()
        if hasattr(self, "demo_tasks"):
            if hasattr(self.demo_tasks, "restore_state"):
                self.demo_tasks.restore_state(data.get("task") or {})
            self.demo_tasks.refresh()
        self.demo_params.refresh_run_state()
        if hasattr(self, "demo_workbench"):
            self.demo_workbench.refresh()
        # 10) 回到保存时所在的页面
        target_page = (data.get("ui") or {}).get("page") or "library"
        if getattr(getattr(self, "shell", None), "pages", None) and \
                target_page in self.shell.pages:
            self.go_page(target_page)
        # 工作区播放器/画幅/滚动位置
        pstate = data.get("player") or {}
        try:
            if hasattr(self, "aspect_combo") and pstate.get("aspect") is not None:
                self.aspect_combo.setCurrentIndex(int(pstate.get("aspect") or 0))
            if hasattr(self, "audio_output") and pstate.get("volume") is not None:
                self.audio_output.setVolume(float(pstate.get("volume", 1.0)))
            if hasattr(self, "player") and pstate.get("position"):
                self.player.setPosition(int(pstate.get("position") or 0))
        except Exception:
            pass
        try:
            scroll = int((data.get("ui") or {}).get("scroll") or 0)
            if hasattr(self, "demo_library"):
                self.demo_library.scroll.verticalScrollBar().setValue(scroll)
        except Exception:
            pass

        self._project_path = path
        self._undo_stack = []
        self._redo_stack = []
        try:
            self._params_seen = self._project_params()
            self._saved_state_hash = self._project_state_hash()
        except Exception:
            self._saved_state_hash = ""
        n_ana = len(self.page_analyze._results) if page is not None else 0
        self.log(f"工程已打开：{path}（{alive_n} 个素材、{n_ana} 个分析片段）")
        if missing:
            names = "、".join(Path(m).name for m in missing[:5])
            more = f" 等 {len(missing)} 个" if len(missing) > 5 else ""
            self.log(f"工程里有素材找不到（路径保留待重连）：{names}{more}")
        return True

    def new_project(self):
        """新建空工程：清素材树、勾选、分析与参数，不重启程序。"""
        if self._project_dirty():
            ok = QMessageBox.question(
                self, "新建工程",
                "当前工程还有未保存的改动，新建会丢失这些改动。\n要继续吗？")
            if ok != QMessageBox.StandardButton.Yes:
                return False
        old = self.backend.ensure()
        if old is None:
            QMessageBox.warning(self, "新建失败", "后台素材库尚未加载。")
            return False
        self._history_busy = True
        try:
            old.tree.clear()
            old.invalidate_materials()
            old.update_count()
            self.controller.selected.clear()
            self.controller.removed.clear()
            self.controller.removed_clips = []
            self.controller.analyzed = False
            self.controller.analyzed_all = False
            self._confirmed_paths = []
            self.material_confirmed = False
            page = getattr(self, "page_analyze", None)
            if page is not None:
                page.clear_results()
            self.reload_library(preserve_selection=False)
            if hasattr(self.demo_params, "_reset_defaults"):
                self.demo_params._reset_defaults()
            try:
                self.backend.sync_params(self.demo_params)
            except Exception:
                pass
        finally:
            self._history_busy = False
        self._project_path = ""
        self._saved_state_hash = ""
        self._undo_stack = []
        self._redo_stack = []
        self._known_paths = set()
        self._refresh_topbar()
        if hasattr(self, "demo_tasks"):
            if hasattr(self.demo_tasks, "restore_state"):
                self.demo_tasks.restore_state({})
            self.demo_tasks.refresh()
        self.demo_params.refresh_run_state()
        if hasattr(self, "demo_workbench"):
            self.demo_workbench.refresh()
        self.go_page("library")
        self.log("已新建空工程")
        return True

    def open_project(self):
        """打开工程：校验 → 提示未保存 → 还原素材树 → 刷新 UI → 恢复分析/参数。"""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", getattr(self, "_project_path", "") or "",
            "MixCut 工程 (*.mixcut)")
        if not path:
            return False
        if self._project_dirty():
            ok = QMessageBox.question(
                self, "有未保存的改动",
                "当前工程还有未保存的改动，打开另一个工程会丢失这些改动。\n要继续打开吗？")
            if ok != QMessageBox.StandardButton.Yes:
                return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("工程文件格式不正确")
            version = int(data.get("version") or 0)
            if version > 3:
                raise ValueError("工程版本比当前软件新，请升级 MixCut")
            if not isinstance(data.get("materials", []), list):
                raise ValueError("工程素材清单格式不正确")
            lib = data.get("library", {})
            if lib is not None and not isinstance(lib, dict):
                raise ValueError("工程素材库结构不正确")
            if isinstance(lib, dict) and lib.get("tree") is not None \
                    and not isinstance(lib.get("tree"), (dict, list)):
                raise ValueError("工程素材树结构不正确")
            for key in ("filters", "analysis", "params", "params_v3", "ui", "player"):
                if data.get(key) is not None and not isinstance(data.get(key), dict):
                    raise ValueError("工程字段 %s 格式不正确" % key)
        except Exception as exc:
            self.log("打开工程失败：%s" % exc)
            QMessageBox.warning(self, "打开失败",
                                "这个文件不是有效的 MixCut 工程：\n%s" % exc)
            return False
        try:
            return self._load_project_data(data, path)
        except Exception as exc:
            self.log("工程恢复中断：%s" % exc)
            QMessageBox.warning(self, "打开失败",
                                "工程数据恢复失败，已保留当前界面：\n%s" % exc)
            return False


    def _open_settings(self):
        """顶部工具栏“设置”：定位到设置页，并立即跑一次真实环境自检。"""
        self._navigate("settings")
        page = getattr(self, "page_settings", None)
        if page is not None:
            try:
                page.on_shown()
            except Exception:
                pass

    def _set_task_running(self, running):
        """任务开始/结束时更新导航栏运行状态（底部任务窗口已移除）。"""
        shell = getattr(self, "shell", None)
        if shell is None:
            return
        if running:
            shell.nav.set_status("运行中", "warning")
        else:
            shell.nav.set_status("就绪", "success")

    def closeEvent(self, event):
        # 有未保存改动先询问，避免直接关掉丢工程
        if not getattr(self, "_force_close", False) and self._project_dirty():
            box = QMessageBox(self)
            box.setWindowTitle("有未保存的改动")
            box.setText("当前工程有未保存的改动，关闭前要保存吗？")
            btn_save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
            btn_discard = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_save)
            box.exec()
            choice = box.clickedButton()
            if choice is btn_cancel or choice is None:
                event.ignore()
                return
            if choice is btn_save:
                if not self.save_project(getattr(self, "_project_path", "") or None):
                    event.ignore()
                    return
        # 先停止后台任务，避免线程仍在读写缓存时主窗口就释放对象。
        worker = getattr(self, "mix_worker", None)
        if worker is not None and worker.isRunning():
            try:
                worker.shutdown()
                if not worker.wait(7000):
                    QMessageBox.warning(
                        self, "任务仍在运行",
                        "正在停止混剪任务。请等任务取消后再关闭软件。"
                    )
                    event.ignore()
                    return
            except Exception:
                event.ignore()
                return
        # 线程停止后再释放纯内存分析缓存。
        try:
            self.analysis_cache.clear()
        except Exception:
            pass
        super().closeEvent(event)

    def _toggle_play(self):
        if getattr(self, "player", None) is not None:
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.pause()
            else:
                self.player.play()

    def _escape_action(self):
        # 预览全屏优先退出（素材预览 / 分析页预览都是容器全屏）
        if self._fullscreen_preview() is not None:
            self._exit_preview_fullscreen()
            return
        if getattr(self, "video_widget", None) is not None and self.video_widget.isFullScreen():
            self._toggle_video_fullscreen()

    def _toggle_fullscreen(self):
        if getattr(self, "video_widget", None) is None:
            return
        self._toggle_video_fullscreen()

    def keyPressEvent(self, event):
        # Esc：退出视频全屏
        if event.key() == Qt.Key_Escape:
            if getattr(self, "video_widget", None) is not None and \
               self.video_widget.isFullScreen():
                self.video_widget.showNormal()
                return
        # 空格：播放/暂停
        if event.key() == Qt.Key_Space:
            if getattr(self, "player", None) is not None:
                if self.player.playbackState() == QMediaPlayer.PlayingState:
                    self.player.pause()
                else:
                    self.player.play()
                return
        super().keyPressEvent(event)

    # =================================================
    # UI
    # =================================================

    def _build_ui(self):

        if getattr(self, "use_new_ui", False):
            # 新界面：旧页面照常构建（控件都保留，供新页面复用），
            # 只是不挂到窗口上，改为四区布局。
            self.tabs = QTabWidget()
            self._build_config_tab()
            self._build_mix_tab()
            self._build_workstation_tab()
            self.tabs.currentChanged.connect(self.refresh_selectors)
            self._build_new_shell()
            from mixcut.ui import theme as _ui_theme
            _ui_theme.apply(QApplication.instance())
            # 可见外壳和四页使用 demo 的原始 QSS；避免旧主题覆盖 demo 的字体像素尺寸。
            from mixcut.ui.demo import theme as _demo_theme
            QApplication.instance().setStyleSheet(_demo_theme.qss())
            return

        central = QWidget()

        self.setCentralWidget(central)

        layout = QVBoxLayout(central)

        self.tabs = QTabWidget()

        layout.addWidget(
            self.tabs
        )

        self._build_config_tab()
        self._build_mix_tab()

        # 工作站入口暂时保留为占位页；当前正式工作流为智能混剪。
        self._build_workstation_tab()

        # 切换页面时刷新智能混剪素材选择框架
        self.tabs.currentChanged.connect(self.refresh_selectors)

        self.setStyleSheet("""
            QMainWindow, QWidget {
                background:#171b24;
                color:#e9edf5;
                font-size:13px;
            }

            QGroupBox {
                border:1px solid #343d4d;
                border-radius:8px;
                margin-top:10px;
                padding:10px;
                font-weight:600;
            }

            QGroupBox::title {
                subcontrol-origin:margin;
                left:10px;
                padding:0 5px;
            }

            QPushButton {
                background:#30394a;
                border:0;
                padding:9px 13px;
                border-radius:5px;
            }

            QPushButton:hover {
                background:#43506a;
            }

            QPushButton:disabled {
                background:#252a35;
                color:#666;
            }

            QLineEdit, QSpinBox, QDoubleSpinBox,
            QComboBox, QTreeWidget,
            QPlainTextEdit, QListWidget {

                background:#222936;
                border:1px solid #394457;
                border-radius:4px;
                padding:6px;
            }

            QHeaderView::section {
                background:#30394a;
                padding:5px;
                border:0;
            }

            QProgressBar {
                border:1px solid #394457;
                border-radius:4px;
                text-align:center;
            }

            QProgressBar::chunk {
                background:#FFFFFF;
                border-radius:2px;
            }

            QTabBar::tab {
                background:#181A1D;
                padding:11px 22px;
                margin-right:2px;
                color:#92969E;
            }

            QTabBar::tab:selected {
                background:#22252A;
                color:#FFFFFF;
                font-weight:bold;
                border-bottom:2px solid #FFFFFF;
            }
        """)

    # 内容固定宽度（网页式居中，超出高度可上下滚动）
    PAGE_CONTENT_WIDTH = 1180

    def _wrap_scroll(self, content):
        """把页面内容包进 QScrollArea：固定宽度居中，垂直滚动，全屏不拉伸。"""
        content.setMaximumWidth(self.PAGE_CONTENT_WIDTH)

        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)
        h.addWidget(content)
        h.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setWidget(container)
        return scroll

    # ---------------- 引擎能力页 ----------------

    def goto_engine_page(self):
        """跳到引擎能力页：新界面走左侧导航，旧界面走标签页。"""
        shell = getattr(self, "shell", None)
        nav = getattr(shell, "nav", None)
        if nav is not None:
            nav.select("engine")
            return
        page = getattr(self, "engine_page", None)
        if page is not None:
            self.tabs.setCurrentWidget(page)

    def refresh_engine_status(self):
        """读取引擎自检信息与缓存条数，刷新「引擎能力」页的状态区。"""
        try:
            info = gpue.describe()
        except Exception as exc:  # 自检本身不能把界面拖垮
            self.engine_status.setText(f"自检失败：{type(exc).__name__}: {exc}")
            return

        exes = info.get("exe", {}) or {}
        lines = [f"引擎目录：{info.get('dir') or '未找到（流程会降级，照常出片）'}"]
        for name in ("sd_asr.exe", "sd_dedup.exe"):
            lines.append(f"{name}：{'在位' if exes.get(name) else '缺失'}")

        engine_bin = Path(info["dir"]) if info.get("dir") else None
        model = (engine_bin.parent / "models" / "whisper-ggml" / "ggml-small.bin"
                 if engine_bin else None)
        lines.append(
            f"whisper 模型：{'在位' if model and model.exists() else '缺失'}（ggml-small.bin）")

        for kind, label, pattern in (
            ("asr", "转写缓存", "*.srt"),
            ("fp", "指纹缓存", "*.json"),
        ):
            try:
                root = gpue.cache_dir(kind)
                count = len(list(root.glob(pattern)))
            except OSError:
                root, count = None, 0
            lines.append(f"{label}：{count} 个（{root}）")

        snap = bool(getattr(self, "mix_sentence_snap", None)
                    and self.mix_sentence_snap.isChecked())
        dedup = bool(getattr(self, "mix_audio_dedup", None)
                    and self.mix_audio_dedup.isChecked())
        lines.append(f"混剪开关：整句切割 {'开' if snap else '关'} / "
                     f"指纹查重 {'开' if dedup else '关'}")
        self.engine_status.setText("\n".join(lines))

    def open_engine_doc(self):
        """打开三项能力的说明页（HTML）。"""
        page = Path(__file__).resolve().parent / "功能说明" / "新增三项能力.html"
        if page.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(page)))
        else:
            QMessageBox.information(
                self, "说明页缺失", f"未找到说明页：\n{page}")

    def open_engine_cache(self):
        """打开缓存目录（转写 SRT 与指纹 JSON 都在它的子目录里）。"""
        try:
            root = gpue.cache_dir("asr").parent
        except OSError as exc:
            QMessageBox.information(self, "打不开缓存目录", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))


    # =================================================
    # 第一页
    # =================================================

    def _build_config_tab(self):

        self.tab_config = QWidget()

        tab_layout = QVBoxLayout(
            self.tab_config
        )

        splitter = QSplitter(
            Qt.Horizontal
        )

        tab_layout.addWidget(
            splitter
        )

        # 左侧

        left = QWidget()

        left_layout = QVBoxLayout(left)

        material_group = QGroupBox(
            "素材管理"
        )

        material_layout = QVBoxLayout(
            material_group
        )

        buttons = QHBoxLayout()

        add_files = QPushButton(
            "添加视频"
        )

        add_folder = QPushButton(
            "扫描文件夹"
        )

        add_category = QPushButton("新建分类")

        remove = QPushButton(
            "移除选中"
        )

        buttons.addWidget(add_files)

        buttons.addWidget(add_folder)

        buttons.addWidget(add_category)

        buttons.addWidget(remove)

        material_layout.addLayout(
            buttons
        )

        confirm_row = QHBoxLayout()

        self.check_btn = QPushButton(
            "检查素材"
        )

        self.confirm_btn = QPushButton(
            "确定使用"
        )

        self.confirm_btn.setStyleSheet(
            "background:#d48a38;color:white;font-weight:bold;"
        )

        confirm_row.addWidget(
            self.check_btn
        )

        confirm_row.addWidget(
            self.confirm_btn
        )

        material_layout.addLayout(
            confirm_row
        )

        self.tree = UndoableTreeWidget()
        # 新 UI 下旧素材库页被摘出主窗口控件树，parentWidget() 链到不了 MainWindow，
        # 这里显式记住宿主，撤销时才能回刷可见界面。
        self.tree.owner_window = self

        self.tree.setHeaderLabels([
            "分类 / 素材名称",
            "文件路径"
        ])

        self.tree.setColumnWidth(
            0,
            230
        )

        self.tree.itemClicked.connect(
            self.on_tree_item_clicked
        )

        material_layout.addWidget(
            self.tree,
            1
        )

        self.count_label = QLabel(
            "0 个素材"
        )

        material_layout.addWidget(
            self.count_label
        )

        left_layout.addWidget(
            material_group,
            3
        )

        # 播放器

        player_group = QGroupBox(
            "视频预览"
        )

        player_layout = QVBoxLayout(
            player_group
        )

        self.video_widget = QVideoWidget()

        self.video_widget.setMinimumHeight(
            260
        )

        self.video_widget.setStyleSheet(
            "background:black;"
        )

        player_layout.addWidget(
            self.video_widget,
            1
        )

        player_controls = QHBoxLayout()

        self.play_btn = QPushButton(
            "播放"
        )

        self.pause_btn = QPushButton(
            "暂停"
        )

        self.stop_btn = QPushButton(
            "停止"
        )

        player_controls.addWidget(
            self.play_btn
        )

        player_controls.addWidget(
            self.pause_btn
        )

        player_controls.addWidget(
            self.stop_btn
        )

        player_layout.addLayout(
            player_controls
        )

        left_layout.addWidget(
            player_group,
            2
        )

        splitter.addWidget(
            left
        )

        # 右侧参数

        right = QWidget()

        right_layout = QVBoxLayout(
            right
        )

        self.info_label = QLabel()

        self.info_label.setStyleSheet(
            "color:#8491a5;line-height:2;"
        )

        self.info_label.setWordWrap(
            True
        )

        right_layout.addWidget(
            self.info_label
        )

        # 详细参数面板：预览哪个视频就显示哪个视频的参数
        detail_group = QGroupBox("详细参数")
        detail_layout = QVBoxLayout(detail_group)
        self.detail_box = QPlainTextEdit()
        self.detail_box.setReadOnly(True)
        self.detail_box.setMinimumHeight(190)
        self.detail_box.setPlainText("点击左侧素材即可查看该视频的详细参数")
        detail_layout.addWidget(self.detail_box)
        right_layout.addWidget(detail_group)

        self.update_info_label()

        right_layout.addStretch()

        splitter.addWidget(
            right
        )

        splitter.setSizes([
            850,
            500
        ])

        self.tabs.addTab(
            self._wrap_scroll(self.tab_config),
            "素材与参数"
        )

        # 信号

        add_files.clicked.connect(
            self.add_files
        )

        add_folder.clicked.connect(
            self.add_folder
        )

        add_category.clicked.connect(self.create_virtual_folder)

        remove.clicked.connect(
            self.remove_selected
        )

        self.check_btn.clicked.connect(
            self.check_materials
        )

        self.confirm_btn.clicked.connect(
            self.confirm_materials
        )

    # =================================================
    # 智能粗剪筛选引擎面板
    # =================================================

    def _has_nvenc(self):
        """ffmpeg 是否带 NVENC 编码器（作为 CUDA 可用的判据）。"""
        ffmpeg = ft.find_ffmpeg()
        if not ffmpeg:
            return False
        try:
            import subprocess
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(ft, "NO_WINDOW_FLAGS", 0),
            )
            return proc.returncode == 0 and "nvenc" in proc.stdout
        except Exception:
            return False

    def _build_filter_panel(self, parent_layout):
        avail = fe.self_check()
        group = QGroupBox("智能粗剪筛选引擎")
        vbox = QVBoxLayout(group)

        # 顶部：一键预设
        preset_row = QHBoxLayout()
        btn_fast = QPushButton("快速批量")
        btn_fine = QPushButton("精细质检")
        preset_row.addWidget(btn_fast)
        preset_row.addWidget(btn_fine)
        vbox.addLayout(preset_row)

        # 强制自检按钮
        btn_check = QPushButton("强制自检")
        vbox.addWidget(btn_check)

        # 硬件加速单选
        hw_row = QHBoxLayout()
        hw_row.addWidget(QLabel("硬件加速："))
        self.filter_hw_group = QButtonGroup(self)
        self.filter_cpu_radio = QRadioButton("CPU")
        self.filter_gpu_radio = QRadioButton("GPU")
        self.filter_cpu_radio.setChecked(True)
        self.filter_hw_group.addButton(self.filter_cpu_radio)
        self.filter_hw_group.addButton(self.filter_gpu_radio)
        hw_row.addWidget(self.filter_cpu_radio)
        hw_row.addWidget(self.filter_gpu_radio)
        hw_row.addStretch()
        vbox.addLayout(hw_row)

        if not self._has_nvenc():
            self.filter_gpu_radio.setEnabled(False)
            self.filter_gpu_radio.setText("GPU（无 CUDA）")

        # 检测组件勾选
        self.filter_checks = {}
        checkbox_spec = [
            ("amverge", "amverge 静止空镜检测", True),
            ("scenedetect", "PySceneDetect 自适应镜头校验", True),
            ("videoscope", "VideoScope 画质缺陷检测", False),
            ("ffmpeg_audio_black", "FFmpeg 静音+黑屏检测", True),
            ("transnetv2", "TransNetV2 高阶镜头切变", False),
        ]
        for key, label, default_on in checkbox_spec:
            cb = QCheckBox(label)
            dep_ok = avail["ffmpeg"] if key == "ffmpeg_audio_black" else avail.get(key, False)
            cb.setChecked(bool(default_on and dep_ok))
            if key == "amverge":
                # 核心基础模块，强制开启不可关
                cb.setEnabled(False)
                cb.setChecked(True)
            elif not dep_ok:
                cb.setEnabled(False)
                cb.setText(label + "（未安装）")
            self.filter_checks[key] = cb
            vbox.addWidget(cb)

        # 状态（不显示进度条）
        self.filter_status = QLabel("就绪")
        self.filter_status.setStyleSheet("color:#8491a5;")
        vbox.addWidget(self.filter_status)

        parent_layout.addWidget(group)

        btn_fast.clicked.connect(
            lambda: self._apply_filter_preset(fe.preset_fast_batch())
        )
        btn_fine.clicked.connect(
            lambda: self._apply_filter_preset(fe.preset_fine_qc())
        )
        btn_check.clicked.connect(self.run_force_self_check)

    def run_force_self_check(self, show_dialog=True):
        """强制自检：重新导入全部组件，刷新勾选/单选状态，返回是否可继续。"""
        avail, report = fe.force_self_check()

        # 刷新 GPU 单选
        has_gpu = self._has_nvenc()
        self.filter_gpu_radio.setEnabled(has_gpu)
        self.filter_gpu_radio.setText("GPU" if has_gpu else "GPU（无 CUDA）")
        if not has_gpu and self.filter_gpu_radio.isChecked():
            self.filter_cpu_radio.setChecked(True)

        # 刷新各组件勾选框
        for key, cb in self.filter_checks.items():
            if key == "amverge":
                continue
            dep_ok = avail["ffmpeg"] if key == "ffmpeg_audio_black" else avail.get(key, False)
            if not dep_ok:
                # 依赖缺失：禁用并取消勾选
                cb.setEnabled(False)
                if "（未安装）" not in cb.text():
                    cb.setText(cb.text() + "（未安装）")
                cb.setChecked(False)
            else:
                cb.setEnabled(True)
                cb.setText(cb.text().replace("（未安装）", ""))

        self.filter_status.setText("强制自检完成")
        if show_dialog:
            QMessageBox.information(
                self, "强制自检结果", "\n".join(report)
            )
        # ffmpeg 缺失属于致命问题，不允许启动
        return bool(avail["ffmpeg"])

    def _apply_filter_preset(self, preset):
        mapping = {
            "amverge": "amverge_enable",
            "scenedetect": "scenedetect_enable",
            "videoscope": "videoscope_enable",
            "ffmpeg_audio_black": "ffmpeg_audio_black_enable",
            "transnetv2": "transnetv2_enable",
        }
        for key, cfg_key in mapping.items():
            cb = self.filter_checks[key]
            if cb.isEnabled():
                cb.setChecked(bool(preset.get(cfg_key, False)))
        want_cpu = bool(preset.get("cpu_mode", True))
        if want_cpu or not self.filter_gpu_radio.isEnabled():
            self.filter_cpu_radio.setChecked(True)
        else:
            self.filter_gpu_radio.setChecked(True)
        self.filter_status.setText("已应用预设")

    def _collect_filter_config(self):
        """汇总本次要用的筛选配置（所有会改变检测结果的参数）。

        这份配置同时作为缓存键的一部分，所以必须包含：组件开关、CPU/GPU、
        视频/静音检测开关以及它们的阈值。
        """
        video_on = self.mix_video_detect.isChecked()
        audio_on = self.mix_audio_detect.isChecked()
        thresholds = {
            key: float(self.detect_threshold_boxes[key].value())
            for key in self.detect_threshold_boxes
        }
        return {
            **fe.DEFAULT_FILTER_CONFIG,
            "cpu_mode": self.filter_cpu_radio.isChecked(),
            "amverge_enable": True,
            "scenedetect_enable": self.filter_checks["scenedetect"].isChecked(),
            "videoscope_enable": self.filter_checks["videoscope"].isChecked(),
            "ffmpeg_audio_black_enable":
                self.filter_checks["ffmpeg_audio_black"].isChecked(),
            "transnetv2_enable": self.filter_checks["transnetv2"].isChecked(),
            # 把混剪页画面/静音开关与检测阈值传给引擎，真正参与检测
            "ffmpeg_options": {
                **thresholds,
                "video_detect": video_on,
                "audio_detect": audio_on,
                "detect_black": video_on,
                "detect_freeze": video_on,
                # CUDA 引擎能力开关：语音整句切割 / 指纹查重
                "sentence_snap": bool(
                    getattr(self, "mix_sentence_snap", None)
                    and self.mix_sentence_snap.isChecked()
                ),
                "audio_dedup": bool(
                    getattr(self, "mix_audio_dedup", None)
                    and self.mix_audio_dedup.isChecked()
                ),
                "asr_model": "small",
                "asr_language": "zh",
            },
        }


    # =================================================
    # 智能混剪界面（第二个界面：随机混剪 + 配乐卡点 + 转场）
    # =================================================

    def _build_mix_tab(self):

        self.mix_worker = None
        self.tab_mix = QWidget()
        root = QVBoxLayout(self.tab_mix)

        # 顶部左右两栏：左=素材选择，右=素材库（参数配置）
        top_row = QHBoxLayout()

        # 素材选择框架：只处理勾选的视频
        self.mix_selector = MaterialSelector(
            "素材选择（勾选要混剪的视频）"
        )
        top_row.addWidget(self.mix_selector, 3)

        # —— 素材库（混剪基础参数移到右侧） ——
        base_group = QGroupBox("素材库 · 参数配置")
        bform = QFormLayout(base_group)
        self.mix_target = QDoubleSpinBox()
        self.mix_target.setRange(3, 3600)
        self.mix_target.setValue(30)
        self.mix_target.setSuffix(" 秒")
        self.mix_tolerance = QDoubleSpinBox()
        self.mix_tolerance.setRange(0, 60)
        self.mix_tolerance.setValue(2)
        self.mix_tolerance.setSuffix(" 秒")
        self.mix_tolerance.setToolTip(
            "目标时长为大概值：成片落在 目标±浮动 范围内即停止，"
            "末段保持自然长度，不再硬凑到精确秒数。"
        )
        self.mix_clip_min = QDoubleSpinBox()
        self.mix_clip_min.setRange(0.5, 60)
        self.mix_clip_min.setValue(2)
        self.mix_clip_min.setSuffix(" 秒")
        self.mix_clip_max = QDoubleSpinBox()
        self.mix_clip_max.setRange(0.5, 120)
        self.mix_clip_max.setValue(5)
        self.mix_clip_max.setSuffix(" 秒")
        self.mix_clip_count = QSpinBox()
        self.mix_clip_count.setRange(1, 100)
        self.mix_clip_count.setValue(1)
        self.mix_clip_count.setToolTip(
            "一次生成 N 个独立的剪映工程草稿，每个工程随机排列不同，"
            "各自按目标时长/片段长短装配。"
        )
        # 清晰度 / 帧率：决定草稿画布尺寸与工程帧率
        self.mix_resolution = QComboBox()
        for label, _w, _h in MIX_RESOLUTION_PRESETS:
            self.mix_resolution.addItem(label)
        self.mix_resolution.setCurrentIndex(MIX_DEFAULT_RESOLUTION_INDEX)
        self.mix_resolution.setToolTip(
            "画布尺寸决定剪映导出的清晰度；素材本身分辨率不足时，"
            "选再高也不会增加细节。"
        )
        self.mix_fps = QComboBox()
        for f in MIX_FPS_OPTIONS:
            self.mix_fps.addItem(f"{f} fps", f)
        self.mix_fps.setCurrentIndex(MIX_DEFAULT_FPS_INDEX)
        self.mix_fps.setToolTip("工程帧率，切点按秒换算到对应帧。")
        self.mix_resolution.currentIndexChanged.connect(self.update_info_label)
        self.mix_fps.currentIndexChanged.connect(self.update_info_label)
        self.update_info_label()
        self.mix_shuffle = QCheckBox("随机排列素材")
        self.mix_shuffle.setChecked(True)
        self.mix_reuse = QCheckBox("允许素材重复使用")
        self.mix_reuse.setChecked(True)
        # —— 语音整句切割 / 音频指纹查重（接 CUDA 引擎，默认关闭） ——
        self.mix_sentence_snap = QCheckBox("整句切割（切点对齐语音句尾）")
        self.mix_sentence_snap.setChecked(False)
        self.mix_sentence_snap.setToolTip(
            "对有人声的素材先做语音转写，把切点吸附到整句结尾，"
            "避免成片里半句话被切断。\n"
            "需要 CUDA 引擎（sd_asr.exe）；首次转写较慢，结果按素材缓存，"
            "同一条素材不会重复转写。"
        )
        self.mix_audio_dedup = QCheckBox("指纹查重（跳过重复素材）")
        self.mix_audio_dedup.setChecked(False)
        self.mix_audio_dedup.setToolTip(
            "用 CUDA 指纹（画面哈希 + 色彩直方图）比对素材，"
            "同一段片子的重复拷贝只保留最长的一条，避免成片来回重复。"
        )
        self.mix_openclip = QCheckBox("OpenCLIP 语义多样性评分")
        self.mix_openclip.setChecked(True)
        self.mix_openclip.setToolTip(
            "对分析页确认保留的候选片段提取中心画面，使用 OpenCLIP 语义向量"
            "降低连续相似画面的优先级。只影响候选排序，不会自动删除片段。"
            "需要 OpenCLIP 独立环境与 ViT-L-14 权重。"
        )
        # 直达「引擎能力」页（标签栏在窄窗口下会折叠，留一个稳的入口）
        self.mix_engine_link = QPushButton("查看「MIX引擎」页")
        self.mix_engine_link.setToolTip(
            "打开「MIX引擎」页：查看引擎是否在位、转写/指纹缓存有多少。"
        )
        self.mix_engine_link.clicked.connect(self.goto_engine_page)
        self.mix_diversity = QComboBox()
        self.mix_diversity.addItem("适中（推荐）", "medium")
        self.mix_diversity.addItem("严格（尽量不重复）", "strict")
        self.mix_diversity.addItem("宽松（保留随机）", "loose")
        self.mix_diversity.setToolTip(
            "相邻重复控制强度：同一素材或同一分类紧挨着出现时，"
            "严格会尽量避开，宽松只做轻微约束。"
        )
        # —— 算法策略（阶段 2 / 3 / 4） ——
        self.mix_rhythm = QComboBox()
        self.mix_rhythm.addItem("均衡时长", "balance")
        self.mix_rhythm.addItem("长短交替", "dynamic")
        self.mix_rhythm.addItem("快速切换", "fast")
        self.mix_rhythm.addItem("舒缓节奏", "slow")
        self.mix_rhythm.addItem("脉冲节奏（短长强对比）", "pulse")
        self.mix_rhythm.addItem("断奏（极短密集）", "staccato")
        self.mix_rhythm.addItem("电影感（长镜头为主）", "cinematic")
        self.mix_rhythm.setToolTip(
            "片段时长策略（受最短/最长片段约束）：均衡=长度集中，"
            "长短交替=长短穿插，快速=偏短，舒缓=偏长。"
        )
        self.mix_version = QComboBox()
        for key, preset in mixbuilder_module.VERSION_STYLES.items():
            if key == "balanced":
                self.mix_version.addItem("均衡混剪", key)
        self.mix_version.addItem("多版本轮换（快节奏/多样性/长短交替/音乐优先）", "rotate")
        self.mix_version.setToolTip(
            "生成多个工程时：选“均衡混剪”则每个工程算法一致、只换随机种子；"
            "选轮换则依次使用快节奏、多样性优先、长短交替、音乐节奏优先等策略。"
        )
        self.mix_beat_step = QComboBox()
        self.mix_beat_step.addItem("自动（按 BPM）", 0)
        self.mix_beat_step.addItem("每 1 拍切换", 1)
        self.mix_beat_step.addItem("每 2 拍切换", 2)
        self.mix_beat_step.addItem("每 4 拍切换", 4)
        self.mix_beat_step.setToolTip("卡点密度：音乐节奏优先版本会在强拍切，其余按此密度。")

        # 高级：弱拍补切概率（避免每个鼓点都机械切换）
        self.mix_weak_chance = QDoubleSpinBox()
        self.mix_weak_chance.setRange(0.0, 1.0)
        self.mix_weak_chance.setSingleStep(0.05)
        self.mix_weak_chance.setDecimals(2)
        self.mix_weak_chance.setValue(0.25)
        self.mix_weak_chance.setToolTip(
            "卡点时允许落在弱拍的概率，越低越只在强拍切，节奏更稳；"
            "越高切点越密。")

        # 高级：卡点容差（切点与鼓点允许的偏差秒数）
        self.mix_beat_tolerance = QDoubleSpinBox()
        self.mix_beat_tolerance.setRange(0.02, 0.5)
        self.mix_beat_tolerance.setSingleStep(0.02)
        self.mix_beat_tolerance.setDecimals(2)
        self.mix_beat_tolerance.setValue(0.12)
        self.mix_beat_tolerance.setToolTip(
            "切点对齐鼓点时允许的时间偏差，越大越容易贴合，"
            "越小越精确但可能找不到落点。")
        bform.addRow("清晰度", self.mix_resolution)
        bform.addRow("帧率", self.mix_fps)
        bform.addRow("目标时长", self.mix_target)
        bform.addRow("时长浮动 ±", self.mix_tolerance)
        bform.addRow("生成工程数", self.mix_clip_count)
        bform.addRow("最短片段", self.mix_clip_min)
        bform.addRow("最长片段", self.mix_clip_max)
        bform.addRow("重复控制", self.mix_diversity)
        bform.addRow("时长策略", self.mix_rhythm)
        bform.addRow("版本策略", self.mix_version)
        bform.addRow("卡点密度", self.mix_beat_step)
        bform.addRow("", self.mix_shuffle)
        bform.addRow("", self.mix_reuse)
        bform.addRow("", self.mix_sentence_snap)
        bform.addRow("", self.mix_audio_dedup)
        bform.addRow("", self.mix_engine_link)

        # —— 草稿位置（可自定义输出文件夹，方便打包传给别人） ——
        loc_group = QGroupBox("草稿位置")
        loc_lay = QVBoxLayout(loc_group)
        loc_tip = QLabel("选择剪映工程路径")
        loc_tip.setWordWrap(True)
        loc_lay.addWidget(loc_tip)
        loc_row = QHBoxLayout()
        self.mix_draft_dir = QLineEdit()
        self.mix_draft_dir.setPlaceholderText("默认剪映草稿目录（点击右侧选择自定义位置）")
        loc_btn = QPushButton("选择位置")
        loc_reset = QPushButton("恢复默认")
        loc_row.addWidget(self.mix_draft_dir)
        loc_row.addWidget(loc_btn)
        loc_row.addWidget(loc_reset)
        loc_lay.addLayout(loc_row)

        # 中栏：智能粗剪筛选引擎（放在素材库旁边）
        filter_col = QWidget()
        filter_lay = QVBoxLayout(filter_col)
        filter_lay.setContentsMargins(0, 0, 0, 0)
        self._build_filter_panel(filter_lay)

        # 右栏：参数配置 + 草稿位置
        right_col = QWidget()
        right_lay = QVBoxLayout(right_col)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(base_group)
        right_lay.addWidget(loc_group)
        right_lay.addStretch()

        top_row.addWidget(filter_col, 2)
        top_row.addWidget(right_col, 2)
        # 整体左移：收紧左右外边距
        top_row.setContentsMargins(2, 2, 18, 2)
        root.addLayout(top_row)

        # —— 废镜头检测（混剪同样先剔除） ——
        mdet_group = QGroupBox("废镜头检测")
        mform = QFormLayout(mdet_group)
        self.mix_video_detect = QCheckBox("画面检测")
        self.mix_video_detect.setChecked(True)
        self.mix_audio_detect = QCheckBox("静音检测")
        self.mix_audio_detect.setChecked(True)
        mform.addRow(self.mix_video_detect)
        mform.addRow(self.mix_audio_detect)

        # 检测阈值：真正传进引擎，也参与分析缓存键
        self.detect_threshold_boxes = {}
        for key, label, lo, hi, step, default, tip in (
            ("black_min_duration", "黑屏最短时长", 0.1, 10.0, 0.1,
             analysis_core.DEFAULT_OPTIONS["black_min_duration"],
             "连续黑屏超过该时长才判为废镜头"),
            ("freeze_min_duration", "静止最短时长", 0.1, 20.0, 0.1,
             analysis_core.DEFAULT_OPTIONS["freeze_min_duration"],
             "画面连续静止超过该时长才判为废镜头"),
            ("silence_min_duration", "静音最短时长", 0.1, 20.0, 0.1,
             analysis_core.DEFAULT_OPTIONS["silence_min_duration"],
             "连续无声超过该时长才判为废镜头"),
        ):
            box = QDoubleSpinBox()
            box.setRange(lo, hi)
            box.setSingleStep(step)
            box.setDecimals(1)
            box.setValue(float(default))
            box.setSuffix(" 秒")
            box.setToolTip(tip)
            self.detect_threshold_boxes[key] = box
            mform.addRow(label, box)

        root.addWidget(mdet_group)

        # —— 配乐卡点 ——
        music_group = QGroupBox("自动配乐 / 卡点 / 闪避")
        mlay = QVBoxLayout(music_group)

        # 配乐方式：手动 / 本地随机 / 云端（占位）
        self.music_mode = QComboBox()
        self.music_mode.addItems(
            ["手动选择 BGM", "本地随机配乐", "云端随机（暂未开放）"]
        )
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("配乐方式"))
        mode_row.addWidget(self.music_mode)
        mlay.addLayout(mode_row)

        # 手动 BGM
        bgm_row = QHBoxLayout()
        self.mix_bgm = QLineEdit()
        self.mix_bgm.setPlaceholderText("选择音频文件")
        bgm_btn = QPushButton("选择音频")
        bgm_row.addWidget(self.mix_bgm)
        bgm_row.addWidget(bgm_btn)
        mlay.addLayout(bgm_row)

        # 本地随机文件夹
        folder_row = QHBoxLayout()
        self.music_folder = QLineEdit()
        self.music_folder.setPlaceholderText("本地音乐文件夹")
        music_folder_btn = QPushButton("选择文件夹")
        folder_row.addWidget(self.music_folder)
        folder_row.addWidget(music_folder_btn)
        mlay.addLayout(folder_row)

        opt_row = QHBoxLayout()
        self.mix_beat_sync = QCheckBox("卡点")
        self.mix_beat_sync.setChecked(True)
        self.mix_ducking = QCheckBox("音频闪避")
        self.mix_ducking.setChecked(True)
        opt_row.addWidget(self.mix_beat_sync)
        opt_row.addWidget(self.mix_ducking)
        opt_row.addStretch()
        mlay.addLayout(opt_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("正常音量"))
        self.mix_base_vol = QDoubleSpinBox()
        self.mix_base_vol.setRange(0, 1)
        self.mix_base_vol.setSingleStep(0.05)
        self.mix_base_vol.setValue(1.0)
        vol_row.addWidget(self.mix_base_vol)
        vol_row.addWidget(QLabel("闪避音量"))
        self.mix_duck_vol = QDoubleSpinBox()
        self.mix_duck_vol.setRange(0, 1)
        self.mix_duck_vol.setSingleStep(0.05)
        self.mix_duck_vol.setValue(0.35)
        vol_row.addWidget(self.mix_duck_vol)
        vol_row.addStretch()
        mlay.addLayout(vol_row)
        root.addWidget(music_group)

        # —— 转场规则 ——
        tr_group = QGroupBox("自动转场 / 黑名单")
        tform = QFormLayout(tr_group)
        self.mix_trans_enable = QCheckBox("启用规则转场")
        self.mix_trans_enable.setChecked(True)
        self.mix_short = QDoubleSpinBox()
        self.mix_short.setRange(0.2, 10)
        self.mix_short.setValue(2.0)
        self.mix_short.setSuffix(" 秒")
        self.mix_long = QDoubleSpinBox()
        self.mix_long.setRange(0.5, 30)
        self.mix_long.setValue(4.0)
        self.mix_long.setSuffix(" 秒")
        self.mix_dissolve_dur = QDoubleSpinBox()
        self.mix_dissolve_dur.setRange(0.1, 3)
        self.mix_dissolve_dur.setValue(0.4)
        self.mix_dissolve_dur.setSuffix(" 秒")
        self.mix_blacklist = QLineEdit("")
        self.mix_blacklist.setToolTip(
            "逗号分隔的分类名，命中的片段之间只硬切。"
        )

        # 转场黑名单：可多选的关键词，与输入框双向同步（后端仍读输入框，无需改算法）
        from mixcut.ui.components import TagMultiSelect
        blacklist_keywords = [
            "产品特写", "特写", "近景", "中景", "远景",
            "走路", "转身", "站立", "坐着",
            "模糊", "过曝", "过暗", "手抖", "黑场",
            "人物", "空镜", "街景",
        ]
        self.blacklist_picker = TagMultiSelect(blacklist_keywords, columns=4)

        def _sync_picker_to_line(kws):
            self.mix_blacklist.setText(",".join(kws))

        def _sync_line_to_picker():
            kws = [x.strip() for x in
                   self.mix_blacklist.text().split(",") if x.strip()]
            self.blacklist_picker.set_selected(kws)

        self.blacklist_picker.selection_changed.connect(
            _sync_picker_to_line)
        self.mix_blacklist.textChanged.connect(_sync_line_to_picker)
        tform.addRow(self.mix_trans_enable)
        tform.addRow("短镜头阈值（硬切）", self.mix_short)
        tform.addRow("长镜头阈值（叠化）", self.mix_long)
        tform.addRow("叠化时长", self.mix_dissolve_dur)
        tform.addRow("黑名单（逗号分隔）", self.mix_blacklist)
        root.addWidget(tr_group)

        # —— 操作 ——
        action_row = QHBoxLayout()
        self.mix_start_btn = QPushButton("开始智能混剪")
        self.mix_start_btn.setMinimumHeight(48)
        self.mix_start_btn.setStyleSheet(
            "background:#FFFFFF;color:#101113;font-size:16px;font-weight:600;"
            "font-weight:700;border-radius:7px;"
        )
        self.mix_cancel_btn = QPushButton("取消")
        self.mix_cancel_btn.setEnabled(False)
        action_row.addWidget(self.mix_start_btn, 4)
        action_row.addWidget(self.mix_cancel_btn, 1)
        root.addLayout(action_row)

        self.mix_progress = QProgressBar()
        root.addWidget(self.mix_progress)

        self.mix_log = QPlainTextEdit()
        self.mix_log.setReadOnly(True)
        self.mix_log.setMaximumBlockCount(500)
        self.mix_log.setStyleSheet(
            "background:#0D0E10;color:#92969E;font-family:Consolas;"
        )
        root.addWidget(self.mix_log, 1)

        self.tabs.addTab(
            self._wrap_scroll(self.tab_mix), "智能混剪"
        )

        # —— 引擎能力页：Super Director 移植过来的三项能力 ——
        engine_page = QWidget()
        eroot = QVBoxLayout(engine_page)
        ehead = QLabel("MIX引擎")
        ehead.setStyleSheet("font-size:18px;font-weight:600;")
        eroot.addWidget(ehead)
        esub = QLabel(
            "三项能力从 Super Director 移植而来，开关在「智能混剪」页底部："
            "「整句切割（切点对齐语音句尾）」和「指纹查重（跳过重复素材）」。"
            "这一页显示引擎是否在位、缓存有多少，以及每项能力负责什么。"
        )
        esub.setWordWrap(True)
        eroot.addWidget(esub)

        st_group = QGroupBox("运行状态")
        st_lay = QVBoxLayout(st_group)
        self.engine_status = QLabel("尚未检查")
        self.engine_status.setWordWrap(True)
        self.engine_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.engine_status.setStyleSheet(
            "font-family:Consolas;font-size:13px;"
        )
        st_lay.addWidget(self.engine_status)
        est_row = QHBoxLayout()
        erefresh_btn = QPushButton("刷新状态")
        edoc_btn = QPushButton("打开说明页")
        ecache_btn = QPushButton("打开缓存目录")
        est_row.addWidget(erefresh_btn)
        est_row.addWidget(edoc_btn)
        est_row.addWidget(ecache_btn)
        est_row.addStretch(1)
        st_lay.addLayout(est_row)
        eroot.addWidget(st_group)

        cap_group = QGroupBox("三项能力")
        cap_lay = QVBoxLayout(cap_group)
        for cap_title, cap_text in (
            ("① 整句切割（切点对齐语音句尾）",
             "对有人声的素材先做语音转写，拿到整句时间轴，再把混剪算出的切点吸附到"
             "最近的句尾——只在最短/最长片段允许的区间内寻找，不打乱节奏。"
             "实测同素材同种子下，切点离句尾的最大偏差从 1.37 秒降到 0.11 秒。"
             "首次需转写，结果按素材缓存，同一条素材不会重复转写。"),
            ("② 指纹查重（跳过重复素材）",
             "用 CUDA 引擎为每条素材提取指纹（关键帧 phash + 色彩直方图），"
             "在本地比对，综合相似度 ≥ 0.92 判为重复；同一组里保留时长最长的一条，"
             "其余跳过并在日志里点名。实测同一文件副本 1.000，不同素材 0.000。"
             "时长比超过 1.4 的两条直接判为不同片子，省掉比对开销。"),
            ("③ CUDA 引擎桥（进程内 sd_fp.dll + 外置 sd_asr.exe）",
             "MixCut 用的是 pip 版 OpenCV，cv2.cuda 恒为 0 个设备，Python 侧拿不到 GPU 算子。"
             "因此 GPU 计算交给随包的引擎：指纹提取由 sd_fp.dll 在 MixCut 进程内直接调用"
             "（CUDA 13.5 + OpenCV CUDA 编译），不再为每条素材起一次 sd_dedup.exe；"
             "语音转写仍由 sd_asr.exe 完成。DLL 缺失或 CUDA 初始化失败时自动回退到外置 "
             "sd_dedup.exe，当前实际后端显示在引擎页状态区。"),
        ):
            ctitle = QLabel(cap_title)
            ctitle.setStyleSheet("font-weight:600;")
            ctext = QLabel(cap_text)
            ctext.setWordWrap(True)
            ctext.setStyleSheet("color:#8B909A;")
            cap_lay.addWidget(ctitle)
            cap_lay.addWidget(ctext)
        eroot.addWidget(cap_group)
        eroot.addStretch(1)

        erefresh_btn.clicked.connect(self.refresh_engine_status)
        edoc_btn.clicked.connect(self.open_engine_doc)
        ecache_btn.clicked.connect(self.open_engine_cache)
        self.tabs.addTab(self._wrap_scroll(engine_page), "MIX引擎")
        self.engine_page = self.tabs.widget(self.tabs.count() - 1)
        self.refresh_engine_status()

        bgm_btn.clicked.connect(self.choose_bgm)
        music_folder_btn.clicked.connect(self.choose_music_folder)
        self.music_mode.currentIndexChanged.connect(self._on_music_mode)
        self._on_music_mode()
        self.mix_start_btn.clicked.connect(self.start_mix)
        self.mix_cancel_btn.clicked.connect(self.cancel_mix)
        loc_btn.clicked.connect(self.choose_draft_dir)
        loc_reset.clicked.connect(
            lambda: self.mix_draft_dir.clear()
        )

    def choose_draft_dir(self):
        start = self.mix_draft_dir.text() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "选择草稿保存位置", start
        )
        if folder:
            self.mix_draft_dir.setText(folder)

    def choose_bgm(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 BGM", "",
            "音频 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
        )
        if path:
            self.mix_bgm.setText(path)

    def choose_music_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择本地音乐文件夹", self.music_folder.text()
        )
        if folder:
            self.music_folder.setText(folder)

    def _on_music_mode(self):
        mode = self.music_mode.currentIndex()
        # 0 手动 / 1 本地随机 / 2 云端占位
        self.mix_bgm.setEnabled(mode == 0)
        self.music_folder.setEnabled(mode == 1)

    AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

    def _resolve_bgm(self):
        """按配乐方式返回 (bgm_path, error_message)。"""
        mode = self.music_mode.currentIndex()
        if mode == 0:
            path = self.mix_bgm.text().strip()
            if not path:
                return "", None
            audio_path = Path(path)
            if not audio_path.is_file():
                return "", "所选 BGM 文件不存在，请重新选择。"
            if audio_path.suffix.lower() not in self.AUDIO_EXTS:
                return "", "BGM 格式不受支持，请选择 MP3、WAV、M4A、AAC、FLAC 或 OGG。"
            return str(audio_path.resolve()), None
        if mode == 2:
            return "", None  # 云端暂未开放，不加载音乐
        # 本地随机
        folder = self.music_folder.text().strip()
        if not folder or not Path(folder).is_dir():
            return "", "本地随机配乐：请先选择有效的音乐文件夹。"
        candidates = [
            str(p) for p in Path(folder).rglob("*")
            if p.is_file() and p.suffix.lower() in self.AUDIO_EXTS
        ]
        if not candidates:
            return "", "该文件夹里没有可用的音频文件。"
        return random.choice(candidates), None

    def collect_source_tags(self, paths):
        """把素材所在的虚拟分类名映射成 {path: [分类名]}。

        素材树里的顶层节点是用户新建的分类，子节点才是素材；只取分类名，
        不生成任何内容语义标签。没有归类的素材不出现在结果里。

        注意：QTreeWidgetItemIterator 不能用来判定“属于哪个分类”——它同一
        时间只维护一个包装对象，取到父节点后子节点的包装会被复用。因此这里
        用 parent() 链向上找分类节点。
        """
        wanted = {str(p) for p in paths}
        result = {}
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            path = item.data(1, Qt.UserRole)
            if isinstance(path, str) and path in wanted:
                category = self._category_of(item)
                if category:
                    tags = result.setdefault(path, [])
                    if category not in tags:
                        tags.append(category)
            iterator += 1
        return result

    @staticmethod
    def _category_of(item):
        """沿父链找到最近的分类节点名（顶层、没有素材路径的节点）。"""
        node = item.parent()
        while node is not None:
            if not node.data(1, Qt.UserRole):
                name = node.text(0).strip()
                if name:
                    return name
            node = node.parent()
        return None

    def _mix_detect_options(self):
        """传给混剪时间轴的检测选项（与筛选引擎保持一致）。

        背景检测在引擎里已经完成，这里主要影响有效区间计算（min_keep 等）
        与旧检测路径；画面/静音开关和阈值必须与用户勾选的一致，不能写死。
        """
        config = self._collect_filter_config()
        return {
            **analysis_core.DEFAULT_OPTIONS,
            **config["ffmpeg_options"],
        }

    def start_mix(self):
        ap = getattr(self, "page_analyze", None)
        has_analysis = bool(ap is not None and getattr(ap, "_results", None))
        pool = ap.mix_pool_paths() if hasattr(ap, "mix_pool_paths") else []
        if has_analysis:
            # 分析页有结果时，混剪池是唯一入口：池里没片段就不能开混剪。
            paths = pool
            if not paths:
                QMessageBox.warning(
                    self, "混剪池为空",
                    "当前没有片段被加入混剪池。\n"
                    "请回到「素材分析」页，在多选片段后点「加入混剪池」，"
                    "或勾选片段卡片右上角的「池」。")
                return
        else:
            paths = self.mix_selector.selected_paths()
        if not paths:
            QMessageBox.warning(self, "提示", "请先在素材选择框架勾选要混剪的视频。")
            return

        # 片段级唯一来源：混剪只使用分析页已确认的片段，不再自行分析素材。
        fragment_pool = (ap.fragment_pool(paths, pool_only=True)
                         if ap is not None else [])
        analyzed_paths = {f.get("path") for f in fragment_pool}
        missing = [p for p in paths if p not in analyzed_paths]
        if missing:
            names = "、".join(os.path.basename(p) for p in missing[:5])
            more = f" 等 {len(missing)} 条" if len(missing) > 5 else ""
            QMessageBox.warning(
                self, "缺少分析结果",
                f"以下素材还没有分析结果：{names}{more}。\n\n"
                "混剪只使用“分析”页确认过的片段，请先完成分析后再开始混剪。")
            return
        if not any(bool(f.get("included", True)) for f in fragment_pool):
            QMessageBox.warning(
                self, "没有可用片段",
                "所选素材的片段全部被标记为“不加入”。\n\n"
                "请在“分析”页把要用的片段加回，或勾选其他素材。")
            return

        # 参数校验必须发生在创建后台线程之前，避免无效任务进入引擎。
        clip_min = self.mix_clip_min.value()
        clip_max = self.mix_clip_max.value()
        if clip_min > clip_max:
            QMessageBox.warning(
                self, "参数错误", "最短片段不能大于最长片段。"
            )
            return

        short_threshold = self.mix_short.value()
        long_threshold = self.mix_long.value()
        if short_threshold > long_threshold:
            QMessageBox.warning(
                self, "参数错误",
                "短镜头阈值不能大于长镜头阈值，请重新调整。"
            )
            return

        # 启动前强制自检：重新导入组件、刷新状态；ffmpeg 缺失则禁止启动
        if not self.run_force_self_check(show_dialog=False):
            QMessageBox.critical(
                self, "环境自检失败",
                "找不到 ffmpeg，无法进行废镜头检测，请先修复环境。"
            )
            return
        # 若有勾选但依赖缺失的组件，提示一次（不阻断，引擎会自动跳过）
        missing = fe.missing_modules(self._collect_filter_config())
        if missing:
            QMessageBox.warning(
                self, "部分模块不可用",
                "以下模块未安装，本次将自动跳过：\n" + "\n".join(missing)
            )

        # 素材总数不足目标时长、且不允许重复使用：提前说清楚，而不是悄悄生成一条更短的片子
        if not self.mix_reuse.isChecked():
            available = self._available_footage(paths)
            need = self.mix_target.value() - self.mix_tolerance.value()
            if available is not None and available < need:
                QMessageBox.warning(
                    self, "素材可能不足",
                    f"所选素材合计可用时长约 {available:.1f} 秒，"
                    f"低于目标时长下限 {need:.1f} 秒，且未勾选“允许素材重复使用”。\n\n"
                    "成片将会比目标时长短。可勾选重复使用、增加素材，或降低目标时长。"
                )

        bgm_path, bgm_error = self._resolve_bgm()
        if bgm_error:
            QMessageBox.warning(self, "提示", bgm_error)
            return
        music_enabled = bool(bgm_path)

        # 新 UI（demo 参数页）会直接给画布尺寸：横屏/4K 在旧预设里没有对应项，
        # 用覆盖值避免按索引套预设把画幅选错。旧界面不走这里，保持原逻辑。
        canvas_override = getattr(self, "_mix_canvas_override", None)
        if canvas_override:
            width, height, fps = canvas_override
        else:
            _, width, height = MIX_RESOLUTION_PRESETS[
                self.mix_resolution.currentIndex()
            ]
            fps = MIX_FPS_OPTIONS[self.mix_fps.currentIndex()]
        canvas = tl_mod.create_canvas(width, height, fps)

        mix_options = {
            "target_duration": self.mix_target.value(),
            "duration_tolerance": self.mix_tolerance.value(),
            "project_count": self.mix_clip_count.value(),
            "clip_min": self.mix_clip_min.value(),
            "clip_max": self.mix_clip_max.value(),
            "shuffle": self.mix_shuffle.isChecked(),
            "reuse": self.mix_reuse.isChecked(),
            "beat_sync": self.mix_beat_sync.isChecked(),
            # 整句切割：切点吸附到语音句尾（mixbuilder 读取）
            "sentence_snap": bool(
                getattr(self, "mix_sentence_snap", None)
                and self.mix_sentence_snap.isChecked()
            ),
            # 用户自建的虚拟分类作为素材标签，供相邻重复控制使用。
            # 没有分类的素材不带标签（不虚构内容语义）。
            "tags_by_source": self.collect_source_tags(paths),
            "diversity": self.mix_diversity.currentData()
            if hasattr(self, "mix_diversity") else "medium",
            "rhythm": self.mix_rhythm.currentData()
            if hasattr(self, "mix_rhythm") else "balance",
            "beat_step": self.mix_beat_step.currentData()
            if hasattr(self, "mix_beat_step") else 0,
            "weak_beat_chance": self.mix_weak_chance.value()
            if hasattr(self, "mix_weak_chance") else 0.25,
            "openclip_enabled": bool(
                getattr(self, "mix_openclip", None)
                and self.mix_openclip.isChecked()),
            "openclip_weight": 2.0,
            "beat_tolerance": self.mix_beat_tolerance.value()
            if hasattr(self, "mix_beat_tolerance") else 0.12,
        }
        # 版本策略：单个工程走固定策略；多工程可轮换不同算法倾向
        version_choice = (
            self.mix_version.currentData()
            if hasattr(self, "mix_version") else "balanced"
        )
        if version_choice and version_choice != "rotate":
            mix_options["version_style"] = version_choice
            mix_options["_rotate_versions"] = False
        else:
            mix_options["_rotate_versions"] = True
        custom_draft_dir = self.mix_draft_dir.text().strip()
        music_settings = {
            "enabled": music_enabled,
            "path": bgm_path,
            "ducking": self.mix_ducking.isChecked(),
            "base_volume": self.mix_base_vol.value(),
            "duck_volume": self.mix_duck_vol.value(),
            "output": "",
            # 自定义草稿位置：非空时草稿输出到该目录而非默认剪映目录
            "draft_dir": custom_draft_dir,
            "draft_dir_custom": bool(custom_draft_dir),
        }
        blacklist = [
            x.strip() for x in self.mix_blacklist.text().split(",") if x.strip()
        ]
        transition_options = {
            "enabled": self.mix_trans_enable.isChecked(),
            "short_threshold": self.mix_short.value(),
            "long_threshold": self.mix_long.value(),
            "dissolve_duration": self.mix_dissolve_dur.value(),
            "blacklist": blacklist,
        }

        self.mix_start_btn.setEnabled(False)
        self.mix_cancel_btn.setEnabled(True)
        self.mix_progress.setValue(0)
        self.mix_log.clear()
        self.mix_log.appendPlainText("初始化智能混剪任务…")
        if self.music_mode.currentIndex() == 2:
            self.mix_log.appendPlainText(
                "云端随机配乐暂未开放，本次不加载音乐。"
            )
        if music_enabled:
            self.mix_log.appendPlainText(f"使用配乐：{Path(bgm_path).name}")
        if custom_draft_dir:
            self.mix_log.appendPlainText(f"草稿输出到自定义位置：{custom_draft_dir}")
        else:
            self.mix_log.appendPlainText("草稿输出到剪映默认草稿目录。")

        self._mix_canceled = False
        self._mix_last_failed = False
        worker = mc_workers.MixBuildWorker(
            paths, canvas, self._mix_detect_options(),
            mix_options, music_settings, transition_options,
            analysis_cache=self.analysis_cache,
            filter_config=self._collect_filter_config(),
            fragment_pool=fragment_pool,
        )
        # 线程归属主窗口：窗口析构前会先走 closeEvent 里的停止流程
        worker.setParent(self)
        worker.progress.connect(self._mix_on_progress)
        worker.built.connect(self.mix_on_built)
        worker.failed.connect(self.mix_on_failed)
        worker.finished.connect(self._mix_on_finished)
        # 逐素材分析结果 → 素材卡片状态（真实检测结果）
        worker.source_analyzed.connect(self._on_source_analyzed)
        # 真实暂停状态 → 任务卡按钮与导航状态
        worker.paused_changed.connect(self._on_worker_paused)
        self.mix_worker = worker

        page = getattr(self, "page_library", None)
        if page is not None:
            page.reset_analysis(paths)

        # 任务卡开始记录（时间来自真实开始时刻）
        shell = getattr(self, "shell", None)
        if shell is not None:
            shell.nav.set_status("运行中", "warning")
        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            card = tasks.start_task(f"智能混剪 · {len(paths)} 个素材")
            tasks.log(f"开始混剪：{len(paths)} 个素材，"
                      f"{mix_options['project_count']} 个工程", "info")
            # 任务卡上的暂停/继续/取消 → worker 的真实接口
            card.pause_requested.connect(self._pause_mix)
            card.resume_requested.connect(self._resume_mix)
            card.cancel_requested.connect(self.cancel_mix)
        worker.start()

    # ---- 新界面：素材参数探测线程 ----
    def _ensure_probe_worker(self):
        worker = getattr(self, "probe_worker", None)
        if worker is None:
            worker = mc_workers.ProbeWorker(self)
            worker.probed.connect(self._on_probe_result)
            worker.probe_failed.connect(self._on_probe_failed)
            worker.thumbnail_ready.connect(self._on_thumbnail_ready)
            self.probe_worker = worker
        return worker

    def _on_probe_result(self, path, details):
        page = getattr(self, "page_library", None)
        if page is not None:
            page.set_probe_result(path, details)
        # 顺便在主线程里缓存一份，详情面板可以复用（不再重复调 ffprobe）
        cache = getattr(self, "_probe_cache", None)
        if cache is None:
            cache = self._probe_cache = {}
        cache[path] = details
        if hasattr(self, "demo_library"):
            self._update_demo_metadata(path)

    def _on_probe_failed(self, path, error):
        page = getattr(self, "page_library", None)
        if page is not None:
            page.set_probe_result(path, None, error)

    def _on_thumbnail_ready(self, path, image_path):
        page = getattr(self, "page_library", None)
        if page is not None:
            page.set_thumbnail(path, image_path)
        if hasattr(self, "demo_library"):
            self._update_demo_thumbnail(path, image_path)

    def _on_source_analyzed(self, path, status, detail):
        """MixBuildWorker 的逐素材分析事件 → 素材卡片状态。"""
        page = getattr(self, "page_library", None)
        if page is not None:
            page.set_analysis_status(path, status, detail)
        _ = detail  # 详情目前只用于卡片提示

    def _available_footage(self, paths):
        """粗估所选素材的可用秒数；任一路径出错就返回 None（不做提示）。"""
        try:
            total = 0.0
            for path in paths:
                total += float(ft.probe_duration(path))
        except Exception:
            return None
        return total

    def _mix_on_progress(self, value, message):
        self.mix_progress.setValue(value)
        self.mix_log.appendPlainText(message)
        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            tasks.update_progress(value, message)

    def cancel_mix(self):
        """取消：任何状态下都能用（运行中 / 暂停中 / 检测阶段）。"""
        worker = self.mix_worker
        if worker is not None and worker.isRunning():
            self._mix_canceled = True
            worker.shutdown()
            self.mix_log.appendPlainText("正在取消…")
            if getattr(self, "_log_sink", None):
                self._log_sink("正在取消…", "warn")
            tasks = getattr(self, "demo_tasks", None)
            if tasks is not None and tasks.current_card() is not None:
                tasks.current_card().step.setText("正在取消…")

    def _pause_mix(self):
        worker = self.mix_worker
        if worker is not None and worker.isRunning():
            worker.pause()
            self.mix_log.appendPlainText("任务已暂停")
            if getattr(self, "_log_sink", None):
                self._log_sink("任务已暂停", "warn")

    def _resume_mix(self):
        worker = self.mix_worker
        if worker is not None and worker.isRunning():
            worker.resume()
            self.mix_log.appendPlainText("任务已继续")
            if getattr(self, "_log_sink", None):
                self._log_sink("任务已继续", "info")

    def _on_worker_paused(self, paused, message):
        """worker 真实暂停状态 → 任务卡与导航状态。"""
        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            tasks.set_paused(paused, message)
        shell = getattr(self, "shell", None)
        if shell is not None:
            if paused:
                shell.nav.set_status("已暂停", "warning")
            else:
                shell.nav.set_status("运行中", "warning")

    def _mix_on_finished(self):
        """QThread 结束（成功/失败/取消）必触发：恢复按钮，取消时给出提示。"""
        worker = self.mix_worker
        was_canceled = bool(self._mix_canceled) or (
            worker is not None and worker.isInterruptionRequested()
        )
        self.mix_start_btn.setEnabled(True)
        self.mix_cancel_btn.setEnabled(False)
        if was_canceled:
            self.mix_progress.setValue(0)
            self.mix_log.appendPlainText(
                "已取消，可重新调整参数后再次点击开始。"
            )
        # 导航栏运行状态 + 任务卡状态跟真实结束信号同步
        shell = getattr(self, "shell", None)
        if shell is not None:
            if was_canceled:
                shell.nav.set_status("已取消", "idle")
            elif getattr(self, "_mix_last_failed", False):
                shell.nav.set_status("任务失败", "error")
            else:
                shell.nav.set_status("就绪", "success")
        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            if was_canceled:
                tasks.finish_task("canceled", "已取消")
            elif getattr(self, "_mix_last_failed", False):
                tasks.finish_task("failed", "处理失败，详情见日志")
            else:
                tasks.finish_task("done", "已完成")
        self._mix_last_failed = False
        self._mix_canceled = False
        # 线程已结束，释放对象（下次开始会重新创建）
        if worker is not None:
            worker.deleteLater()
            self.mix_worker = None

    def mix_on_built(self, result):
        self.mix_start_btn.setEnabled(True)
        self.mix_cancel_btn.setEnabled(False)
        self.mix_progress.setValue(100)

        items = result.get("results", [result])
        for item in items:
            message = (
                f"工程完成：{item['draft_name']}"
                f"（{item['clip_count']} 片段 / {item.get('duration', 0):.2f} 秒）"
            )
            self.mix_log.appendPlainText(message)
            if getattr(self, "_log_sink", None):
                self._log_sink(message, "info")

        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            tasks.set_last_outputs(
                items, str(Path(items[0]["draft_path"]).parent) if items else "")
        self._last_results = items

        voice = ""
        if self.mix_beat_sync.isChecked() and result.get("beat_sync") is False:
            voice = "\n注意：BGM 未检测到可用鼓点，本次未卡点。"

        n = result.get("project_count", len(items))
        names = "\n".join(f"{i+1}. {r['draft_name']}"
                          for i, r in enumerate(items))
        target = float(result.get("target_duration") or 0.0)
        made = float(result.get("duration") or 0.0)
        gap_hint = ""
        if target > 0 and made + 1e-6 < target - self.mix_tolerance.value():
            gap_hint = (
                f"\n注意：成片约 {made:.1f} 秒，短于目标 {target:.0f} 秒。"
                "不可重复使用素材时受可用素材总量限制。"
            )
            if getattr(self, "_log_sink", None):
                self._log_sink(gap_hint.strip(), "warn")
        QMessageBox.information(
            self, "完成",
            f"已生成 {n} 个独立剪映工程，可在剪映中分别二次编辑。\n\n"
            f"{names}\n\n保存目录：\n{Path(items[0]['draft_path']).parent}"
            f"{gap_hint}{voice}",
        )

    def mix_on_failed(self, message):
        self.mix_start_btn.setEnabled(True)
        self.mix_cancel_btn.setEnabled(False)
        self.mix_progress.setValue(0)
        self._mix_last_failed = True
        self.mix_log.appendPlainText(f"发生错误：\n{message}")
        if getattr(self, "_log_sink", None):
            self._log_sink(f"发生错误：\n{message}", "error")
        QMessageBox.critical(self, "失败", "智能混剪出错，请查看日志。")


    # =================================================
    # 工作站占位页
    # =================================================

    def _build_workstation_tab(self):
        """工作站占位页：仅展示未开放提示，不挂任何交互逻辑。"""
        page = QWidget()
        layout = QVBoxLayout(page)

        placeholder = QLabel("工作站尚未开放，敬请期待。")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color:#9ba7ba;font-size:18px;")
        layout.addWidget(placeholder)
        layout.addStretch()

        self.tabs.addTab(
            self._wrap_scroll(page), "工作站（未开放）"
        )

    # =================================================
    # 播放器
    # =================================================

    def _init_player(self):

        self.player = QMediaPlayer()

        self.audio_output = QAudioOutput()

        self.player.setAudioOutput(
            self.audio_output
        )

        self.player.setVideoOutput(
            self.video_widget
        )

        self.play_btn.clicked.connect(
            self._user_play
        )

        self.pause_btn.clicked.connect(
            self._user_pause
        )

        self.stop_btn.clicked.connect(
            self._user_stop
        )

        self.player.errorOccurred.connect(
            self.on_player_error
        )

        # 进度条信号连接（player 创建后立刻连，不能放在 eventFilter 里）
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_preview_media_status)
        # seek_slider 只在播放器卡片里创建（新界面由 _build_player_card 建）。
        seek_slider = getattr(self, "seek_slider", None)
        if seek_slider is not None:
            seek_slider.sliderReleased.connect(self._on_seek)

        # demo 工作台首次进入时载入当前素材并预热首帧，避免预览区一直黑屏。
        if hasattr(self, "demo_workbench"):
            self.demo_workbench.attach_player()
            cur = self.controller.current_clip()
            if cur.get("path"):
                self.show_clip_preview(cur["path"])

        # 给 video_widget 装事件过滤器：全屏时也能收到 Esc / 空格
        self.video_widget.installEventFilter(self)

    # ---- 预览首帧预热 ----

    def show_clip_preview(self, path):
        """切换预览源并预热首帧。

        QMediaPlayer 只有真正播放过一次，才会把解码帧交给 QVideoWidget；
        单纯 setSource + pause 会一直停在黑屏。这里用“播放极短一段再暂停”的方式
        让首帧落到预览控件上，再把位置回到 0。预热全程静音，结束后停在暂停态，
        不会出声、也不会在后台继续播放。
        预览代理开启时，缓存在就用代理文件；没有则先播原片并后台生成。
        """
        if not path or getattr(self, "player", None) is None:
            return
        # 换素材等于一次新的预览请求：先取消上一次的预热收尾，状态从暂停开始。
        self._user_play_requested = False
        self._prime_pending = False
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._preview_path = path
        self._ensure_proxy_listener()
        try:
            from mixcut import preview_proxy
            src = preview_proxy.resolve(path)
            if preview_proxy.get_width() and src == path:
                self._set_proxy_status("代理生成中…（先播原片）")
            elif src != path:
                self._set_proxy_status("代理已启用")
            else:
                self._set_proxy_status("")
        except Exception:
            src = path
        self._apply_preview_source(src, keep_position=False)

    def _ensure_proxy_listener(self):
        if getattr(self, "_proxy_listener_added", False):
            return
        try:
            from mixcut import preview_proxy
            preview_proxy.add_listener(self._on_proxy_event)
            self._proxy_listener_added = True
        except Exception:
            pass

    def _set_proxy_status(self, text):
        wb = getattr(self, "demo_workbench", None)
        setter = getattr(wb, "set_proxy_status", None)
        if callable(setter):
            setter(text)

    def _kick_proxy_queue(self):
        """开启代理后，把素材库里的全部视频一次性排队生成，不用一条条点。"""
        try:
            from mixcut import preview_proxy
            width = preview_proxy.get_width()
            if not width:
                return 0
            paths = [p for p in (self._probe_paths() or []) if p]
            n = preview_proxy.request_all(paths)
        except Exception:
            return 0
        if n:
            self._set_proxy_status("已排队 %d 条素材生成代理…" % n)
        return n

    def _on_proxy_event(self, src, pct, proxy="", error="", done=0, total=0):
        """预览代理进度回调：队列整体进度 + 当前预览素材完成时切到代理文件。"""
        ahead = " · %d/%d" % (done, total) if total > 1 else ""
        if proxy and src == getattr(self, "_preview_path", ""):
            self._apply_preview_source(proxy, keep_position=True)
            self._set_proxy_status("代理已启用%s" % ahead)
            return
        if error:
            if src == getattr(self, "_preview_path", ""):
                self._set_proxy_status("代理失败，继续用原片")
            return
        if pct:
            self._set_proxy_status("代理生成中 %d%%%s" % (pct, ahead))

    def _apply_preview_source(self, src, keep_position=False):
        player = getattr(self, "player", None)
        if player is None:
            return
        pos = player.position() if keep_position else 0
        playing = player.playbackState() == QMediaPlayer.PlayingState
        self._preview_pending = (pos, playing) if keep_position else None
        player.setSource(QUrl.fromLocalFile(src))
        if not keep_position:
            self._prime_first_frame()

    def _on_preview_media_status(self, status):
        pending = getattr(self, "_preview_pending", None)
        if status == QMediaPlayer.MediaStatus.LoadedMedia and pending:
            self._preview_pending = None
            pos, playing = pending
            if pos:
                self.player.setPosition(pos)
            if playing:
                self.player.play()

    def set_preview_proxy_width(self, width):
        """切换代理开关/分辨率：全部素材排队生成，当前预览立即换源（保留位置）。"""
        try:
            from mixcut import preview_proxy
            width = preview_proxy.set_width(width)
        except Exception:
            return
        cur = getattr(self, "_preview_path", "")
        if not width:
            preview_proxy.cancel()
            if cur:
                self._apply_preview_source(cur, keep_position=True)
            self._set_proxy_status("")
            return
        self._ensure_proxy_listener()
        self._kick_proxy_queue()
        if not cur:
            return
        src = preview_proxy.resolve(cur)
        self._apply_preview_source(src, keep_position=True)
        if src == cur:
            self._set_proxy_status("代理生成中…")

    def _prime_first_frame(self):
        """让预览显示第一帧，但绝不留在“播放中”状态。

        以前这里是 play() 后 180ms 暂停：解码稍慢、或中途又换了素材，定时器
        错过收尾，播放器就会一直在后台播放（还带声音）。现在固定为：
        静音 → 播很短一瞬准备解码 → 到点强制暂停回 0 帧 → 解除静音；
        并用 _prime_pending 标记保证只有一个收尾逻辑在跑。
        """
        player = getattr(self, "player", None)
        if player is None:
            return
        self._set_audio_muted(True)
        if getattr(self, "_prime_timer", None) is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._end_prime)
            self._prime_timer = timer
        timer = self._prime_timer
        timer.stop()
        self._prime_pending = True
        player.play()
        timer.start(180)

    def _preview_visible(self):
        """素材预览页是否真正在界面上：不可见时绝不允许出声播放。"""
        page = getattr(self, "demo_workbench", None)
        if page is None:
            return False
        checker = getattr(page, "is_active", None)
        if callable(checker):
            try:
                return bool(checker())
            except RuntimeError:
                return False
        try:
            return bool(page.isVisible())
        except RuntimeError:
            return False

    def _end_prime(self):
        if not getattr(self, "_prime_pending", False):
            return
        self._prime_pending = False
        player = getattr(self, "player", None)
        if player is None:
            return
        # 收尾必须“无条件停住”：哪怕解码没跟上也只在暂停态，
        # 否则素材预览页就会在后台一直响。
        if player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            player.pause()
        player.setPosition(0)
        # 预热只在可见时允许短暂出声；不可见时保持静音。
        keep_sound = bool(getattr(self, "_user_play_requested", False)) \
            and self._preview_visible()
        self._set_audio_muted(not keep_sound)

    def _set_audio_muted(self, muted):
        audio = getattr(self, "audio_output", None)
        if audio is None:
            return
        try:
            audio.setMuted(bool(muted))
        except Exception:
            pass

    def _user_play(self):
        """用户主动播放：先取消预热定时器，避免刚点播放就被预热逻辑暂停。"""
        if not self._preview_visible():
            # 预览页没显示时点了播放也不出声（播放器藏在后台最容易“莫名其妙有声音”）。
            self.stop_preview_playback()
            return
        self._user_play_requested = True
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._prime_pending = False
        self._set_audio_muted(False)
        if getattr(self, "player", None) is not None:
            self.player.play()

    def _user_pause(self):
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._prime_pending = False
        self._user_play_requested = False
        if getattr(self, "player", None) is not None:
            self.player.pause()

    def _user_stop(self):
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._prime_pending = False
        self._user_play_requested = False
        if getattr(self, "player", None) is not None:
            self.player.stop()
        self._set_audio_muted(False)

    def stop_preview_playback(self):
        """选素材 / 切页 / 打开工程时调用：停掉预览播放并静音。

        用户点的是“素材库里的一个素材”，不该让预览器在后台出声播放。
        """
        self._user_play_requested = False
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._prime_pending = False
        player = getattr(self, "player", None)
        if player is not None:
            try:
                if player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
                    player.pause()
            except Exception:
                pass
        # 停止/暂停后一律静音：避免暂停状态下音频通道仍在输出最后一段。
        self._set_audio_muted(True)

    def _typing_focus(self):
        """焦点在文本输入里时，不要抢 P 键。"""
        from PySide6.QtWidgets import QTextEdit
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        try:
            if event.type() == QEvent.KeyPress:
                key = event.key()
                # 1) 预览已全屏：Esc / P 都退出。全屏是独立窗口，
                #    所以这个过滤器装在 QApplication 上，谁拿到焦点都能收到。
                if self._fullscreen_preview() is not None:
                    if key in (Qt.Key_Escape, Qt.Key_P):
                        self._exit_preview_fullscreen()
                        return True
                # 2) 没全屏：P 在当前预览页进入全屏（素材预览 / AI 分析）
                if key == Qt.Key_P and not self._typing_focus():
                    target = self._current_preview_target()
                    if target is not None:
                        self._enter_preview_fullscreen(target)
                        return True
                if obj is getattr(self, "video_widget", None):
                    if key == Qt.Key_Space:
                        if self.player.playbackState() == QMediaPlayer.PlayingState:
                            self.player.pause()
                        else:
                            self.player.play()
                        return True
        except RuntimeError:
            # 退出时控件可能已被 Qt 释放，事件过滤器静默跳过
            pass
        return super().eventFilter(obj, event)

    def on_player_error(self, error, message):

        self.log(
            f"播放器错误：{message}"
        )

    # =================================================
    # 输出目录 / 日志
    # =================================================

    def log(self, text):
        """向“运行日志”输出；新版界面接在自己的日志面板上。

        旧界面没有 _log_sink 时退回 mix_log 面板。
        """
        message = str(text)
        sink = getattr(self, "_log_sink", None)
        if callable(sink):
            sink(message, "info")
            return
        log_widget = getattr(self, "mix_log", None)
        if log_widget is not None:
            log_widget.appendPlainText(message)
        else:
            print(message)

    def update_info_label(self, *args):
        """显示与智能混剪当前选择一致的画布规格。"""
        width, height = MIX_RESOLUTION_PRESETS[MIX_DEFAULT_RESOLUTION_INDEX][1:]
        fps = MIX_FPS_OPTIONS[MIX_DEFAULT_FPS_INDEX]
        if hasattr(self, "mix_resolution"):
            _, width, height = MIX_RESOLUTION_PRESETS[
                self.mix_resolution.currentIndex()
            ]
        if hasattr(self, "mix_fps"):
            fps = MIX_FPS_OPTIONS[self.mix_fps.currentIndex()]

        self.info_label.setText(
            f"输出规格：{width} × {height} @ {fps} fps（竖屏 9:16）\n\n"
            "工程格式：剪映可编辑草稿\n\n"
            "当前版本：MixCut Studio\n\n"
            "提示：提高画布分辨率不会增加低分辨率素材的原始细节。\n\n"
            "注意：生成后请在剪映中检查素材链接。"
        )


if __name__ == "__main__":
    import sys
    from mixcut.ui.splash import SplashScreen

    # 任务栏身份：统一用 MixCut.Studio.Desktop，安装包的快捷方式也带同一个
    # AppUserModelID，Windows 才会用快捷方式/exe 的图标画任务栏按钮。
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MixCut.Studio.Desktop")
    except Exception:
        pass

    app = QApplication(sys.argv)
    # Qt 自带按钮（保存/放弃/取消/是/否）默认英文；装载中文翻译，
    # 并显式把默认区域设为中文，避免工程保存确认框出现英文按钮。
    try:
        QLocale.setDefault(QLocale(QLocale.Language.Chinese,
                                   QLocale.Country.China))
        _qt_zh = QTranslator(app)
        if _qt_zh.load("qtbase_zh_CN",
                       QLibraryInfo.path(
                           QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(_qt_zh)
    except Exception:
        pass
    wheel_guard = WheelGuard(app)

    # 设置页的主题/强调色持久化在 config/ui.json，构建界面前先恢复
    try:
        from mixcut.ui.demo import theme as _demo_theme
        _demo_theme.load_prefs()
    except Exception:
        pass

    icon_path = resolve_app_icon()
    if icon_path:
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    splash = SplashScreen()
    if icon_path:
        splash.setWindowIcon(QIcon(icon_path))
    splash.move(app.primaryScreen().geometry().center() - splash.rect().center())
    splash.show()
    app.processEvents()

    def _step(text, pct, delay=400):
        splash.set_status(text, pct)
        app.processEvents()
        QTimer_process = QTimer()
        QTimer_process.setSingleShot(True)
        QTimer_process.start(delay)
        while QTimer_process.isActive():
            app.processEvents()

    _step("加载核心模块…", 10, 500)
    from mixcut.ui import theme as _ui_theme
    _step("加载素材库…", 25, 500)
    from mixcut.ui import library_page
    _step("加载MIX引擎…", 45, 400)
    from mixcut.ui import mix_page
    _step("加载任务中心…", 60, 300)
    from mixcut.ui.demo import pages_tasks_real
    _step("初始化主窗口…", 75, 500)

    window = MainWindow()
    if icon_path:
        window.setWindowIcon(QIcon(icon_path))
    window.show()
    splash.set_status("完成", 100)
    app.processEvents()
    QTimer.singleShot(400, splash.finish)

    sys.exit(app.exec())
