# -*- coding: utf-8 -*-
"""页面导航、界面刷新与运行日志（从 main.py 原样搬出）。

包含页面跳转、顶部栏/素材卡片刷新、日志输出、分析前后联动，
以及智能混剪页的规格说明文本。

拆分方式说明
------------
Mixin：方法仍挂在 MainWindow 上。这批方法被其它模块大量调用
（``log`` 被十余处调用），用 Mixin 保持 ``self.log(...)`` 原样。

宿主契约
--------
**页面与子系统**
    ``self.shell``  ``self.controller``
    ``self.demo_library``  ``self.demo_params``  ``self.demo_tasks``
    ``self.page_analyze``

**状态**
    ``self._log_sink``  ``self.material_confirmed``
    ``self.info_label``  ``self.mix_resolution``  ``self.mix_fps``

**宿主方法**
    ``self.get_all_video_paths``  ``self.update_count``
    ``self.stop_preview_playback``  ``self._navigate``

依赖方向：只导入 PySide6、标准库与 appkit 自身的常量，
方法内对 mixcut.ui 的导入沿用原写法（延迟导入）。
"""
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from .constants import (
    MIX_RESOLUTION_PRESETS, MIX_DEFAULT_RESOLUTION_INDEX,
    MIX_FPS_OPTIONS, MIX_DEFAULT_FPS_INDEX,
)


class ShellNavMixin:
    """页面导航、刷新与日志。"""

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

