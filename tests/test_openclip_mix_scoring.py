# -*- coding: utf-8 -*-
"""OpenCLIP 混剪接入回归：只验证语义新鲜度评分，不需要加载模型。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mixcut import mixbuilder as MB


def main():
    ranges = [
        {"source_id": 1, "start": 0.0, "end": 5.0, "cursor": 0.0,
         "quality_score": 70.0, "clip_embedding": [1.0, 0.0]},
        {"source_id": 2, "start": 0.0, "end": 5.0, "cursor": 0.0,
         "quality_score": 70.0, "clip_embedding": [0.0, 1.0]},
    ]
    state = MB._decision_state()
    state["last_source"] = 1
    state["clip_recent"] = [[1.0, 0.0]]
    state["openclip_weight"] = 2.0
    weights = dict(MB._REPEAT_WEIGHTS["medium"])
    weights.update(quality=2.5, openclip=2.0)

    same = MB._candidate_score(0, ranges, state, 10.0, {}, "medium", weights)
    novel = MB._candidate_score(1, ranges, state, 10.0, {}, "medium", weights)
    assert novel > same, (same, novel)

    # 无向量时必须退化为原有评分，不应凭空产生语义加分。
    plain = [dict(r, clip_embedding=None) for r in ranges]
    plain_state = MB._decision_state()
    plain_state["last_source"] = 1
    plain_state["openclip_weight"] = 2.0
    base_weights = dict(weights)
    base_weights["openclip"] = 2.0
    score_without_vector = MB._candidate_score(
        1, plain, plain_state, 10.0, {}, "medium", base_weights)
    no_clip_state = MB._decision_state()
    no_clip_state["last_source"] = 1
    no_clip_state["openclip_weight"] = 0.0
    no_clip_weights = dict(base_weights)
    no_clip_weights["openclip"] = 0.0
    baseline = MB._candidate_score(
        1, plain, no_clip_state, 10.0, {}, "medium", no_clip_weights)
    assert abs(score_without_vector - baseline) < 1e-9

    # 决策记录只保留最近 5 个向量，避免长成片持续增长内存。
    for i in range(7):
        MB._record_decision(state, 0, ranges[0], set(), float(i), 1.0)
    assert len(state["clip_recent"]) == 5
    print("[PASS] OpenCLIP 语义不同的候选获得更高新鲜度评分")
    print("[PASS] 缺失向量时完全回退原评分")
    print("[PASS] 语义历史只保留最近 5 个向量")
    print("全部通过")


if __name__ == "__main__":
    main()
