"""
MixCut · 自动配乐与卡点

  - librosa 节拍检测：给出 BPM 与每个鼓点的时间，混剪可直接在鼓点上切点
  - 音频闪避（ducking）：检测有人声的片段，生成 BGM 音量关键帧，
    人声出现时压低 BGM，人声结束恢复

解码统一走 ffmpeg 管道（mp4 等容器 librosa/soundfile 不能直接读）。
"""

import subprocess

import numpy as np

from . import ffmpeg_tools as ft
from . import timeline as tl

DEFAULT_SR = 22050


# ---------------------------------------------------------
# 解码
# ---------------------------------------------------------

def load_mono(path, sr=DEFAULT_SR):
    """用 ffmpeg 把任意媒体的音轨解码为单声道 float32 numpy 数组。"""
    ffmpeg = ft.find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，无法解码音频。")
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-f", "f32le", "-ac", "1", "-ar", str(sr), "-",
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=ft.NO_WINDOW_FLAGS,
    )
    if not proc.stdout:
        # 无音轨：返回空数组
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


# ---------------------------------------------------------
# 节拍检测
# ---------------------------------------------------------

def _estimate_downbeats(y, sr, beat_times):
    """估计强拍（小节重拍）的时间点。

    做法：按拍点把音频切成“拍内能量”，相邻拍能量差明显为正的那一拍
    视为重拍种子；再统计种子的拍距，取最常见的拍距（2/3/4 拍）作为小节
    长度，把种子对齐到统一相位。无法可靠估计时返回空列表（不伪造）。
    """
    beats = [float(b) for b in beat_times]
    if len(beats) < 4:
        return []

    # 用帧级 RMS：帧长取最小拍间隔的 1/4，保证每个拍都能落上真实能量
    gaps = [b - a for a, b in zip(beats, beats[1:]) if b > a]
    beat_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0.5
    frame = max(64, int(sr * max(0.02, beat_gap / 4.0)))
    hop = frame
    n_frames = max(1, (len(y) - frame) // hop) if len(y) >= frame else 0
    if n_frames == 0:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(y[:n_frames * hop], frame)[::hop]
    rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))

    energies = []
    for i, b in enumerate(beats):
        nxt = beats[i + 1] if i + 1 < len(beats) else b + beat_gap
        a = int(max(0, (b - beat_gap * 0.15)) * sr) // hop
        z = int(max(0, (min(nxt, b + beat_gap * 1.15)) * sr)) // hop
        a = min(max(0, a), len(rms) - 1)
        z = min(max(a + 1, z), len(rms))
        energies.append(float(rms[a:z].max()) if z > a else 0.0)

    # 相邻拍能量差为正且高于平均值的拍，作为重拍候选
    diffs = [energies[0]] + [
        energies[i] - energies[i - 1] for i in range(1, len(energies))
    ]
    positive = [d for d in diffs if d > 0]
    if not positive:
        return []
    threshold = float(np.mean(positive)) * 0.6
    seeds = [i for i, d in enumerate(diffs) if d > 0 and d >= threshold]
    if len(seeds) < 3:
        return []

    # 统计候选之间的拍距，选出现最多的作为小节拍数
    step = [b - a for a, b in zip(seeds, seeds[1:]) if b - a >= 2]
    if not step:
        return []
    counts = {}
    for g in step:
        counts[g] = counts.get(g, 0) + 1
    meter, _best = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))

    # 相位：让尽可能多的种子落在同一条相位上
    phase_counts = {}
    for s in seeds:
        phase_counts[s % meter] = phase_counts.get(s % meter, 0) + 1
    phase = max(phase_counts.items(), key=lambda kv: kv[1])[0]

    return [round(beats[i], 4) for i in range(phase, len(beats), meter)]


def analyze_bgm(path):
    """返回 BGM 的 BPM、鼓点时间列表与时长。

    ``downbeats`` 为估计的强拍（小节重拍）时间点，用于卡点时区分强弱拍；
    ``meter`` 为估计出的小节拍数（估计失败时为 0，downbeats 为空列表）。
    这两个字段是追加项，原有字段名称与含义不变。
    """
    import librosa

    y, sr = librosa.load(path, sr=DEFAULT_SR, mono=True)
    duration = len(y) / sr if sr else 0.0

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

    downbeats = []
    meter = 0
    try:
        downbeats = _estimate_downbeats(y, sr, beat_times)
        if downbeats and len(beat_times) >= 4:
            idx = [min(range(len(beat_times)),
                       key=lambda i: abs(beat_times[i] - d))
                   for d in downbeats[:6]]
            gaps = sorted(b - a for a, b in zip(idx, idx[1:]) if b > a)
            if gaps:
                meter = int(gaps[len(gaps) // 2])
    except Exception:
        downbeats = []
        meter = 0
    if meter not in (2, 3, 4):
        # 只接受常见的小节长度，其余视为估计不可靠
        meter = 0 if not downbeats else meter

    return {
        "path": str(path),
        "bpm": round(bpm, 2),
        "beats": [round(float(b), 4) for b in beat_times],
        "downbeats": downbeats,
        "meter": meter,
        "duration": round(float(duration), 4),
        "sr": sr,
    }


def nearest_beat(beats, target, tolerance):
    """返回容差内距 target 最近的鼓点，否则 None。"""
    if not beats:
        return None
    arr = np.asarray(beats, dtype=float)
    idx = int(np.argmin(np.abs(arr - target)))
    chosen = float(arr[idx])
    if abs(chosen - target) <= tolerance:
        return chosen
    return None


# ---------------------------------------------------------
# 人声 / 闪避
# ---------------------------------------------------------

def segment_has_voice(mono, sr, start, end, energy_threshold=0.08,
                      min_voiced_fraction=0.25):
    """
    基于能量的轻量人声判断（不是严格 VAD，但对“有人说话/明显人声”足够）。
    用短窗 RMS，超过阈值的窗占比达到要求即判定有人声。
    """
    if mono is None or len(mono) == 0:
        return False

    a = max(0, int(start * sr))
    b = min(len(mono), int(end * sr))
    if b <= a:
        return False

    chunk = mono[a:b].astype(np.float32)
    frame = max(1, int(0.04 * sr))           # 40ms 窗
    n_frames = len(chunk) // frame
    if n_frames == 0:
        return False
    trimmed = chunk[: n_frames * frame].reshape(n_frames, frame)
    rms = np.sqrt(np.mean(trimmed ** 2, axis=1))
    voiced_fraction = float(np.mean(rms >= energy_threshold))
    return voiced_fraction >= min_voiced_fraction


def build_ducking_keyframes(timeline, energy_threshold=0.08,
                            duck_volume=0.35, base_volume=1.0,
                            pad=0.08):
    """把相邻或重叠的人声段合并后再生成 BGM 闪避关键帧。"""
    mono_cache = {}
    voice_intervals = []
    total = tl.total_duration(timeline)
    source_map = {s["id"]: s for s in timeline.get("sources", [])}

    for clip in timeline.get("clips", []):
        sid = clip["source_id"]
        if sid not in mono_cache:
            source = source_map.get(sid)
            if source is None:
                continue
            mono_cache[sid] = load_mono(source["path"])
        mono = mono_cache[sid]

        voiced = segment_has_voice(
            mono, DEFAULT_SR,
            clip["source_start"], clip["source_end"],
            energy_threshold=energy_threshold,
        )
        if not voiced:
            continue

        t_in = max(0.0, float(clip["timeline_start"]) - pad)
        t_out = min(total, float(clip["timeline_end"]) + pad)
        if t_out > t_in:
            voice_intervals.append((t_in, t_out))

    # 先合并相交/相接区间，避免后一段的“恢复音量”覆盖前一段仍在持续的人声。
    merged = []
    for start, end in sorted(voice_intervals):
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    if not merged:
        return [{"time": 0.0, "volume": base_volume, "duck": False}]

    keyframes = []
    if merged[0][0] > 0:
        keyframes.append({"time": 0.0, "volume": base_volume, "duck": False})

    for start, end in merged:
        keyframes.append({"time": round(start, 3), "volume": duck_volume,
                          "duck": True})
        keyframes.append({"time": round(end, 3), "volume": base_volume,
                          "duck": False})

    # 量化到毫秒后去重，优先保留同一时刻最后一个状态。
    dedup = {}
    for kf in keyframes:
        dedup[kf["time"]] = kf
    return [dedup[t] for t in sorted(dedup)]


def attach_music(timeline, bgm_path, do_ducking=True,
                 base_volume=1.0, duck_volume=0.35,
                 energy_threshold=0.08, fade_in=0.4, fade_out=0.6,
                 bgm_info=None):
    """把 BGM 信息（含闪避关键帧）挂到时间轴上。可复用已分析的节拍结果。"""
    info = bgm_info or analyze_bgm(bgm_path)
    music = {
        "path": info["path"],
        "bpm": info["bpm"],
        "beats": info["beats"],
        "duration": info["duration"],
        "source_start": 0.0,
        "base_volume": base_volume,
        "duck_volume": duck_volume,
        "fade_in": fade_in,
        "fade_out": fade_out,
        "ducking_keyframes": [],
    }
    if do_ducking:
        music["ducking_keyframes"] = build_ducking_keyframes(
            timeline,
            energy_threshold=energy_threshold,
            duck_volume=duck_volume,
            base_volume=base_volume,
        )
    timeline["music"] = music
    return music
