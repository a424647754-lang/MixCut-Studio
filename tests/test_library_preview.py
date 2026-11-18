# -*- coding: utf-8 -*-
"""素材库 / 预览器回归测试（离屏，真实旧树 + 新界面）。

覆盖用户报告的：
  A. 素材归类进文件夹后，在「全部素材」里立刻消失
  B. 移动后可见界面同步
  C. 单击素材不会再在后台出声播放
  D. 离开工作台 / 切页会停掉预览播放
"""
import os
import sys

try:
    import faulthandler
    faulthandler.dump_traceback_later(150, exit=True)
except Exception:
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MIXCUT_NO_RECOVERY", "1")
os.environ.pop("MIXCUT_LEGACY_UI", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

FAILS = []


def check(cond, label, extra=""):
    if cond:
        print("PASS  %s" % label, flush=True)
    else:
        print("FAIL  %s %s" % (label, extra), flush=True)
        FAILS.append(label)


import main as MAIN  # noqa: E402
from mixcut.ui.demo import mock  # noqa: E402

win = MAIN.MainWindow()
check(bool(getattr(win, "use_new_ui", False)), "运行的是新界面", win.use_new_ui)
check(getattr(win, "shell", None) is not None, "新界面外壳已建立")
check(getattr(win, "demo_library", None) is not None, "素材库页已建立")
app.processEvents()

lib = win.demo_library
bridge = win.backend

# ---- 构造真实旧树：一个分类「甲」+ 两个视频文件（不存在的路径，仅结构） ----
from PySide6.QtWidgets import QTreeWidgetItem  # noqa: E402

tree = win.tree
tree.blockSignals(True)
tree.clear()
# 约定：第一个顶层目录是「素材库」根容器（folder_path 为空），
# 其余顶层目录才是用户分类。这里按真实结构搭一棵树。
root_node = QTreeWidgetItem(tree, ["素材库", ""])
folder = QTreeWidgetItem(root_node, ["甲", ""])
videos = []
for name in ("a.mp4", "b.mp4"):
    path = os.path.join(os.environ.get("TEMP", "."), "mixcut_test_%s" % name)
    item = QTreeWidgetItem([name, os.path.dirname(path)])
    item.setData(1, Qt.ItemDataRole.UserRole, path)
    root_node.addChild(item)
    videos.append((path, item))
tree.blockSignals(False)
win.reload_library(preserve_selection=False)
app.processEvents()

check(len(mock.CLIPS) == 2, "素材库载入 2 条素材", len(mock.CLIPS))
names = [f["name"] for f in lib.folders]
check("甲" in names, "文件夹列表包含「甲」", names)

# ---- A. 归类后「全部素材」里不再显示 ----
lib._select_folder("all")
app.processEvents()
check(len(lib._shown_ids) == 2, "「全部素材」先显示 2 条", sorted(lib._shown_ids))

# 模拟"把一条素材移进「甲」"：走真实旧树 + 真实后台桥的 move_paths
target_path, item = videos[0]
moved = bridge.move_paths([target_path], "甲")
check(moved == 1, "后台 move_paths 成功移动 1 条", moved)
win.reload_library(preserve_selection=True)
app.processEvents()

lib._select_folder("all")
app.processEvents()
shown = {c["id"]: c for c in mock.CLIPS}
base_shown = [c for c in mock.CLIPS if c["id"] in lib._shown_ids]
check(len(base_shown) == 1, "归类后「全部素材」只剩 1 条",
      [c["name"] for c in base_shown])
check(all(not (c.get("folder_path") or "") for c in base_shown),
      "「全部素材」里没有已归类素材",
      [(c["name"], c.get("folder_path")) for c in base_shown])
lib._select_folder("p:甲")
app.processEvents()
check(len(lib._shown_ids) == 1, "进入「甲」能看到 1 条", sorted(lib._shown_ids))

# 移动到「全部素材」应重新出现
lib._select_folder("all")
app.processEvents()

# ---- C. 单击素材不后台出声播放 ----
player = getattr(win, "player", None)
check(player is not None, "播放器已初始化")
if player is not None:
    if mock.CLIPS:
        lib._toggle(mock.CLIPS[1]["id"])
        app.processEvents()
    QTimer.singleShot(500, app.quit)
    app.exec()
    from PySide6.QtMultimedia import QMediaPlayer
    check(player.playbackState() != QMediaPlayer.PlaybackState.PlayingState,
          "单击素材 500ms 后预览器不是播放中", player.playbackState())
    check(not getattr(win, "_prime_pending", False), "预热收尾状态已复位",
          getattr(win, "_prime_pending", None))
    audio = getattr(win, "audio_output", None)
    # 收尾后必须静音：播放器停在暂停态，不允许音频通道继续输出。
    check(audio is None or audio.isMuted(),
          "预热结束后音频保持静音（避免后台残留声音）",
          audio.isMuted() if audio is not None else "no audio")

    # 在非预览页调用播放，不应该出声
    win.go_page("library")
    app.processEvents()
    win._user_play()
    app.processEvents()
    check(player.playbackState() != QMediaPlayer.PlaybackState.PlayingState,
          "在素材库页调用播放不会出声", player.playbackState())

    # ---- D. 切页停止播放 ----
    win.go_page("work")
    app.processEvents()
    win._user_play()
    app.processEvents()
    win.go_page("analysis")
    app.processEvents()
    check(player.playbackState() != QMediaPlayer.PlaybackState.PlayingState,
          "从工作台切走后预览器停止播放", player.playbackState())

print()
if FAILS:
    print("FAILED %d:" % len(FAILS), FAILS, flush=True)
    os._exit(1)
print("ALL PASS", flush=True)
os._exit(0)
