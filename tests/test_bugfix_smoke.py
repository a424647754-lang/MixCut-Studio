# -*- coding: utf-8 -*-
"""四个 BUG 修复后的冒烟测试（offscreen 平台）。

历史背景：本测试最初验证四项修复。随着分析页改版，第 2 项
「分析页存在开始分析/开始混剪两个独立按钮」的 UI 已不存在
（按钮职责已并入素材库页与参数页），因此该条改为验证
「分析页仍持有可用的停止按钮与状态门控」，不再断言具体按钮名。

当前验证：
1. 主窗口能正常构造，导航/页面不再含 ai
2. 分析页存在停止按钮，且初始禁用；分析页可正常实例化
3. 缓存实例持久：重复构造 worker 前页面只持有一个 cache
4. 停止按钮与停止逻辑引用仍在，且无 _StopDialog 残留
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from main import MainWindow  # noqa: E402


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    shell = win.shell

    # 1. AI 页面与导航项已移除
    assert "ai" not in shell.pages, "AI 页面仍被注册"
    assert "ai" not in shell.sidebar.items, "AI 导航项仍存在"

    # 2. 分析页基础控件（改版后不再有 analyze_btn / mix_btn）
    page = shell.pages["analysis"]
    assert page is not None, "分析页未注册"
    assert hasattr(page, "stop_btn"), "分析页缺少停止按钮"
    assert page.stop_btn.isEnabled() is False, "未开始分析时停止按钮应禁用"

    # 3. 缓存持久化：页面持有一个 cache，且 _launch 不再重建
    cache1 = page._cache
    assert cache1 is not None
    # 模拟再点一次分析（无素材时会提前返回，cache 不应被替换）
    page._launch()
    assert page._cache is cache1, "缓存实例被重建"

    # 4. 停止弹窗类已删除
    assert not hasattr(page, "_stop_dlg")
    import mixcut.ui.analyze_page as ap_mod
    assert not hasattr(ap_mod, "_StopDialog")

    # 页面可正常切换
    shell.show_page("analysis")
    shell.show_page("settings")
    shell.show_page("library")
    app.processEvents()

    print("SMOKE_OK: AI 已移除 / 分析页可用 / 缓存持久 / 停止弹窗已删")


if __name__ == "__main__":
    main()
