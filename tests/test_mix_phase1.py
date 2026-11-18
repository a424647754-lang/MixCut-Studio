# -*- coding: utf-8 -*-
"""
阶段 1 测试：素材使用统计、分布均衡、相邻重复控制。

运行：python tests/test_mix_phase1.py
"""

import statistics
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


def make_analysis(spec):
    """spec: [(source_id, duration, tags)]"""
    items = []
    for sid, dur, tags in spec:
        items.append({
            "source": TL.make_source(sid, f"src{sid}.mp4", dur),
            "junk": [],
            "_junk_merged": [],
            "_scenes": None,
            "_tags": tags,
        })
    return items


def tags_map_from(spec):
    return {sid: tags for sid, _d, tags in spec}


def build(analysis, tags_map, seed, **over):
    opts = dict(target_duration=60.0, duration_tolerance=2.0,
                clip_min=2.0, clip_max=4.0, reuse=True, shuffle=True,
                diversity="medium", tags_by_source=tags_map)
    opts.update(over)
    return MB.build_mix_timeline(None, TL.create_canvas(),
                                 mix_options=opts, analysis=analysis,
                                 seed=seed)


def adjacency(project):
    """返回 (同源相邻次数, 同标签相邻次数, 总相邻次数)"""
    clips = project["clips"]
    same_src = same_tag = 0
    tag_map = {}
    for c in clips:
        tag_map.setdefault(c["source_id"], set(c.get("tags") or []))
    for a, b in zip(clips, clips[1:]):
        if a["source_id"] == b["source_id"]:
            same_src += 1
        if tag_map[a["source_id"]] & tag_map[b["source_id"]]:
            same_tag += 1
    return same_src, same_tag, max(0, len(clips) - 1)


def usage_counts(project):
    counts = {}
    for c in project["clips"]:
        counts[c["source_id"]] = counts.get(c["source_id"], 0) + 1
    return counts


def main():
    # 12 个素材，每个 30 秒，标签交替两组
    spec = [(i, 30.0, ["A"] if i % 2 == 0 else ["B"]) for i in range(12)]
    analysis = make_analysis(spec)
    tags_map = tags_map_from(spec)

    # —— 测试 1：均衡性（6 个素材 / 60 秒成片，接近真实界面参数）——
    real_spec = [(i, 30.0, ["A"] if i % 2 == 0 else ["B"]) for i in range(6)]
    real_an = make_analysis(real_spec)
    real_tags = tags_map_from(real_spec)
    counts_all = []
    for seed in range(20):
        p = build(real_an, real_tags, seed, target_duration=60.0,
                  clip_min=2.0, clip_max=4.0)
        counts_all.extend(usage_counts(p).values())
    spread = statistics.pstdev(counts_all) / max(1e-9, statistics.mean(counts_all))
    check("1 素材使用次数离散度 < 0.25", spread < 0.25, f"变异系数 {spread:.3f}")

    # 素材很多时：每个素材都应被用到
    many = [(i, 30.0, ["A"] if i % 2 == 0 else ["B"]) for i in range(12)]
    p = build(make_analysis(many), tags_map_from(many), 1,
              target_duration=90.0, clip_min=2.0, clip_max=4.0)
    used = set(c["source_id"] for c in p["clips"])
    check("1b 12 个素材的成片里都出现", len(used) == 12, f"{len(used)}/12")

    # 素材很少时不应把所有片段都堆在一个素材上
    small = make_analysis([(0, 40.0, ["A"]), (1, 40.0, ["B"]), (2, 40.0, ["C"])])
    small_tags = tags_map_from([(0, 40.0, ["A"]), (1, 40.0, ["B"]), (2, 40.0, ["C"])])
    worst = 0.0
    for seed in range(20):
        p = build(small, small_tags, seed)
        c = usage_counts(p)
        total = sum(c.values())
        worst = max(worst, max(c.values()) / total)
    check("2 3 个素材时没有单个素材占绝对多数", worst < 0.6, f"最高占比 {worst:.2f}")

    # —— 测试 2：相邻重复控制 ——
    # 用“素材少、每素材区间多”的压力场景：素材只有 4 个，区间却很多，
    # 宽松策略下同源相邻才会真实出现，三种强度才有可比性。
    stress_spec = [(i, 120.0, ["A" if i % 2 == 0 else "B"]) for i in range(4)]
    stress = make_analysis(stress_spec)
    stress_tags = tags_map_from(stress_spec)
    results = {}
    for level in ("strict", "medium", "loose"):
        same_src = same_tag = total = 0
        for seed in range(20):
            p = build(stress, stress_tags, seed, diversity=level,
                      target_duration=90.0, clip_min=2.0, clip_max=4.0)
            s, t, n = adjacency(p)
            same_src += s
            same_tag += t
            total += n
        results[level] = (same_src, same_tag, total)
        print(f"    {level}: 同源相邻 {same_src}/{total}，同标签相邻 {same_tag}/{total}")
    check("3 strict 下同源相邻为 0", results["strict"][0] == 0)
    # 强弱对比：宽松模式的“同源刚用过”几乎不扣分，顺序模式下同源素材会
    # 连着取；严格模式不会。用 3 个素材、顺序排列测（纯随机模式下均衡
    # 本身就会把同源错开，看不出强度差异）。
    edge_spec = [(0, 300.0, ["A"]), (1, 300.0, ["A"]), (2, 300.0, ["A"])]
    edge = make_analysis(edge_spec)
    edge_tags = tags_map_from(edge_spec)
    edge_res = {}
    for level in ("strict", "loose"):
        same_src = same_tag = total = 0
        for seed in range(10):
            p = build(edge, edge_tags, seed, diversity=level, shuffle=False,
                      target_duration=60.0, clip_min=2.0, clip_max=4.0)
            s, t, n = adjacency(p)
            same_src += s
            same_tag += t
            total += n
        edge_res[level] = (same_src, same_tag, total)
        print(f"    顺序模式 {level}: 同源相邻 {same_src}/{total}，"
              f"同标签相邻 {same_tag}/{total}")
    check("4 严格 < 宽松（顺序模式同源相邻率）",
          edge_res["strict"][0] < edge_res["loose"][0],
          f"strict {edge_res['strict'][0]} vs loose {edge_res['loose'][0]}")
    check("5 同源相邻：严格 <= 适中 <= 宽松",
          results["strict"][0] <= results["medium"][0] <= results["loose"][0],
          f"{results['strict'][0]} / {results['medium'][0]} / {results['loose'][0]}")
    check("5b 同标签相邻：严格 <= 适中 <= 宽松",
          results["strict"][1] <= results["medium"][1] <= results["loose"][1],
          f"{results['strict'][1]} / {results['medium'][1]} / {results['loose'][1]}")

    # —— 测试 3：reuse=False 不重复消耗 ——
    an = make_analysis([(0, 10.0, []), (1, 10.0, [])])
    for seed in range(10):
        p = build(an, {}, seed, target_duration=18.0, tolerance=0.0,
                  reuse=False, clip_min=1.0, clip_max=2.0)
        segs = sorted((c["source_id"], c["source_start"], c["source_end"])
                      for c in p["clips"])
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                if segs[i][0] != segs[j][0]:
                    continue
                if segs[j][1] < segs[i][2] - 1e-6:
                    check("6 reuse=False 无重复取用", False, f"seed {seed}")
                    return
    check("6 reuse=False 无重复取用", True)

    # —— 测试 4：shuffle=False 顺序模式（严格强度）仍按导入顺序 ——
    # 宽松强度在顺序模式会“就近复用”上一段素材（这是设计差异），
    # 所以顺序语义要用严格/适中强度验证。
    order = make_analysis([(0, 20.0, []), (1, 20.0, []), (2, 20.0, [])])
    p = build(order, {}, 1, shuffle=False, diversity="strict")
    srcs = [c["source_id"] for c in p["clips"]]
    pairs = [srcs[i] for i in range(len(srcs)) if i == 0 or srcs[i] != srcs[i - 1]]
    first_three = pairs[:3]
    check("7 顺序模式仍按导入顺序循环", first_three == [0, 1, 2], str(pairs[:8]))

    # —— 测试 5：同 seed 复现 ——
    a = build(analysis, tags_map, 42, diversity="strict")
    b = build(analysis, tags_map, 42, diversity="strict")
    sig_a = [(c["source_id"], c["source_start"], c["source_end"]) for c in a["clips"]]
    sig_b = [(c["source_id"], c["source_start"], c["source_end"]) for c in b["clips"]]
    check("8 相同 seed 结果完全一致", sig_a == sig_b)
    c = build(analysis, tags_map, 43, diversity="strict")
    sig_c = [(c0["source_id"], c0["source_start"], c0["source_end"]) for c0 in c["clips"]]
    check("9 不同 seed 结果不同", sig_a != sig_c)

    # —— 测试 6：不越界、不为负、片段数合法 ——
    ok = True
    for seed in range(10):
        p = build(analysis, tags_map, seed, target_duration=25.0, tolerance=2.0)
        for clip in p["clips"]:
            if clip["source_end"] - clip["source_start"] <= 1e-6:
                ok = False
            if clip["timeline_end"] - clip["timeline_start"] <= 1e-6:
                ok = False
        if TL.total_duration(p) > 27.0 + 1e-6:
            ok = False
    check("10 无负时长/空片段/超容差", ok)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
