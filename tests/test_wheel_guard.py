# -*- coding: utf-8 -*-
"""验证滚轮守卫：未点击忽略、左键点击后放行、右键点击也放行、点别处会失效。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDoubleSpinBox, QComboBox, QLineEdit,
)

import main as M  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


class FakeEvent:
    def __init__(self, etype, button=Qt.NoButton):
        self._t = etype
        self._b = button

    def type(self):
        return self._t

    def button(self):
        return self._b


app = QApplication([])
guard = M.WheelGuard(app)

spin = QDoubleSpinBox()
combo = QComboBox()
edit = QLineEdit()

WHEEL = FakeEvent(QEvent.Type.Wheel)
LB = FakeEvent(QEvent.Type.MouseButtonPress, Qt.LeftButton)
RB = FakeEvent(QEvent.Type.MouseButtonPress, Qt.RightButton)
MB = FakeEvent(QEvent.Type.MouseButtonPress, Qt.MiddleButton)

# 1 未点击：滚轮被拦
check("1 未点击时滚轮被拦截", guard.eventFilter(spin, WHEEL) is True)
check("1b 未点击时下拉框滚轮被拦截", guard.eventFilter(combo, WHEEL) is True)
check("1c 普通输入框不受影响", guard.eventFilter(edit, WHEEL) is False)

# 2 左键点击后：滚轮放行
guard.eventFilter(spin, LB)
check("2 左键点击后滚轮放行", guard.eventFilter(spin, WHEEL) is False)
# 同一控件右键也要放行
guard.eventFilter(combo, RB)
check("2b 右键点击后滚轮放行", guard.eventFilter(combo, WHEEL) is False)

# 3 点另一个控件：前一个失效
guard.eventFilter(combo, LB)
check("3 点另一个控件后，原来的控件滚轮被拦",
      guard.eventFilter(spin, WHEEL) is True)
check("3b 新点击的控件滚轮放行", guard.eventFilter(combo, WHEEL) is False)

# 4 中键点击不算“选中”
guard.eventFilter(spin, MB)
check("4 中键点击不放行滚轮", guard.eventFilter(spin, WHEEL) is True)

# 5 点空白处（非参数控件）会让之前的选择失效
guard.eventFilter(combo, LB)
check("5 点击前：下拉框放行", guard.eventFilter(combo, WHEEL) is False)
guard.eventFilter(edit, LB)
check("5b 点普通输入框后，下拉框滚轮被拦",
      guard.eventFilter(combo, WHEEL) is True)

print()
if FAILED:
    print("失败:", ", ".join(FAILED))
    sys.exit(1)
print("全部通过")
sys.exit(0)
