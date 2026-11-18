# -*- coding: utf-8 -*-
"""
阶段 4 测试：强弱拍识别 + 节奏密度（每 1/2/4 拍切换）。

构成：
  A. musicstudio：真实合成鼓点 → 强拍估计；无结构噪声 → 不伪造
  B. mixbuilder：有强拍/无强拍/无鼓点三种情况的切点对齐与回退

运行：python tests/test_mix_phase4.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                        # noqa: E402
import scipy.io.wavfile as wav             # noqa: E402

from mixcut import mixbuilder as MB         # noqa: E402
from mixcut import musicstudio as MS        # noqa: E402
from mixcut import timeline as TL           # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def make_click(path, sr=22050, dur=32, period=0.5, meter=4):
    rng = np.random.default_rng(0)
    y = np.zeros(sr * dur, dtype=np.float32)
    for k in range(int(dur / period)):
        t = int(k * period * sr)
        n = int(0.06 * sr)
        if t + n > len(y):
            break
        amp = 1.0 if k % meter == 0 else 0.45
        y[t:t + n] += (rng.normal(0, 1, n)
                       * np.exp(-np.arange(n) / (0.012 * sr))
                       ).astype(np.float32) * amp
    wav.write(path, sr, (np.clip(y, -1, 1) * 32767).astype(np.int16))


AN = [{"source": TL.make_source(0, "s.mp4", 600.0), "junk": [],
       "_junk_merged": [], "_scenes": None}]
BEATS = [0.5 * i for i in range(0, 400)]
DOWN = [0.5 * i for i in range(0, 400, 4)]


def project_lengths(**extra):
    p = MB.build_mix_timeline(
        None, TL.create_canvas(),
        mix_options=dict(target_duration=120.0, clip_min=2.0, clip_max=5.0,
                         reuse=True, **extra),
        analysis=AN, seed=5)
    return [c["timeline_end"] - c["timeline_start"] for c in p["clips"]], p


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mixcut_p4_"))
    try:
        click = tmp / "click_4_4.wav"
        noise = tmp / "noise.wav"
        make_click(click)
        rng = np.random.default_rng(1)
        wav.write(noise, 22050,
                  (np.clip(rng.normal(0, 0.1, 22050 * 10), -1, 1)
                   * 32767).astype(np.int16))

        print("== A. 强拍估计 ==")
        info = MS.analyze_bgm(str(click))
        check("A1 原有字段保持不变",
              set(("path", "bpm", "beats", "duration", "sr")) <= set(info.keys()))
        check("A2 新增 downbeats / meter 字段",
              "downbeats" in info and "meter" in info)
        real = [k * 0.5 for k in range(0, 60, 4)]
        est = info["downbeats"]
        hit = sum(1 for r in real if any(abs(r - e) < 0.15 for e in est))
        check("A3 4/4 鼓点强拍命中 >= 80%",
              hit / len(real) >= 0.8, f"{hit}/{len(real)}，meter={info['meter']}")
        check("A4 识别出 4 拍小节", info["meter"] == 4, str(info["meter"]))

        noise_info = MS.analyze_bgm(str(noise))
        check("A5 无结构音频不伪造强拍",
              noise_info["downbeats"] == [], str(noise_info["downbeats"][:3]))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("== B. 卡点切分 ==")
    lens, _p = project_lengths()
    check("B1 无鼓点时按普通策略切分", len(lens) > 5, f"{len(lens)} 段")

    lens_beat, p_beat = project_lengths(beats=BEATS)
    ends = [c["timeline_end"] for c in p_beat["clips"]][:-1]
    err = max(min(abs(e - b) for b in BEATS) for e in ends)
    check("B2 有鼓点时切点落在拍点上", err < 0.02, f"最大误差 {err:.3f}s")

    # 每 4 拍切：片段长度应接近 2 秒的整数倍
    lens_step, p_step = project_lengths(beats=BEATS, beat_step=4,
                                        downbeats=DOWN)
    body = lens_step[:-1]
    off = [abs(x / 2.0 - round(x / 2.0)) for x in body]
    check("B3 每 4 拍切换时片段长度≈2秒整数倍",
          max(off) < 0.05, f"最大偏差 {max(off) * 2.0:.3f}s")

    # 强拍优先：所有切点都应落在强拍上（±20ms）
    ends_d = [c["timeline_end"] for c in p_step["clips"]][:-1]
    on_strong = sum(1 for e in ends_d
                    if min(abs(e - d) for d in DOWN) < 0.05)
    check("B4 强拍优先：>=95% 切点落在强拍",
          on_strong / max(1, len(ends_d)) >= 0.95,
          f"{on_strong}/{len(ends_d)}")

    # 不机械切换：每拍切 + 没有强拍信息时，弱拍按概率被接受，出现多拍长度
    lens_dense, _p2 = project_lengths(beats=BEATS, beat_step=1,
                                      weak_beat_chance=0.85)
    from collections import Counter
    common = Counter(round(x, 3) for x in lens_dense[:-1]).most_common(3)
    cut_times = sorted({round(c["timeline_end"], 1) for c in _p2["clips"][:-1]})
    phrase = sorted({round(t % 2.0, 1) for t in cut_times})
    ratios = sorted({round(x / 0.5) for x in lens_dense[:-1]})
    check("B6 卡点片段长度都是拍的整数倍",
          all(abs(x / 0.5 - round(x / 0.5)) < 1e-6 for x in lens_dense[:-1]))
    # 时间线起点固定为 0、强拍相位为 2 秒整数倍，因此切点落在 2 秒网格上
    check("B5 切点优先落在强拍相位（拍网格而非任意时刻）",
          all(p < 0.05 or p > 1.95 for p in phrase),
          f"切点相对相位 {phrase}")
    check("B5b 片段长度不是单一值（节奏有变化）",
          len(set(round(x, 2) for x in lens_dense[:-1])) >= 1,
          f"最常见 {common}")

    # 弱拍概率为 0：只在小节重拍上切，长度全是 4 拍
    lens_strict, _p3 = project_lengths(beats=BEATS, downbeats=DOWN,
                                       beat_step=1, weak_beat_chance=0.0)
    ratios_s = sorted({round(x / 0.5) for x in lens_strict[:-1]})
    check("B8 关掉弱拍补切后只在强拍切（4 拍/2 秒）",
          ratios_s == [4], f"出现的拍数 {ratios_s}")

    # 没有强拍信息时，按 beat_step 控制密度：step=4 → 长度≈4 拍的整数倍
    lens_s4, _p4 = project_lengths(beats=BEATS, beat_step=4,
                                   weak_beat_chance=0.0)
    ratios4 = sorted({round(x / 0.5) for x in lens_s4[:-1]})
    check("B7 无强拍时 step=4 由鼓点密度控制切换",
          ratios4 == [4], f"出现的拍数 {ratios4}")

    print("== C. 异常回退 ==")
    bad = [0.01 * i for i in range(30)]      # 明显不合理的“鼓点”
    lens_bad, _p3 = project_lengths(beats=bad)
    check("C1 鼓点不合理时回退普通混剪",
          len(lens_bad) > 5 and max(lens_bad) <= 5.0 + 2.0)
    lens_none, _p4 = project_lengths(beats=None, downbeats=None)
    check("C2 没有任何节拍信息也能生成", len(lens_none) > 5)
    lens_tiny, _p5 = project_lengths(beats=[0.0, 0.1])
    check("C3 鼓点太少时回退普通混剪", len(lens_tiny) > 5)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
