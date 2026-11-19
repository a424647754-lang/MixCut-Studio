# -*- coding: utf-8 -*-
"""MixCut · OpenCV 画面质量评估

按固定时间窗口分别评估画面质量。质量分用于混剪候选排序，不直接删除
低分片段，避免单帧异常或素材量不足造成误杀。
"""

from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from . import frame_pipe


@dataclass
class ShotQuality:
    """一个固定时间窗口的画面质量统计。"""
    start_sec: float
    end_sec: float
    sharpness: float = 0.0       # 拉普拉斯方差，越高通常越清晰
    brightness: float = 0.0      # 平均亮度 0-255
    contrast: float = 0.0        # 灰度标准差
    noise: float = 0.0           # 中值滤波残差估计
    motion: float = 0.0          # 相邻采样帧的画面变化，仅作信息，不判手抖
    quality_score: float = 70.0  # 0-100，越高越好
    bad_frame_ratio: float = 0.0 # 窗口内明显异常帧比例
    sample_count: int = 0
    is_black: bool = False
    is_white: bool = False
    is_blurry: bool = False
    is_overexposed: bool = False
    is_underexposed: bool = False
    is_shaky: bool = False       # 保留兼容字段，当前不再用帧差判定
    is_bad: bool = False
    reason: str = ""


class BadShotDetector:
    """轻量画面质量评估器。每个窗口独立采样并用中位数聚合。"""

    BLACK_THRESHOLD = 5.0
    WHITE_THRESHOLD = 250.0
    BLUR_THRESHOLD = 35.0
    BRIGHTNESS_LOW = 24.0
    BRIGHTNESS_HIGH = 235.0
    NOISE_THRESHOLD = 20.0
    MAX_FRAME_SIDE = 960

    def __init__(self):
        self.shots: List[ShotQuality] = []

    def analyze_video(
        self,
        video_path: str,
        segment_duration: float = 5.0,
        sample_count_per_segment: int = 3,
        progress_cb=None,
        cancel_check=None,
    ) -> List[ShotQuality]:
        """按固定窗口分析整条视频，返回每个窗口的独立评分。

        优化点：不再用 cv2 随机 seek 逐帧取画面，而是交给 ffmpeg 顺序解码一次、
        边解边按目标帧率抽帧（见 frame_pipe）。默认每 5 秒 3 帧等于 0.6fps，
        与原先窗口内等距采样等价，但避免了每条素材被重复解码几十遍。
        """
        meta = frame_pipe.probe_media(video_path)
        if not meta:
            self.shots = []
            return []
        _, _, duration = meta
        if duration <= 0:
            self.shots = []
            return []

        window = max(0.5, float(segment_duration or 5.0))
        sample_count = max(1, int(sample_count_per_segment or 3))
        sample_fps = sample_count / window
        window_count = max(1, int(np.ceil(duration / window)))

        buckets: List[List[ShotQuality]] = [[] for _ in range(window_count)]
        previous_gray = None
        processed = 0

        try:
            # 显式持有生成器，保证 cancel 后立即关闭并杀掉 ffmpeg，
            # 而不是等 GC（期间 ffmpeg 会一直解码占内存）。
            gen = frame_pipe.iter_frames(
                video_path,
                fps=sample_fps,
                max_side=self.MAX_FRAME_SIDE,
            )
            try:
                for timestamp, frame in gen:
                    if cancel_check and cancel_check():
                        break
                    index = int(timestamp // window)
                    if index < 0 or index >= window_count:
                        continue
                    q, gray = self._eval_frame(frame, previous_gray)
                    previous_gray = gray
                    buckets[index].append(q)
                    processed += 1
                    if progress_cb and processed % max(1, sample_count) == 0:
                        done = min(window_count, index + 1)
                        progress_cb(done, window_count, f"画面质量评估 {done}/{window_count}")
            finally:
                try:
                    gen.close()
                except Exception:
                    pass
        except Exception:
            # 单条素材解码异常不应该炸掉整批分析，保留已采集到的部分。
            pass

        results: List[ShotQuality] = []
        for window_index, frame_results in enumerate(buckets):
            if not frame_results:
                continue
            start = window_index * window
            end = min(duration, start + window)
            results.append(self._aggregate_window(start, end, frame_results))

        if progress_cb:
            progress_cb(window_count, window_count, f"画面质量评估 {window_count}/{window_count}")

        self.shots = results
        return results

    def _eval_frame(self, frame: np.ndarray, prev_gray: Optional[np.ndarray]):
        """评估单帧；先缩小到最长边 960，控制高分辨率素材的计算成本。"""
        h, w = frame.shape[:2]
        scale = min(1.0, self.MAX_FRAME_SIDE / float(max(h, w)))
        if scale < 1.0:
            frame = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                               interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        q = ShotQuality(start_sec=0.0, end_sec=0.0)

        laplacian = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        q.sharpness = laplacian
        q.is_blurry = laplacian < self.BLUR_THRESHOLD

        q.brightness = float(np.mean(gray))
        q.contrast = float(gray.std())
        dark_ratio = float(np.mean(gray < 12))
        bright_ratio = float(np.mean(gray > 245))
        q.is_black = q.brightness < self.BLACK_THRESHOLD or dark_ratio > 0.98
        q.is_white = q.brightness > self.WHITE_THRESHOLD or bright_ratio > 0.98
        q.is_underexposed = (q.brightness < self.BRIGHTNESS_LOW and dark_ratio > 0.65
                             and not q.is_black)
        q.is_overexposed = (q.brightness > self.BRIGHTNESS_HIGH and bright_ratio > 0.65
                            and not q.is_white)

        denoised = cv2.medianBlur(gray, 3)
        q.noise = float(cv2.absdiff(gray, denoised).mean())

        if prev_gray is not None:
            if prev_gray.shape != gray.shape:
                prev_gray = cv2.resize(prev_gray, (gray.shape[1], gray.shape[0]))
            q.motion = float(cv2.absdiff(gray, prev_gray).mean())
        else:
            q.motion = 0.0

        # 逐帧评分只用于窗口聚合；运动强度不参与质量扣分。
        q.quality_score = self._frame_quality_score(q)
        reasons = []
        if q.is_black:
            reasons.append("黑场")
        if q.is_white:
            reasons.append("白场")
        if q.is_blurry:
            reasons.append("疑似模糊")
        if q.is_overexposed:
            reasons.append("过曝")
        if q.is_underexposed:
            reasons.append("欠曝")
        if q.noise > self.NOISE_THRESHOLD:
            reasons.append("噪点偏高")
        q.reason = "、".join(reasons)
        q.is_bad = bool(reasons)
        return q, gray

    @staticmethod
    def _clamp(value, low=0.0, high=100.0):
        return max(low, min(high, float(value)))

    def _frame_quality_score(self, q: ShotQuality) -> float:
        # 清晰度采用渐进扣分，不因轻微失焦直接归零。
        sharp = self._clamp((max(0.0, q.sharpness) / 220.0) ** 0.5 * 100.0)
        exposure = 100.0
        if q.is_black or q.is_white:
            exposure = 5.0
        elif q.is_underexposed or q.is_overexposed:
            exposure = 35.0
        else:
            exposure -= min(35.0, abs(q.brightness - 128.0) * 0.35)
        contrast = self._clamp(q.contrast / 48.0 * 100.0)
        noise = self._clamp(100.0 - max(0.0, q.noise - 2.0) * 4.0)
        return self._clamp(sharp * 0.45 + exposure * 0.25 + contrast * 0.15 + noise * 0.15)

    def _aggregate_window(self, start, end, samples: List[ShotQuality]) -> ShotQuality:
        """使用中位数和异常帧占比聚合，避免一帧异常代表整个窗口。"""
        def med(field):
            return float(np.median([getattr(x, field) for x in samples]))

        bad_ratio = sum(1 for x in samples if x.is_bad) / float(len(samples))
        result = ShotQuality(
            start_sec=float(start), end_sec=float(end),
            sharpness=med("sharpness"), brightness=med("brightness"),
            contrast=med("contrast"), noise=med("noise"), motion=med("motion"),
            quality_score=med("quality_score"), bad_frame_ratio=bad_ratio,
            sample_count=len(samples),
        )

        # 分类标记要求多数样本支持；单帧异常不会把整段标为废片。
        for flag in ("is_black", "is_white", "is_blurry", "is_overexposed", "is_underexposed"):
            setattr(result, flag, sum(bool(getattr(x, flag)) for x in samples) / len(samples) >= 0.67)

        reasons = []
        reason_map = (
            ("is_black", "黑场"), ("is_white", "白场"),
            ("is_blurry", "疑似模糊"), ("is_overexposed", "过曝"),
            ("is_underexposed", "欠曝"),
        )
        for flag, label in reason_map:
            if getattr(result, flag):
                reasons.append(label)
        if result.noise > self.NOISE_THRESHOLD:
            reasons.append("噪点偏高")

        result.is_bad = bad_ratio >= 0.67 or result.quality_score < 25.0
        if result.is_bad and not reasons:
            reasons.append("综合画质偏低")
        result.reason = "、".join(reasons)
        return result

    def _bad_score(self, q: ShotQuality) -> float:
        """兼容旧调用：返回窗口异常程度，越高越差。"""
        return (100.0 - q.quality_score) + q.bad_frame_ratio * 30.0

    def filter_good_shots(self, shots: List[ShotQuality]) -> List[ShotQuality]:
        """兼容旧接口；主混剪流程不使用此硬过滤，改用质量分排序。"""
        return [s for s in shots if not s.is_bad]

    def get_summary(self) -> dict:
        if not self.shots:
            return {"total": 0, "bad": 0, "good": 0, "bad_reasons": []}
        total = len(self.shots)
        bad = sum(1 for s in self.shots if s.is_bad)
        reasons = sorted({s.reason for s in self.shots if s.reason})
        return {
            "total": total, "bad": bad, "good": total - bad,
            "bad_reasons": reasons,
            "average_quality": round(float(np.mean([s.quality_score for s in self.shots])), 1),
        }
