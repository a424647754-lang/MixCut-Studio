"""混剪（一键成片）：从素材库按节奏铺满时间线的纯逻辑。

这里只做"计划"，不改任何工程数据，因此可以完全不依赖 Qt 做单元测试；
真正的写入由 TimelineWidget 通过撤销命令完成。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import Clip, MediaAsset, Project

MIN_SEGMENT_MS = 200


@dataclass
class MixSegment:
    asset_id: str
    path: str
    name: str
    start: int
    source_in: int
    duration: int

    def to_clip(self) -> Clip:
        return Clip(
            name=self.name,
            asset_id=self.asset_id,
            path=self.path,
            start=self.start,
            source_in=self.source_in,
            duration=self.duration,
        )


def plan_mix(
    assets: list[MediaAsset],
    total_ms: int,
    segment_ms: int,
    order: str = "sequence",
    seed: int | None = None,
    kinds: tuple[str, ...] = ("video", "image"),
) -> list[MixSegment]:
    """按"每段 segment_ms"把素材铺满 total_ms。

    规则：
      - 只使用 kinds 指定的素材（默认视频与图片，音频不进画面轨）
      - 视频素材足够长时按顺序滑动入点，避免每段都是同一个开头
      - 素材短于一段时长时整段使用，不会凭空拉长
      - 总时长是硬上限，最后一段会被裁到剩余时间
    """
    pool = [a for a in assets if a.kind in kinds and a.path]
    if not pool or total_ms <= 0:
        return []

    seg = max(MIN_SEGMENT_MS, int(segment_ms))
    total = int(total_ms)
    if order == "random":
        rng = random.Random(seed)
        rng.shuffle(pool)
    else:
        rng = random.Random(0)

    result: list[MixSegment] = []
    cursor = 0
    index = 0
    guard = 0
    while cursor < total and guard < 5000:
        guard += 1
        asset = pool[index % len(pool)]
        index += 1
        remaining = total - cursor

        if asset.kind == "image" or asset.duration <= 0:
            length = min(seg, remaining)
            source_in = 0
        else:
            length = min(seg, remaining, max(1, asset.duration))
            room = max(0, asset.duration - length)
            if room <= 0:
                source_in = 0
            elif order == "random":
                source_in = rng.randint(0, room)
            else:
                source_in = (index * seg) % (room + 1)
            source_in = int(source_in // 50 * 50)

        result.append(MixSegment(
            asset_id=asset.id,
            path=asset.path,
            name=asset.name,
            start=cursor,
            source_in=source_in,
            duration=max(1, int(length)),
        ))
        cursor += int(length)
        if length <= 0:
            break

    return result


def build_clips(plan: list[MixSegment], track_start: int = 0) -> list[Clip]:
    """把计划转换成片段（可整体平移到 track_start）。"""
    clips: list[Clip] = []
    for segment in plan:
        clip = segment.to_clip()
        clip.start += int(track_start)
        clips.append(clip)
    return clips


def append_offset(project: Project, track_id: str) -> int:
    """追加方式的起始位置：该轨道现有内容末尾（对齐到帧）。"""
    track = project.track(track_id)
    if track is None:
        return 0
    return track.content_end()
