"""MixCut Editor 入口：python -m editor"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from .app import run_app
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出人话提示
        sys.stderr.write(
            "启动失败：缺少依赖。\n"
            f"  详情：{exc}\n\n"
            "请用下面的命令安装依赖后重试：\n"
            "  py -m pip install -r requirements.txt\n"
            "（如果旧版工具能运行，说明 PySide6 已安装，可能是 Python 版本不对）\n"
        )
        return 1
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
