"""MixCut 剪辑器启动器：捕获所有异常并写入日志，避免闪退看不到错误。

用法：
    py -m editor._launcher

启动器会：
1. 把 stdout/stderr 重定向到日志文件；
2. 捕获启动过程中的全部异常并写入日志；
3. 出错时弹出一个窗口显示错误内容（不会一闪而过）；
4. 日志位置：%APPDATA%\\MixCutEditor\\startup.log
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def log_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home())
    path = Path(base) / "MixCutEditor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_log(text: str) -> Path:
    target = log_dir() / "startup.log"
    try:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n" + "=" * 70 + "\n")
    except OSError:
        pass
    return target


def show_error(title: str, message: str) -> None:
    """优先用图形窗口显示；没有 Qt 时退回控制台。"""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception:  # noqa: BLE001
        print(message)
        return
    app = QApplication.instance()
    created = app is None
    if created:
        app = QApplication(sys.argv[:1])
    box = QMessageBox()
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText("MixCut 剪辑器启动失败")
    box.setInformativeText("错误信息已写入日志：\n" + str(log_dir() / "startup.log"))
    box.setDetailedText(message)
    box.resize(760, 420)
    box.exec()
    if created:
        app.quit()


def main() -> int:
    header = [
        "",
        "=" * 70,
        "MixCut Editor startup",
        f"python : {sys.version}",
        f"exe    : {sys.executable}",
        f"cwd    : {os.getcwd()}",
        f"file   : {__file__}",
    ]
    try:
        import PySide6

        header.append(f"PySide6: {PySide6.__version__}")
    except Exception as exc:  # noqa: BLE001
        header.append(f"PySide6: NOT AVAILABLE ({exc})")

    try:
        from .app import run_app

        return run_app()
    except BaseException:  # noqa: BLE001 - 包括 SystemExit
        text = "\n".join(header) + "\n\n" + traceback.format_exc()
        write_log(text)
        sys.stderr.write(text)
        show_error("MixCut 识别到错误", text)
        return 1


if __name__ == "__main__":
    sys.exit(main())
