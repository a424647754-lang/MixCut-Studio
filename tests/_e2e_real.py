# -*- coding: utf-8 -*-
"""用真实素材跑一遍端到端：检测 -> 可用区间 -> 混剪装配 -> 草稿生成。"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mixcut import analysis as A           # noqa: E402
from mixcut import filter_engine as fe     # noqa: E402
from mixcut import mixbuilder as MB        # noqa: E402
from mixcut import project_builder as PB   # noqa: E402
from mixcut import timeline as TL          # noqa: E402
from mixcut import transitions as TR       # noqa: E402

FOLDER = Path(r"C:\Users\Administrator\Desktop\片段\高婷11.4\11.3（连衣裙灰）")


def main():
    paths = sorted(str(p) for p in FOLDER.glob("*.mp4"))
    print(f"素材 {len(paths)} 个")

    cfg = dict(fe.DEFAULT_FILTER_CONFIG, cpu_mode=True)
    res = fe.run_filter(paths, cfg)
    analysis = fe.to_mix_analysis(paths, res)

    total_valid = 0.0
    print(f"{'文件':<26}{'时长':>7}{'废段':>6}{'有效区间':>8}{'可用秒数':>10}")
    print("-" * 60)
    for item in analysis:
        name = Path(item["source"]["path"]).name
        segs = A.valid_segments_for(item)
        secs = sum(e - s for s, e in segs)
        total_valid += secs
        print(f"{name:<26}{item['source']['duration']:>7.2f}"
              f"{len(item.get('_junk_merged') or []):>6}{len(segs):>8}{secs:>10.2f}")
    print("-" * 60)
    print(f"可用素材总时长: {total_valid:.1f} 秒")

    project = MB.build_mix_timeline(
        paths, TL.create_canvas(1080, 1920, 30),
        mix_options=dict(target_duration=20.0, duration_tolerance=2.0,
                         clip_min=1.0, clip_max=3.0, reuse=True),
        analysis=analysis, seed=7)
    print(f"混剪装配: {len(project['clips'])} 片段, "
          f"总时长 {TL.total_duration(project):.2f} 秒")

    TR.apply_transition_rules(project, TR.DEFAULT_TRANSITION_OPTIONS)
    out = Path(tempfile.mkdtemp(prefix="mixcut_real_"))
    try:
        result = PB.build_draft(project, str(out), draft_name="real_test")
        print("草稿生成:", result["draft_name"], result["clip_count"], "片段",
              f"{result['duration']:.2f} 秒")
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main()
