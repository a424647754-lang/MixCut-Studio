# -*- coding: utf-8 -*-
"""预览全屏与画面比例（从 main.py 原样搬出）。

包含画幅切换、预览区布局、全屏进入/退出、以及全屏快捷键处理。

拆分方式说明
------------
Mixin：方法仍挂在 MainWindow 上。这批方法互相调用频繁
（``_fullscreen_preview`` / ``_enter_preview_fullscreen`` 等），
Mixin 让调用保持 ``self.xxx()`` 原样，不引入回调。

宿主契约
--------
**状态属性**
    ``self._preview_aspect``  ``self._aspect_ratios``
    ``self._preview_holder``  ``self._analysis_preview``
    ``self._fs_home``

**控件**
    ``self.video_widget``  ``self.player``
    ``self.seek_slider``（可选）  ``self.aspect_combo``（可选）

依赖方向：只导入 PySide6 与标准库，不导入 main.py 或业务模块。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QFrame, QApplication


class PreviewStageMixin:
    """预览舞台：画面比例与全屏。"""

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

