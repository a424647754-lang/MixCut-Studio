# -*- coding: utf-8 -*-
"""
阶段 2 测试：动态片段时长分配（均衡 / 长短交替 / 快速 / 舒缓）。

运行：python tests/test_mix_phase2.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mixcut import mixbuilder as MB          # noqa: E402
from mixcut import timeline as TL            # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


ANALYSIS = [{"source": TL.make_source(0, "s.mp4", 600.0), "junk": [],
             "_junk_merged": [], "_scenes": None}]


def lengths(mode, seed, clip_min=2.0, clip_max=6.0, target=120.0):
    p = MB.build_mix_timeline(
        None, TL.create_canvas(),
        mix_options=dict(target_duration=target, clip_min=clip_min,
                         clip_max=clip_max, reuse=True, rhythm=mode),
        analysis=ANALYSIS, seed=seed)
    return [c["timeline_end"] - c["timeline_start"]
            for c in p["clips"]], p


def stats(mode, n=10):
    means, spread, alt = [], [], []
    for seed in range(n):
        lens, _p = lengths(mode, seed)
        body = lens[:-1] or lens
        means.append(sum(body) / len(body))
        spread.append(max(body) - min(body))
        alt.append(sum(1 for a, b in zip(body, body[1:]) if abs(a - b) > 2.0)
                   / max(1, len(body) - 1))
    return (sum(means) / n, sum(spread) / n, sum(alt) / n)


def main():
    s = {m: stats(m) for m in ("balance", "dynamic", "fast", "slow")}
    for m, (mean, spread, alt) in s.items():
        print(f"    {m}: 平均 {mean:.2f}s 跨度 {spread:.2f}s 长短邻接比例 {alt:.2f}")

    check("1 快速 < 均衡 < 舒缓（平均时长）",
          s["fast"][0] < s["balance"][0] < s["slow"][0],
          f"{s['fast'][0]:.2f} / {s['balance'][0]:.2f} / {s['slow'][0]:.2f}")
    check("2 长短交替明显多于均衡（邻接长度差）",
          s["dynamic"][2] > s["balance"][2] + 0.3,
          f"{s['dynamic'][2]:.2f} vs {s['balance'][2]:.2f}")
    check("3 均衡比其它策略更集中在中间",
          s["balance"][2] <= min(s["fast"][2], s["slow"][2]) + 0.15,
          f"{s['balance'][2]:.2f}")

    # 边界：最短=最长时所有片段等长
    lens, _p = lengths("dynamic", 3, clip_min=3.0, clip_max=3.0, target=60.0)
    check("4 最短=最长时片段长度恒定",
          all(abs(x - 3.0) < 1e-6 for x in lens), str(set(round(x, 3) for x in lens)))

    # 边界：上下限都很窄
    lens, _p = lengths("fast", 4, clip_min=4.8, clip_max=5.0, target=60.0)
    check("5 窄区间仍在上下限内",
          all(4.8 - 1e-6 <= x <= 5.0 + 1e-6 for x in lens[:-1]) or len(lens) <= 2)

    # 不复现：同 seed 同结果
    a, _ = lengths("dynamic", 7)
    b, _ = lengths("dynamic", 7)
    check("6 同 seed 复现", a == b)
    c, _ = lengths("dynamic", 8)
    check("7 不同 seed 不同", a != c)

    # 不越界/不超容差
    ok = True
    for mode in ("balance", "dynamic", "fast", "slow"):
        for seed in range(6):
            lens, p = lengths(mode, seed, target=40.0)
            total = TL.total_duration(p)
            if total > 40.0 + 2.0 + 1e-6:
                ok = False
            if any(x <= 1e-6 for x in lens):
                ok = False
    check("8 时长不超容差、无空片段", ok)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
