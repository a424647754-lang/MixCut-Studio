# -*- coding: utf-8 -*-
"""分析页顶部统计卡：换一批素材后必须是“本轮”口径，不混历史批次。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MIXCUT_NO_RECOVERY", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from mixcut.ui.analyze_page import AnalyzePage

FAILS = []


def check(cond, label, extra=""):
    if cond:
        print("PASS  %s" % label, flush=True)
    else:
        print("FAIL  %s %s" % (label, extra), flush=True)
        FAILS.append(label)


class FakeWin:
    def __init__(self):
        self._confirmed_paths = []
        self._probe_cache = {}

    def _demo_clip(self, path):
        return None


win = FakeWin()
page = AnalyzePage(win)
page._auto_preview = False
page.resize(1400, 900)
page.show()


def make_items(path, count, seg_len=5.0, bad_every=0):
    out = []
    for i in range(count):
        out.append({
            "path": path, "name": os.path.basename(path),
            "seg_index": i, "seg_start": i * seg_len,
            "seg_end": i * seg_len + seg_len, "duration": seg_len,
            "source_duration": count * seg_len, "score": 80.0,
            "is_bad": bool(bad_every and i % bad_every == 0),
            "included": True, "frag_id": "%s-%d" % (os.path.basename(path), i),
        })
    return out


def stat(card):
    return card._value_label.text()


# ---- 第一批：棕色 2 条素材，共 40 段 ----
brown = [r"D:\素材\棕\BROWNA.mp4", r"D:\素材\棕\BROWNB.mp4"]
win._confirmed_paths = list(brown)
batch1 = make_items(brown[0], 20) + make_items(brown[1], 20)
page.load_results(batch1, finished=True)
app.processEvents()

check(stat(page.stat_total) == "2", "第一批 素材总数=2", stat(page.stat_total))
check(stat(page.stat_analyzed) == "40", "第一批 分析片段=40",
      stat(page.stat_analyzed))

# ---- 第二批：紫色 3 条素材，共 30 段（历史 40 段仍在 _results 里） ----
purple = [r"D:\素材\紫\PA.mp4", r"D:\素材\紫\PB.mp4", r"D:\素材\紫\PC.mp4"]
win._confirmed_paths = list(purple)
batch2 = (make_items(purple[0], 10) + make_items(purple[1], 10)
          + make_items(purple[2], 10))
page._results = list(batch1) + list(batch2)   # 模拟历史结果仍保留
page._refresh_stats()
app.processEvents()

check(stat(page.stat_total) == "3", "换批后 素材总数=3（本轮）",
      stat(page.stat_total))
check(stat(page.stat_analyzed) == "30",
      "换批后 分析片段=30（只算本轮，不含历史 40）",
      stat(page.stat_analyzed))
check(stat(page.stat_duration) == "02:30",
      "换批后 总时长=02:30（30 段 × 5 秒）",
      stat(page.stat_duration))
check(len(page._results) == 70, "历史结果本身没有被删除（仍保留 70 段）",
      len(page._results))

# ---- 废片段也只算本轮 ----
batch2_bad = make_items(purple[0], 10, bad_every=5) + batch2[10:]
page._results = list(batch1) + list(batch2_bad)
page._refresh_stats()
app.processEvents()
check(stat(page.stat_bad) == "2", "废片段只统计本轮（2 个）", stat(page.stat_bad))

print()
if FAILS:
    print("FAILED %d:" % len(FAILS), FAILS, flush=True)
    os._exit(1)
print("ALL PASS", flush=True)
os._exit(0)
