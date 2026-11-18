# -*- coding: utf-8 -*-
"""
诊断“正常素材被判成整段废镜头”的问题。

用法：
  python tests/diagnose_detection.py <视频文件或文件夹> [更多路径...]

对每个素材输出：
  1. ffprobe 基本信息（时长/分辨率/是否有音轨）
  2. 三项检测各自识别出的区间（黑屏 / 静止 / 静音）
  3. 合并后的废段
  4. 最终有效区间 valid_segments_for 的结果
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mixcut import analysis as A          # noqa: E402
from mixcut import ffmpeg_tools as ft     # noqa: E402
from mixcut import filter_engine as fe    # noqa: E402


def dump(label, intervals):
    if not intervals:
        print(f"    {label}: 无")
        return
    text = ", ".join(f"{s:.2f}-{e:.2f}" for s, e in intervals[:8])
    print(f"    {label}: {len(intervals)} 段  [{text}{' ...' if len(intervals) > 8 else ''}]")


def diagnose(path):
    print("=" * 70)
    print("素材:", path)
    try:
        details = ft.probe_details(path)
    except Exception as exc:
        print("  ffprobe 失败:", exc)
        return
    fmt = details.get("format") or {}
    video = details.get("video") or {}
    audio = details.get("audio")
    print(f"  时长 {fmt.get('duration')}s  分辨率 {video.get('width')}x"
          f"{video.get('height')}  编码 {video.get('codec')}  "
          f"音轨 {'有' if audio else '无'}")

    duration = float(fmt.get("duration") or 0.0)
    print("  --- 单独跑检测 ---")
    for name, fn in (
        ("黑屏", lambda: ft.detect_black(
            path,
            A.DEFAULT_OPTIONS["black_min_duration"],
            A.DEFAULT_OPTIONS["black_pixel_threshold"],
            A.DEFAULT_OPTIONS["black_picture_threshold"])),
        ("静止", lambda: ft.detect_freeze(
            path,
            A.DEFAULT_OPTIONS["freeze_min_duration"],
            A.DEFAULT_OPTIONS["freeze_noise"])),
        ("静音", lambda: ft.detect_silence(
            path,
            A.DEFAULT_OPTIONS["silence_min_duration"],
            A.DEFAULT_OPTIONS["silence_noise_db"])),
    ):
        try:
            dump(name, fn())
        except Exception as exc:
            print(f"    {name}: 失败 {type(exc).__name__}: {exc}")

    print("  --- detect_all（引擎实际使用的联合检测）---")
    try:
        detected = ft.detect_all(path, A.DEFAULT_OPTIONS, duration=duration)
        dump("black", detected["black"])
        dump("freeze", detected["freeze"])
        dump("silence", detected["silence"])
    except Exception as exc:
        print("    detect_all 失败:", exc)
        detected = {"black": [], "freeze": [], "silence": []}

    raw = []
    for s, e in detected["black"]:
        raw.append((s, e, {A.TYPE_BLACK}))
    for s, e in detected["freeze"]:
        raw.append((s, e, {A.TYPE_FREEZE}))
    for s, e in detected["silence"]:
        raw.append((s, e, {A.TYPE_SILENCE}))
    junk = A.merge_intervals(raw, merge_gap=A.DEFAULT_OPTIONS["merge_gap"])
    print("  --- 合并废段 ---")
    print("    junk:", [(round(s, 2), round(e, 2), t) for s, e, t in junk])

    item = {
        "source": {"id": 0, "path": str(path), "name": Path(path).name,
                   "duration": duration},
        "junk": [],
        "_junk_merged": junk,
        "_scenes": None,
    }
    print("  --- 无镜头边界时的有效区间 ---")
    print("   ", [(round(s, 2), round(e, 2))
                  for s, e in A.valid_segments_for(item)])

    # 带真实镜头边界（引擎路径）
    print("  --- 走筛选引擎（含镜头边界）---")
    cfg = dict(fe.DEFAULT_FILTER_CONFIG, cpu_mode=True)
    try:
        res = fe.run_filter([str(path)], cfg)
        src = res["sources"].get(str(path)) or {}
        print("    废段:", [(w["start"], w["end"], w["source"])
                          for w in src.get("waste_segments", [])])
        print("    切点:", [c["time"] for c in src.get("shot_cuts", [])][:10],
              "共", len(src.get("shot_cuts", [])))
        print("    组件错误:", src.get("errors"))
        item2 = fe.to_mix_analysis([str(path)], res)[0]
        segs = A.valid_segments_for(item2)
        print("    最终有效区间:", [(round(s, 2), round(e, 2)) for s, e in segs])
        if not segs:
            print("    !! 有效区间为空 —— 这就是报错的原因")
            print("       junk:", item2.get("_junk_merged"))
            print("       scenes:", item2.get("_scenes"))
    except Exception as exc:
        print("    引擎失败:", type(exc).__name__, exc)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    targets = []
    for arg in argv[1:]:
        p = Path(arg)
        if p.is_dir():
            targets.extend(sorted(
                str(f) for f in p.rglob("*")
                if f.suffix.lower() in (".mp4", ".mov", ".mkv", ".avi")))
        elif p.is_file():
            targets.append(str(p))
        else:
            print("找不到:", arg)
    for t in targets:
        diagnose(t)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
