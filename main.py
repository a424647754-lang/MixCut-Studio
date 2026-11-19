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
from mixcut.appkit.preview_player import PreviewPlayerMixin
from mixcut.appkit.project_state import ProjectStateMixin
from mixcut.appkit.preview_stage import PreviewStageMixin
from mixcut.appkit.shell_nav import ShellNavMixin
from mixcut.appkit.mix_task import MixTaskMixin
from mixcut.appkit.param_build import ParamBuildMixin

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

class MainWindow(WindowChromeMixin, LibraryOpsMixin, PreviewPlayerMixin,
                 ProjectStateMixin, PreviewStageMixin, ShellNavMixin,
                 MixTaskMixin, ParamBuildMixin,
                 QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("MixCut Studio")

        # 旧界面默认 1280x720；新界面稍后按 demo 的 1600x1000 起步
        self._ratio_guard = False
        self.resize(1280, 720)
        self.setMinimumSize(960, 540)

        self.material_confirmed = False
        self._confirmed_paths = []

        # 素材勾选状态的唯一持有者（解耦第2步）。
        # 旧素材库页把它转接到这里的 selection 上，外部模块也应改为读写本对象，
        # 这样新界面不再依赖旧素材库页实例。
        from mixcut.appkit.selection import MaterialSelection
        self.selection = MaterialSelection(self)

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

        # 旧素材库页（mixcut.ui.library_page.LibraryPage）已整体移除：
        #   · 素材勾选状态 → 宿主持有的 self.selection（解耦第2步）
        #   · 后台探测/缩略图线程 → 本就归宿主 self.probe_worker（第3步）
        #   · 播放器卡片挂载点 preview_slot → 不再使用（批次B）
        # 可见的素材库是新界面 demo_library（mixcut.ui.demo.pages_library）。
        # 其它旧页面（MixPage / SettingsPage / EnginePage / 旧工作台）更早移除。
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
        # 旧页仍然保留做后台兼容，但不允许它们作为窗口子控件在左上角漏画碎片
        for _child in self.children():
            if isinstance(_child, QWidget) and _child is not self.shell \
                    and _child.objectName() != "ResizeZone":
                _child.hide()

        # 旧顶部/设置方法仍然使用旧页面；日志统一走任务中心。
        self._log_sink = self._new_log
        self._new_ui_ready = True
        self._ensure_probe_worker()

        # 播放器卡片（seek_slider / aspect_combo / 旧播放按钮等控件）不再挂进
        # 旧素材库页：video_widget 早已被 demo 工作台的 PreviewStage 接管，
        # 卡片本身只剩一批不可见的旧控件，供 _on_seek / _on_position 等方法操作。
        # 直接挂到主窗口下并隐藏，旧页因此不再承担任何挂载职责。
        self._demo_player_card.setParent(self)
        self._demo_player_card.hide()

        self._refresh_topbar()
        self.shell.show_page("library")
        self._refresh_materials_now()
        # 代理配置是持久化的：上次开着就继续把现有素材排队生成
        self._ensure_proxy_listener()
        self._kick_proxy_queue()
        # 快捷键统一在 QApplication 级绑定：Ctrl+I/R/S/Z/Y、Ctrl+1..5、空格、Esc、P
        self._bind_app_shortcuts()

    # ---- demo 页面需要的宿主接口（直接复用旧后台） ----


    def _bind_app_shortcuts(self):
        if getattr(self, "_app_shortcuts", None):
            return
        # 旧隐藏树自己也挂了 Ctrl+Z，同一窗口内两个同键 QShortcut 会互相抢成
        # activatedAmbiguously，两边都不触发；这里禁掉旧树的，由新 UI 统一接管。
        # 旧素材库页已移除，但它用的 self.tree 是宿主自己的 UndoableTreeWidget，
        # 旧的撤销快捷键仍挂在上面，所以这段保留。
        legacy_tree = getattr(self, "tree", None)
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
        # 另存为：与顶部工具栏「另存为」按钮同一入口
        bind("Ctrl+Shift+S", self.save_project_as)
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

        # 自动保存：每分钟检查一次，仅在工程有改动时静默写入
        # _project_backups/<工程名>.autosave.mixcut
        try:
            self.start_autosave()
        except Exception:
            pass

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
        # 停止预览代理转码并杀掉它的 ffmpeg。
        # 代理服务跑在 daemon 线程上，程序退出时 daemon 会被直接终止，
        # 它内部的 finally 来不及执行，ffmpeg 就成了孤儿进程继续吃内存
        # （实测退出后仍残留数个约 2GB 的 ffmpeg）。
        # 这里在关闭前主动取消并等待线程退出，确保子进程被回收。
        try:
            from mixcut import preview_proxy as _pp
            _pp.cancel()
            _pp.shutdown()
        except Exception:
            pass
        # 兜底：结束所有由 frame_pipe 登记的解码进程
        try:
            from mixcut import frame_pipe as _fp
            _fp.kill_all()
        except Exception:
            pass
        super().closeEvent(event)

    def _toggle_play(self):
        if getattr(self, "player", None) is not None:
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.pause()
            else:
                self.player.play()


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
    # 输出目录 / 日志
    # =================================================


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
    from mixcut.ui.demo import pages_library
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
