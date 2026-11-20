# -*- coding: utf-8 -*-
"""直接打开指定工程启动剪辑器（跳过项目管理器）。

用途
====
MixCut 混剪完成后「一键打开编辑」会以独立进程调用本模块：

    python -m editor.open_project "D:\\path\\to\\project.mixcut"

为什么不复用 ``app.run_app()``
-----------------------------
``run_app`` 的流程是「启动画面 → 项目管理器 → 用户选择工程 → 主窗口」。
我们要的是**直接进主窗口**，跳过选择这一步。

为什么用独立进程而不是同进程内嵌
--------------------------------
MixCut 自己已经有一个 ``QApplication``。Qt 不允许同一进程里有两个
``QApplication`` 实例，硬塞会崩。用子进程最干净：
剪辑器崩了也不会带崩 MixCut。
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("用法: python -m editor.open_project <工程文件>")
        return 2

    target = Path(argv[0])
    if not target.is_file():
        print("工程文件不存在：%s" % target)
        return 2

    try:
        from PySide6.QtWidgets import QApplication
        from .main_window import MainWindow

        app = QApplication.instance() or QApplication(sys.argv)
        from . import ui_theme as T
        app.setApplicationName(T.APP_NAME)
        app.setApplicationDisplayName(T.APP_NAME)

        window = MainWindow(str(target))
        window.show()
        window.raise_()
        return app.exec()
    except Exception:      # noqa: BLE001 - 启动失败要让人看到原因
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
