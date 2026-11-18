# -*- coding: utf-8 -*-
"""扫描真实素材：音轨码率 / 最大音量 / 静音检测结论。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mixcut import ffmpeg_tools as ft

FOLDER = Path(r"C:\Users\Administrator\Desktop\片段\高婷11.4\11.3（连衣裙灰）")


def main():
    ff = ft.find_ffmpeg()
    header = f"{'文件':<30}{'时长':>8}{'音轨码率':>10}{'最大音量':>10}  判定"
    print(header)
    print("-" * 74)
    stats = {"整段静音": 0, "部分静音": 0, "有声": 0, "无音轨": 0}
    for f in sorted(FOLDER.glob("*.mp4")):
        d = ft.probe_details(str(f))
        a = d["audio"]
        dur = float(d["format"]["duration"] or 0)
        if a is None:
            stats["无音轨"] += 1
            print(f"{f.name:<30}{dur:>8.2f}{'-':>10}{'-':>10}  无音轨")
            continue
        segs = ft.detect_silence(str(f), 0.5, -35.0)
        if segs and abs(segs[0][0]) < 0.1 and abs(segs[0][1] - dur) < 0.3:
            verdict = "整段静音"
        elif segs:
            verdict = f"部分静音({len(segs)}段)"
        else:
            verdict = "有声"
        stats["整段静音" if verdict == "整段静音" else
              ("部分静音" if segs else "有声")] += 1
        r = subprocess.run(
            [ff, "-hide_banner", "-i", str(f), "-af", "volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
        maxv = m.group(1) + " dB" if m else "?"
        bitrate = a.get("bit_rate") or 0
        print(f"{f.name:<30}{dur:>8.2f}{bitrate:>10}{maxv:>10}  {verdict}")
    print("-" * 74)
    print("统计:", stats)


if __name__ == "__main__":
    main()
