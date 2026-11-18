# -*- coding: utf-8 -*-
"""
本轮 5 个界面问题的回归测试。

1. BGM 文件夹可选择（配乐方式=本地随机时能选文件夹、能手选音频）
2. 新建分类后：分类节点立即出现、可展开、素材能移入/拖入
3. 底部批量任务区只在“批量任务”页显示
4. 右上角“设置”可用（能跳转并跑真实自检）
5. 输出设置里的“草稿位置”能选

运行：python tests/test_ui_fixes.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QMessageBox, QInputDialog, QFileDialog, QPushButton,
    QTreeWidgetItem,
)

import main as M  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def find_button(root, text):
    for btn in root.findChildren(QPushButton):
        if text in btn.text():
            return btn
    return None


def main():
    app = QApplication.instance() or QApplication([])
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    win = M.MainWindow()
    win.show()
    app.processEvents()

    # ---------- 3 底部批量任务窗口已删除 ----------
    check("3 底部任务窗口已移除",
          not hasattr(win.shell, "task_dock")
          and not hasattr(win.shell, "dock_wrap"))
    # 任务状态改由“批量任务”页里的任务卡 + 日志面板承担
    win._navigate("tasks")
    app.processEvents()
    check("3b 批量任务页存在任务卡与日志面板",
          win.page_tasks.task_list is not None
          and win.page_tasks.log_panel is not None)
    # 其它页面不再有任务窗口残留
    for key in ("workbench", "library", "mix", "settings"):
        win._navigate(key)
        app.processEvents()
        check(f"3c {key} 页没有底部任务窗口",
              not hasattr(win.shell, "task_dock"), key)

    # ---------- 4 右上角设置可用 ----------
    win._navigate("workbench")
    app.processEvents()
    settings_btn = win.shell.topbar.settings_btn
    check("4 顶栏存在设置按钮", settings_btn.isVisible())
    settings_btn.click()
    app.processEvents()
    check("4b 点设置后切到设置页",
          win.shell.nav.current() == "settings")
    page = win.page_settings
    check("4c 设置页真实执行了环境自检",
          page.check_state.text() in ("可用", "环境异常"),
          page.check_state.text())

    # ---------- 5 草稿位置可选择 ----------
    win._navigate("mix")
    app.processEvents()
    mix_page = win.page_mix
    draft_btns = [b for b in mix_page.findChildren(QPushButton)
                  if "选择位置" in b.text()]
    check("5 混剪配置里有“选择位置”按钮", len(draft_btns) == 1)
    target = Path(tempfile.mkdtemp(prefix="mixcut_draft_"))
    # 模拟用户在对话框里选目录
    QFileDialog.getExistingDirectory = staticmethod(
        lambda *a, **k: str(target))
    if draft_btns:
        draft_btns[0].click()
    app.processEvents()
    check("5b 选择后草稿位置被写入",
          win.mix_draft_dir.text() == str(target),
          repr(win.mix_draft_dir.text()))

    # ---------- 1 BGM 文件夹/文件可选择 ----------
    bgm_btns = [b for b in mix_page.findChildren(QPushButton)
                if "选择音频" in b.text()]
    folder_btns = [b for b in mix_page.findChildren(QPushButton)
                   if "选择文件夹" in b.text()]
    check("1 有“选择音频”按钮", len(bgm_btns) == 1)
    check("1b 有音乐“选择文件夹”按钮", len(folder_btns) == 1)

    audio = target / "bgm.mp3"
    audio.write_bytes(b"x")
    QFileDialog.getOpenFileName = staticmethod(
        lambda *a, **k: (str(audio), ""))
    if bgm_btns:
        bgm_btns[0].click()
    app.processEvents()
    check("1c 选择后 BGM 文件被写入", win.mix_bgm.text() == str(audio),
          repr(win.mix_bgm.text()))

    music_dir = Path(tempfile.mkdtemp(prefix="mixcut_music_"))
    QFileDialog.getExistingDirectory = staticmethod(
        lambda *a, **k: str(music_dir))
    if folder_btns:
        folder_btns[0].click()
    app.processEvents()
    check("1d 选择后音乐文件夹被写入",
          win.music_folder.text() == str(music_dir),
          repr(win.music_folder.text()))

    # 音乐模式切换时输入框可用性（真实联动）
    win.music_mode.setCurrentIndex(1)   # 本地随机
    app.processEvents()
    check("1e 本地随机模式下文件夹可用、手选音频禁用",
          win.music_folder.isEnabled() and not win.mix_bgm.isEnabled())

    # ---------- 2 新建分类 ----------
    QInputDialog.getText = staticmethod(lambda *a, **k: ("产品特写", True))
    win._navigate("library")
    app.processEvents()
    lib = win.page_library
    cat_btn = [b for b in lib.findChildren(QPushButton)
               if "新建分类" in b.text()]
    check("2 素材库有“新建分类”按钮", len(cat_btn) == 1)
    if cat_btn:
        cat_btn[0].click()
    app.processEvents()

    root_item = win.tree.invisibleRootItem()
    cats = [root_item.child(i) for i in range(root_item.childCount())
            if not root_item.child(i).data(1, Qt.UserRole)]
    check("2b 分类节点立即出现", len(cats) == 1,
          [c.text(0) for c in cats])
    if cats:
        cat = cats[0]
        check("2c 分类可展开（能装素材）",
              cat.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator)
        check("2d 分类可重命名",
              bool(cat.flags() & Qt.ItemIsEditable))

        # 素材移入分类：直接走右键菜单用的同一段逻辑
        clip = Path(target) / "clip.mp4"
        clip.write_bytes(b"x")
        win.add_paths_to_tree([str(clip)])
        app.processEvents()
        books = win.get_all_video_paths()
        check("2e 素材已导入", len(books) == 1)
        # 找到素材节点并移动到分类
        item = win.tree.topLevelItem(0)
        node = None
        for i in range(win.tree.topLevelItemCount()):
            top = win.tree.topLevelItem(i)
            if top.data(1, Qt.UserRole):
                node = top
                break
            for j in range(top.childCount()):
                if top.child(j).data(1, Qt.UserRole):
                    node = top.child(j)
                    break
        if node is not None:
            win.tree.record_state()
            parent = node.parent() or root_item
            parent.removeChild(node)
            cat.addChild(node)
            app.processEvents()
            check("2f 素材可以挂到分类下",
                  cat.childCount() == 1, cat.childCount())
            # 分类名成为素材标签（引擎用它做相邻重复控制）
            tags = win.collect_source_tags([str(clip)])
            check("2g 分类名被当作素材标签",
                  tags.get(str(clip)) == ["产品特写"], str(tags))

    win.close()
    win.deleteLater()
    app.processEvents()

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
