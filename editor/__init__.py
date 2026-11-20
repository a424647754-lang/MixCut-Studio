"""MixCut Editor - 剪辑器数据模型.

时间统一用毫秒整数（int），帧率用 Fraction 精确表示，避免长时间项目浮点漂移。
本模块不依赖 Qt，因此可以在无界面环境下单元测试。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mxf", ".wmv", ".flv", ".ts"}
AUDIO_EXTS = {".wav", ".mp3", ".aac", ".flac", ".m4a", ".ogg", ".aif", ".aiff", ".wma"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS

PROJECT_FORMAT = "mixcut-editor"
PROJECT_VERSION = 2   # 2：Clip 增加 exposure/tint/sharpness（缺省=中性，旧工程自动兼容）

FPS_PRESETS = [
    ("23.976", Fraction(24000, 1001)),
    ("24", Fraction(24, 1)),
    ("25", Fraction(25, 1)),
    ("29.97", Fraction(30000, 1001)),
    ("30", Fraction(30, 1)),
    ("50", Fraction(50, 1)),
    ("59.94", Fraction(60000, 1001)),
    ("60", Fraction(60, 1)),
]


def new_id() -> str:
    return uuid.uuid4().hex


def write_text_atomic(path, text: str) -> Path:
    """先写 `.tmp` 再 `replace`：中断/断电不会留下半个文件（B-30）。

    工程保存、自动保存、recent.json 一律走这里，避免各写一套。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return target


def normalize_path(path: str) -> str:
    """路径比较用的规范形式（绝对化 + 统一大小写与分隔符）。

    Windows 下 "C:/m/a.mp4" 与 "C:\\m\\a.mp4" 指向同一文件，必须统一后
    再比较，否则重复导入会产生重复素材条目。
    """
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (OSError, ValueError):
        return str(path)


def fps_to_str(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


def fps_from_str(text: str) -> Fraction:
    try:
        if "/" in str(text):
            num, den = str(text).split("/", 1)
            value = Fraction(int(num), int(den))
        else:
            value = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return Fraction(30, 1)
    return value if value > 0 else Fraction(30, 1)


def media_kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    return "unknown"


def is_media_file(path: str) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTS


def format_timecode(ms: int, fps: Fraction) -> str:
    """毫秒 -> HH:MM:SS:FF（非丢帧）。"""
    ms = max(0, int(ms))
    nominal = max(1, round(float(fps)))
    total_seconds, rem_ms = divmod(ms, 1000)
    frames = min(nominal - 1, int(rem_ms * nominal / 1000))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def frame_duration_ms(fps: Fraction) -> int:
    return max(1, round(1000 * fps.denominator / fps.numerator))


def snap_to_frame(ms: int, fps: Fraction) -> int:
    """把毫秒量化到最近的帧边界。

    先换算成帧序号再还原成毫秒，避免 `round(ms/frame)*frame` 在 23.976
    这类非整数帧长上累积截断误差（B-26/B-25）。
    """
    frame = 1000 * fps.denominator / fps.numerator
    index = round(ms / frame)
    return max(0, int(round(index * frame)))


@dataclass
class MediaAsset:
    id: str = field(default_factory=new_id)
    path: str = ""
    name: str = ""
    kind: str = "video"
    duration: int = 0          # 毫秒
    width: int = 0
    height: int = 0
    fps: str = "30/1"
    has_video: bool = True
    has_audio: bool = False
    codec: str = ""

    def __post_init__(self) -> None:
        if not self.name and self.path:
            self.name = Path(self.path).name

    @property
    def exists(self) -> bool:
        return bool(self.path) and Path(self.path).exists()

    @property
    def fps_fraction(self) -> Fraction:
        return fps_from_str(self.fps)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "path": self.path, "name": self.name, "kind": self.kind,
            "duration": self.duration, "width": self.width, "height": self.height,
            "fps": self.fps, "has_video": self.has_video, "has_audio": self.has_audio,
            "codec": self.codec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MediaAsset":
        return cls(
            id=data.get("id") or new_id(),
            path=data.get("path", ""),
            name=data.get("name", ""),
            kind=data.get("kind", "video"),
            duration=int(data.get("duration", 0)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            fps=str(data.get("fps", "30/1")),
            has_video=bool(data.get("has_video", True)),
            has_audio=bool(data.get("has_audio", False)),
            codec=data.get("codec", ""),
        )


@dataclass
class Clip:
    """时间线片段。timeline_start 为轨道上的位置，source_in 为素材内入点。

    speed 只改变素材消耗速度，时间线长度 duration 保持不变；调色四个字段
    与 ffmpeg 的 eq/colorbalance 参数一一对应，导出时按片段写入滤镜链。
    """

    id: str = field(default_factory=new_id)
    name: str = ""
    asset_id: str = ""
    path: str = ""
    start: int = 0
    source_in: int = 0
    duration: int = 1000
    speed: float = 1.0
    enabled: bool = True
    volume: float = 1.0
    opacity: float = 1.0
    brightness: float = 0.0      # -1~1  （eq brightness）
    contrast: float = 1.0        # 0~3   （eq contrast）
    saturation: float = 1.0      # 0~3   （eq saturation）
    temperature: float = 0.0     # -1~1  （暖 / 冷，colorbalance）
    exposure: float = 0.0        # -4~4 EV（lutyuv 线性增益）
    tint: float = 0.0            # -50~50（绿 / 品红，colorbalance midtones）
    sharpness: float = 0.0       # 0~100 （unsharp）

    @property
    def end(self) -> int:
        return self.start + self.duration

    @property
    def source_span(self) -> int:
        """本片段在素材内占用的时长（速度会影响）。"""
        return int(self.duration * max(0.01, self.speed))

    def covers(self, t: int) -> bool:
        return self.start <= t < self.end

    def source_time(self, timeline_ms: int) -> int:
        """时间线位置 -> 素材内位置。"""
        return self.source_in + int(max(0, timeline_ms - self.start) * self.speed)

    @property
    def is_graded(self) -> bool:
        return (abs(self.brightness) > 1e-4 or abs(self.contrast - 1.0) > 1e-4
                or abs(self.saturation - 1.0) > 1e-4 or abs(self.temperature) > 1e-4
                or abs(self.exposure) > 1e-4 or abs(self.tint) > 1e-4
                or abs(self.sharpness) > 1e-4)

    def grade(self) -> dict:
        return {
            "brightness": self.brightness,
            "contrast": self.contrast,
            "saturation": self.saturation,
            "temperature": self.temperature,
            "exposure": self.exposure,
            "tint": self.tint,
            "sharpness": self.sharpness,
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "asset_id": self.asset_id, "path": self.path,
            "start": self.start, "source_in": self.source_in, "duration": self.duration,
            "speed": self.speed, "enabled": self.enabled, "volume": self.volume,
            "opacity": self.opacity,
            "brightness": self.brightness, "contrast": self.contrast,
            "saturation": self.saturation, "temperature": self.temperature,
            "exposure": self.exposure, "tint": self.tint, "sharpness": self.sharpness,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Clip":
        return cls(
            id=data.get("id") or new_id(),
            name=data.get("name", ""),
            asset_id=data.get("asset_id", ""),
            path=data.get("path", ""),
            start=int(data.get("start", 0)),
            source_in=int(data.get("source_in", 0)),
            duration=max(1, int(data.get("duration", 1000))),
            speed=float(data.get("speed", 1.0) or 1.0),
            enabled=bool(data.get("enabled", True)),
            volume=float(data.get("volume", 1.0)),
            opacity=float(data.get("opacity", 1.0)),
            brightness=float(data.get("brightness", 0.0)),
            contrast=float(data.get("contrast", 1.0)),
            saturation=float(data.get("saturation", 1.0)),
            temperature=float(data.get("temperature", 0.0)),
            exposure=float(data.get("exposure", 0.0)),
            tint=float(data.get("tint", 0.0)),
            sharpness=float(data.get("sharpness", 0.0)),
        )


TRACK_KINDS = ("video", "audio")


@dataclass
class Track:
    id: str = field(default_factory=new_id)
    name: str = "V1"
    kind: str = "video"
    muted: bool = False
    locked: bool = False
    hidden: bool = False
    height: int = 64
    clips: list[Clip] = field(default_factory=list)

    def sorted_clips(self) -> list[Clip]:
        return sorted(self.clips, key=lambda c: c.start)

    def clip_at(self, t: int) -> Clip | None:
        for clip in self.clips:
            if clip.covers(t):
                return clip
        return None

    def find(self, clip_id: str) -> Clip | None:
        for clip in self.clips:
            if clip.id == clip_id:
                return clip
        return None

    def content_end(self) -> int:
        return max((c.end for c in self.clips), default=0)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "muted": self.muted,
            "locked": self.locked, "hidden": self.hidden, "height": self.height,
            "clips": [c.to_dict() for c in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        track = cls(
            id=data.get("id") or new_id(),
            name=data.get("name", "V1"),
            kind=data.get("kind", "video"),
            muted=bool(data.get("muted", False)),
            locked=bool(data.get("locked", False)),
            hidden=bool(data.get("hidden", False)),
            height=int(data.get("height", 64)),
        )
        track.clips = [Clip.from_dict(c) for c in data.get("clips", [])]
        track.clips.sort(key=lambda c: c.start)
        return track


@dataclass
class Marker:
    id: str = field(default_factory=new_id)
    time: int = 0
    label: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "time": self.time, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict) -> "Marker":
        return cls(
            id=data.get("id") or new_id(),
            time=int(data.get("time", 0)),
            label=data.get("label", ""),
        )


@dataclass
class Project:
    """完整的可编辑工程。"""

    name: str = "未命名工程"
    width: int = 1920
    height: int = 1080
    fps: str = "30/1"
    assets: list[MediaAsset] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    path: str = ""
    # 基底视频轨的稳定身份（导出合成的底层）。旧工程为空，按首条视频轨解析。
    base_video_id: str = ""

    def __post_init__(self) -> None:
        if not self.tracks:
            self.tracks.append(Track(name="V1", kind="video"))
            self.tracks.append(Track(name="A1", kind="audio"))
        if not self.base_video_id:
            base = self.first_track("video")
            self.base_video_id = base.id if base is not None else ""

    # -- 帧率 -------------------------------------------------------------
    @property
    def fps_fraction(self) -> Fraction:
        return fps_from_str(self.fps)

    # -- 素材 -------------------------------------------------------------
    def asset(self, asset_id: str) -> MediaAsset | None:
        for asset in self.assets:
            if asset.id == asset_id:
                return asset
        return None

    def asset_by_path(self, path: str) -> MediaAsset | None:
        target = normalize_path(path)
        for asset in self.assets:
            if normalize_path(asset.path) == target:
                return asset
        return None

    def add_asset(self, asset: MediaAsset) -> MediaAsset:
        existing = self.asset_by_path(asset.path)
        if existing:
            return existing
        self.assets.append(asset)
        return asset

    # -- 轨道 -------------------------------------------------------------
    def add_track(self, kind: str = "video", name: str = "") -> Track:
        """新建轨道。名字取现有最大序号 +1，不重排也不改名已有轨道。"""
        numbers = []
        prefix = "V" if kind == "video" else "A"
        for track in self.tracks:
            if track.kind != kind:
                continue
            suffix = track.name[len(prefix):] if track.name.startswith(prefix) else ""
            if suffix.isdigit():
                numbers.append(int(suffix))
        index = (max(numbers) + 1) if numbers else 1
        track = Track(name=name or f"{prefix}{index}", kind=kind)
        track.height = 64 if kind == "video" else 48
        self.tracks.append(track)
        return track

    def track(self, track_id: str) -> Track | None:
        for track in self.tracks:
            if track.id == track_id:
                return track
        return None

    def first_track(self, kind: str) -> Track | None:
        for track in self.tracks:
            if track.kind == kind:
                return track
        return None

    def base_video_track(self) -> Track | None:
        """基底轨（画面合成的底层）。

        优先用显式记录的 `base_video_id`；旧工程/基线轨被删除时回落到
        首条视频轨，并把基线 id 就地修正（不改变轨道顺序与命名）。
        """
        track = self.track(self.base_video_id) if self.base_video_id else None
        if track is not None and track.kind == "video":
            return track
        base = self.first_track("video")
        self.base_video_id = base.id if base is not None else ""
        return base

    def layer_video_tracks(self) -> list[Track]:
        """按合成顺序返回视频轨：第 0 条是基底（底层），越靠后越靠上层。"""
        tracks = self.video_tracks()
        base = self.base_video_track()
        if base is None:
            return tracks
        return [base] + [t for t in tracks if t.id != base.id]

    def locate(self, clip_id: str) -> tuple[Track, Clip] | tuple[None, None]:
        for track in self.tracks:
            clip = track.find(clip_id)
            if clip is not None:
                return track, clip
        return None, None

    # -- 总体 -------------------------------------------------------------
    def duration(self) -> int:
        return max((t.content_end() for t in self.tracks), default=0)

    def video_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "video"]

    def audio_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "audio"]

    # -- 序列化 -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "assets": [a.to_dict() for a in self.assets],
            "tracks": [t.to_dict() for t in self.tracks],
            "markers": [m.to_dict() for m in self.markers],
            "base_video_id": self.base_video_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        if data.get("format") not in (PROJECT_FORMAT, None):
            raise ValueError("不是 MixCut 工程文件")
        # 版本必须校验：更高版本的工程可能带未知字段，静默降级会丢数据（B-32）
        raw_version = data.get("version", 0)
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            raise ValueError(f"工程版本号无效：{raw_version!r}")
        if version > PROJECT_VERSION:
            raise ValueError(
                f"工程版本 {version} 高于当前支持的 {PROJECT_VERSION}，"
                "请使用更新版本的 MixCut 打开")
        project = cls(
            name=data.get("name", "未命名工程"),
            width=int(data.get("width", 1920)),
            height=int(data.get("height", 1080)),
            fps=str(data.get("fps", "30/1")),
        )
        project.tracks = []
        project.assets = [MediaAsset.from_dict(a) for a in data.get("assets", [])]
        project.tracks = [Track.from_dict(t) for t in data.get("tracks", [])]
        project.markers = [Marker.from_dict(m) for m in data.get("markers", [])]
        if not project.tracks:
            project.tracks.append(Track(name="V1", kind="video"))
        # 旧工程没有 base_video_id：迁移为"当前首条视频轨"，行为与旧版一致。
        stored_base = str(data.get("base_video_id", "") or "")
        project.base_video_id = stored_base
        project.base_video_track()
        return project

    def save(self, path: str) -> None:
        target = Path(path)
        write_text_atomic(target,
                          json.dumps(self.to_dict(), ensure_ascii=False, indent=2))
        self.path = str(target)

    @classmethod
    def load(cls, path: str) -> "Project":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        project = cls.from_dict(data)
        project.path = str(Path(path))
        return project
