"""多轨预览合成（B-04）。

预览的层叠顺序必须与导出一致：`Project.video_tracks()` 的列表顺序就是合成顺序，
列表首条（V1）是基底、最底，越靠后越靠上；音频轨不参与画面。
导出的空隙补黑场行为在预览里同样成立：没有基底画面就是黑场，而不是"显示别的片段"。

本模块只做"取层 + 画图"，不碰播放器、不写工程数据，便于单测。
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from . import IMAGE_EXTS, Clip, Project
from .media import _CREATE_NO_WINDOW, find_tool, grade_filters


def grade_tag(grade: dict | None) -> str:
    """调色参数的稳定短标签，用于缓存键与抽帧文件名。

    不含调色时返回 `plain`；含调色时按参数哈希，保证"同一帧不同调色"不会
    复用同一张缓存图（项目 12 把调色扩到七键后，键必须覆盖全部字段）。
    """
    if not grade:
        return "plain"
    text = ";".join(f"{k}={round(float(v), 4)}" for k, v in sorted(grade.items()))
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------------------
# 取层
# ---------------------------------------------------------------------------
def layer_stack(project: Project, pos: int) -> list[Clip]:
    """返回 pos 时刻实际参与合成的画面层，**底 → 顶**排序。

    - 隐藏的视频轨不出画；
    - 禁用片段视作空隙（与导出把禁用片段当空隙的约定一致）；
    - 音频轨不参与。
    """
    layers: list[Clip] = []
    for track in project.video_tracks():
        if track.hidden:
            continue
        clip = track.clip_at(pos)
        if clip is None or not clip.enabled:
            continue
        layers.append(clip)
    return layers


def is_image_clip(project: Project, clip: Clip) -> bool:
    asset = project.asset(clip.asset_id)
    if asset is not None and asset.kind == "image":
        return True
    return Path(clip.path).suffix.lower() in IMAGE_EXTS


def fit_rect(source: tuple[int, int], target: tuple[int, int]) -> QRect:
    """等比缩放并居中（等价导出链里的 scale + pad，不裁切、不拉伸）。"""
    sw, sh = source
    tw, th = target
    if sw <= 0 or sh <= 0:
        return QRect(0, 0, tw, th)
    scale = min(tw / sw, th / sh)
    width, height = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    return QRect((tw - width) // 2, (th - height) // 2, width, height)


# ---------------------------------------------------------------------------
# 画图
# ---------------------------------------------------------------------------
def render_composite(project: Project, layers: list[Clip], pos: int,
                     size: tuple[int, int],
                     provider: Callable[[Clip, int], QImage | None]) -> QImage:
    """把各层按底→顶叠加成一张图。

    基底缺层时画黑场（与导出补黑一致）；每层用 `clip.opacity` 做整体不透明度。
    """
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    canvas = QImage(width, height, QImage.Format_ARGB32)
    canvas.fill(Qt.black)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    try:
        for clip in layers:
            image = provider(clip, clip.source_time(pos))
            if image is None or image.isNull() or image.width() <= 0 or image.height() <= 0:
                continue
            painter.setOpacity(max(0.0, min(1.0, float(clip.opacity))))
            painter.drawImage(fit_rect((image.width(), image.height()), (width, height)),
                              image)
    finally:
        painter.setOpacity(1.0)
        painter.end()
    return canvas


# ---------------------------------------------------------------------------
# 取帧（图片直接读，视频走一次 ffmpeg 抽帧）
# ---------------------------------------------------------------------------
def _extract_video_frame(path: str, at_ms: int, out_path: Path,
                         size: tuple[int, int],
                         grade: dict | None = None) -> bool:
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        return False
    width, height = max(2, int(size[0])), max(2, int(size[1]))
    width -= width % 2
    height -= height % 2
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 调色必须在缩放前，与导出链 `_clip_video_chain` 的顺序一致（grade → scale）
    chain = grade_filters(grade)
    chain += [f"scale={width}:{height}:force_original_aspect_ratio=decrease",
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"]
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{max(0, at_ms) / 1000:.3f}", "-i", path,
             "-frames:v", "1",
             "-vf", ",".join(chain),
             str(out_path)],
            capture_output=True, timeout=30, creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and out_path.is_file()


def make_frame_provider(project: Project, size: tuple[int, int],
                        work_dir: Path | None) -> Callable[[Clip, int], QImage | None]:
    """构造带缓存的取帧函数；键含片段 id、50ms 量化源时间与**调色标签**。

    调色标签是项目 13 的验收修复项：此前键只有 `(id, bucket)`，同一帧改了
    调色仍命中旧图，多轨合成视图会"调了没反应"；抽帧时也不带 grade 滤镜，
    两层以上暂停看到的是未调色画面，与导出不一致。
    """
    cache: dict[tuple[str, int, str], QImage] = {}

    def provider(clip: Clip, source_ms: int) -> QImage | None:
        bucket = int(source_ms // 50) * 50
        tag = grade_tag(clip.grade() if clip.is_graded else None)
        key = (clip.id, bucket, tag)
        cached = cache.get(key)
        if cached is not None:
            return cached
        # 图片也要走 ffmpeg 才能上调色；只有无调色时才直接读文件（更快）
        if is_image_clip(project, clip) and tag == "plain":
            image = QImage(clip.path)
        else:
            if work_dir is None:
                return None
            out = Path(work_dir) / f"layer_{clip.id}_{bucket}_{tag}.jpg"
            if not out.is_file() and not _extract_video_frame(
                    clip.path, bucket, out, size, clip.grade() if clip.is_graded else None):
                return None
            image = QImage(str(out))
        if image.isNull():
            return None
        if len(cache) > 96:
            cache.clear()
        cache[key] = image
        return image

    return provider
