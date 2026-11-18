"""
MixCut · 工程生成器

把统一时间轴（mixcut.timeline）构建为剪映可编辑草稿工程（pyJianYingDraft）。
输出的是可在剪映 / 时间线里继续二次编辑的工程，而不是一条成片；同时把时间轴
JSON 一并存进工程文件夹，方便回到 MixCut 重新调整。

本模块只负责“按时间轴落地”，检测与规则在其它模块完成。
"""

import json
import time
from pathlib import Path

try:
    import pyJianYingDraft as draft
    from pyJianYingDraft import trange_seconds
    DRAFT_AVAILABLE = True
except ImportError:  # pragma: no cover
    draft = None
    trange_seconds = None
    DRAFT_AVAILABLE = False

from . import timeline as tl


# 转场时长最多占相邻片段长度的比例：叠化比邻近片段还长时剪映容易错位
TRANSITION_MAX_RATIO = 0.5


def _jianying_draft_dir():
    return (
        Path.home()
        / "AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft"
    )


def _append_track(script, track_type, name):
    """兼容新旧版本创建轨道。"""
    if hasattr(script, "append_track") and hasattr(draft, "TrackSpec"):
        return script.append_track(draft.TrackSpec(track_type, name))
    if hasattr(script, "add_track"):
        script.add_track(track_type)
        return None
    raise RuntimeError("当前 pyJianYingDraft 不支持轨道创建接口。")


def _save(script):
    if hasattr(script, "save"):
        script.save()
    else:
        raise RuntimeError("找不到草稿保存接口。")


def _enable_draft_proxy(draft_path, timeline):
    """让新建剪映草稿默认开启代理，并登记全部原片为代理素材。"""
    if not draft_path.is_dir():
        return
    canvas = timeline.get("canvas") or {}
    width = int(canvas.get("width", 1920) or 1920)
    height = int(canvas.get("height", 1080) or 1080)
    if width >= height:
        conv_w, conv_h = 1280, 720
    else:
        conv_w, conv_h = 720, 1280

    config_path = draft_path / "draft_agency_config.json"
    config = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    config.update({
        "is_auto_agency_enabled": True,
        "is_auto_agency_popup": False,
        "is_single_agency_mode": False,
        "use_converter": True,
        "video_resolution": 720,
        "marterials": None,
    })
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    sources = [
        str(src.get("path") or "").replace("\\", "/")
        for src in (timeline.get("sources") or [])
        if src.get("path")
    ]
    if not sources:
        return
    info = {"marterial": [{
        "converter_height": conv_h,
        "converter_width": conv_w,
        "source_path": path,
    } for path in sources]}
    (draft_path / "draft_agency_info.json").write_text(
        json.dumps(info, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")



def _looks_like_path(text):
    """判断输出位置是否像一个具体目录（而不是相对名字）。"""
    s = str(text or "").strip()
    if not s:
        return False
    p = Path(s)
    if p.is_absolute() or p.drive:
        return True
    if s.startswith(("./", ".\\", "..", "../", "..\\")):
        return True
    if any(ch in s for ch in ("/", "\\")):
        return True
    # 已经存在的相对目录也视为路径
    return p.is_dir()


def resolve_target_dir(output_dir, force_target=False):
    """
    决定草稿落地目录。
    - 用户自定义了具体目录（或在界面里手动输入）：一律用它；
    - 否则优先剪映默认草稿目录，剪映目录不存在时才退回给定目录。
    """
    jy = _jianying_draft_dir()
    text = str(output_dir or "").strip()

    if force_target and _looks_like_path(text):
        target = Path(text)
        target.mkdir(parents=True, exist_ok=True)
        return target

    if jy.exists():
        return jy

    if not text:
        raise RuntimeError(
            "找不到剪映草稿目录，也没有设置自定义输出位置。"
            "请在「草稿位置」里选择一个保存目录。"
        )
    target = Path(text)
    target.mkdir(parents=True, exist_ok=True)
    return target


def build_draft(timeline, output_dir, draft_name=None, options=None,
                force_target=False):
    """
    根据时间轴构建剪映草稿。

    返回 {"draft_name", "draft_path", "target_dir", "clip_count", "duration"}。
    """
    if not DRAFT_AVAILABLE:
        raise RuntimeError("没有安装 pyJianYingDraft，无法生成工程。")

    options = options or {}
    canvas = timeline["canvas"]
    target_dir = resolve_target_dir(
        output_dir, force_target=force_target
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    if not draft_name:
        stamp = int(time.time() * 1000)
        draft_name = f"MixCut_Mix_{stamp}"

    folder = draft.DraftFolder(str(target_dir))
    script = folder.create_draft(
        draft_name,
        int(canvas["width"]),
        int(canvas["height"]),
        int(canvas.get("fps", tl.DEFAULT_FPS)),
        allow_replace=True,
    )

    video_ref = _append_track(script, draft.TrackType.video, "主视频轨道")

    # 每个源只构造一次 VideoMaterial，多片段复用，避免素材重复登记
    materials = {}
    for source in timeline["sources"]:
        materials[source["id"]] = draft.VideoMaterial(source["path"])

    # 先构造全部视频片段对象，便于在落地前统一加转场。
    # 以 VideoMaterial 的权威时长钳制源区间（ffprobe 与库的时长可能略有出入），
    # 并按真实长度顺序重排目标时间，保证 speed=1 且不越界。
    segments = []
    segment_by_clip_id = {}
    segment_lengths = {}
    cursor = 0.0

    for clip in timeline["clips"]:
        material = materials[clip["source_id"]]
        mat_dur = material.duration / 1_000_000.0

        src_start = max(0.0, float(clip["source_start"]))
        src_end = min(float(clip["source_end"]), mat_dur)
        length = src_end - src_start
        if length <= 0.05:
            continue

        target_tr = trange_seconds(cursor, duration=length)
        source_tr = trange_seconds(src_start, duration=length)
        seg = draft.VideoSegment(
            material, target_tr, source_timerange=source_tr
        )
        segments.append(seg)
        segment_by_clip_id[clip["id"]] = seg
        segment_lengths[clip["id"]] = length
        cursor += length

    authoritative_total = cursor

    _apply_transitions(segment_by_clip_id, segment_lengths, timeline)
    # 无效/已移动的 BGM 不写进草稿，避免剪映里出现一条离线音频轨。
    music = timeline.get("music")
    if isinstance(music, dict) and music.get("path"):
        if not Path(str(music["path"])).is_file():
            timeline["music"] = None
    _apply_music(script, timeline, options, authoritative_total)

    for seg in segments:
        script.add_segment(seg, video_ref)

    _save(script)

    # 剪映默认开启代理：草稿里的素材全部登记为代理源，打开工程即可生成代理。
    draft_path = target_dir / draft_name
    _enable_draft_proxy(draft_path, timeline)

    # 把时间轴 JSON 存进工程文件夹，便于回溯 / 再编辑
    tl.save_timeline(timeline, draft_path / "mixcut_timeline.json")

    return {
        "draft_name": draft_name,
        "draft_path": str(draft_path),
        "target_dir": str(target_dir),
        "clip_count": len(segments),
        "duration": round(authoritative_total, 3),
    }


# ---------------------------------------------------------
# 转场（由 timeline["transitions"] 驱动，transitions.py 生成规则）
# ---------------------------------------------------------

def _apply_transitions(segment_by_clip_id, segment_lengths, timeline):
    """按稳定的片段 ID 应用转场，避免跳过无效片段后索引错位。

    只处理原时间线里仍然相邻、且都成功构建的那一对片段；转场时长会被
    限制在相邻两个片段长度的合理范围内，避免叠化比片段本身还长。
    """
    rules = timeline.get("transitions") or []
    if not rules:
        return

    built_ids = [clip_id for clip_id in segment_by_clip_id]
    adjacent_pairs = set(zip(built_ids, built_ids[1:]))

    for rule in rules:
        pair = (rule.get("after_clip_id"), rule.get("to_clip_id"))
        if pair not in adjacent_pairs or rule.get("kind") == "hard":
            continue
        transition_name = rule.get("transition")
        if not transition_name:
            continue
        ttype = getattr(draft.TransitionType, transition_name, None)
        if ttype is None:
            continue

        kwargs = {}
        duration = rule.get("duration")
        if duration:
            left_len = segment_lengths.get(pair[0], 0.0)
            right_len = segment_lengths.get(pair[1], 0.0)
            limit = min(left_len, right_len) * TRANSITION_MAX_RATIO
            safe = min(float(duration), max(0.0, limit))
            if safe > 0.01:
                kwargs["duration"] = int(round(safe * 1_000_000))
        segment_by_clip_id[pair[0]].add_transition(ttype, **kwargs)


# ---------------------------------------------------------
# 配乐 / 闪避（由 timeline["music"] 驱动，musicstudio 生成）
# ---------------------------------------------------------

def _apply_music(script, timeline, options, authoritative_total=None):
    music = timeline.get("music")
    if not music or not music.get("path"):
        return

    path = music["path"]
    if not Path(path).is_file():
        return

    total = authoritative_total or tl.total_duration(timeline)
    if total <= 0:
        return

    music_duration = float(music.get("duration") or 0.0)
    # 音乐比成片短时只铺到音乐结束，不向剪映写入超出素材长度的区间；
    # 音乐比成片长时只取成片需要的长度。
    usable = min(total, music_duration) if music_duration > 0 else total
    if usable <= 0:
        return

    audio_ref = _append_track(script, draft.TrackType.audio, "背景音乐")

    target_tr = trange_seconds(0.0, duration=usable)
    source_tr = trange_seconds(
        float(music.get("source_start", 0.0)), duration=usable
    )
    audio_seg = draft.AudioSegment(
        draft.AudioMaterial(path), target_tr, source_timerange=source_tr
    )

    base_volume = float(music.get("base_volume", 1.0))
    duck_volume = float(music.get("duck_volume", 0.35))

    # 闪避关键帧：在人声区间把 BGM 压到 duck_volume，其余回到 base_volume
    usable_us = int(usable * 1_000_000)
    for kf in music.get("ducking_keyframes", []):
        at = int(kf["time"] * 1_000_000)
        if at > usable_us:
            continue
        vol = duck_volume if kf.get("duck") else base_volume
        audio_seg.add_keyframe(max(0, at), vol)

    fade_in = int(float(music.get("fade_in", 0) or 0) * 1_000_000)
    fade_out = int(float(music.get("fade_out", 0) or 0) * 1_000_000)
    if fade_in or fade_out:
        audio_seg.add_fade(min(fade_in, usable_us), min(fade_out, usable_us))

    script.add_segment(audio_seg, audio_ref)
