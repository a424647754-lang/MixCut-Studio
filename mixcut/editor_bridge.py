# -*- coding: utf-8 -*-
"""MixCut 混剪时间线 ↔ 剪辑器工程 的适配层。

两个包的关系
============
本仓库有两个并列的包，**互不 import**：

| 包 | 职责 |
|---|---|
| ``mixcut/`` | 素材分析、判废、混剪决策、剪映草稿 |
| ``editor/`` | 专业 NLE 剪辑器（多轨时间线、调色、导出） |

这一层是两者**唯一的通信点**。它只依赖 ``editor`` 的公开数据结构
（``Project`` / ``Track`` / ``Clip`` / ``MediaAsset``），
``editor/`` 那边完全不知道 ``mixcut/`` 的存在。

单位差异（重要）
================
| | mixcut/timeline.py | editor/__init__.py |
|---|---|---|
| 时间单位 | **秒（float）** | **毫秒（int）** |
| 片段位置 | ``timeline_start`` / ``timeline_end`` | ``start`` + ``duration`` |
| 素材入点 | ``source_start`` | ``source_in`` |
| 素材引用 | ``sources[].id`` | ``Clip.asset_id`` |

本模块负责把这四个差异全部吃掉。浮点秒转毫秒时**用 round 而不是 int**，
否则 2.9996 秒会被截成 2999 毫秒，累积下来画面会偏一帧。
"""
from __future__ import annotations

import os
from pathlib import Path

# 剪辑器工程文件的默认后缀（与 editor.PROJECT_FORMAT 对应）
EDITOR_SUFFIX = ".mixcut"

# 秒 → 毫秒
def _ms(seconds):
    """秒（float）→ 毫秒（int），四舍五入而不是截断。"""
    try:
        return int(round(float(seconds) * 1000.0))
    except (TypeError, ValueError):
        return 0


def _fps_string(value):
    """把 canvas 里的 fps（可能是 int/float/字符串）规范成 editor 要的 "30/1" 形式。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "30/1"
    if abs(f - round(f)) < 1e-6:
        return "%d/1" % int(round(f))
    # 23.976 → 24000/1001 这类；先试常见值，否则放大成整数比
    for num, den in ((24000, 1001), (30000, 1001), (60000, 1001),
                     (48000, 1001)):
        if abs(f - num / den) < 0.01:
            return "%d/%d" % (num, den)
    return "%d/%d" % (int(round(f * 1000)), 1000)


def to_editor_project(timeline, *, name="", width=None, height=None,
                      fps=None):
    """把 ``mixcut.timeline`` 的时间线转成 ``editor.Project``。

    Args:
        timeline: ``timeline.create_timeline()`` 的产物
        name: 工程名；空则用 "MixCut 混剪"
        width/height/fps: 覆盖画布参数；不给就用 timeline["canvas"]

    Returns:
        ``editor.Project``

    Raises:
        ImportError: editor 包不可用
        ValueError: timeline 结构不完整
    """
    from editor import Clip, MediaAsset, Project, Track

    tl = timeline or {}
    clips_in = list(tl.get("clips") or [])
    canvas = tl.get("canvas") or {}

    proj = Project(
        name=str(name or "MixCut 混剪"),
        width=int(width or canvas.get("width") or 1920),
        height=int(height or canvas.get("height") or 1080),
        fps=_fps_string(fps or canvas.get("fps") or 30),
    )
    # Project() 会预置 V1/A1 两条空轨，这里清空后按 need 重建
    proj.tracks = []
    proj.assets = []

    # ---- 素材：timeline["sources"] → MediaAsset ----
    asset_of_source = {}
    for src in tl.get("sources") or []:
        try:
            sid = int(src.get("id"))
        except (TypeError, ValueError):
            continue
        path = str(src.get("path") or "")
        if not path:
            continue
        asset = MediaAsset(
            path=path,
            name=str(src.get("name") or Path(path).name),
            kind="video",
            duration=_ms(src.get("duration")),
            fps=proj.fps,
        )
        proj.assets.append(asset)
        asset_of_source[sid] = asset

    # ---- 视频轨 ----
    vtrack = Track(name="V1", kind="video")
    for c in clips_in:
        try:
            sid = int(c.get("source_id"))
        except (TypeError, ValueError):
            continue
        asset = asset_of_source.get(sid)
        if asset is None:
            # 找不到素材引用就跳过——宁可少一段，也不要生成指向空路径的片段
            continue
        t_start = _ms(c.get("timeline_start"))
        t_end = _ms(c.get("timeline_end"))
        s_in = _ms(c.get("source_start"))
        s_end = _ms(c.get("source_end"))
        dur = max(0, t_end - t_start)
        if dur <= 0:
            continue
        vtrack.clips.append(Clip(
            name=str(c.get("tags") and "、".join(c["tags"]) or asset.name),
            asset_id=asset.id,
            path=asset.path,
            start=t_start,
            source_in=s_in,
            duration=dur,
        ))
    vtrack.clips.sort(key=lambda x: x.start)

    # ---- 音频轨：配乐放 A1 ----
    atrack = Track(name="A1", kind="audio")
    music = tl.get("music") or {}
    music_path = str(music.get("path") or "")
    if music_path and os.path.isfile(music_path):
        from editor import MediaAsset as _MA
        music_asset = _MA(
            path=music_path,
            name=Path(music_path).name,
            kind="audio",
            duration=_ms(music.get("duration")),
            has_video=False,
            has_audio=True,
        )
        proj.assets.append(music_asset)
        # 音乐铺满整个视频内容长度
        content_ms = vtrack.content_end() if vtrack.clips else 0
        m_dur = _ms(music.get("duration")) or content_ms
        atrack.clips.append(Clip(
            name=Path(music_path).name,
            asset_id=music_asset.id,
            path=music_path,
            start=0,
            source_in=0,
            duration=min(m_dur, content_ms) if content_ms else m_dur,
            volume=float(music.get("volume") or 1.0),
        ))

    proj.tracks.append(vtrack)
    if atrack.clips:
        proj.tracks.append(atrack)
    else:
        proj.tracks.append(atrack)      # 空音轨也保留，方便用户往里拖音乐
    proj.base_video_id = vtrack.id
    return proj


def save_editor_project(timeline, path, **kwargs):
    """时间线 → 剪辑器工程文件（JSON）。

    Args:
        timeline: mixcut 时间线
        path: 输出路径（建议 ``.mixcut``）

    Returns:
        实际写入的路径
    """
    proj = to_editor_project(timeline, **kwargs)
    target = Path(path)
    if target.suffix.lower() != EDITOR_SUFFIX:
        target = target.with_suffix(EDITOR_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    proj.save(str(target))
    return str(target)


def available():
    """剪辑器是否可用（PySide6 等依赖是否齐）。

    只做**导入检查**，不启动 GUI。
    """
    try:
        import editor  # noqa: F401
        from editor import Project  # noqa: F401
        return True
    except Exception:
        return False


def open_in_editor(project_path, *, wait=False):
    """以**独立进程**启动剪辑器并打开指定工程。

    为什么用子进程
    --------------
    MixCut 自己已经有一个 ``QApplication``。Qt 不允许同一进程里
    存在两个 ``QApplication``，同进程内嵌会直接崩。
    用独立进程还有一个好处：剪辑器崩了不会带崩 MixCut。

    Args:
        project_path: ``save_editor_project()`` 写出的工程文件
        wait: True 时阻塞到剪辑器退出（测试用），False 时立即返回

    Returns:
        ``subprocess.Popen`` 对象；失败返回 None。
    """
    import subprocess
    import sys

    target = Path(project_path)
    if not target.is_file():
        return None

    # 工程包在仓库根目录下，把根目录放进 PYTHONPATH 让子进程能找到 editor
    repo_root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (str(repo_root) + (os.pathsep + old if old else ""))

    cmd = [sys.executable, "-m", "editor.open_project", str(target)]
    try:
        proc = subprocess.Popen(cmd, cwd=str(repo_root), env=env,
                                close_fds=True)
    except Exception:
        return None
    if wait:
        try:
            proc.wait(timeout=60)
        except Exception:
            pass
    return proc


def describe():
    """给界面用的一句话说明。"""
    if available():
        return "剪辑器可用：混剪完成后可一键打开编辑"
    return "剪辑器不可用（缺少依赖）"
