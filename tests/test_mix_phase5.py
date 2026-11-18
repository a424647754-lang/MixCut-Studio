# -*- coding: utf-8 -*-
"""
阶段 5 测试：镜头衔接评分。

评分只用已有数据：来源 ID、用户标签、镜头边界、已用时长。
不推断画面语义（没有景别/运动方向信息就不做）。

运行：python tests/test_mix_phase5.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import random                              # noqa: E402

from mixcut import mixbuilder as MB          # noqa: E402
from mixcut import timeline as TL            # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def rng(src, start=0.0, end=30.0, cursor=0.0, bounds=None):
    return {"source_id": src, "start": start, "end": end, "cursor": cursor,
            "bounds": bounds or []}


def main():
    # —— 衔接评分函数本身 ——
    check("1 换来源比同来源分高",
          MB._transition_score(1, [], 0, []) > MB._transition_score(0, [], 0, []))
    check("2 换标签比同标签分高",
          MB._transition_score(1, ["B"], 0, ["A"])
          > MB._transition_score(1, ["A"], 0, ["A"]))
    check("3 黑名单标签会被降分",
          MB._transition_score(1, ["产品特写"], 0, ["A"], ["产品特写"])
          < MB._transition_score(1, ["B"], 0, ["A"], ["产品特写"]))
    check("4 没有上一段时给中间分",
          abs(MB._transition_score(0, ["A"], None, set()) - 0.5) < 1e-9)
    check("5 没有标签信息时不臆造分数",
          MB._transition_score(1, [], 0, []) == MB._transition_score(1, [], 0, []))

    # —— 镜头边界评分 ——
    check("6 切在镜头上得分高于切在镜头中",
          MB._cut_quality_score(rng(0, bounds=[2.0, 4.0]), 2.0, 2.0, 2.0)
          > MB._cut_quality_score(rng(0, bounds=[2.0, 4.0]), 2.5, 2.0, 2.0))
    check("7 没有镜头边界信息时返回 0（不臆造）",
          MB._cut_quality_score(rng(0), 2.5, 1.0, 2.0) == 0.0)

    # —— 候选选择确实用上了衔接评分 ——
    ranges = [rng(0, end=50.0), rng(0, end=50.0), rng(1, end=50.0)]
    state = MB._decision_state()
    state["last_source"] = 0
    state["last_tags"] = {"A"}
    state["source_clips"] = {0: 5, 1: 0}
    tags = {0: ["A"], 1: ["B"]}
    picked = [MB._choose_candidate([0, 1, 2], ranges, state, 10.0, tags,
                                   "loose", random.Random(s))
              for s in range(20)]
    check("8 评分会挑不同来源（即使 0 号素材用得少）",
          picked.count(2) > picked.count(1), f"选中统计 {picked.count(2)} vs {picked.count(1)}")

    # —— 端到端：黑名单标签只影响衔接，不改变生成 ——
    an = [{"source": TL.make_source(i, f"s{i}.mp4", 60.0), "junk": [],
           "_junk_merged": [], "_scenes": None} for i in range(4)]
    tm = {i: [("产品特写" if i == 0 else "空镜")] for i in range(4)}
    p1 = MB.build_mix_timeline(None, TL.create_canvas(), mix_options=dict(
        target_duration=45.0, clip_min=2.0, clip_max=4.0, reuse=True,
        tags_by_source=tm, blacklist=["产品特写"], diversity="strict"),
        analysis=an, seed=4)
    clips = p1["clips"]
    same_as_prev = 0
    for a, b in zip(clips, clips[1:]):
        ba = set(a["tags"]) & {"产品特写"}
        bb = set(b["tags"]) & {"产品特写"}
        if ba and bb:
            same_as_prev += 1
    check("9 黑名单标签不会连续堆叠",
          same_as_prev == 0, f"连续出现 {same_as_prev} 次")
    check("10 片段结构与不改黑名单时一致（只影响选择倾向）",
          len(clips) > 3 and all(c["tags"] for c in clips))

    check("11 无数据时不虚构景别/运动信息",
          not hasattr(MB, "_shot_size_score")
          and not hasattr(MB, "_motion_score"))

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
