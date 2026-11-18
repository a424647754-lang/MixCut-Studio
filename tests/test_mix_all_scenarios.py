# -*- coding: utf-8 -*-
"""
阶段 6：需求文档第八节要求的 15 个场景全量测试。

覆盖：
 1 普通随机混剪            2 顺序排列混剪
 3 允许重复使用素材        4 禁止重复使用素材
 5 素材总时长不足          6 素材总时长充足
 7 存在音乐鼓点            8 没有音乐鼓点
 9 素材区间较少            10 素材区间较多
11 目标时长较短            12 目标时长较长
13 中途取消任务            14 相同 seed 复现结果
15 多版本生成差异化

重点检查：不死循环、不产生负数/零时长、不越界、不超容差、源/目标时间正确。

运行：python tests/test_mix_all_scenarios.py
"""

import sys
import time
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


def item(sid, dur, junk=(), scenes=None):
    return {"source": TL.make_source(sid, f"src{sid}.mp4", dur), "junk": [],
            "_junk_merged": list(junk), "_scenes": scenes}


def build(analysis, seed=1, **over):
    opts = dict(target_duration=60.0, duration_tolerance=2.0,
                clip_min=2.0, clip_max=5.0, reuse=True, shuffle=True,
                diversity="medium", rhythm="balance")
    opts.update(over)
    t0 = time.time()
    project = MB.build_mix_timeline(None, TL.create_canvas(),
                                    mix_options=opts, analysis=analysis,
                                    seed=seed)
    return project, time.time() - t0, opts


def validate(project, opts, analysis, label):
    """统一校验：不越界、不为负、时间线连续、时长不超容差。"""
    clips = project["clips"]
    problems = []
    total = TL.total_duration(project)
    if total > opts["target_duration"] + opts["duration_tolerance"] + 1e-6:
        problems.append(f"超出目标上限 {total:.2f}s")
    lim = {it["source"]["id"]: it for it in analysis}
    prev_end = 0.0
    for c in clips:
        if c["source_end"] - c["source_start"] <= 1e-6:
            problems.append("源区间为空/负")
        if c["timeline_end"] - c["timeline_start"] <= 1e-6:
            problems.append("时间线片段为空/负")
        if abs(c["timeline_start"] - prev_end) > 1e-6:
            problems.append("时间线不连续")
        prev_end = c["timeline_end"]
        it = lim.get(c["source_id"])
        if it is None:
            problems.append("引用了不存在的素材")
            continue
        if (c["source_start"] < -1e-6
                or c["source_end"] > it["source"]["duration"] + 1e-3):
            problems.append("源时间越界")
        for js, je, _t in it["_junk_merged"]:
            if c["source_start"] < je - 1e-6 and c["source_end"] > js + 1e-6:
                problems.append("取到了废镜头区间")
    check(f"{label}：结构校验", not problems,
          "; ".join(sorted(set(problems))[:3]))


def main():
    A = lambda n, d=120.0: [item(i, d) for i in range(n)]

    # 1 普通随机混剪
    p, dt, o = build(A(6))
    validate(p, o, A(6), "1 普通随机混剪")
    srcs = [c["source_id"] for c in p["clips"]]
    check("1 随机混剪用到多个素材", len(set(srcs)) >= 4,
          f"{len(set(srcs))} 个来源，{len(srcs)} 片段，{dt * 1000:.0f}ms")

    # 2 顺序排列混剪（顺序模式保持相邻素材按导入顺序轮转，起点随 seed）
    p, dt, o = build(A(4), shuffle=False, diversity="strict")
    srcs = [c["source_id"] for c in p["clips"]]
    seq = [srcs[i] for i in range(len(srcs))
           if i == 0 or srcs[i] != srcs[i - 1]]
    steps = [(seq[i] - seq[i - 1]) % 4 for i in range(1, len(seq))]
    check("2 顺序排列按导入顺序循环",
          len(set(steps)) == 1 and steps[0] in (1, 3), str(seq[:8]))

    # 3 允许重复使用素材
    an = [item(i, 10.0) for i in range(2)]
    p, dt, o = build(an, target_duration=50.0, reuse=True)
    validate(p, o, an, "3 允许重复使用")
    check("3 重复使用时素材被循环使用", len(p["clips"]) > 8,
          f"{len(p['clips'])} 片段")

    # 4 禁止重复使用素材
    an = [item(i, 10.0) for i in range(2)]
    p, dt, o = build(an, target_duration=15.0, reuse=False)
    validate(p, o, an, "4 禁止重复使用")
    segs = sorted((c["source_id"], c["source_start"], c["source_end"])
                  for c in p["clips"])
    dup = any(segs[j][1] < segs[i][2] - 1e-6
              for i in range(len(segs)) for j in range(i + 1, len(segs))
              if segs[i][0] == segs[j][0])
    check("4 禁止重复时不重复取用同一段", not dup)

    # 5 素材总时长不足
    an = [item(0, 5.0), item(1, 5.0)]
    try:
        MB.build_mix_timeline(None, TL.create_canvas(), mix_options=dict(
            target_duration=60.0, duration_tolerance=2.0, clip_min=2.0,
            clip_max=5.0, reuse=False), analysis=an, seed=1)
        check("5 素材不足时报错", False)
    except RuntimeError as exc:
        check("5 素材不足时报错", "低于目标时长下限" in str(exc), str(exc)[:40])

    # 6 素材总时长充足
    p, dt, o = build(A(10), target_duration=120.0)
    validate(p, o, A(10), "6 素材充足")
    check("6 素材充足时接近目标", abs(TL.total_duration(p) - 120.0) <= 2.0 + 1e-6,
          f"{TL.total_duration(p):.2f}s")

    # 7 存在音乐鼓点 / 8 没有音乐鼓点
    beats = [0.5 * i for i in range(0, 600)]
    p, dt, o = build(A(4), beats=beats)
    ends = [c["timeline_end"] for c in p["clips"]][:-1]
    err = max(min(abs(e - b) for b in beats) for e in ends)
    check("7 有鼓点时切点落在拍点", err < 0.02, f"最大误差 {err:.3f}s")
    p, dt, o = build(A(4))
    check("8 没有鼓点也能正常生成", len(p["clips"]) > 5)

    # 9 素材区间较少 / 10 素材区间较多
    small = [item(0, 40.0)]
    p, dt, o = build(small, target_duration=30.0)
    check("9 区间很少时仍能生成", len(p["clips"]) >= 5)
    many = [item(i, 60.0, junk=[(10.0, 20.0, ["black"]), (40.0, 50.0, ["black"])]) for i in range(30)]
    p, dt, o = build(many, target_duration=60.0)
    validate(p, o, many, "10 区间很多")
    check("10 区间很多时性能可接受", dt < 2.0, f"{dt * 1000:.0f}ms")

    # 11 目标时长较短 / 12 目标时长较长
    p, dt, o = build(A(5), target_duration=8.0, duration_tolerance=1.0,
                     clip_min=1.5, clip_max=3.0)
    validate(p, o, A(5), "11 短目标")
    p, dt, o = build(A(20), target_duration=600.0)
    validate(p, o, A(20), "12 长目标")
    check("12 长目标性能", dt < 5.0,
          f"600 秒 / {len(p['clips'])} 片段 / {dt * 1000:.0f}ms")

    # 13 中途取消
    state = {"n": 0}

    def cancel():
        state["n"] += 1
        return state["n"] > 30

    p = MB.build_mix_timeline(None, TL.create_canvas(), mix_options=dict(
        target_duration=600.0, clip_min=2.0, clip_max=5.0, reuse=True),
        analysis=A(20), seed=1, cancel_check=cancel)
    check("13 取消检查生效（提前结束）", TL.total_duration(p) < 600.0
          and state["n"] > 30, f"停在 {TL.total_duration(p):.1f}s")

    # 14 相同 seed 复现
    a, _, _ = build(A(6), seed=99)
    b, _, _ = build(A(6), seed=99)
    sig = lambda pr: [(c["source_id"], c["source_start"], c["source_end"]) for c in pr["clips"]]
    check("14 相同 seed 结果完全一致", sig(a) == sig(b))
    c, _, _ = build(A(6), seed=100)
    check("14b 不同 seed 结果不同", sig(a) != sig(c))

    # 15 多版本差异化
    styles = {}
    for i in range(5):
        opts, style = MB.resolve_version_options(dict(
            target_duration=90.0, clip_min=2.0, clip_max=5.0, reuse=True), i, 5)
        proj = MB.build_mix_timeline(None, TL.create_canvas(),
                                     mix_options=opts, analysis=A(8),
                                     seed=1000 + i)
        styles[style] = (tuple(sig(proj)), len(proj["clips"]))
    check("15 轮换出 5 个版本", len(styles) == 5)
    check("15b 五个版本互不相同", len({v[0] for v in styles.values()}) == 5)
    check("15c 快节奏版本片段数多于均衡版本",
          styles["fast"][1] > styles["balanced"][1],
          f"{styles['fast'][1]} vs {styles['balanced'][1]}")

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
