"""应用启动编排：Splash → 项目管理器 → 主窗口。

启动顺序（第一阶段）：
  1. 创建 QApplication 并应用全局主题
  2. 显示启动画面，逐个初始化组件并更新进度
  3. 关闭启动画面，进入项目管理器
  4. 用户选择项目后创建主窗口

任何一步失败都由 _launcher 捕获并写入日志，不会静默闪退。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

from . import ui_theme as T


def _initialization_steps():
    """返回 (进度, 状态文字, 初始化动作) 列表。

    动作只做轻量、可失败的探测，重活留给后台线程（第二阶段）。
    """
    def check_ffmpeg() -> str:
        try:
            from .media import find_tool

            path = find_tool("ffmpeg")
            return "FFmpeg 就绪" if path else "未找到 FFmpeg（导出将不可用）"
        except Exception:  # noqa: BLE001
            return "FFmpeg 检测跳过"

    def load_recent() -> str:
        try:
            from .project_manager import RecentProjects

            count = len(RecentProjects().entries())
            return f"载入 {count} 个最近项目"
        except Exception:  # noqa: BLE001
            return "最近项目不可用"

    return [
        (8, "初始化界面框架…", None),
        (26, "加载设计主题…", None),
        (44, "检测媒体组件…", check_ffmpeg),
        (62, "读取最近项目…", load_recent),
        (80, "准备项目管理器…", None),
        (100, "就绪", None),
    ]


def create_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or (sys.argv if sys.argv else ["mixcut"]))
    app.setApplicationName(T.APP_NAME)
    app.setApplicationDisplayName(T.APP_NAME)
    app.setApplicationVersion(T.APP_VERSION)
    app.setOrganizationName("MixCut")
    T.apply_theme(app)
    return app


def run_app(argv: list[str] | None = None) -> int:
    """完整启动流程，返回进程退出码。"""
    app = create_app(argv)

    from .project_manager import ProjectManagerDialog
    from .splash import SplashScreen

    # 关键：启动阶段会先关掉启动画面（此时还没有主窗口），
    # 若保持默认的 quitOnLastWindowClosed，Qt 会在启动画面关闭的瞬间
    # 退出整个程序，导致「闪一下就没了」。等主窗口出现后再恢复该行为。
    app.setQuitOnLastWindowClosed(False)

    splash = SplashScreen()
    splash.show()
    splash.raise_()

    # 逐条执行初始化并刷新启动画面
    for percent, text, action in _initialization_steps():
        detail = text
        if action is not None:
            try:
                note = action()
                if note:
                    detail = f"{text} — {note}"
            except Exception:  # noqa: BLE001 - 初始化探测失败不应阻断启动
                detail = text
        splash.set_status(detail, percent)

    manager = ProjectManagerDialog()
    window_holder: dict[str, object] = {}

    def open_manager() -> None:
        if manager.exec() != QDialog.Accepted or manager.selected_action == "quit":
            app.quit()
            return

        from .main_window import MainWindow

        path = manager.selected_path or None
        window = MainWindow(path)
        window_holder["window"] = window
        app._mixcut_window = window  # type: ignore[attr-defined]
        # 主窗口已就绪：此后关闭最后一个窗口即退出程序
        app.setQuitOnLastWindowClosed(True)
        window.show()
        window.raise_()
        window.activateWindow()

    splash.finish(None, 240)
    QTimer.singleShot(280, open_manager)

    exit_code = app.exec()
    return int(exit_code) if exit_code is not None else 0


if __name__ == "__main__":
    sys.exit(run_app())
