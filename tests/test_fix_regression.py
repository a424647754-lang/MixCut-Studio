# -*- coding: utf-8 -*-
"""
MixCut 修复回归测试（不启动界面，不依赖真实剪映）

覆盖：
  B1  已删除模块不再存在、spec 不再整体收集 mixcut
  B2  seed 可复现、不同 seed 排列不同
  B3  worker 异常也一定发 finished；取消能真正停下
  B4  不生成短于“最短片段”的碎片
  B5  镜头边界落在废段里不丢画面
  B6  有效区间互不重叠，reuse=False 不重复取同一段
  B7  检测开关真正生效（画面检测关掉后黑屏不再剔除）
  B8  缓存键按来源隔离、开关变化即失效
  B9  素材不足时能识别出来
  B10 素材清单失效会清空选择框（界面测试单独做）

运行：python tests/test_fix_regression.py
"""

import os
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mixcut import analysis as A                      # noqa: E402
from mixcut import analysis_cache as AC               # noqa: E402
from mixcut import filter_engine as FE                # noqa: E402
from mixcut import mixbuilder as MB                   # noqa: E402
from mixcut import timeline as TL                     # noqa: E402
from mixcut import workers as W                       # noqa: E402


FAILED = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def item(sid, dur, junk=(), scenes=None, path=None):
    return {
        "source": TL.make_source(sid, path or f"src{sid}.mp4", dur),
        "junk": [],
        "_junk_merged": list(junk),
        "_scenes": scenes,
    }


# ---------------------------------------------------------
# B1：废弃模块已删除
# ---------------------------------------------------------
def test_b1_modules_removed():
    check("B1 engine.py 已删除", not (ROOT / "mixcut" / "engine.py").exists())
    check("B1 roughcut.py 已删除", not (ROOT / "mixcut" / "roughcut.py").exists())
    spec = (ROOT / "MixCutStudio.spec").read_text(encoding="utf-8")
    check("B1 spec 不再整体收集 mixcut",
          "collect_submodules('mixcut')" not in spec)
    check("B1 spec 包含 filter_engine",
          "'mixcut.filter_engine'" in spec)
    for p in [ROOT / "main.py", *sorted((ROOT / "mixcut").glob("*.py"))]:
        src = p.read_text(encoding="utf-8")
        check(f"B1 {p.name} 无废弃引用",
              "roughcut" not in src and "from .engine" not in src)


# ---------------------------------------------------------
# B2：种子
# ---------------------------------------------------------
def test_b2_seed():
    an = [item(0, 300.0)]
    opts = dict(target_duration=60.0, duration_tolerance=2.0,
                clip_min=2.0, clip_max=5.0, reuse=True)

    def build(seed):
        return MB.build_mix_timeline(None, TL.create_canvas(),
                                     mix_options=opts, analysis=an, seed=seed)

    a, b, c = build(11), build(11), build(12)

    def sig(p):
        return [(x["source_start"], x["source_end"]) for x in p["clips"]]

    check("B2 同 seed 可复现", sig(a) == sig(b))
    check("B2 不同 seed 排列不同", sig(a) != sig(c))


# ---------------------------------------------------------
# B3：worker 生命周期
# ---------------------------------------------------------
def test_b3_worker():
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])

    class Boom(W.BaseWorker):
        def _work(self):
            raise RuntimeError("故意失败")

    boom = Boom()
    got = {"finished": False, "failed": False}
    boom.finished.connect(lambda: got.__setitem__("finished", True))
    boom.failed.connect(lambda _m: got.__setitem__("failed", True))
    boom.start()
    boom.wait(5000)
    app.processEvents()
    check("B3 异常也触发 finished", got["finished"])
    check("B3 异常触发 failed", got["failed"])

    # 取消：装配中的循环必须能被取消请求打断
    an = [item(0, 20000.0)]
    calls = {"n": 0}

    def cancel_after_start():
        # 让任务“跑一会儿”再发取消，模拟界面上用户点取消
        calls["n"] += 1
        return calls["n"] > 1 and worker.isInterruptionRequested()

    worker = W.MixBuildWorker(
        ["x.mp4"], TL.create_canvas(), {},
        dict(target_duration=20000.0, clip_min=2.0, clip_max=5.0, reuse=True),
        {"enabled": False}, {"enabled": False},
    )
    worker._analysis = lambda: an
    worker._cancel = cancel_after_start
    worker.start()
    time.sleep(0.2)
    t0 = time.time()
    worker.shutdown()
    worker.wait(3000)
    check("B3 取消请求能打断装配", not worker.isRunning()
          and time.time() - t0 < 0.5 and calls["n"] > 1,
          f"{time.time() - t0:.2f}s，取消检查 {calls['n']} 次")


# ---------------------------------------------------------
# B4：片段长度
# ---------------------------------------------------------
def _lengths_ok(lens, mn, mx):
    """所有片段都在 [最短, 最长]；唯一例外是收尾段（放不下时）。"""
    for x in lens[:-1]:
        if x < mn - 1e-6 or x > mx + 1e-6:
            return False
    return lens[-1] <= mx + 1e-6


def test_b4_clip_length():
    an = [item(0, 100000.0)]
    bad_clip = 0
    bad_total = 0
    runs = 0
    for mn, mx in ((2.0, 5.0), (4.0, 6.0), (2.0, 3.0)):
        for tol in (0.0, 1.0, 2.0):
            for seed in range(20):
                runs += 1
                p = MB.build_mix_timeline(
                    None, TL.create_canvas(),
                    mix_options=dict(target_duration=120.0,
                                     duration_tolerance=tol,
                                     clip_min=mn, clip_max=mx, reuse=True),
                    analysis=an, seed=seed)
                lens = [c["timeline_end"] - c["timeline_start"]
                        for c in p["clips"]]
                if not _lengths_ok(lens, mn, mx):
                    bad_clip += 1
                total = TL.total_duration(p)
                if total > 120.0 + tol + 1e-6:
                    bad_total += 1
                # 距离目标太远且没有素材限制：不允许出现短片
                if total < 120.0 - tol - max(mn, 1.0) - 1e-6:
                    bad_total += 1
    check("B4 片段长度都在最短/最长之间", bad_clip == 0, f"{runs} 次生成")
    check("B4 成片时长贴近目标", bad_total == 0, f"{runs} 次生成")


# ---------------------------------------------------------
# B5：镜头边界落在废段里不丢画面
# ---------------------------------------------------------
def test_b5_no_footage_loss():
    it = item(0, 7.4, junk=[(3.0, 4.233, ["black"])],
              scenes=[(0.0, 2.85), (2.85, 4.3), (4.3, 7.4)])
    segs = A.valid_segments_for(it)
    total = sum(e - s for s, e in segs)
    check("B5 可用画面不再被截断", abs(total - 6.167) < 1e-3,
          f"{total:.3f}s（真实可用 6.167s）")

    # 镜头边界对齐时结果不变
    it2 = item(0, 7.4, junk=[(3.0, 4.233, ["black"])],
               scenes=[(0.0, 3.0), (4.233, 7.4)])
    check("B5 对齐场景结果不变",
          A.valid_segments_for(it2) == [(0.0, 3.0), (4.233, 7.4)])


# ---------------------------------------------------------
# B6：区间互不重叠
# ---------------------------------------------------------
def test_b6_no_overlap():
    cases = {
        "场景跨废段": item(0, 7.4, junk=[(3.0, 4.233, ["b"])],
                        scenes=[(0.0, 2.9), (2.9, 4.2), (4.2, 7.4)]),
        "废段被切碎": item(0, 7.4, junk=[(3.0, 4.233, ["b"])],
                        scenes=[(0.0, 3.0), (3.0, 3.3), (3.3, 4.233),
                                (4.233, 7.4)]),
    }
    ok = True
    for label, it in cases.items():
        segs = sorted(A.valid_segments_for(it))
        for (s1, e1), (s2, e2) in zip(segs, segs[1:]):
            if s2 < e1 - 1e-6:
                ok = False
                print(f"    重叠: {label} {s1, e1} / {s2, e2}")
    check("B6 有效区间互不重叠", ok)

    # reuse=False：同一源时刻不应被两次取用（允许“首尾精确相接”的长镜头）
    dup_runs = 0
    for seed in range(60):
        p = MB.build_mix_timeline(
            None, TL.create_canvas(),
            mix_options=dict(target_duration=6.0, duration_tolerance=0,
                             clip_min=1.0, clip_max=2.0, reuse=False),
            analysis=[cases["场景跨废段"]], seed=seed)
        segs = sorted((c["source_start"], c["source_end"]) for c in p["clips"])
        overlapped = False
        for (s1, e1), (s2, e2) in zip(segs, segs[1:]):
            if s2 < e1 - 1e-6:
                overlapped = True
        if overlapped:
            dup_runs += 1
    check("B6 reuse=False 无重复取用", dup_runs == 0, f"{dup_runs}/60 次出现重复")


# ---------------------------------------------------------
# B7：检测开关真正生效
# ---------------------------------------------------------
def _make_src(workdir):
    """生成一段 7.4 秒、中间 3~4.2 秒黑屏+静音的测试素材。"""
    import subprocess
    from mixcut import ffmpeg_tools as ft
    ff = ft.find_ffmpeg()
    if not ff:
        return None
    out = Path(workdir) / "开关测试 素材.mp4"
    if out.exists():
        return str(out)
    r = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=7.4",
         "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=44100:duration=7.4",
         "-vf",
         "drawbox=x=0:y=0:w=320:h=180:color=black:t=fill:"
         "enable=between(t\\,3\\,4.2)",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    return str(out) if r.returncode == 0 else None


def _junk_with(path, video_on):
    cfg = dict(FE.DEFAULT_FILTER_CONFIG, cpu_mode=True)
    cfg["ffmpeg_options"] = {
        "video_detect": video_on, "audio_detect": False,
        "detect_black": video_on, "detect_freeze": video_on,
    }
    res = FE.run_filter([path], cfg)
    return FE.to_mix_analysis([path], res)[0]["_junk_merged"]


def test_b7_switches(workdir):
    path = _make_src(workdir)
    if not path:
        check("B7 检测开关（缺少 ffmpeg，跳过）", True)
        return

    on = _junk_with(path, True)
    off = _junk_with(path, False)
    check("B7 画面检测开：识别出黑屏", any(e - s > 1.0 for s, e, _t in on),
          str(on))
    check("B7 画面检测关：黑屏不再剔除", off == [], str(off))

    # 关掉画面检测后，同一素材的有效区间应是完整时长
    it = {"source": TL.make_source(0, path, 7.4), "junk": [],
          "_junk_merged": off, "_scenes": None}
    check("B7 关掉后可用整段", A.valid_segments_for(it) == [(0.0, 7.4)])


# ---------------------------------------------------------
# B8：缓存键
# ---------------------------------------------------------
def test_b8_cache(workdir):
    f = Path(workdir) / "cache 素材.mp4"
    f.write_bytes(b"x" * 128)
    cache = AC.AnalysisCache()
    opts_a = {"cpu_mode": True, "ffmpeg_options": {"video_detect": True}}
    opts_b = {"cpu_mode": True, "ffmpeg_options": {"video_detect": False}}
    # 缓存现在按单个素材存储（增量执行），用的是只含 path 的轻量条目
    item = {"source": {"id": 0, "path": str(f)}, "_junk_merged": []}
    cache.set_entry(str(f), opts_a, item, source=AC.DEFAULT_SOURCE)
    check("B8 配置不变命中",
          cache.get_entry(str(f), opts_a,
                          source=AC.DEFAULT_SOURCE) is not None)
    check("B8 检测开关变化即失效",
          cache.get_entry(str(f), opts_b,
                          source=AC.DEFAULT_SOURCE) is None)
    check("B8 分析来源不同不互相命中",
          cache.get_entry(str(f), opts_a, source=AC.LEGACY_SOURCE) is None)
    check("B8 缺省来源与 filter 兼容",
          cache.get_entry(str(f), opts_a) is not None)
    # 逐素材：改一个文件不影响其它文件的命中
    g = Path(workdir) / "cache 素材2.mp4"
    g.write_bytes(b"y" * 64)
    cache.set_entry(str(g), opts_a, {"source": {"id": 1, "path": str(g)}},
                    source=AC.DEFAULT_SOURCE)
    hits, misses = cache.get_many([str(f), str(g)], opts_a,
                                  source=AC.DEFAULT_SOURCE)
    check("B8b 逐素材缓存：两个文件都命中且互不干扰",
          len(hits) == 2 and not misses, f"命中 {len(hits)}，未命中 {len(misses)}")


# ---------------------------------------------------------
# B9：素材不足识别
# ---------------------------------------------------------
def test_b9_shortage():
    an = [item(0, 8.0)]
    raised = False
    try:
        MB.build_mix_timeline(
            None, TL.create_canvas(),
            mix_options=dict(target_duration=30.0, duration_tolerance=2.0,
                             clip_min=2.0, clip_max=5.0, reuse=False),
            analysis=an, seed=1)
    except RuntimeError as exc:
        raised = "低于目标时长下限" in str(exc)
    check("B9 素材不足时明确报错而不是静默出短片", raised)


def main():
    workdir = tempfile.mkdtemp(prefix="mixcut_reg_")
    try:
        test_b1_modules_removed()
        test_b2_seed()
        test_b3_worker()
        test_b4_clip_length()
        test_b5_no_footage_loss()
        test_b6_no_overlap()
        test_b7_switches(workdir)
        test_b8_cache(workdir)
        test_b9_shortage()
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.exit(main())
