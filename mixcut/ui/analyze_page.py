# -*- coding: utf-8 -*-
"""
MixCut · 分析页（素材分析中心）

功能：
  - 三档分析路线：快速 / 中度 / 严格（采样深度、判废门槛不同）
  - 长视频按固定时长窗口分段（每段 5 秒），完整覆盖到结尾，每个子片段独立分析
  - 帧级缓存：OpenCV 客观指标按帧缓存，切换分析模式只补算增量，不重复劳动
  - 分析中可随时切换模式；已分析片段切换后照样可用（worker 完成即退出，
    换模式时复用同一缓存重启，只补缺失帧）
  - 右侧 QVideoWidget 预览：单击定位静态帧，双击播放/暂停，可独立全屏
  - 判废完全基于本地 OpenCV 规则，不依赖任何外部大模型或网络服务
"""

import os
import hashlib
import logging
import queue
import threading
import time
from collections import OrderedDict

import cv2
import numpy as np

from PySide6.QtCore import (
    Qt, QThread, Signal, QUrl, QEvent, QRectF, QPoint, Property, QEasingCurve,
    QMimeData, QSize,
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QFont, QPixmap, QPainterPath, QDrag, QImageReader,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QPushButton, QProgressBar, QSplitter, QScrollArea, QSizePolicy,
    QComboBox, QMenu, QDialog, QRadioButton, QDialogButtonBox, QApplication,
    QListWidget, QListWidgetItem, QCheckBox, QInputDialog, QAbstractItemView,
    QMessageBox, QStyle,
)

from . import theme
from .components import Card
from mixcut.ui.demo import theme as T
from mixcut.ui.demo.ui_kit import thumb_pixmap
from mixcut.bad_shot_detector import BadShotDetector
from mixcut.ffmpeg_tools import (
    probe_duration, probe_details, make_thumbnail,
)


logger = logging.getLogger("mixcut.scene")

# 长视频分段门槛：时长超过该值才做多重画面分析，否则只分析一遍
SEGMENT_THRESHOLD = 5.0
# 长视频固定窗口分段长度（秒）——镜头检测不可用时的回退方案
FALLBACK_WINDOW = 5.0
# 每个子片段的标准 OpenCV 采样网格大小
GRID_SIZE = 12

# ---- 镜头级分段门槛（第二十一阶段）------------------------------------
# 按镜头边界分段时，段数必须在 [MIN, MAX] 之间，否则认为检测结果不可信、
# 回退到固定窗口。上限按「一小时内每 2 秒一个镜头」估算，超了基本是误检。
_SHOT_MIN_SEGMENTS = 1
_SHOT_MAX_SEGMENTS = 2000
# 单段最短时长：短于此视为碎渣
_SHOT_MIN_DUR = 0.4
# 碎渣占比超过此值 → 切点太密（高频纹理/噪点误触发），回退固定窗口
_SHOT_MAX_TINY_RATIO = 0.5

# ---- 分段阶段的检测预算（第二十四阶段）--------------------------------
# 分析页的「分段」只用于把长素材切成可编辑的片段，
# 边界差半秒完全可接受；而 amverge 边缘检测要逐关键帧 open+seek，
# 在 4K 素材上实测 1.276 秒/视频秒——157 秒的素材卡了 200 秒，
# 1 小时素材要 76 分钟，用户看到的就是「一直卡在正在分段」。
#
# 因此分段阶段改用**低成本检测器**（PySceneDetect 实测 0.018 秒/视频秒，
# 比 amverge 快约 70 倍），并给绝对时长兜底。
# 需要高精度镜头的场景（如正式混剪的 run_filter）仍走完整检测链。
_SHOT_DETECT_BUDGET = 20.0      # 单个素材的检测总预算（秒）
_SHOT_SKIP_DETECT_DUR = 600.0   # 超过此时长直接放弃检测，用固定窗口
# 分段允许的最小切点间隔：不追求逐帧精度，只求边界大致合理
_SHOT_SEG_MIN_GAP = 1.0

# ---- amverge 成本闸门（第二十四阶段）--------------------------------
# 实测：4K 素材 amverge 边缘检测约 **1.276 秒/视频秒**
#   → 157 秒素材 200 秒，1 小时素材 76 分钟。
# 分段阶段不需要这个精度，所以估算超限就不跑。
_AMVERGE_SEC_PER_VIDEO_SEC = 1.3      # 保守取值（比实测 1.276 略高）
_AMVERGE_COST_LIMIT = 15.0            # 预计超过 15 秒就不跑 amverge


def fragment_id(path, start, end):
    """片段稳定唯一 ID：由「绝对路径 + 起止时间」派生，重跑分析也不变。"""
    key = "{0}|{1:.3f}|{2:.3f}".format(
        os.path.normcase(os.path.abspath(str(path))),
        float(start), float(end))
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:12]


# =========================================================
# 三档分析路线配置
# =========================================================
ANALYSIS_MODES = {
    "fast": {
        "label": "快速分析",
        "opencv_samples": 3,
        "bad_ratio": 0.66,
        "desc": "只看关键帧，速度最快，适合粗筛",
    },
    "medium": {
        "label": "中度分析",
        "opencv_samples": 6,
        "bad_ratio": 0.5,
        "desc": "均衡采样，准确度与速度平衡，推荐日常使用",
    },
    "strict": {
        "label": "严格分析",
        "opencv_samples": 12,
        "bad_ratio": 0.34,
        "desc": "多帧深度采样，判废更严格，适合精筛",
    },
}


class ModeSelectDialog(QDialog):
    """分析模式选择弹窗（从超级混剪页点"开始分析"时先弹这个）。"""

    def __init__(self, current="medium", parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择分析模式")
        self.setMinimumWidth(440)
        self.setStyleSheet("QDialog{background:%s;}" % theme.COLORS["panel"])

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        tip = QLabel("请选择本次素材分析的模式：")
        tip.setStyleSheet(
            "color:%s;font-size:14px;font-weight:600;" % theme.COLORS["text"])
        root.addWidget(tip)

        self._radios = {}
        for key, cfg in ANALYSIS_MODES.items():
            radio = QRadioButton(cfg["label"])
            radio.setCursor(Qt.PointingHandCursor)
            radio.setStyleSheet(
                "QRadioButton{color:%s;font-size:13px;font-weight:600;}"
                "QRadioButton::indicator{width:14px;height:14px;}"
                % theme.COLORS["text"])
            root.addWidget(radio)

            sub = QLabel(cfg["desc"])
            sub.setWordWrap(True)
            sub.setStyleSheet(
                "color:%s;font-size:11px;padding-left:24px;"
                % theme.COLORS["muted"])
            root.addWidget(sub)
            self._radios[key] = radio

        if current in self._radios:
            self._radios[current].setChecked(True)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = btns.button(QDialogButtonBox.Ok)
        ok_btn.setText("开始分析")
        ok_btn.setStyleSheet(
            "QPushButton{background:%s;color:#101113;border:none;"
            "border-radius:6px;padding:8px 18px;font-weight:600;}"
            "QPushButton:hover{background:#FFFFFF;}"
            % theme.COLORS["accent"])
        cancel_btn = btns.button(QDialogButtonBox.Cancel)
        cancel_btn.setText("取消")
        cancel_btn.setStyleSheet(
            "QPushButton{background:transparent;color:%s;"
            "border:1px solid %s;border-radius:6px;padding:8px 16px;}"
            "QPushButton:hover{background:%s;}"
            % (theme.COLORS["text"], theme.COLORS["border"],
               theme.COLORS["hover"]))
        root.addWidget(btns)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

    def selected_mode(self):
        for key, radio in self._radios.items():
            if radio.isChecked():
                return key
        return "medium"


# =========================================================
# 可双击 / 可全屏的视频组件
# =========================================================
class _PreviewVideo(QVideoWidget):
    """QVideoWidget：双击切换播放，Esc 退出全屏。"""
    double_clicked = Signal()
    escape_pressed = Signal()

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.escape_pressed.emit()
            return
        super().keyPressEvent(event)


# =========================================================
# 独立缓存层（不随 worker 销毁，换模式复用）
# =========================================================
class AnalysisCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.scene_cache = {}    # normalized_path -> (duration, segments, fps, total)
        # 帧缓存必须同时包含素材路径和帧号，避免多视频同帧号互相污染。
        self.cv_cache = {}       # (normalized_path, abs_frame) -> OpenCV 结果 dict
        # 镜头检测结果：normalized_path -> {"cuts", "source", "ok", "error"}
        # 与 scene_cache 分开存：分段结果要复用，但镜头明细也要留给混剪与诊断。
        self.shot_cache = {}


# =========================================================
# 后台分析 Worker（帧缓存 + 任务队列 + 动态模式，完成即退出）
# =========================================================
class AnalysisWorker(QThread):
    progress = Signal(int, int, str)
    item_updated = Signal(dict)
    finished_all = Signal(list)
    worker_stopped = Signal()
    error = Signal(str)

    def __init__(self, paths: list, mode_key: str = "medium",
                 cache: AnalysisCache = None, initial_items: list = None,
                 verdict_config=None):
        super().__init__()
        self.paths = list(paths)
        self._mode_key = mode_key
        self.mode = ANALYSIS_MODES[mode_key]
        self.cache = cache or AnalysisCache()
        # 判废规则：用户可在「分析设置」弹窗里自定义哪些因素才算废
        from mixcut.verdict_config import VerdictConfig
        self.verdict_config = (verdict_config
                               if isinstance(verdict_config, VerdictConfig)
                               else VerdictConfig())
        self._stop = False
        self._paused = False
        self._gate = threading.Event()
        self._gate.set()

        self._q = queue.Queue()
        self._queued = set()

        self.detector = BadShotDetector()

        self._roster = []
        self._roster_map = {}
        self._items = {}
        self._initial_items = list(initial_items or [])
        self._done_count = 0
        self._cap = None
        self._cap_path = ""

    # -----------------------------------------------------
    def stop(self):
        self._stop = True
        self._gate.set()
        # 兜底：立刻终止本次分析启动的所有 ffmpeg。
        # 只标 _stop 的话，正在管道上阻塞的 ffmpeg 要等主循环走到检查点
        # 才会被关闭，期间仍占据 GB 级内存。
        try:
            frame_pipe.kill_all()
        except Exception:
            pass

    def pause(self):
        self._paused = True
        self._gate.clear()

    def resume(self):
        self._paused = False
        self._gate.set()

    def is_paused(self):
        return self._paused

    def request_mode(self, key):
        """主线程：分析中切换模式，重新把不完整片段入队（增量补齐）。"""
        if key not in ANALYSIS_MODES:
            return
        with self.cache.lock:
            self._mode_key = key
            self.mode = ANALYSIS_MODES[key]
            self._enqueue_missing_locked()

    def set_manual_included(self, path, seg_index, included):
        """同步 UI 的手动选择，确保当前 worker 后续重算时不会覆盖用户决定。"""
        key = (path, int(seg_index))
        with self.cache.lock:
            item = self._items.get(key)
            if item is not None:
                item["included"] = bool(included)
                item["manual_included"] = True

    # -----------------------------------------------------
    def run(self):
        try:
            self.progress.emit(0, 0, "正在准备素材分段，请稍候…")
            self._build_roster()
            # 把页面已有的手动选择状态带进本次运行，重算时保留
            for r in self._initial_items:
                key = (r["path"], r["seg_index"])
                if key in self._roster_map:
                    self._items[key] = r

            with self.cache.lock:
                self._enqueue_missing_locked()

            # ---- 唯一路径：顺序流式分析 ----
            # 用 ffmpeg 一次解码整条视频、按目标采样率抽帧并缩放到 960，
            # 边扫边按片段分桶评估。
            # cv2 逐段路径（每段 grab 150 帧 4K 全尺寸再 retrieve）已移除：
            # 实测同样素材下流式更快，而且不会把整张 4K 帧搬进 Python 内存。
            self._run_streaming()
            self._close_cap()
            self.worker_stopped.emit()
        except Exception as e:
            self.error.emit(str(e))
            self._close_cap()
            self.worker_stopped.emit()

    # -----------------------------------------------------
    def _stream_supported(self):
        """当前环境能否走顺序流式抽帧（需要 ffmpeg + frame_pipe）。

        流式是唯一分析路径，ffmpeg 缺失时直接报错而不是回退到 cv2 逐段。
        """
        try:
            from mixcut import frame_pipe
        except Exception:
            return False
        try:
            return bool(frame_pipe.find_ffmpeg())
        except Exception:
            return False

    def _run_streaming(self):
        """顺序流式分析：一次解码整条视频，边扫边分桶。"""
        if not self._stream_supported():
            raise RuntimeError(
                "找不到 ffmpeg，无法进行顺序解码分析。"
                "请确认程序目录下的 ffmpeg/bin/ 完整，或设置 MIXCUT_FFMPEG 环境变量。")
        from mixcut import frame_pipe

        # 按文件分组：同一文件的片段共享一次顺序扫描
        by_path = {}
        for entry in self._roster:
            by_path.setdefault(entry["path"], []).append(entry)

        total_segments = len(self._roster)
        if total_segments == 0:
            self.finished_all.emit(self._snapshot())
            return True

        # 每个文件需要哪些采样帧（按片段）
        for path, entries in by_path.items():
            if self._stop:
                break
            # 该文件是否有片段缺帧
            pending = []
            for entry in entries:
                cv_frames = self._required_frames(entry)
                if any(self._frame_cache_key(entry, f) not in self.cache.cv_cache
                       for f in cv_frames):
                    pending.append(entry)
            if not pending:
                continue

            name = entries[0]["name"]
            # 段长：相邻片段起点的差（通常 5 秒）
            seg_dur = 5.0
            if len(entries) > 1:
                seg_dur = max(0.5, float(entries[1]["seg_start"] - entries[0]["seg_start"]))
            elif entries:
                seg_dur = max(0.5, float(entries[0]["seg_end"] - entries[0]["seg_start"]))
            sample_count = len(self._cv_ratios())
            sample_fps = max(0.05, sample_count / seg_dur)

            # 以“相对时间点”方式给每帧归档到片段
            # 段 k 覆盖 [k*seg_dur, (k+1)*seg_dur)
            entries_by_index = {e["seg_index"]: e for e in entries}
            buckets = {e["seg_index"]: [] for e in pending}

            self.progress.emit(
                0, total_segments, f"顺序解码 {name}（每 {seg_dur:.0f} 秒 {sample_count} 帧）…")

            frame_no = 0
            ok_any = True
            # 显式持有生成器并保证最终关闭：
            # 直接 for ... in iter_frames(...) 时，break 后生成器要等 GC 回收，
            # 期间里面的 ffmpeg 会继续解码并占用 GB 级内存，多个叠加会拖垮机器。
            # 这里用 try/finally + gen.close() 立即触发生成器的清理逻辑。
            gen = None
            try:
                gen = frame_pipe.iter_frames(
                    path, fps=sample_fps,
                    max_side=self.detector.MAX_FRAME_SIDE)
                for ts, frame in gen:
                    if self._stop:
                        ok_any = False
                        break
                    if self._paused:
                        # 暂停期间主动让出解码进程：ffmpeg 会因管道写满而阻塞，
                        # 但进程本身仍在，因此暂停结束时重新建一次更稳妥。
                        while self._paused and not self._stop:
                            self._gate.wait(0.25)
                        if self._stop:
                            ok_any = False
                            break
                    frame_no += 1
                    # 按时间戳定位所属片段：片段区间是 [seg_start, seg_end)
                    # 不能用 int(ts // seg_dur) 推算，因为段边界可能不是
                    # seg_dur 的整数倍（末段还会被截短），会错配到相邻片段。
                    idx = None
                    for _e in entries:
                        if _e["seg_start"] - 1e-6 <= ts < _e["seg_end"] - 1e-6:
                            idx = _e["seg_index"]
                            break
                    if idx is None:
                        # 落在末段边界外（浮点误差）时，归到最后一段
                        if entries and abs(ts - entries[-1]["seg_end"]) < 0.2:
                            idx = entries[-1]["seg_index"]
                        else:
                            continue
                    if idx not in buckets:
                        continue
                    buckets[idx].append((ts, frame))
                    if frame_no % 30 == 0:
                        self.progress.emit(
                            self._done_count, total_segments,
                            f"{name} 解码中… 已抽 {frame_no} 帧")
            except Exception as exc:
                logger.debug("顺序解码失败 %s: %s", path, exc)
                ok_any = False
            finally:
                # 无条件关闭生成器 → 立即执行 frame_pipe 内部的 finally，杀掉 ffmpeg
                if gen is not None:
                    try:
                        gen.close()
                    except Exception:
                        pass

            # 分桶评估：每段的采样帧按比例取 sample_count 个
            for seg_index in sorted(buckets):
                if self._stop:
                    break
                frames = buckets[seg_index]
                if not frames:
                    continue
                entry = entries_by_index[seg_index]
                self._evaluate_stream_bucket(entry, frames, sample_count)
                self._done_count += 1
            # 释放本文件的帧桶，避免多文件时累积
            buckets.clear()

        if self._stop:
            return True

        # 全部完成
        self.progress.emit(total_segments, total_segments, "分析完成")
        self.finished_all.emit(self._snapshot())
        return True

    def _evaluate_stream_bucket(self, entry, frames, sample_count):
        """用顺序流里采到的帧评估一个片段，结果写进 cv_cache。

        ``frames`` 是 [(时间戳, BGR)]。这里按时间戳把抽到的帧映射到
        ``_required_frames`` 计算出的目标帧号上：每个目标帧取时间上最接近的
        采样帧来评估。这样 cv_cache 的键与原来逐段路径完全一致，
        ``_assemble`` 读到的就是完整数据，不会出现大量评分为 0。
        """
        key = entry["key"]
        cv_frames = self._required_frames(entry)
        fps = float(entry.get("fps") or 30.0) or 30.0
        if not frames or not cv_frames:
            with self.cache.lock:
                item = self._assemble(entry, cv_frames)
                self._items[key] = item
            self.item_updated.emit(item)
            return

        times = [float(t) for t, _f in frames]
        prev_gray = None
        for fidx in cv_frames:
            if self._stop:
                break
            target_t = fidx / fps
            # 找时间上最接近的采样帧
            best = min(range(len(times)), key=lambda i: abs(times[i] - target_t))
            frame = frames[best][1]
            if frame is None:
                continue
            q, gray = self.detector._eval_frame(frame, prev_gray,
                                                verdict=self.verdict_config)
            prev_gray = gray
            cache_key = self._frame_cache_key(entry, fidx)
            self.cache.cv_cache[cache_key] = self._pack_frame_result(q, fidx)
        with self.cache.lock:
            item = self._assemble(entry, cv_frames)
            self._items[key] = item
        self.item_updated.emit(item)
        self.progress.emit(
            self._done_count + 1, len(self._roster),
            f"完成 {entry['name']} 片段{entry['seg_index']+1}")

    # -----------------------------------------------------
    def _build_roster(self):
        for path in self.paths:
            self.progress.emit(
                0, 0, f"正在分段（{os.path.basename(path)}）…")
            duration, segments, fps, total = self._get_scene_info(path)
            name = os.path.basename(path)
            shot_info = self.cache.shot_cache.get(path) or {}
            for si, (s, e) in enumerate(segments):
                key = (path, si)
                entry = {
                    "key": key, "path": path, "name": name,
                    "seg_index": si, "seg_start": s, "seg_end": e,
                    "duration": duration, "fps": fps, "total_frames": total,
                    # 镜头级分段信息（第二十二阶段）。
                    # segmented_by 只反映「最终用了哪种分段方式」，
                    # 与「检测器是否成功」分开：
                    #   detection_ok=False + window → 检测失败，回退
                    #   detection_ok=True  + window → 单镜头/切点不可用，回退
                    #   detection_ok=True  + shot   → 真正按镜头切
                    "segmented_by": str(shot_info.get("segmentation_mode")
                                        or "window"),
                    "shot_detection_ok": bool(shot_info.get("detection_ok")),
                    "shot_source": shot_info.get("source") or "",
                    "shot_fallback": shot_info.get("error") or "",
                }
                self._roster.append(entry)
                self._roster_map[key] = entry

    def _probe_meta(self, path):
        """一次探测拿全 fps/宽高/总帧数/时长；ffprobe 不可用时回退 OpenCV。"""
        try:
            details = probe_details(path)
            vid = details.get("video") or {}
            return {
                "fps": float(vid.get("fps") or 30.0),
                "width": int(vid.get("width") or 0),
                "height": int(vid.get("height") or 0),
                "total": int(vid.get("nb_frames") or 0),
                "duration": float(
                    (details.get("format") or {}).get("duration") or 0.0),
            }
        except Exception:
            cap = cv2.VideoCapture(path)
            meta = {
                "fps": float(cap.get(cv2.CAP_PROP_FPS) or 30.0),
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                "total": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
                "duration": 0.0,
            }
            cap.release()
            return meta

    def _get_scene_info(self, path):
        """带缓存的分段信息（时长/分段/帧率/总帧数，重复分析不重复探测）。

        分段策略（2026-11-20 第二十一阶段改造）
        --------------------------------------
        优先用**镜头边界**分段，而不是固定 5 秒窗口：

        · 检测成功且切点合理 → 按真实镜头边界切（``_segments_from_shots``）
        · 检测失败 / 太密 / 太少   → 回退固定窗口（``_fixed_windows``），
          行为与改造前完全一致，不会被新功能拖垮

        改造前：分析页从头到尾只做固定窗口切分，镜头检测（filter_engine 里
        TransNetV2 / amverge / PySceneDetect）的结果**从来没进入过这条链路**，
        混剪拿到的永远是 5 秒窗口。详见台账第二十一阶段。

        失败时**不缓存空结果**，下次仍会重试——避免「一次失败永久降级」。
        """
        if path in self.cache.scene_cache:
            return self.cache.scene_cache[path]

        meta = self._probe_meta(path)
        fps, total = meta["fps"], meta["total"]
        try:
            duration = probe_duration(path) or 0.0
        except Exception:
            duration = 0.0
        if not duration:
            duration = meta["duration"] or (total / fps if fps else 0.0)

        segments, shots = self._segments_by_shots(path, duration)

        info = (duration, segments, fps, total)
        self.cache.scene_cache[path] = info
        # 镜头信息单独存：它比 segments 信息量大，且要供混剪使用
        self.cache.shot_cache[path] = shots
        return info

    def _segments_by_shots(self, path, duration):
        """分段。

        ⚠ 2026-11-20 第二十四阶段：**分析页分段一律用固定窗口**，
        不再在这里做镜头检测。

        为什么改回来
        ------------
        第二十一阶段曾让分段优先走镜头检测，想法是「按真实镜头切更自然」。
        实测代价不可接受（素材 C3084.MP4，157 秒 4K 1.94 GB）：

        · amverge 边缘检测 **200 秒**（每个关键帧都要 open+seek，
          314 个窗口 × 约 639 毫秒）
        · PySceneDetect 只要 2.6 秒，但这条素材**检测到 0 个切点**

        于是分段阶段要么卡 200 秒、要么白跑，用户看到的就是
        「一直卡在正在分段」。

        更要紧的是**职责本来就不同**：
        · 分析页分段 = 把长素材切成可编辑的单元，边界差半秒无害
        · 智能粗剪筛选 = 需要真实镜头边界，那里有完整的检测链
          （filter_engine.run_filter，含 TransNetV2/amverge/废片）

        所以分段只做便宜的事。需要逐镜头边界请用「智能粗剪筛选」。

        Returns:
            (segments, shots)
            shots 里的检测字段恒为「未执行」，供界面如实显示。
        """
        shots = {"cuts": [], "source": "", "detection_ok": False,
                 "segmentation_mode": "window",
                 "error": "分析页分段使用固定窗口（镜头边界见「智能粗剪筛选」）"}
        return self._split_segments(duration), shots

    def _segments_by_shots_DISABLED(self, path, duration):
        """【已停用】按镜头边界分段。

        保留这段实现是因为它是对的——只是太慢，不适合放在
        「点一下就开始」的分析页流程里。将来如果要做后台异步检测，
        可以从这里恢复。

        状态语义（第二十二阶段修正，仍有效）：
        · ``detection_ok``    检测器是否成功执行（没有抛异常）
        · ``segmentation_mode`` 最终采用的分段方式："shot" / "window"
        """
        shots = {"cuts": [], "source": "", "detection_ok": False,
                 "segmentation_mode": "window", "error": ""}
        if duration <= 0:
            return self._split_segments(duration), shots

        try:
            cuts, source = self._detect_shot_cuts(path)
        except Exception as exc:      # noqa: BLE001 - 记录后回退，不中断分析
            shots["error"] = "%s: %s" % (type(exc).__name__, exc)
            shots["detection_ok"] = False
            shots["segmentation_mode"] = "window"
            return self._split_segments(duration), shots

        # 走到这里说明检测器成功执行了（没抛异常）
        shots["detection_ok"] = True
        shots["source"] = source
        shots["cuts"] = list(cuts)

        if not cuts:
            # 单镜头长素材：检测成功但确实没有切点。
            # 用固定窗口是为了还能切出片段可用的区间，
            # 但**不能说这是镜头级分段**。
            if not source:
                # 连检测器都没跑起来（比如模块缺失），不算「成功执行」
                shots["detection_ok"] = False
                shots["error"] = "没有可用的镜头检测器"
            else:
                shots["error"] = "未检测到镜头切点（可能是单镜头素材），已用固定窗口"
            return self._split_segments(duration), shots

        segs = self._segments_from_shots(cuts, duration)
        if segs is None:
            # 切点太密或太少，不适合直接当片段用 → 回退固定窗口
            shots["error"] = "切点分布不适合直接分段，已回退固定窗口"
            shots["segmentation_mode"] = "window"
            return self._fixed_windows(duration), shots

        shots["segmentation_mode"] = "shot"
        return segs, shots

    def _segments_from_shots(self, cuts, duration):
        """切点 → 连续片段区间；不合理时返回 None 让调用方回退。

        合理性门槛（宁可用固定窗口，也不给用户一堆碎渣）：

        · 段数不少于 ``_SHOT_MIN_SEGMENTS`` 且不多于 ``_SHOT_MAX_SEGMENTS``
        · 短于 ``_SHOT_MIN_DUR`` 的片段占比不超过 ``_SHOT_MAX_TINY_RATIO``

        切点太密通常意味着检测器在高频纹理/噪点上误触发
        （第二十阶段实测过这种素材），这种情况固定窗口反而更稳。
        """
        pts = sorted({round(float(c), 3) for c in cuts
                      if 0.0 < float(c) < duration})
        if not pts:
            return None
        bounds = [0.0] + pts + [float(duration)]
        segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
        segs = [(s, e) for s, e in segs if e - s > 1e-3]
        if not (_SHOT_MIN_SEGMENTS <= len(segs) <= _SHOT_MAX_SEGMENTS):
            return None
        tiny = sum(1 for s, e in segs if (e - s) < _SHOT_MIN_DUR)
        if tiny / len(segs) > _SHOT_MAX_TINY_RATIO:
            return None
        return segs

    def _detect_shot_cuts(self, path):
        """调用引擎检测镜头切点，返回 (cuts, source_name)。

        成本分级（第二十四阶段重写）
        ---------------------------
        这里只服务于**分析页的分段**——把长素材切成可编辑片段。
        边界差半秒完全可接受，所以不该为此付出逐帧解码的代价。

        早期版本先跑 amverge 边缘检测（要逐关键帧 open+seek），
        实测 4K 素材 **1.276 秒/视频秒**：157 秒素材卡 200 秒，
        1 小时素材要 76 分钟，界面表现就是「一直卡在正在分段」。

        现在的顺序：

        1. **超过 ``_SHOT_SKIP_DETECT_DUR``（600 秒）→ 直接不检测**，
           用固定窗口。一小时素材切成 5 秒窗口本来就够用。
        2. **PySceneDetect 优先**（实测 0.018 秒/视频秒，比 amverge 快约 70 倍）
        3. 只有 PySceneDetect 没结果、且素材不算太长时，才补一次 amverge

        正式混剪流程走 ``filter_engine.run_filter``，那里的检测链不受影响。

        Returns:
            (cuts, source_name)
        """
        from mixcut import filter_engine as fe

        cuts, sources = [], []
        # 分段用的最小间隔：比 run_filter 的 0.5 秒宽松，减少碎渣
        min_keep = _SHOT_SEG_MIN_GAP

        # ---- 1) PySceneDetect：快（实测 0.018 秒/视频秒）----
        t0 = time.time()
        try:
            got = fe._scenedetect_cuts(path)
            if got:
                cuts.extend(t for t, _k, _s in got)
                sources.append("scenedetect")
        except Exception:
            pass
        spent = time.time() - t0

        if cuts:
            return sorted(set(cuts)), "+".join(sources)

        # ---- 2) amverge 边缘：慢（实测 1.276 秒/视频秒）----
        #
        # ⚠ 关键修正：**无论 PySceneDetect 有没有结果，都不能无条件跑 amverge**。
        #   上一版把「PySceneDetect 返回空」当成「该补一次 amverge」，
        #   而 4K 素材上 PySceneDetect 经常确实返回空（它的阈值偏保守），
        #   于是每次都退化成 200 秒的 amverge，修复等于没做。
        #
        #   现在改成：amverge 只在**预计耗时可接受**时才跑。
        #   预计耗时按实测的 1.3 秒/视频秒 估算（留一点余量）。
        est = self._estimate_amverge_cost(path)
        if est > _AMVERGE_COST_LIMIT:
            # 太贵，直接用 PySceneDetect 的结果（可能为空 → 上层回退固定窗口）
            sources.append("amverge-skipped")
            return sorted(set(cuts)), "+".join(sources)

        try:
            got = fe._amverge_cuts(path, min_keep)
            if got:
                cuts.extend(t for t, _k, _s in got)
                sources.append("amverge")
        except Exception:
            pass

        return sorted(set(cuts)), "+".join(sources)

    def _estimate_amverge_cost(self, path):
        """估算 amverge 边缘检测要花多少秒（超过限制就不跑）。

        按实测系数：4K 素材约 1.3 秒/视频秒。
        分辨率越高、时长越长越贵；这里只按时长线性估算，
        分辨率差异用 2K 以下打 5 折再近似一次（保守取值）。
        """
        try:
            duration = float(probe_duration(path) or 0.0)
        except Exception:
            duration = 0.0
        if duration <= 0:
            return 0.0
        factor = _AMVERGE_SEC_PER_VIDEO_SEC
        try:
            meta = self._probe_meta(path)
            w = int(meta.get("width") or 0)
            h = int(meta.get("height") or 0)
            # 1080p 及以下大约便宜一半（像素数约 1/4，但解码不是线性的）
            if w and h and w * h <= 1920 * 1080:
                factor *= 0.5
        except Exception:
            pass
        return duration * factor

    def _split_segments(self, duration):
        """短视频不切分；长视频按固定时长窗口切分，完整覆盖到结尾。"""
        if duration <= SEGMENT_THRESHOLD:
            return [(0.0, float(duration))]
        return self._fixed_windows(duration)

    def _fixed_windows(self, duration):
        segs, t = [], 0.0
        while t < duration:
            e = min(t + FALLBACK_WINDOW, duration)
            segs.append((t, e))
            t = e
        return segs

    # -----------------------------------------------------
    def _enqueue_missing_locked(self):
        for entry in self._roster:
            key = entry["key"]
            if key in self._queued:
                continue
            if not self._segment_complete(entry):
                self._q.put(key)
                self._queued.add(key)

    def _all_complete(self):
        return all(self._segment_complete(e) for e in self._roster)

    def _verdict_signature(self):
        """当前判废规则的指纹，用于区分「同一帧在不同规则下的结论」。

        改了判废规则后，旧缓存里的 is_bad 就不再可信，必须换一个命名空间，
        否则用户调整了规则却看到旧结果。
        """
        vcfg = getattr(self, "verdict_config", None)
        if vcfg is None:
            return "default"
        parts = []
        for key in sorted(vcfg.enabled):
            if vcfg.is_on(key):
                parts.append("%s=%.1f" % (key, vcfg.threshold(key)))
        return "|".join(parts) or "none"

    def _frame_cache_key(self, entry, frame_index):
        """帧缓存键 = (规范化路径, 绝对帧号)。

        **不含判废规则**：缓存里存的是「这一帧算出来的客观数值」
        （清晰度、亮度、对比度……），这些与规则无关。
        「是否算废」在每次聚合时按当前规则现算——这是毫秒级操作，
        不需要重新解码视频。

        早期版本把规则指纹放进了键里，导致用户改一档阈值就要重跑整轮分析，
        属于设计缺陷，已移除。见台账第十七阶段。
        """
        path_key = os.path.normcase(os.path.abspath(str(entry["path"])))
        return path_key, int(frame_index)

    def _pack_frame_result(self, q, frame_no):
        """把一帧的评估结果打包成缓存条目。

        **只存客观实测值**，不存「是否算废」的结论——
        结论依赖判废规则，规则一变就过期；实测值与规则无关，可以长期复用。
        判定在 ``_assemble`` / ``_judge_frame`` 里按当前规则现算。

        Args:
            q: ``ShotQuality``，_eval_frame 的返回
            frame_no: 绝对帧号（明细里要显示用）
        """
        return {
            "frame_no": int(frame_no),
            "quality_score": q.quality_score,
            "measured": {
                "sharpness": q.sharpness,
                "brightness": q.brightness,
                "contrast": q.contrast,
                "noise": q.noise,
                "motion": q.motion,
                "edge_density": q.edge_density,
                "dark_ratio": q.dark_ratio,
                "saturation": q.saturation,
                "color_cast": q.color_cast,
            },
        }

    def _judge_frame(self, packed):
        """按**当前**判废规则判定一帧，返回 (is_bad, reason, hits)。

        与 `verdict_advice.frame_hits` 口径一致：只看已启用的维度。
        """
        from mixcut.verdict_advice import frame_hits
        cfg = getattr(self, "verdict_config", None)
        if cfg is None:
            from mixcut.verdict_config import VerdictConfig as _VC
            cfg = _VC()
        hit, keys = frame_hits(packed, cfg)
        if not hit:
            return False, "", []
        labels = []
        try:
            from mixcut.verdict_config import DIMENSIONS
            labels = [DIMENSIONS[k]["label"] for k in keys if k in DIMENSIONS]
        except Exception:
            labels = list(keys)
        return True, "、".join(labels), keys

    def _segment_complete(self, entry):
        cv_frames = self._required_frames(entry)
        return all(
            self._frame_cache_key(entry, f) in self.cache.cv_cache
            for f in cv_frames)

    # -----------------------------------------------------
    def _cv_ratios(self):
        n = self.mode["opencv_samples"]
        grid = np.linspace(0.08, 0.92, GRID_SIZE)
        if n >= GRID_SIZE:
            idx = np.arange(GRID_SIZE)
        else:
            idx = sorted(set(np.linspace(
                0, GRID_SIZE - 1, n).round().astype(int)))
        return [float(grid[i]) for i in idx]

    def _abs_frame(self, entry, ratio):
        fps = entry["fps"]
        total = entry["total_frames"]
        s, e = entry["seg_start"], entry["seg_end"]
        t = s + (e - s) * ratio
        return max(0, min(int(t * fps), max(0, total - 1)))

    def _required_frames(self, entry):
        """当前模式下该片段需要的 OpenCV 采样帧（绝对帧号）。"""
        return sorted(set(
            self._abs_frame(entry, r) for r in self._cv_ratios()))

    # -----------------------------------------------------
    def _process(self, key):
        if self._stop:
            return
        entry = self._roster_map[key]
        self.progress.emit(
            self._done_count, len(self._roster),
            f"{entry['name']} 片段{entry['seg_index']+1} "
            f"({entry['seg_start']:.1f}-{entry['seg_end']:.1f}s)")

        cap = self._get_cap(entry["path"])

        cv_frames = self._required_frames(entry)
        cv_missing = [f for f in cv_frames
                      if self._frame_cache_key(entry, f) not in self.cache.cv_cache]
        # 一次顺序 grab 批量读取所有要评估的帧，避免反复随机 seek
        batch = self._read_batch(cap, cv_missing)
        missing_targets = [f for f in cv_missing if f not in batch]
        if missing_targets:
            raise RuntimeError(
                f"视频解码不完整：{entry['name']} 片段{entry['seg_index'] + 1}，"
                f"缺少 {len(missing_targets)} 个采样帧。")

        # OpenCV 客观评估
        prev_gray = None
        for fidx in cv_frames:
            if self._stop:
                break
            cache_key = self._frame_cache_key(entry, fidx)
            if cache_key in self.cache.cv_cache:
                continue
            frame = batch.get(fidx)
            if frame is None:
                continue
            # _eval_frame 返回 (ShotQuality, gray) 二元组；gray 已按内部缩放比例
            # 处理过，回传它作为下一帧的 prev_gray，保证运动强度口径一致。
            # verdict 传当前判废规则：用户勾掉的维度不会让这一帧变「差」。
            q, gray = self.detector._eval_frame(frame, prev_gray,
                                                verdict=self.verdict_config)
            self.cache.cv_cache[cache_key] = self._pack_frame_result(q, fidx)
            prev_gray = gray

        # 与 UI 手动选择共用同一把锁，避免重算覆盖刚刚做出的人工决定。
        with self.cache.lock:
            item = self._assemble(entry, cv_frames)
            self._items[key] = item
        self._done_count += 1
        self.item_updated.emit(item)
        self.progress.emit(
            self._done_count, len(self._roster),
            f"完成 {entry['name']} 片段{entry['seg_index']+1}")

    def _get_cap(self, path):
        if self._cap is None or self._cap_path != path:
            self._close_cap()
            self._cap = cv2.VideoCapture(path)
            self._cap_path = path
            if not self._cap.isOpened():
                self._close_cap()
                raise RuntimeError(f"无法打开视频进行解码：{path}")
        return self._cap

    def _close_cap(self):
        if self._cap is not None:
            self._cap.release()
        self._cap = None
        self._cap_path = ""

    def _read_batch(self, cap, targets):
        """顺序 grab 推进到目标帧再 retrieve，比随机 seek 快一个数量级。"""
        frames = {}
        targets = sorted(set(targets))
        if not targets:
            return frames
        cur = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        first = targets[0]
        if first < cur:
            cap.set(cv2.CAP_PROP_POS_FRAMES, first)
            cur = first
        for fidx in targets:
            # CAP_PROP_POS_FRAMES 表示下一帧索引；目标帧本身也必须 grab 一次。
            while cur <= fidx:
                if self._stop:
                    return frames
                if not cap.grab():
                    return frames
                cur += 1
            if cur == fidx + 1:
                ret, frame = cap.retrieve()
                if ret and frame is not None:
                    frames[fidx] = frame
        return frames

    # -----------------------------------------------------
    def _cv_quality(self, cv_res):
        """用 OpenCV 客观指标合成 0-100 画质分：按最差帧的罚分扣减。

        罚分来自 BadShotDetector._bad_score（黑/白场、模糊、曝光、手抖等
        客观硬伤的加权和），无硬伤即为满分，结果确定、可复现。
        """
        if not cv_res:
            return 0
        worst_penalty = max(int(r.get("penalty", 0) or 0) for r in cv_res)
        return max(0, min(100, 100 - worst_penalty * 3))

    def _assemble(self, entry, cv_frames):
        raw = [self.cache.cv_cache[self._frame_cache_key(entry, f)]
               for f in cv_frames
               if self._frame_cache_key(entry, f) in self.cache.cv_cache]
        n_cv = len(raw)

        # ---- 逐帧判定：按**当前规则现算** ----
        # 缓存里只有实测值，这里是「实测 vs 阈值」的比较，毫秒级。
        # 因此用户可以随时改判废规则，不需要重新解码视频。
        cv_res = []
        for packed in raw:
            is_bad, reason, keys = self._judge_frame(packed)
            cv_res.append({**packed, "is_bad": is_bad, "reason": reason,
                           "hit_keys": keys})

        bad_cv = sum(1 for q in cv_res if q["is_bad"])
        ratio = bad_cv / n_cv if n_cv else 0.0
        score = self._cv_quality(cv_res)

        # ---- 片段级判定（判定规则见 mixcut/verdict_config.py）----
        # 旧逻辑是「任意一帧出问题就整段判废」，导致纯色背景、浅景深等
        # 正常画面被大量误伤；且三档模式的 bad_ratio 被这条规则架空了。
        # 现在只有两种条件会判废：
        #   1. 差帧占比达到 bad_ratio
        #   2. 整体质量分低于 min_score
        vcfg = self.verdict_config
        ratio_limit = vcfg.bad_ratio if vcfg is not None \
            else self.mode["bad_ratio"]
        score_limit = vcfg.min_score if vcfg is not None else 25.0

        reasons = []
        if n_cv and ratio >= ratio_limit:
            reasons.append("画质差帧过多(%d/%d)" % (bad_cv, n_cv))
        if score < score_limit:
            reasons.append("整体质量分过低(%.0f)" % score)

        # 差帧的具体原因只作为说明信息列出，不单独构成判废依据
        detail = []
        if bad_cv > 0:
            for q in cv_res:
                if not q["is_bad"]:
                    continue
                for part in q["reason"].replace("，", "、").split("、"):
                    part = part.strip()
                    if part and part not in detail:
                        detail.append(part)

        uniq = [r for r in reasons if r]
        is_bad = len(uniq) > 0
        # 未判废但有个别差帧时，把明细放进 bad_reason 供界面提示
        detail_text = "、".join(detail)

        old = self._items.get(entry["key"], {})
        manual = old.get("manual_included", False)
        included = old.get("included") if manual else (not is_bad)

        # ---- 明细：每一帧的实测值 / 阈值 / 判定（A：判废明细弹窗的数据源）----
        # 用户想知道「为什么这段被废」，光有 is_bad 解释不了，
        # 必须把每一帧算出来的数值和当时用的阈值一起带出去。
        #
        # 注意顺序：frames_detail 必须在「C：可信度标注」之前算好，
        # 因为 of_item 要看它判断「数据是否完整」。
        # 早期版本先调 of_item 再定义 frames_detail，触发 NameError 又被
        # except 吞掉，导致 integrity 恒为 None、C 功能整体失效。见台账第二十一阶段。
        frames_detail = []
        # 当前规则下各启用维度的阈值；明细里显示的「阈值」用的是它
        cur_limits = {}
        if vcfg is not None:
            for k in vcfg.enabled:
                if vcfg.is_on(k):
                    cur_limits[k] = vcfg.threshold(k)
        for f in cv_frames:
            key_f = self._frame_cache_key(entry, f)
            packed = self.cache.cv_cache.get(key_f)
            if packed is None:
                continue
            # 判定按当前规则现算（缓存里只有实测值）
            is_bad_f, reason_f, keys_f = self._judge_frame(packed)
            # 采样帧时间点 = 帧号 / 视频真实帧率。
            # ⚠ 基准 C（帧号换算），**只能用于界面显示，不能当剪辑时间**。
            # 可变帧率素材上这个换算会失真；剪辑一律使用镜头切点等
            # 源时间轴时间（见 mixcut/timebase.py）。
            fps = float(entry.get("fps") or 0) or 0.0
            t = (float(f) / fps) if fps else 0.0
            measured = dict(packed.get("measured") or {})
            frames_detail.append({
                "frame_no": int(f),
                "time": round(t, 3),
                # 显式标记时间基准，供下游判断能不能用于剪辑
                "time_base": "frame_index",
                "is_bad": bool(is_bad_f),
                "reason": reason_f,
                "hit_keys": keys_f,
                "measured": measured,
                # 只保留这一帧实际命中的维度阈值，明细更干净
                "limits": {k: cur_limits[k] for k in keys_f
                           if k in cur_limits},
                "all_limits": dict(cur_limits),
                "quality_score": packed.get("quality_score"),
            })

        # ---- C：标注这份结果的可信度 ----
        # 必须放在 frames_detail 算完之后：of_item 靠它判断「数据是否完整」。
        #
        # 这里**不用裸 except 吞异常**。早期版本 frames_detail 先用后定义，
        # NameError 被 `except Exception: integrity_d = None` 悄悄吃掉，
        # 结果是 C 功能整体失效、界面永远不报警，而日志里一个字都没有。
        # 现在只捕获可预期的错误，并把原因带到结果里，供界面/日志显示。
        integrity_d = None
        integrity_error = ""
        try:
            from mixcut.verdict_integrity import of_item
            integrity = of_item(
                {"frames_detail": frames_detail,
                 "is_bad": is_bad, "score": score},
                expected_frames=len(cv_frames),
                current_signature=self._verdict_signature())
            integrity_d = integrity.to_dict()
        except Exception as exc:      # noqa: BLE001 - 记录而非静默
            integrity_error = "%s: %s" % (type(exc).__name__, exc)
            try:
                ctrl = getattr(self.win, "controller", None)
                if ctrl is not None and hasattr(ctrl, "log"):
                    ctrl.log("分析中心 · 可信度标注失败：%s" % integrity_error)
            except Exception:
                pass

        return {
            "frag_id": fragment_id(entry["path"], entry["seg_start"], entry["seg_end"]),
            "path": entry["path"],
            "name": entry["name"],
            "duration": max(0.0, entry["seg_end"] - entry["seg_start"]),
            "source_duration": entry["duration"],
            "seg_index": entry["seg_index"],
            "seg_start": entry["seg_start"],
            "seg_end": entry["seg_end"],
            "score": score,
            "min_score": score,
            "is_bad": is_bad,
            "bad_reason": "；".join(uniq),
            "minor_issues": detail_text,
            "preview_time": (entry["seg_start"] + entry["seg_end"]) / 2.0,
            "bad_frame_ratio": ratio,
            "included": included,
            "manual_included": manual,
            "analyzed_mode": self._mode_key,
            # ---- 判废明细（A：弹窗数据源）----
            "frames_detail": frames_detail,
            # ---- C：可信度标注 ----
            "integrity": integrity_d,
            # 标注失败时的原因（空串=正常）。留着便于排查，
            # 而不是像早期版本那样让异常彻底消失。
            "integrity_error": integrity_error,
            "analyzed_signature": self._verdict_signature(),
            "verdict_limits": {
                "bad_ratio": ratio_limit,
                "min_score": score_limit,
                "bad_frames": bad_cv,
                "total_frames": n_cv,
                "measured_score": score,
                # 判定用的条件文字，弹窗直接显示
                "conditions": [
                    {"label": "差帧占比", "op": ">=",
                     "limit": ratio_limit, "actual": ratio,
                     "hit": bool(n_cv and ratio >= ratio_limit)},
                    {"label": "整体质量分", "op": "<",
                     "limit": score_limit, "actual": score,
                     "hit": bool(score < score_limit)},
                ],
            },
        }

    def _snapshot(self):
        return [self._items[e["key"]] for e in self._roster
                if e["key"] in self._items]


# =========================================================
# 分析页
# =========================================================

WASTE_RED = "#F05A52"
# 「手动保留」标记色：算法判废但用户坚持保留的片段
WASTE_AMBER = "#E7B06B"

# 缩略图缓存上限：超出后按最久未用淘汰。
# 以前是无上限的全局字典，分析大素材时每张卡片都会往里面塞一份
# QPixmap，片段一多内存就随进度持续上涨，最后把进程撑爆。
_PM_CACHE = OrderedDict()
_PM_CACHE_MAX = 240
# 片段缩略图路径映射上限（key 是 frag_id，value 是 jpg 路径）
_SEG_THUMB_MAX = 2000


def _pm_cache_put(key, value):
    """写入缩略图缓存，超过上限时淘汰最久未用的条目。"""
    _PM_CACHE[key] = value
    _PM_CACHE.move_to_end(key)
    while len(_PM_CACHE) > _PM_CACHE_MAX:
        _PM_CACHE.popitem(last=False)


def _file_pixmap(path, w, h):
    """按绝对路径读缩略图，缩放到卡片尺寸（保持比例裁切）。

    关键：用 QImageReader 指定解码尺寸，而不是 QPixmap(path) 先加载原图
    再缩放。原图是 4K 时，QPixmap 一次性要 24MB，每张卡片都这么来一次，
    内存峰值会瞬间拉高；按目标尺寸解码只占几十 KB。
    """
    if not path:
        return None
    key = (path, w, h)
    cached = _PM_CACHE.get(key)
    if cached is not None:
        _PM_CACHE.move_to_end(key)
        return cached

    pm = None
    try:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        src = reader.size()
        if src.isValid() and src.width() > 0 and src.height() > 0:
            # 按“填充目标框”的比例设置解码尺寸（等价于 KeepAspectRatioByExpanding）
            sx = w / float(src.width())
            sy = h / float(src.height())
            scale = max(sx, sy)
            tw = max(1, int(src.width() * scale + 0.5))
            th = max(1, int(src.height() * scale + 0.5))
            reader.setScaledSize(QSize(tw, th))
        img = reader.read()
        if not img.isNull():
            pm = QPixmap.fromImage(img)
    except Exception:
        pm = None

    if pm is None or pm.isNull():
        # 兜底：老方式（个别格式 QImageReader 不认）
        pm = QPixmap(str(path))
        if pm.isNull():
            return None
        pm = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                       Qt.TransformationMode.SmoothTransformation)

    if pm.width() != w or pm.height() != h:
        pm = pm.copy(max(0, (pm.width() - w) // 2),
                     max(0, (pm.height() - h) // 2), w, h)
    _pm_cache_put(key, pm)
    return pm


def clear_pixmap_cache():
    """清空缩略图缓存（重新分析/切换工程时调用，及时回收内存）。"""
    _PM_CACHE.clear()


class _SegmentThumbWorker(QThread):
    """给每个片段抽一帧做卡片缩略图（后台串行，复用 ffmpeg 缩略图缓存）。"""

    ready = Signal(str, str)   # frag_key, jpg 路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._stop_flag = False

    def enqueue(self, key, path, seek):
        if not path:
            return
        self._queue.put((str(key), str(path), float(seek)))

    def stop(self):
        self._stop_flag = True
        self._queue.put((None, None, 0.0))

    def run(self):
        while not self._stop_flag:
            try:
                key, path, seek = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if key is None:
                break
            try:
                out = make_thumbnail(path, seek_seconds=seek,
                                     width=320, height=180)
            except Exception as exc:
                logger.debug("片段缩略图失败 %s: %s", path, exc)
                out = None
            if out:
                self.ready.emit(key, out)


class FolderList(QListWidget):
    """分析页左侧文件夹栏，支持把片段卡片直接拖进文件夹。"""

    fragsDropped = Signal(str, list)   # folder_id, [frag_id]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(False)
        self.setMinimumHeight(120)

    def _mime_ids(self, mime):
        data = mime.data("application/x-mixcut-frag-ids")
        if not data:
            return []
        try:
            text = bytes(data).decode("utf-8")
        except Exception:
            return []
        return [x for x in text.split(",") if x]

    def dragEnterEvent(self, e):
        if self._mime_ids(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._mime_ids(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        ids = self._mime_ids(e.mimeData())
        if not ids:
            super().dropEvent(e)
            return
        item = self.itemAt(e.position().toPoint())
        folder_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        self.fragsDropped.emit(folder_id, ids)
        e.acceptProposedAction()


class AnalysisFolderTile(QFrame):
    """右侧“全部素材”里的文件夹卡片，单击进入，接受片段卡片拖入。"""

    entered = Signal(str)
    fragsDropped = Signal(str, list)
    folderMenuRequested = Signal(str, QPoint)

    def __init__(self, folder_id, name, count=0, parent=None):
        super().__init__(parent)
        self.folder_id = str(folder_id or "")
        # 尺寸策略和片段卡片完全一致：高度固定 198，宽度随网格拉伸。
        # 以前写死 236×198，大屏下会和片段卡片宽度对不齐，看着别扭。
        self.setFixedHeight(198)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.folderMenuRequested.emit(
                self.folder_id, self.mapToGlobal(pos)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QFrame{background:%s;border:1px solid %s;border-radius:12px;}"
            "QFrame:hover{border-color:%s;background:%s;}"
            % (theme.COLORS["panel"], theme.COLORS["border"],
               theme.COLORS["accent"], theme.COLORS["hover"]))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 16)
        lay.setSpacing(8)

        icon = QLabel("📁")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(
            "font-size:56px;background:transparent;border:none;")
        lay.addWidget(icon, 1)

        self.name_label = QLabel(str(name or ""))
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(
            "color:%s;font-size:13px;font-weight:600;"
            "background:transparent;border:none;" % theme.COLORS["text"])
        lay.addWidget(self.name_label)

        self.count_label = QLabel("%d 个片段" % int(count))
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setStyleSheet(
            "color:%s;font-size:11px;background:transparent;border:none;"
            % theme.COLORS["faint"])
        lay.addWidget(self.count_label)

    def set_count(self, count):
        self.count_label.setText("%d 个片段" % int(count))

    def set_name(self, name):
        try:
            self.name_label.setText(str(name or ""))
        except RuntimeError:
            pass

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.entered.emit(self.folder_id)
            e.accept()
            return
        super().mousePressEvent(e)

    def _normal_style(self):
        self.setStyleSheet(
            "QFrame{background:%s;border:1px solid %s;border-radius:12px;}"
            "QFrame:hover{border-color:%s;background:%s;}"
            % (theme.COLORS["panel"], theme.COLORS["border"],
               theme.COLORS["accent"], theme.COLORS["hover"]))

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat("application/x-mixcut-frag-ids"):
            e.acceptProposedAction()
            self.setStyleSheet(
                "QFrame{background:%s;border:1px solid %s;border-radius:12px;}"
                % (theme.COLORS["panel_alt"], theme.COLORS["accent"]))
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._normal_style()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        data = e.mimeData().data("application/x-mixcut-frag-ids")
        try:
            ids = [x for x in bytes(data).decode("utf-8").split(",") if x]
        except Exception:
            ids = []
        if ids:
            self.fragsDropped.emit(self.folder_id, ids)
            e.acceptProposedAction()
        else:
            e.ignore()
        self._normal_style()


class FragmentCard(QWidget):
    """一个分析片段的卡片，样式对齐素材库的 ClipCard。

    · 正常片段：普通卡片外观，不显示评分；
    · 废镜头：边框红色，右下角红字「废」；
    · 手动排除的片段：缩略图左上角加一个「不加入」小标。
    """

    clicked = Signal(int)
    clickedModifiers = Signal(int, object)
    menuRequested = Signal(int, QPoint)
    poolChecked = Signal(int, bool)

    THUMB_H = 118
    HEIGHT = 198

    def __init__(self, row, item, thumb_of, parent=None):
        super().__init__(parent)
        self.row = row
        self.item = item
        self.thumb_of = thumb_of
        self.selected = False
        self._hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.pool_check = QCheckBox("池", self)
        self.pool_check.setToolTip("勾选：这个片段参与混剪；取消：不参与")
        self.pool_check.setStyleSheet(
            "QCheckBox{color:#FFFFFF;font-size:11px;"
            "background:rgba(0,0,0,150);border-radius:5px;padding:1px 6px;}"
            "QCheckBox::indicator{width:12px;height:12px;}")
        self.pool_check.toggled.connect(
            lambda checked: self.poolChecked.emit(self.row, checked))
        self._drag_start = None
        self.drag_ids = []
        self.drag_ids_provider = None

    def set_drag_ids(self, ids):
        self.drag_ids = [str(i) for i in (ids or []) if i]

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.pool_check.move(max(4, self.width() - 62), 12)

    def set_pool_state(self, on):
        self.pool_check.blockSignals(True)
        self.pool_check.setChecked(bool(on))
        self.pool_check.blockSignals(False)

    def set_item(self, item):
        self.item = item
        self.update()

    def set_selected(self, on, additive=False, ctrl=False):
        if self.selected != on:
            self.selected = on
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.menuRequested.emit(self.row, e.globalPosition().toPoint())
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.position().toPoint()
            self.clicked.emit(self.row)
            self.clickedModifiers.emit(self.row, e.modifiers())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._drag_start is not None
                and e.buttons() & Qt.MouseButton.LeftButton):
            moved = (e.position().toPoint() - self._drag_start).manhattanLength()
            if moved >= QApplication.startDragDistance():
                ids = None
                provider = getattr(self, "drag_ids_provider", None)
                if callable(provider):
                    try:
                        ids = provider(self.row)
                    except Exception:
                        ids = None
                if not ids:
                    ids = self.drag_ids or [self.item.get("frag_id") or ""]
                mime = QMimeData()
                mime.setData("application/x-mixcut-frag-ids",
                             ",".join(str(i) for i in ids).encode("utf-8"))
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.CopyAction)
                self._drag_start = None
                return
        super().mouseMoveEvent(e)

    def paintEvent(self, _e):
        it = self.item
        waste = bool(it.get("is_bad"))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        fill = QColor(T.CARD_HI if self._hover else T.CARD)
        if waste:
            border, pen_w = QColor(WASTE_RED), 1.6
        elif self.selected:
            border, pen_w = QColor(T.ACCENT), 1.4
        elif self._hover:
            border, pen_w = QColor(T.LINE_HI), 1.0
        else:
            border, pen_w = QColor(T.LINE), 1.0
        p.setPen(QPen(border, pen_w))
        p.setBrush(fill)
        p.drawRoundedRect(r, 14, 14)

        tw = max(1, int(r.width() - 16))
        th = self.THUMB_H
        thumb_rect = QRectF(r.left() + 8, r.top() + 8, tw, th)
        clip = QPainterPath()
        clip.addRoundedRect(thumb_rect, 9, 9)
        p.setClipPath(clip)
        pm = _file_pixmap(self.thumb_of(it), tw, th)
        if pm is None:
            p.fillRect(thumb_rect, QColor(T.INSET))
            p.setClipping(False)
            p.setPen(QColor(T.TEXT_3))
            p.setFont(T.font(11))
            p.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, "无缩略图")
        else:
            p.drawPixmap(int(thumb_rect.left()), int(thumb_rect.top()), pm)
        p.setClipping(False)

        # 时长（缩略图右下角）
        dur = float(it.get("duration") or 0.0)
        label = "%.1fs" % dur if dur else "—"
        p.setFont(T.font(10, QFont.Weight.DemiBold, mono=True))
        fm = p.fontMetrics()
        dw = fm.horizontalAdvance(label)
        badge = QRectF(thumb_rect.right() - dw - 18,
                       thumb_rect.bottom() - 22, dw + 12, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(badge, 5, 5)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)

        # 手动排除：左上角「不加入」
        if not it.get("included", True):
            chip = QRectF(thumb_rect.left() + 8, thumb_rect.top() + 8, 52, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(chip, 5, 5)
            p.setPen(QColor(T.TEXT_2))
            p.setFont(T.font(9, QFont.Weight.DemiBold))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "不加入")
        elif waste and it.get("manual_included"):
            # 手动保留：算法判废、但用户坚持要用的片段。
            # 必须和普通片段区分开，否则用户会疑惑「明明标了废，怎么还进混剪了」。
            chip = QRectF(thumb_rect.left() + 8, thumb_rect.top() + 8, 66, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(WASTE_AMBER))
            p.drawRoundedRect(chip, 5, 5)
            p.setPen(QColor("#1A1206"))
            p.setFont(T.font(9, QFont.Weight.DemiBold))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "手动保留")

        # 标题
        x = r.left() + 12
        avail = r.width() - 24
        p.setFont(T.font(13, QFont.Weight.DemiBold))
        p.setPen(QColor(T.TEXT))
        name = p.fontMetrics().elidedText(
            str(it.get("name") or ""), Qt.TextElideMode.ElideMiddle, int(avail))
        p.drawText(QRectF(x, thumb_rect.bottom() + 9, avail, 18),
                   Qt.AlignmentFlag.AlignVCenter, name)

        # 片段序号 + 原始时间范围
        p.setFont(T.font(11))
        p.setPen(QColor(T.TEXT_3))
        sub = "片段%d · %.1f–%.1fs" % (
            int(it.get("seg_index", 0)) + 1,
            float(it.get("seg_start", 0.0)),
            float(it.get("seg_end", 0.0)))
        p.drawText(QRectF(x, thumb_rect.bottom() + 28, avail, 16),
                   Qt.AlignmentFlag.AlignVCenter, sub)

        # 废镜头：右下角红字「废」
        if waste:
            p.setFont(T.font(13, QFont.Weight.Bold))
            p.setPen(QColor(WASTE_RED))
            p.drawText(QRectF(r.right() - 34, r.bottom() - 26, 22, 20),
                       Qt.AlignmentFlag.AlignCenter, "废")
        p.end()


class MixPoolDialog(QDialog):
    """混剪池管理：已分析素材多选、逐条勾选、文件夹分组。"""

    def __init__(self, page, parent=None):
        super().__init__(parent or page)
        self.page = page
        self._loading = False
        self.setWindowTitle("混剪池管理")
        self.resize(760, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        tip = QLabel("勾选加入混剪池；Ctrl/Shift 多选后点按钮批量加入或移出。"
                     "文件夹取消勾选后，整组素材都不会进入混剪。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:%s;font-size:12px;" % theme.COLORS["muted"])
        root.addWidget(tip)

        self.list = QListWidget()
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAlternatingRowColors(True)
        self.list.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.list, 1)

        row = QHBoxLayout()
        b_add = QPushButton("加入选中")
        b_remove = QPushButton("移出选中")
        b_folder = QPushButton("新建文件夹")
        b_into = QPushButton("移入文件夹")
        for b in (b_add, b_remove, b_folder, b_into):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        b_add.clicked.connect(self._add_selected)
        b_remove.clicked.connect(self._remove_selected)
        b_folder.clicked.connect(self._new_folder)
        b_into.clicked.connect(self._assign_folder)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("文件夹："))
        self.folder_box = QComboBox()
        self.folder_box.setMinimumWidth(180)
        frow.addWidget(self.folder_box)
        self.folder_host = QWidget()
        self.folder_lay = QVBoxLayout(self.folder_host)
        self.folder_lay.setContentsMargins(0, 0, 0, 0)
        self.folder_lay.setSpacing(4)
        fscroll = QScrollArea()
        fscroll.setWidgetResizable(True)
        fscroll.setWidget(self.folder_host)
        fscroll.setMaximumHeight(150)
        root.addWidget(QLabel("整组启停（取消勾选 = 该文件夹不参与混剪）"))
        root.addWidget(fscroll)

        buttons = QDialogButtonBox()
        b_close = buttons.addButton(
            "关闭", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh()

    def _paths(self):
        items = self.list.selectedItems()
        return [str(it.data(Qt.ItemDataRole.UserRole)) for it in items
                if it.data(Qt.ItemDataRole.UserRole)]

    def _refresh(self):
        self._loading = True
        self.list.clear()
        for item in self.page.source_summary():
            it = QListWidgetItem(
                "%s　%d 段 / %d 段可用　%s" % (
                    item["name"], int(item["segments"]),
                    int(item["included_segments"]),
                    item.get("folder_name") or "全部素材"))
            it.setData(Qt.ItemDataRole.UserRole, item["path"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(
                Qt.CheckState.Checked if item.get("in_pool")
                else Qt.CheckState.Unchecked)
            self.list.addItem(it)

        self.folder_box.clear()
        self.folder_box.addItem("全部素材", "__all__")
        while self.folder_lay.count():
            w = self.folder_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for folder in self.page.pool_state().get("folders") or []:
            self.folder_box.addItem(folder["name"], folder["id"])
            box = QCheckBox("%s（%d 条）" % (
                folder["name"], int(folder.get("count", 0))))
            box.setChecked(bool(folder.get("checked", True)))
            box.toggled.connect(
                lambda on, fid=folder["id"]: self._toggle_folder(fid, on))
            self.folder_lay.addWidget(box)
        self._loading = False

    def _on_item_changed(self, item):
        if self._loading:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        self.page.set_mix_member(
            path, item.checkState() == Qt.CheckState.Checked)
        # 只刷新状态文字，不重建列表，避免打断当前多选
        item.setText(item.text())

    def _add_selected(self):
        for path in self._paths():
            self.page.set_mix_member(path, True)
        self._refresh()

    def _remove_selected(self):
        for path in self._paths():
            self.page.set_mix_member(path, False)
        self._refresh()

    def _new_folder(self):
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        name = str(name or "").strip()
        if not ok or not name:
            return
        fid = self.page.create_mix_folder(name)
        paths = self._paths()
        if fid and paths:
            self.page.assign_mix_folder(paths, fid)
        self._refresh()

    def _assign_folder(self):
        paths = self._paths()
        if not paths:
            return
        fid = self.folder_box.currentData() or ""
        self.page.assign_mix_folder(paths, fid)
        self._refresh()

    def _toggle_folder(self, fid, checked):
        if self._loading:
            return
        self.page.set_mix_folder_checked(fid, checked)
        self._refresh()


class AnalyzePage(QWidget):
    analysisFinished = Signal(int)   # 分析完成时发出片段数量

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._worker = None
        self._results = []
        self._cache = AnalysisCache()
        self._mode_key = "medium"
        # 判废规则：用户在「分析设置」弹窗里自定义；跨轮次保留
        from mixcut.verdict_config import VerdictConfig
        self.verdict_config = VerdictConfig()
        self._finished = False

        # 已分析素材的混剪池：结果缓存保留全部批次，池子只决定谁参与混剪。
        self._pool_paths = set()
        self._pool_frag_ids = set()   # 片段级池：80 个片段各自独立进出
        self._pool_last_scope = None
        self._pool_folders = []        # [{"id":..., "name":..., "checked":True}]
        self._pool_folder_of = {}      # frag_id -> folder id
        self._frag_recency = {}        # frag_id -> 归类序号（越大越新，排最前）
        self._frag_seq = 0
        self._active_folder_id = ""    # 当前选中的文件夹（用于移入目标）
        self._current_folder_id = "__all__"  # 右侧网格当前显示的文件夹

        # 片段缩略图：frag_id → 抽帧 jpg；抽帧在后台线程，不阻塞界面
        self._seg_thumbs = {}
        self._thumb_pending = set()
        self._thumb_worker = None

        self._preview_player = None
        self._preview_audio = None
        self._preview_source = ""
        self._pending_pos = 0
        # 单击卡片只定位静态帧、不自动播放：预览源由单击真正加载时，
        # 也必须保持暂停（见 _locate_preview / _on_preview_media_status）。
        self._auto_preview = True

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ---- 顶部 ----
        head = QHBoxLayout()
        title = QLabel("分析中心")
        title.setObjectName("Hero")
        head.addWidget(title)
        head.addStretch(1)

        head.addWidget(QLabel("分析模式："))
        self.mode_box = QComboBox()
        for key, cfg in ANALYSIS_MODES.items():
            self.mode_box.addItem(cfg["label"], key)
        self.mode_box.setCurrentIndex(1)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        head.addWidget(self.mode_box)

        self.mode_desc = QLabel(ANALYSIS_MODES["medium"]["desc"])
        self.mode_desc.setStyleSheet(
            "color:%s;font-size:11px;" % theme.COLORS["faint"])
        head.addWidget(self.mode_desc)

        # 分析由素材库「分析选中」启动，这里只控制暂停/继续与停止
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setStyleSheet(
            "QPushButton{background:%s;color:black;border:none;border-radius:6px;"
            "padding:8px 18px;font-weight:600;}"
            "QPushButton:hover{background:#FFFFFF;}"
            "QPushButton:disabled{background:%s;}"
            % (theme.COLORS["accent"], theme.COLORS["faint"]))
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.pause_btn.setToolTip("暂停 / 继续素材分析")
        self.pause_btn.setEnabled(False)
        head.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setStyleSheet(
            "QPushButton{background:transparent;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:8px 14px;}"
            "QPushButton:hover{background:%s;}"
            % (theme.COLORS["text"], theme.COLORS["border"],
               theme.COLORS["hover"]))
        self.stop_btn.clicked.connect(self._stop_analysis)
        self.stop_btn.setEnabled(False)
        head.addWidget(self.stop_btn)
        root.addLayout(head)

        # ---- 统计卡片 ----
        stats = QHBoxLayout()
        self.stat_total = self._stat_card("素材总数", "0")
        self.stat_analyzed = self._stat_card("分析片段", "0")
        self.stat_duration = self._stat_card("总时长", "00:00")
        self.stat_bad = self._stat_card("废片段", "0")
        for c in (self.stat_total, self.stat_analyzed,
                  self.stat_duration, self.stat_bad):
            stats.addWidget(c)
        root.addLayout(stats)

        # ---- 进度条 ----
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet(
            "QProgressBar{background:%s;border:none;border-radius:2px;}"
            "QProgressBar::chunk{background:%s;border-radius:2px;}"
            % (theme.COLORS["panel_alt"], theme.COLORS["accent"]))
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(
            "color:%s;font-size:12px;" % theme.COLORS["muted"])
        root.addWidget(self.progress_label)

        # ---- 主体：左侧片段卡片网格 + 右侧画面预览 ----
        body_split = QSplitter(Qt.Horizontal)
        body_split.setHandleWidth(1)
        body_split.setStyleSheet(
            "QSplitter::handle{background:%s;}" % theme.COLORS["border_soft"])

        list_card = Card(padding=10)
        lc = list_card.layout

        self.empty_label = QLabel("还没有分析结果，请先分析喵～(∠・ω< )⌒★")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(
            "color:%s;font-size:13px;background:transparent;"
            % theme.COLORS["faint"])
        lc.addWidget(self.empty_label)

        # 直接长在片段卡片区上方的混剪池工具条：不再另开弹窗。
        self.pool_toolbar = QWidget()
        self.pool_toolbar.setStyleSheet("background:transparent;")
        pool_lay = QVBoxLayout(self.pool_toolbar)
        pool_lay.setContentsMargins(0, 8, 0, 8)
        pool_lay.setSpacing(6)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.pool_status_label = QLabel("混剪池 -/-")
        self.pool_status_label.setStyleSheet(
            "color:%s;font-size:12px;font-weight:600;" % theme.COLORS["text"])
        row1.addWidget(self.pool_status_label)
        self.pool_add_btn = QPushButton("加入混剪池")
        self.pool_remove_btn = QPushButton("移出混剪池")
        self.pool_new_btn = QPushButton("新建文件夹")
        self.pool_folder_combo = QComboBox()
        self.pool_folder_combo.setMinimumWidth(120)
        self.pool_folder_combo.setPlaceholderText("选择文件夹")
        self.pool_move_btn = QPushButton("移入文件夹")
        self.pool_all_btn = QPushButton("全选片段")
        self.pool_clear_btn = QPushButton("取消选择")
        self.pool_clear_btn.setToolTip("取消当前已选片段（也可以直接点网格空白处）")
        for b in (self.pool_add_btn, self.pool_remove_btn, self.pool_new_btn,
                  self.pool_move_btn, self.pool_all_btn, self.pool_clear_btn):
            b.setStyleSheet(
                "QPushButton{background:%s;color:%s;border:1px solid %s;"
                "border-radius:6px;padding:6px 10px;font-size:12px;}"
                "QPushButton:hover{background:%s;}"
                "QPushButton:disabled{color:%s;border-color:%s;}"
                % (theme.COLORS["panel"], theme.COLORS["text"],
                   theme.COLORS["border"], theme.COLORS["hover"],
                   theme.COLORS["faint"], theme.COLORS["border"]))
        row1.addWidget(self.pool_add_btn)
        row1.addWidget(self.pool_remove_btn)
        row1.addWidget(self.pool_new_btn)
        row1.addWidget(self.pool_folder_combo)
        row1.addWidget(self.pool_move_btn)
        row1.addWidget(self.pool_all_btn)
        row1.addWidget(self.pool_clear_btn)
        row1.addStretch(1)
        pool_lay.addLayout(row1)
        self.pool_toolbar.setVisible(True)
        lc.addWidget(self.pool_toolbar)
        self.pool_add_btn.clicked.connect(self._pool_add_selected)
        self.pool_remove_btn.clicked.connect(self._pool_remove_selected)
        self.pool_new_btn.clicked.connect(self._pool_new_folder)
        self.pool_move_btn.clicked.connect(self._pool_move_selected)
        self.pool_all_btn.clicked.connect(self._pool_select_all)
        self.pool_clear_btn.clicked.connect(self.clear_selection)
        self.pool_folder_combo.currentIndexChanged.connect(
            self._on_pool_folder_combo_changed)

        # ---- 文件夹视图：左侧文件夹栏 + 右侧当前文件夹内容 ----
        self.folder_split = QSplitter(Qt.Horizontal)
        self.folder_split.setHandleWidth(8)
        self.folder_split.setChildrenCollapsible(False)

        folder_side = QFrame()
        folder_side.setObjectName("Panel")
        folder_side.setFixedWidth(190)
        fl = QVBoxLayout(folder_side)
        fl.setContentsMargins(10, 10, 10, 8)
        fl.setSpacing(6)

        fh = QHBoxLayout()
        ftitle = QLabel("文件夹")
        ftitle.setStyleSheet(
            "color:%s;font-size:14px;font-weight:700;" % theme.COLORS["text"])
        fh.addWidget(ftitle)
        fh.addStretch(1)
        self.folder_back_btn = QPushButton("← 返回")
        self.folder_back_btn.setStyleSheet(
            "QPushButton{background:transparent;color:%s;border:1px solid %s;"
            "border-radius:5px;padding:3px 6px;font-size:10px;}"
            "QPushButton:hover{background:%s;}"
            "QPushButton:disabled{color:%s;border-color:%s;}"
            % (theme.COLORS["muted"], theme.COLORS["border"],
               theme.COLORS["hover"], theme.COLORS["faint"],
               theme.COLORS["border"]))
        self.folder_back_btn.clicked.connect(
            lambda: self._enter_folder("__all__"))
        fh.addWidget(self.folder_back_btn)
        fl.addLayout(fh)
        fsub = QLabel("按目录分类管理素材")
        fsub.setStyleSheet(
            "color:%s;font-size:10px;margin-left:2px;" % theme.COLORS["faint"])
        fl.addWidget(fsub)
        fsub = QLabel("按分析片段分类")
        fsub.setStyleSheet("color:%s;font-size:10px;" % theme.COLORS["faint"])
        fl.addWidget(fsub)

        self.folder_list = FolderList()
        self.folder_list.setStyleSheet(
            "QListWidget{background:transparent;border:none;"
            "color:%s;font-size:12px;outline:none;}"
            "QListWidget::viewport{background:transparent;}"
            "QListWidget::item{height:30px;padding-left:6px;color:%s;"
            "background:transparent;border-radius:5px;}"
            "QListWidget::item:hover{background:%s;}"
            "QListWidget::item:selected{background:%s;color:%s;}"
            % (theme.COLORS["text"], theme.COLORS["text"],
               theme.COLORS["hover"], theme.COLORS["selected_hi"],
               theme.COLORS["text"]))
        fl.addWidget(self.folder_list, 1)
        self.folder_new_side_btn = QPushButton("新建文件夹")
        self.folder_new_side_btn.setStyleSheet(
            "QPushButton{background:%s;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:6px 10px;font-size:12px;}"
            "QPushButton:hover{background:%s;}"
            % (theme.COLORS["panel_alt"], theme.COLORS["text"],
               theme.COLORS["border"], theme.COLORS["hover"]))
        self.folder_new_side_btn.clicked.connect(self._pool_new_folder)
        fl.addWidget(self.folder_new_side_btn)
        self.folder_list.fragsDropped.connect(self._on_folder_frags_dropped)
        self.folder_list.itemChanged.connect(self._on_folder_item_changed)
        self.folder_list.currentItemChanged.connect(self._on_folder_row_selected)
        self.folder_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_list.customContextMenuRequested.connect(
            self._on_folder_context_menu)
        self.folder_split.addWidget(folder_side)

        right_host = QWidget()
        right_lay = QVBoxLayout(right_host)
        right_lay.setContentsMargins(8, 0, 0, 0)
        right_lay.setSpacing(8)
        self.folder_path_label = QLabel("全部素材")
        self.folder_path_label.setStyleSheet(
            "color:%s;font-size:13px;font-weight:600;" % theme.COLORS["text"])
        right_lay.addWidget(self.folder_path_label)

        # 文件夹卡片和片段卡片共用同一个网格：文件夹永远占据第一行，
        # 片段接在文件夹下面。以前文件夹是独立的一条横向滚动条，
        # 片段全被归类后它就孤零零掉到网格下方，用户看到的就是“莫名出现在底部”。
        self.folder_tiles = {}          # folder_id -> AnalysisFolderTile
        self.folder_strip_scroll = None   # 兼容旧引用，已不再使用
        self.folder_strip_host = None
        self.folder_strip_lay = None

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}")
        self.grid_host = QWidget()
        self.grid_host.setStyleSheet("background:transparent;")
        self.grid_lay = QGridLayout(self.grid_host)
        self.grid_lay.setContentsMargins(0, 0, 0, 0)
        self.grid_lay.setSpacing(12)
        self.grid_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid_scroll.setWidget(self.grid_host)
        self.grid_scroll.hide()
        right_lay.addWidget(self.grid_scroll, 1)

        self.folder_split.addWidget(right_host)
        self.folder_split.setStretchFactor(0, 0)
        self.folder_split.setStretchFactor(1, 1)
        self.folder_split.setSizes([190, 560])
        lc.addWidget(self.folder_split, 1)

        self.cards = {}
        self._selected_row = -1
        self._selected_rows = set()
        self._anchor_row = -1
        self._cols = 0
        body_split.addWidget(list_card)

        preview_card = Card(padding=10)
        pl = preview_card.layout
        pl.setContentsMargins(10, 10, 10, 10)

        phead = QHBoxLayout()
        ptitle = QLabel("画面预览")
        ptitle.setObjectName("SectionTitle")
        phead.addWidget(ptitle)
        phead.addStretch(1)
        self.fs_btn = QPushButton("全屏")
        self.fs_btn.setObjectName("Ghost")
        self.fs_btn.clicked.connect(self._toggle_preview_fullscreen)
        phead.addWidget(self.fs_btn)
        pl.addLayout(phead)

        self.preview_host = QFrame()
        self.preview_host.setStyleSheet(
            "background:%s;border-radius:6px;" % theme.COLORS["bg"])
        self.preview_grid = QGridLayout(self.preview_host)
        self.preview_grid.setContentsMargins(0, 0, 0, 0)

        self.preview_video = _PreviewVideo()
        self.preview_video.setMinimumSize(300, 300)
        self.preview_video.double_clicked.connect(self._toggle_play)
        self.preview_video.escape_pressed.connect(self._exit_preview_fullscreen)
        self.preview_grid.addWidget(self.preview_video, 0, 0)

        self.preview_placeholder = QLabel(
            "单击左侧片段定位画面　双击播放/暂停")
        self.preview_placeholder.setAlignment(Qt.AlignCenter)
        self.preview_placeholder.setStyleSheet(
            "color:%s;font-size:13px;background:transparent;"
            % theme.COLORS["faint"])
        self.preview_grid.addWidget(self.preview_placeholder, 0, 0)

        # 预览容器右下角的悬浮全屏按钮（与素材预览页保持一致）
        self.fs_overlay = QPushButton("⤢", self.preview_host)
        self.fs_overlay.setFixedSize(30, 30)
        self.fs_overlay.setCursor(Qt.PointingHandCursor)
        self.fs_overlay.setToolTip("全屏（P）")
        self.fs_overlay.setStyleSheet(
            "QPushButton{background:rgba(0,0,0,150);color:white;border:none;"
            "border-radius:8px;font-size:13px;}"
            "QPushButton:hover{background:rgba(0,0,0,215);}")
        self.fs_overlay.clicked.connect(self._toggle_preview_fullscreen)
        self.preview_host.installEventFilter(self)

        pl.addWidget(self.preview_host, 1)

        self._preview_player = QMediaPlayer()
        self._preview_audio = QAudioOutput()
        self._preview_player.setAudioOutput(self._preview_audio)
        self._preview_player.setVideoOutput(self.preview_video)
        self._preview_player.mediaStatusChanged.connect(
            self._on_preview_media_status)

        self.preview_info = QLabel("")
        self.preview_info.setWordWrap(True)
        self.preview_info.setStyleSheet(
            "color:%s;font-size:12px;" % theme.COLORS["muted"])
        pl.addWidget(self.preview_info)

        body_split.addWidget(preview_card)
        body_split.setSizes([560, 380])
        body_split.setStretchFactor(0, 3)
        body_split.setStretchFactor(1, 2)
        root.addWidget(body_split, 1)

        # 应用级事件过滤器：全屏时可靠捕获 Esc（QVideoWidget 全屏后自身
        # keyPressEvent 可能被原生窗口拦截，收不到按键）
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        # 网格空白处点击 → 取消片段选择（和工具栏「取消选择」等价）
        self.install_blank_click()
        self._update_pool_btn()

    def eventFilter(self, obj, event):
        try:
            # 全屏的是 preview_host 容器，Esc / P 都能退出
            if event.type() == QEvent.Type.KeyPress and event.key() in (
                    Qt.Key_Escape, Qt.Key_P):
                if self.preview_host.isFullScreen():
                    self._exit_preview_fullscreen()
                    return True
            if obj is self.preview_host and event.type() == QEvent.Type.Resize:
                self._place_fs_overlay()
            if obj in self._blank_click_targets():
                if self._blank_area_clicked(obj, event):
                    return True
        except RuntimeError:
            pass
        return super().eventFilter(obj, event)

    def _blank_click_targets(self):
        if not getattr(self, "_blank_click_installed", False):
            return ()
        if getattr(self, "_blank_targets_cache", None) is None:
            self._blank_targets_cache = tuple(self._iter_blank_targets())
        return self._blank_targets_cache

    # -----------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_stats()

    def _on_mode_changed(self):
        key = self.mode_box.currentData()
        self._mode_key = key
        self.mode_desc.setText(ANALYSIS_MODES[key]["desc"])
        if self._worker is not None and self._worker.isRunning():
            # 分析中切换：worker 增量补齐
            self._worker.request_mode(key)
        elif self._results:
            # 已分析完：复用缓存重启，只补缺失帧，已分析结果保留
            self._launch()

    def set_mode(self, mode_key):
        """外部弹窗设置模式，同步下拉框（会触发 _on_mode_changed）。"""
        if mode_key not in ANALYSIS_MODES:
            mode_key = "medium"
        idx = self.mode_box.findData(mode_key)
        if idx >= 0:
            self.mode_box.setCurrentIndex(idx)
        else:
            self._mode_key = mode_key
            self.mode_desc.setText(ANALYSIS_MODES[mode_key]["desc"])

    def _stat_card(self, label, value):
        f = QFrame()
        f.setStyleSheet(
            "background:%s;border-radius:6px;" % theme.COLORS["panel_alt"])
        v = QVBoxLayout(f)
        v.setContentsMargins(16, 12, 16, 12)
        l = QLabel(label)
        l.setStyleSheet("color:%s;font-size:11px;" % theme.COLORS["faint"])
        v.addWidget(l)
        val = QLabel(value)
        val.setStyleSheet(
            "color:%s;font-size:22px;font-weight:700;" % theme.COLORS["text"])
        v.addWidget(val)
        f._value_label = val
        return f

    def _get_paths(self):
        return getattr(self.win, "_confirmed_paths", None) or []

    # ---- 已分析片段的混剪池（片段级，不是文件级） --------------------
    def _frag_id(self, item):
        return self._frag_key(item)

    def _pool_folder(self, folder_id):
        for folder in self._pool_folders:
            if folder["id"] == folder_id:
                return folder
        return None

    def _folder_of_row(self, row):
        if not (0 <= row < len(self._results)):
            return ""
        return str(self._pool_folder_of.get(
            self._frag_id(self._results[row]), "") or "")

    def _rows_in_current_folder(self):
        current = str(getattr(self, "_current_folder_id", "__all__") or "")
        if current in ("", "__all__"):
            # 「全部素材」= 已分析库里所有尚未归入文件夹的片段；
            # 归类的片段只出现在各自文件夹里，不会在全部素材里重复出现。
            return [i for i in range(len(self._results))
                    if not self._folder_of_row(i)]
        return [i for i in range(len(self._results))
                if self._folder_of_row(i) == current]

    def _folder_display_name(self, folder_id):
        folder_id = str(folder_id or "")
        if folder_id in ("", "__all__"):
            return "全部素材"
        folder = self._pool_folder(folder_id)
        return folder["name"] if folder else "全部素材"

    def _frag_in_pool(self, item):
        fid = self._frag_id(item)
        if fid not in self._pool_frag_ids:
            return False
        folder = self._pool_folder(self._pool_folder_of.get(fid))
        return bool(folder.get("checked", True)) if folder else True

    def pool_fragment_ids(self):
        """当前真正参与混剪的片段 id（文件夹取消勾选后整组排除）。"""
        return {self._frag_id(item) for item in self._results
                if self._frag_in_pool(item)}

    def _result_paths(self):
        out = []
        for item in self._results:
            path = str(item.get("path") or "")
            if path and path not in out:
                out.append(path)
        return out

    def _path_in_pool(self, path):
        """文件级兼容：该文件只要还有片段在池里就算在池。"""
        return any(str(item.get("path") or "") == path and self._frag_in_pool(item)
                   for item in self._results)

    def _sync_pool_paths(self):
        self._pool_paths = {str(item.get("path") or "")
                            for item in self._results
                            if item.get("path") and self._frag_in_pool(item)}

    def source_summary(self):
        """兼容旧界面的按源文件汇总；混剪池本身已改为片段级。"""
        groups = {}
        for item in self._results:
            path = str(item.get("path") or "")
            if not path:
                continue
            group = groups.setdefault(path, {
                "path": path, "name": item.get("name") or os.path.basename(path),
                "segments": 0, "included_segments": 0, "duration": 0.0,
                "score_total": 0.0, "in_pool": False,
            })
            group["segments"] += 1
            group["included_segments"] += 1 if item.get("included", True) else 0
            group["duration"] += max(
                0.0, float(item.get("seg_end", 0.0)) - float(item.get("seg_start", 0.0)))
            group["score_total"] += float(item.get("score", 70.0))
            group["in_pool"] = group["in_pool"] or self._frag_in_pool(item)
        return [{
            "path": path, "name": g["name"], "segments": g["segments"],
            "included_segments": g["included_segments"],
            "duration": round(g["duration"], 3),
            "score": round(g["score_total"] / max(1, g["segments"]), 1),
            "in_pool": g["in_pool"], "folder": "", "folder_name": "",
        } for path, g in groups.items()]

    def pool_state(self):
        counts = {}
        for fid_id, folder_id in self._pool_folder_of.items():
            if folder_id:
                counts[folder_id] = counts.get(folder_id, 0) + 1
        folders = [{
            "id": f["id"], "name": f["name"],
            "checked": bool(f.get("checked", True)),
            "count": counts.get(f["id"], 0),
        } for f in self._pool_folders]
        items = []
        for item in self._results:
            fid = self._frag_id(item)
            folder_id = self._pool_folder_of.get(fid, "")
            folder = self._pool_folder(folder_id) if folder_id else None
            items.append({
                "frag_id": fid,
                "path": str(item.get("path") or ""),
                "name": str(item.get("name") or os.path.basename(
                    str(item.get("path") or ""))),
                "seg_index": int(item.get("seg_index", 0)),
                "seg_start": float(item.get("seg_start", 0.0)),
                "seg_end": float(item.get("seg_end", 0.0)),
                "segments": 1,
                "included_segments": 1 if item.get("included", True) else 0,
                "duration": round(max(
                    0.0,
                    float(item.get("seg_end", 0.0)) -
                    float(item.get("seg_start", 0.0)),
                ), 3),
                "score": float(item.get("score", 70.0)),
                "in_pool": self._frag_in_pool(item),
                "folder": folder_id,
                "folder_name": folder["name"] if folder else "",
            })
        self._sync_pool_paths()
        return {
            "items": items,
            "folders": folders,
            "paths": list(self._pool_paths),
            "frag_ids": list(self._pool_frag_ids),
            "folder_of": dict(self._pool_folder_of),
        }

    def load_pool_state(self, state):
        """从工程恢复混剪池；旧工程没有该字段时保持默认全量。"""
        if not isinstance(state, dict) or not state:
            return
        folders = []
        for f in state.get("folders") or []:
            if isinstance(f, dict) and f.get("id"):
                folders.append({
                    "id": str(f["id"]),
                    "name": str(f.get("name") or f["id"]),
                    "checked": bool(f.get("checked", True)),
                })
        self._pool_folders = folders
        all_fids = {self._frag_id(item) for item in self._results}
        raw_folder_of = state.get("folder_of") or {}
        converted = {}
        for key, folder_id in raw_folder_of.items():
            if not key or not folder_id:
                continue
            key = str(key)
            if key in all_fids:
                converted[key] = str(folder_id)
            else:
                # 旧工程按文件路径保存 folder_of，兼容成该文件的所有片段。
                for item in self._results:
                    if str(item.get("path") or "") == key:
                        converted[self._frag_id(item)] = str(folder_id)
        self._pool_folder_of = converted
        frags = state.get("frag_ids")
        if isinstance(frags, list):
            raw = {str(f) for f in frags if f}
            all_paths = {
                str(item.get("path") or "") for item in self._results}
            # 旧工程可能把“文件路径”写进 frag_ids；路径要展开成该文件
            # 的全部分析片段，避免 32 个文件只剩 32 个片段进入混剪池。
            path_hits = raw & all_paths
            fid_hits = raw & all_fids
            expanded = {
                self._frag_id(item) for item in self._results
                if str(item.get("path") or "") in path_hits}
            self._pool_frag_ids = fid_hits | expanded
        else:
            paths = state.get("paths") or []
            wanted = {str(p) for p in paths}
            self._pool_frag_ids = {
                self._frag_id(item) for item in self._results
                if str(item.get("path") or "") in wanted}
        self._pool_last_scope = None
        self._sync_pool_paths()
        if hasattr(self, "cards"):
            self._refresh_pool_ui()

    def mix_pool_paths(self):
        return [p for p in self._result_paths() if self._path_in_pool(p)]

    def pool_fragment_rows(self):
        return [i for i in self._results if self._frag_in_pool(i)]

    def set_mix_member(self, key, included):
        """key 可以是片段 id，也可以是文件路径（路径时批量处理该文件所有片段）。"""
        key = str(key)
        fids = {key} if key in self._pool_frag_ids or any(
            self._frag_id(i) == key for i in self._results) else {
            self._frag_id(i) for i in self._results
            if str(i.get("path") or "") == key}
        for fid in fids:
            if included:
                self._pool_frag_ids.add(fid)
            else:
                self._pool_frag_ids.discard(fid)
                self._pool_folder_of.pop(fid, None)
        self._sync_pool_paths()

    def set_frag_member(self, frag_id, included):
        self.set_mix_member(frag_id, included)

    def create_mix_folder(self, name):
        """新建一个文件夹；同名直接复用（不会在文件夹里再套一层）。"""
        name = str(name or "").strip()
        if not name:
            return ""
        existing = {f["name"] for f in self._pool_folders}
        if name in existing:
            return next(f["id"] for f in self._pool_folders if f["name"] == name)
        used = {f["id"] for f in self._pool_folders}
        i = 1
        while "f%d" % i in used:
            i += 1
        fid = "f%d" % i
        self._pool_folders.append({"id": fid, "name": name, "checked": True})
        # 文件夹是平的（没有父子层级）：新建后立刻把文件夹卡片和左侧栏刷新出来，
        # 避免“在文件夹里又建出一个文件夹”的错觉。
        self._refresh_folder_views()
        return fid

    def _refresh_folder_views(self):
        """文件夹结构变化后统一刷新：左侧栏 + 网格里的文件夹卡片 + 状态文字。"""
        try:
            self._sync_folder_tiles()
            self._relayout_cards()
            self._refresh_folder_bar()
            self._update_pool_btn()
        except RuntimeError:
            pass

    def assign_mix_folder(self, paths, folder_id):
        """paths 可传片段 id 或源文件路径；传路径时该文件的所有片段一起处理。"""
        folder_id = str(folder_id or "")
        all_ids = {self._frag_id(i) for i in self._results}
        targets = set()
        for key in paths:
            key = str(key)
            if key in all_ids:
                targets.add(key)
            else:
                targets |= {self._frag_id(i) for i in self._results
                            if str(i.get("path") or "") == key}
        for fid in targets:
            self._pool_frag_ids.add(fid)
            if folder_id:
                self._pool_folder_of[fid] = folder_id
            else:
                self._pool_folder_of.pop(fid, None)
        self._sync_pool_paths()
        if not folder_id:
            return True
        folder = self._pool_folder(folder_id)
        if folder is None:
            return False
        folder["checked"] = True
        return True

    def assign_frags_to_folder(self, frag_ids, folder_id):
        """只按片段 id 归类（拖入 / 右键「加入文件夹」用），返回实际归类数量。

        归类不会把片段移出混剪池：进文件夹的片段自动留在池里，文件夹整组
        取消勾选时才整体排除（见 ``_frag_in_pool``）。
        """
        folder_id = str(folder_id or "")
        folder = self._pool_folder(folder_id)
        if folder is None:
            return 0
        all_ids = {self._frag_id(i) for i in self._results}
        moved = 0
        for key in (frag_ids or []):
            fid = str(key or "")
            if not fid or fid not in all_ids:
                continue
            self._pool_folder_of[fid] = folder_id
            self._pool_frag_ids.add(fid)
            moved += 1
        folder["checked"] = True
        self._sync_pool_paths()
        return moved

    def folder_of_frag(self, frag_id):
        """片段当前所在文件夹 id；不在文件夹里返回空串。"""
        return str(self._pool_folder_of.get(str(frag_id or ""), "") or "")

    def set_mix_folder_checked(self, folder_id, checked):
        folder = self._pool_folder(folder_id)
        if folder is None:
            return False
        folder["checked"] = bool(checked)
        return True

    def fragment_pool(self, paths=None, pool_only=False):
        """导出片段级分析结果，作为混剪的唯一片段来源。

        每个片段带稳定 frag_id、原始路径、准确起止、画质分、废片判定和
        人工加入/排除状态。``paths`` 非空时只导出这些文件的片段。
        ``pool_only=True`` 时再按混剪池的片段勾选过滤。
        """
        allowed = set(paths) if paths else None
        allowed_ids = self.pool_fragment_ids() if pool_only else None
        pool = []
        for item in self._results:
            path = item.get("path")
            if allowed is not None and path not in allowed:
                continue
            start = float(item.get("seg_start", 0.0))
            end = float(item.get("seg_end", 0.0))
            fid = item.get("frag_id") or fragment_id(path, start, end)
            if allowed_ids is not None and fid not in allowed_ids:
                continue
            folder_id = self._pool_folder_of.get(fid, "")
            if allowed_ids is not None and folder_id:
                folder = self._pool_folder(folder_id)
                if folder is not None and not folder.get("checked", True):
                    continue
            pool.append({
                "frag_id": fid,
                "path": path,
                "name": item.get("name") or os.path.basename(str(path)),
                "start": start,
                "end": end,
                "source_duration": float(item.get("source_duration") or 0.0),
                "score": float(item.get("score", 70.0)),
                "is_bad": bool(item.get("is_bad", False)),
                "reason": str(item.get("bad_reason") or ""),
                "included": bool(item.get("included", True)),
                "manual_included": bool(item.get("manual_included", False)),
            })
        return pool

    def included_paths(self):
        """文件级：任一子片段加入则文件加入；无结果返回 None。"""
        if not self._results:
            return None
        included_files = {
            r["path"] for r in self._results if r.get("included", True)}
        ordered = []
        for p in self._get_paths():
            if p in included_files and p not in ordered:
                ordered.append(p)
        for p in included_files:
            if p not in ordered:
                ordered.append(p)
        return ordered

    def _cached_duration(self, path):
        cache = getattr(self.win, "_probe_cache", None)
        if cache and path in cache:
            d = cache[path].get("format", {}).get("duration")
            if d:
                return float(d)
        return None

    def _refresh_stats(self):
        """顶部统计卡：一律按“本轮素材”口径，不混入历史批次。

        以前“素材总数”用本轮路径（3 条），而“分析片段/总时长”用全部历史结果
        （几百段），换一批素材后两组数字就对不上了。
        """
        paths = [str(p) for p in (self._get_paths() or [])]
        scope = set(paths)
        if scope:
            results = [r for r in self._results
                       if str(r.get("path") or "") in scope]
        else:
            results = list(self._results)
        self.stat_total._value_label.setText(str(len(paths)))
        self.stat_analyzed._value_label.setText(str(len(results)))

        # 时长一律按“本轮片段”累加：没结果时才退回素材总时长兜底。
        total_dur = sum(float(r.get("duration") or 0.0) for r in results)
        if total_dur <= 0:
            for p in paths:
                d = self._cached_duration(p)
                if d:
                    total_dur += d
        self.stat_duration._value_label.setText(self._fmt_duration(total_dur))
        bad = sum(1 for r in results if r.get("is_bad"))
        self.stat_bad._value_label.setText(str(bad))

    def _fmt_duration(self, seconds):
        return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"

    # -----------------------------------------------------
    def _launch(self):
        paths = self._get_paths()
        if not paths:
            self.progress_label.setText("请先在素材库确认要分析的素材")
            return

        # 旧批次结果继续保留，供用户在「混剪池」里手动加入/移出；
        # 新批次默认只把本轮路径放进池子，避免上一轮结果自动混入。
        new_scope = {str(p) for p in paths}
        if self._pool_last_scope != new_scope:
            self._pool_paths = set(new_scope)
            self._pool_frag_ids = {
                self._frag_id(item) for item in self._results
                if str(item.get("path") or "") in new_scope}
            self._pool_last_scope = set(new_scope)

        # 复用持久缓存实例：已分析片段命中 cv cache，不重复计算
        self._reset_preview()
        # 新一轮分析开始前清空缩略图缓存：上一批卡片的 QPixmap 已经没有用了，
        # 留着只会和新卡片一起占内存（大素材分析时这是内存上涨的来源之一）。
        clear_pixmap_cache()
        self._finished = False
        self.pause_btn.setText("暂停")
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.progress_bar.show()
        self.progress_bar.setValue(0)

        self._worker = AnalysisWorker(
            paths, self._mode_key, self._cache,
            initial_items=self._results,
            verdict_config=self.verdict_config)
        self._worker.progress.connect(self._on_progress)
        self._worker.item_updated.connect(self._on_item_updated)
        self._worker.finished_all.connect(self._on_finished_all)
        self._worker.worker_stopped.connect(self._on_worker_stopped)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _start_analysis(self):
        """对外入口（主窗口 go_analyze 调用）：复用缓存的一轮分析。"""
        self._launch()

    def _toggle_pause(self):
        """暂停/继续当前分析（分析由素材库「分析选中」启动）。"""
        worker = self._worker
        if worker is None or not worker.isRunning():
            self.progress_label.setText("当前没有正在运行的分析任务")
            return
        if worker.is_paused():
            worker.resume()
            self.pause_btn.setText("暂停")
            self.progress_label.setText("已继续分析…")
        else:
            worker.pause()
            self.pause_btn.setText("继续")
            self.progress_label.setText("已暂停分析（点「继续」恢复）")

    def analysis_ready(self):
        """分析是否已完成：参数页「开始智能混剪」的启用条件。"""
        return bool(self._finished and self._results)

    def drop_paths(self, removed_paths):
        """素材被删掉时同步清掉对应片段；删空则整页回到空态。"""
        removed = set(removed_paths or [])
        if not removed:
            return 0
        before = len(self._results)
        removed_fids = {self._frag_id(r) for r in self._results
                        if r.get("path") in removed}
        self._results = [r for r in self._results if r.get("path") not in removed]
        self._pool_frag_ids -= removed_fids
        self._pool_paths -= removed
        for fid in removed_fids:
            self._pool_folder_of.pop(fid, None)
        self._sync_pool_paths()
        dropped = before - len(self._results)
        if not self._results:
            self.clear_results()
            return dropped
        if dropped:
            self._rebuild_list_from_results()
            self._refresh_stats()
        return dropped

    def load_results(self, items, finished=True, mode_key=None):
        """从工程恢复分析结果：只重建界面，不重新跑分析。

        ⚠ 注意 clear_results() 会清缩略图缓存（clear_pixmap_cache）。
        撤销/重做时调本方法若也清缓存，就会出现「贴图全没了」——
        所以这里**先备份再还原**缩略图，避免一次撤销把画面变成空白。

        （2026-11-20 第二十六阶段：用户反馈 Ctrl+Z 后贴图消失且无法恢复。）
        """
        # 备份已生成的缩略图：撤销/重做不该让卡片变成空壳
        saved_thumbs = dict(getattr(self, "_seg_thumbs", {}) or {})
        self.clear_results()
        # 还原缩略图（clear_results 里调了 clear_pixmap_cache）
        try:
            if saved_thumbs:
                self._seg_thumbs.update(saved_thumbs)
        except Exception:
            pass
        if mode_key in ANALYSIS_MODES:
            self._mode_key = mode_key
            try:
                idx = self.mode_box.findData(mode_key)
                if idx >= 0:
                    self.mode_box.blockSignals(True)
                    self.mode_box.setCurrentIndex(idx)
                    self.mode_box.blockSignals(False)
                    self.mode_desc.setText(ANALYSIS_MODES[mode_key]["desc"])
            except Exception:
                pass
        restored = [dict(r) for r in (items or []) if isinstance(r, dict) and r.get("path")]
        self._results = restored
        if not self._pool_paths:
            # 工程恢复后默认全部已分析片段可参与混剪，用户仍可在池里取消。
            self._pool_frag_ids = {
                self._frag_id(r) for r in restored if r.get("path")}
            self._pool_paths = {str(r["path"]) for r in restored if r.get("path")}
            self._pool_last_scope = set(self._pool_paths)
        self._finished = bool(finished and restored)
        self._rebuild_list_from_results()
        self._refresh_stats()
        self._update_pool_btn()
        if restored:
            self.progress_label.setText(
                "已从工程恢复 %d 个片段%s" %
                (len(restored), "，可继续混剪" if self._finished else ""))
        if self._finished:
            self.analysisFinished.emit(len(restored))
        return len(restored)

    def clear_results(self, keep_stats=False):
        """清空分析结果（素材全删/新建工程时用）：停线程、清卡片、回到空态。"""
        worker = self._worker
        if worker is not None and worker.isRunning():
            try:
                worker.stop()
            except Exception:
                pass
        self._worker = None
        self._results = []
        self._finished = False
        # 结果清空后缩略图缓存也没用了，一并释放。
        clear_pixmap_cache()
        self._seg_thumbs.clear()
        self._thumb_pending.clear()
        self._pool_paths.clear()
        self._pool_frag_ids.clear()
        self._pool_last_scope = None
        self._pool_folders = []
        self._pool_folder_of = {}
        self._current_folder_id = "__all__"
        self._active_folder_id = ""
        try:
            self._clear_cards()
        except Exception:
            pass
        if hasattr(self, "empty_label"):
            self.empty_label.setVisible(True)
        if hasattr(self, "grid_scroll"):
            self.grid_scroll.setVisible(False)
        self._reset_preview()
        if not keep_stats:
            self._refresh_stats()
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(0)
            self.progress_bar.hide()
        if hasattr(self, "progress_label"):
            self.progress_label.setText("还没有分析结果，从素材库点「分析选中」开始")
        for name in ("pause_btn", "stop_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(False)
                if name == "pause_btn":
                    btn.setText("暂停")
        self._update_pool_btn()

    def _stop_analysis(self):
        if self._worker and self._worker.isRunning():
            # 立即反馈，不依赖 worker 线程退出：不再弹常驻模态框
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.progress_label.setText("正在取消…")
            self._worker.stop()

    # -----------------------------------------------------
    def _on_progress(self, current, total, message):
        if total > 0:
            self.progress_bar.setValue(int(current / total * 100))
            self.progress_label.setText(f"{message}  ({current}/{total})")
        else:
            # 分段等准备阶段：还没有总数，只显示提示文字
            self.progress_bar.setValue(0)
            self.progress_label.setText(message)

    def _on_item_updated(self, item):
        existing = self._find_result_row(item["path"], item["seg_index"])
        if existing is None:
            self._results.append(item)
            fid = self._frag_id(item)
            if (self._pool_last_scope is None
                    or str(item.get("path") or "") in self._pool_last_scope):
                self._pool_frag_ids.add(fid)
            self._add_card(len(self._results) - 1)
            self._relayout_cards()
        else:
            self._results[existing] = item
            self._refresh_list_row(existing)
        self._refresh_stats()

    def _find_result_row(self, path, seg_index):
        for i, r in enumerate(self._results):
            if r["path"] == path and r["seg_index"] == seg_index:
                return i
        return None

    def _on_finished_all(self, results):
        self._results = list(results)
        if self._pool_last_scope is None:
            self._pool_frag_ids = {self._frag_id(r) for r in self._results}
        else:
            self._pool_frag_ids |= {
                self._frag_id(r) for r in self._results
                if str(r.get("path") or "") in self._pool_last_scope}
        self._finished = True
        self._rebuild_list_from_results()
        self._refresh_stats()
        self._update_pool_btn()
        self.progress_label.setText(
            f"分析完成，共 {len(results)} 个片段，可到「混剪参数」开始混剪")
        self.analysisFinished.emit(len(self._results))

    def _on_worker_stopped(self):
        was_stop = self._worker is not None and getattr(self._worker, "_stop", False)
        if was_stop:
            self._results = self._worker._snapshot()
            self._rebuild_list_from_results()
            self.progress_label.setText("已取消，已分析片段保留可用")
        self._refresh_stats()
        self.pause_btn.setText("暂停")
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.progress_bar.hide()

    # -----------------------------------------------------
    # 片段卡片：一个片段一张卡，样式对齐素材库，不显示评分
    # -----------------------------------------------------
    def _thumb_of(self, path):
        """取该素材的缩略图文件（来自素材库探测缓存）。"""
        demo = getattr(self.win, "_demo_clip", None)
        if callable(demo):
            clip = demo(path)
            if clip and clip.get("thumb_path"):
                return clip["thumb_path"]
        return ""

    # ---- 片段级缩略图：后台抽帧，避免同一素材的片段长得一模一样 ----
    def _frag_key(self, item):
        fid = item.get("frag_id")
        if fid:
            return str(fid)
        return "%s|%.3f|%.3f" % (
            item.get("path"),
            float(item.get("seg_start", 0.0)),
            float(item.get("seg_end", 0.0)))

    def _thumb_for(self, item):
        key = self._frag_key(item)
        jpg = self._seg_thumbs.get(key)
        if jpg:
            return jpg
        if key not in self._thumb_pending:
            self._thumb_pending.add(key)
            seek = float(item.get("preview_time") or 0.0)
            if not seek:
                seek = (float(item.get("seg_start", 0.0))
                        + float(item.get("seg_end", 0.0))) / 2.0
            self._ensure_thumb_worker().enqueue(key, item.get("path"), seek)
        return self._thumb_of(item.get("path"))

    def _ensure_thumb_worker(self):
        if self._thumb_worker is None:
            self._thumb_worker = _SegmentThumbWorker(self)
            self._thumb_worker.ready.connect(self._on_seg_thumb)
            self._thumb_worker.start()
        return self._thumb_worker

    def _on_seg_thumb(self, key, jpg):
        self._seg_thumbs[key] = jpg
        self._thumb_pending.discard(key)
        # 片段缩略图映射同样限流：数值是路径字符串，但片段量大时也要控制增长。
        while len(self._seg_thumbs) > _SEG_THUMB_MAX:
            self._seg_thumbs.pop(next(iter(self._seg_thumbs)))
        for row, card in self.cards.items():
            if row < len(self._results) and self._frag_key(self._results[row]) == key:
                card.update()

    def closeEvent(self, event):
        if self._thumb_worker is not None:
            self._thumb_worker.stop()
            self._thumb_worker.wait(2000)
            self._thumb_worker = None
        super().closeEvent(event)

    def _clear_cards(self):
        # 网格里同时放着文件夹卡片和片段卡片，整格清空后字典必须同步清零，
        # 否则会留下已被销毁的控件引用。
        while self.grid_lay.count():
            it = self.grid_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.cards = {}
        self.folder_tiles = {}
        self._selected_row = -1
        self._cols = 0

    def _add_card(self, row):
        card = FragmentCard(row, self._results[row], self._thumb_for)
        card.clickedModifiers.connect(
            lambda row, mods: self._on_card_clicked(row, mods))
        card.menuRequested.connect(self._on_card_menu)
        card.poolChecked.connect(self._on_card_pool_checked)
        card.set_pool_state(self._frag_in_pool(self._results[row]))
        card.drag_ids_provider = self._drag_ids_for_row
        card.set_drag_ids([self._frag_id(self._results[row])])
        self.cards[row] = card
        return card

    def _drag_ids_for_row(self, row):
        """拖动时实时取选中片段；避免拖动只带走最后一张卡。"""
        if not (0 <= row < len(self._results)):
            return []
        selected = [r for r in sorted(self._selected_rows)
                    if 0 <= r < len(self._results)]
        if row in selected and len(selected) > 1:
            return [self._frag_id(self._results[r]) for r in selected]
        return [self._frag_id(self._results[row])]

    def _update_card_drag_ids(self):
        selected = [self._frag_id(self._results[r]) for r in self._selected_rows
                    if 0 <= r < len(self._results)]
        for row, card in self.cards.items():
            if row in self._selected_rows and len(selected) > 1:
                card.set_drag_ids(selected)
            elif 0 <= row < len(self._results):
                card.set_drag_ids([self._frag_id(self._results[row])])

    def _on_card_pool_checked(self, row, checked):
        if 0 <= row < len(self._results):
            self.set_mix_member(self._frag_id(self._results[row]), checked)
            self._refresh_pool_ui()
            self._notify_selectors()

    # ---- 显示顺序 ----
    def _folder_order_key(self):
        """文件夹在左侧栏里的固定顺序：按创建顺序，不按片数。

        文件夹没有片段时也不许沉到底部——它一直置顶、顺序永远不变。
        """
        order = {}
        for i, folder in enumerate(self._pool_folders):
            order[folder["id"]] = i
        return order

    def _rows_in_current_folder_ordered(self):
        """当前文件夹里的片段行，按“刚放进来/刚拖入的排最前”排序。

        排序依据是片段最近一次被归类的先后（``_frag_recency``），
        而不是分析完成的先后；这样拖进文件夹的片段会立刻出现在最上面。
        """
        rows = self._rows_in_current_folder()
        recency = getattr(self, "_frag_recency", None) or {}
        order = []
        for row in rows:
            fid = self._frag_id(self._results[row])
            seq = recency.get(fid, -1)
            # 同一时间归类的保持原有先后；越晚归类越靠前。
            order.append((-int(seq), row))
        order.sort()
        return [row for _seq, row in order]

    def _touch_frag_order(self, frag_ids):
        """记录这些片段刚刚被归类，用于“排到最前”。

        同一批拖入的片段：先拖的那个显示在更上面；整批都在旧片段前面。
        """
        if getattr(self, "_frag_recency", None) is None:
            self._frag_recency = {}
        ids = [str(f) for f in (frag_ids or []) if f]
        base = int(getattr(self, "_frag_seq", 0)) + len(ids) + 1
        for offset, fid in enumerate(ids):
            self._frag_recency[fid] = base - offset
        self._frag_seq = base

    def _rebuild_list_from_results(self, preserve_selection=False):
        selected = set(self._selected_rows) if preserve_selection else set()
        selected_row = self._selected_row if preserve_selection else -1
        anchor_row = self._anchor_row if preserve_selection else -1
        self._clear_cards()
        self._selected_rows = {r for r in selected
                               if 0 <= r < len(self._results)}
        self._selected_row = selected_row if selected_row in self._selected_rows else -1
        self._anchor_row = anchor_row if anchor_row in self._selected_rows else -1
        rows = self._rows_in_current_folder_ordered()
        for row in rows:
            self._add_card(row)
        self._update_card_drag_ids()
        in_folder_view = self._current_folder_id not in ("", "__all__")
        self.empty_label.setText(
            "这个文件夹还没有片段：把片段拖进来，或右键片段选「加入文件夹」"
            if (self._results and in_folder_view)
            else ("还没有分析结果，请先分析喵～(∠・ω< )⌒★"
                  if not self._results else
                  "全部片段都已经归入文件夹了，点左侧文件夹或下面的文件夹卡片查看"))
        # 文件夹卡片本身也是内容：即使「全部素材」里没有片段，网格也要显示。
        self._sync_folder_tiles()
        has_content = bool(rows) or bool(getattr(self, "folder_tiles", None))
        self.empty_label.setVisible(not has_content)
        self.grid_scroll.setVisible(has_content)
        self._update_pool_btn()
        self._relayout_cards()

    def _folder_tiles_for_view(self):
        """当前视图需要显示的文件夹卡片。

        「全部素材」永远显示全部文件夹（按创建顺序，固定在最上面）；
        进入某个具体文件夹后，文件夹卡片收起，只显示里面的片段。
        """
        current = str(getattr(self, "_current_folder_id", "__all__") or "")
        if current not in ("", "__all__"):
            return []
        order = self._folder_order_key()
        return sorted(self._pool_folders,
                      key=lambda f: order.get(f["id"], len(order)))

    def _sync_folder_tiles(self):
        """按需增删文件夹卡片；已有卡片复用，避免闪屏与丢失拖拽状态。"""
        wanted = self._folder_tiles_for_view()
        wanted_ids = [f["id"] for f in wanted]
        for fid in list(self.folder_tiles.keys()):
            if fid not in wanted_ids:
                tile = self.folder_tiles.pop(fid)
                tile.setParent(None)
                tile.deleteLater()
        counts = {}
        for item in self._results:
            fid = self._pool_folder_of.get(self._frag_id(item), "")
            if fid:
                counts[fid] = counts.get(fid, 0) + 1
        for folder in wanted:
            tile = self.folder_tiles.get(folder["id"])
            if tile is None:
                tile = AnalysisFolderTile(
                    folder["id"], folder["name"],
                    counts.get(folder["id"], 0), self.grid_host)
                tile.entered.connect(self._enter_folder)
                tile.fragsDropped.connect(self._on_folder_frags_dropped)
                tile.folderMenuRequested.connect(
                    self._on_folder_tile_context_menu)
                self.folder_tiles[folder["id"]] = tile
            else:
                tile.set_count(counts.get(folder["id"], 0))
            tile.set_name(folder["name"])

    def _folder_rows_count(self, cols):
        """文件夹卡片要占掉多少整行（每个文件夹卡片独占，并且整行留给文件夹）。"""
        tiles = self._folder_tiles_for_view()
        if not tiles:
            return 0
        # 文件夹卡片独占顶部若干整行：不够一整行也补齐，
        # 保证片段一定从文件夹下面开始，不会挤在文件夹右边的空位里。
        return (len(tiles) + cols - 1) // cols

    def layout_plan(self):
        """当前网格的排列计划：[("folder", id, row, col) / ("frag", fid, row, col)]。

        文件夹卡片永远在最上面占整行，片段从文件夹下面那行开始；
        回归测试直接核对这个结果，不用去读 Qt 布局内部状态。
        """
        view_w = self.grid_scroll.viewport().width()
        if view_w <= 60:
            view_w = max(self.width() - 420, 400)
        gap, card_w = 12, 236
        cols = max(1, int((view_w + gap) // (card_w + gap)))
        plan = []
        for i, folder in enumerate(self._folder_tiles_for_view()):
            plan.append(("folder", folder["id"], i // cols, i % cols))
        start = self._folder_rows_count(cols) * cols
        for i, row in enumerate(self._rows_in_current_folder_ordered()):
            pos = start + i
            plan.append(("frag", self._frag_id(self._results[row]),
                         pos // cols, pos % cols))
        return plan

    def _relayout_cards(self):
        if not getattr(self, "folder_tiles", None):
            self._sync_folder_tiles()
        view_w = self.grid_scroll.viewport().width()
        if view_w <= 60:
            # 页面还没显示过，先按整页宽度估个列数
            view_w = max(self.width() - 420, 400)
        gap = 12
        card_w = 236
        cols = max(1, int((view_w + gap) // (card_w + gap)))

        # 列数没变时跳过重排。
        # QGridLayout.addWidget 只是把控件挪到新的 (row, col)，
        # 不清掉旧位置留下的占位；窗口拖动时 resizeEvent 会疯狂触发，
        # 每次都重排容易让布局里积下过期的行列占位，
        # 表现就是「一行有时 2 个有时 3 个」「顺序从左到右乱跳」。
        # 只有列数真的变了才重排，能大幅减少这种抖动。
        if getattr(self, "_cols", None) == cols and \
                getattr(self, "_laid_out_count", None) == len(self.cards):
            return
        self._cols = cols
        self._laid_out_count = len(self.cards)
        # 文件夹卡片固定占顶部整行，片段从文件夹下面那行开始。
        for i, folder in enumerate(self._folder_tiles_for_view()):
            tile = self.folder_tiles.get(folder["id"])
            if tile is not None:
                self.grid_lay.addWidget(tile, i // cols, i % cols)
        start = self._folder_rows_count(cols) * cols
        for i, row in enumerate(sorted(self.cards)):
            pos = start + i
            self.grid_lay.addWidget(self.cards[row], pos // cols, pos % cols)
        for c in range(cols):
            self.grid_lay.setColumnStretch(c, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_cards()

    def _on_card_clicked(self, row, mods=None):
        if row >= len(self._results):
            return
        if mods is None:
            mods = QApplication.keyboardModifiers()
        self._apply_card_selection(row, mods)
        item = self._results[row]
        # 离线回归测试用：_AUTO_PREVIEW 为假时只做选择，不去播放预览。
        if getattr(self, "_auto_preview", True):
            self._locate_preview(item)
            self._update_preview_info(item)

    def _apply_card_selection(self, row, mods):
        if not (0 <= row < len(self._results)):
            return
        visible = sorted(getattr(self, "cards", {}) or {})
        if row not in visible:
            visible = [row]
        anchor = self._anchor_row if self._anchor_row in visible else row
        if mods & Qt.KeyboardModifier.ShiftModifier:
            i, j = sorted((visible.index(anchor), visible.index(row)))
            rows = set(visible[i:j + 1])
            if mods & Qt.KeyboardModifier.ControlModifier:
                self._selected_rows |= rows
            else:
                self._selected_rows = rows
        elif mods & Qt.KeyboardModifier.ControlModifier:
            if row in self._selected_rows:
                self._selected_rows.discard(row)
            else:
                self._selected_rows.add(row)
        else:
            # 已多选后按住某张卡开始拖动，鼠标按下时通常不带 Ctrl/Shift。
            # 这里保留整组选择，避免拖拽时只剩最后一张卡。
            if not (row in self._selected_rows and len(self._selected_rows) > 1):
                self._selected_rows = {row}
        self._anchor_row = row
        self._selected_row = row if row in self._selected_rows else -1
        self._sync_selected_cards()
        self._update_card_drag_ids()
        self._update_pool_btn()

    def _sync_selected_cards(self):
        for r, card in self.cards.items():
            try:
                card.set_selected(r in self._selected_rows)
            except Exception:
                pass

    def _selection_items(self):
        rows = sorted(getattr(self, "_selected_rows", set()) or set())
        return [self._results[r] for r in rows
                if 0 <= r < len(self._results)]

    def _refresh_pool_ui(self):
        for row, card in self.cards.items():
            if 0 <= row < len(self._results):
                try:
                    card.set_pool_state(self._frag_in_pool(self._results[row]))
                except Exception:
                    pass
        self._update_pool_btn()

    def _pool_add_selected(self):
        items = self._selection_items()
        if not items:
            QMessageBox.information(self, "加入混剪池", "请先在片段卡片上选择要加入的片段。")
            return
        for item in items:
            self.set_mix_member(self._frag_id(item), True)
        self._refresh_pool_ui()
        self._notify_selectors()

    def _pool_remove_selected(self):
        items = self._selection_items()
        if not items:
            QMessageBox.information(self, "移出混剪池", "请先在片段卡片上选择要移出的片段。")
            return
        for item in items:
            self.set_mix_member(self._frag_id(item), False)
        self._refresh_pool_ui()
        self._notify_selectors()

    def _pool_new_folder(self):
        # 不论当前停在「全部素材」还是某个文件夹里，新建的都是**同级顶层文件夹**，
        # 不会套进当前文件夹里；建完仍停在原来的视图。
        name, ok = QInputDialog.getText(
            self, "新建文件夹", "文件夹名称（新建与会话中的所有文件夹平级）：")
        if not ok:
            return
        name = str(name or "").strip()
        if not name:
            return
        existing = {f["name"] for f in self._pool_folders}
        if name in existing:
            # 同名直接复用，不再弹模态框：既避免嵌套，也不打断操作。
            self.progress_label.setText(
                "已有同名文件夹「%s」，已直接复用，不会再嵌套建一个。" % name)
            self._refresh_folder_views()
            return
        folder_id = self.create_mix_folder(name)
        self._active_folder_id = folder_id
        # 新建只建壳：不抢占当前视图、不自动进入新文件夹。
        items = self._selection_items()
        if items:
            self.assign_frags_to_folder(
                [self._frag_id(i) for i in items], folder_id)
            self._touch_frag_order([self._frag_id(i) for i in items])
        self._selected_rows = set()
        self._sync_selected_cards()
        # 只刷新文件夹相关的控件（左侧栏、文件夹卡片、状态文字）。
        # 不重建片段卡片：整片重建会让网格闪一下，而新建文件夹本身
        # 不改变已显示片段的内容，最多是选中的片段被移走后要隐藏。
        if self._current_folder_id in ("", "__all__") and items:
            self._rebuild_list_from_results()
        else:
            self._refresh_folder_views()
        self._notify_selectors()

    def _pool_move_selected(self):
        items = self._selection_items()
        if not items:
            QMessageBox.information(self, "移入文件夹", "请先在片段卡片上选择片段。")
            return
        folder_id = str(self._active_folder_id or
                        self.pool_folder_combo.currentData() or "")
        if not folder_id:
            QMessageBox.information(
                self, "移入文件夹",
                "请先在左侧文件夹列表或顶部下拉框里选择目标文件夹。")
            return
        self.assign_frags_to_folder([self._frag_id(i) for i in items], folder_id)
        self._touch_frag_order([self._frag_id(i) for i in items])
        self._active_folder_id = folder_id
        self._set_combo_folder(folder_id)
        # 显式「移入文件夹」才跳到目标文件夹；拖入 / 右键归类保持当前视图。
        self._selected_rows = set()
        self._sync_selected_cards()
        self._current_folder_id = folder_id
        self._rebuild_list_from_results()
        self._notify_selectors()

    def _pool_select_all(self):
        self._selected_rows = set(self._rows_in_current_folder_ordered())
        rows = sorted(self._selected_rows)
        self._anchor_row = rows[0] if rows else -1
        self._selected_row = rows[0] if rows else -1
        self._sync_selected_cards()
        self._update_card_drag_ids()
        self._update_pool_btn()

    def clear_selection(self):
        """取消当前全部选择。

        修用户反馈的坑：新建文件夹会把手上的选择一起带进去，
        之后反而没有入口把选择取消掉。点工具栏「取消选择」或网格空白处都能清空。
        """
        if not self._selected_rows:
            return 0
        count = len(self._selected_rows)
        self._selected_rows = set()
        self._selected_row = -1
        self._anchor_row = -1
        self._sync_selected_cards()
        self._update_card_drag_ids()
        self._update_pool_btn()
        return count

    def _iter_blank_targets(self):
        """能用来“点空白取消选择”的容器：网格宿主、网格视口。"""
        for name in ("grid_scroll", "grid_host"):
            w = getattr(self, name, None)
            if w is not None:
                yield w
                try:
                    vp = w.viewport()
                except Exception:
                    vp = None
                if vp is not None:
                    yield vp

    def install_blank_click(self):
        """给网格空白区域装点击取消选择（只装一次）。"""
        if getattr(self, "_blank_click_installed", False):
            return
        self._blank_click_installed = True
        for w in self._iter_blank_targets():
            try:
                w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                w.installEventFilter(self)
            except Exception:
                pass

    def _blank_area_clicked(self, obj, event):
        """判断这次鼠标左键是否点在“空白处”，是就取消选择并吃掉事件。"""
        if event.type() != QEvent.Type.MouseButtonPress:
            return False
        try:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
        except Exception:
            return False
        # 点在片段卡片 / 文件夹卡片上：那是有内容的点击，交给卡片自己处理。
        for widget in list(getattr(self, "cards", {}).values()) \
                + list(getattr(self, "folder_tiles", {}).values()):
            try:
                if widget.isVisible() and widget.geometry().contains(
                        widget.mapFrom(obj, event.position().toPoint())):
                    return False
            except (RuntimeError, TypeError, AttributeError):
                continue
        return self.clear_selection() > 0

    def _notify_selectors(self):
        rs = getattr(self.win, "refresh_selectors", None)
        if callable(rs):
            try:
                rs()
            except Exception:
                pass

    def _exec_menu(self, menu, pos):
        """弹出右键菜单。

        单独抽成一个方法，离屏回归测试可以替换它来检查菜单项，
        而不需要在无头环境里真的弹出模态菜单（会阻塞）。
        """
        return menu.exec(pos)

    def _refresh_folder_bar(self):
        if not hasattr(self, "folder_list"):
            return
        counts = {}
        unassigned = 0
        for item in self._results:
            fid = self._frag_id(item)
            folder_id = self._pool_folder_of.get(fid, "")
            if folder_id:
                counts[folder_id] = counts.get(folder_id, 0) + 1
            else:
                unassigned += 1

        valid_ids = {f["id"] for f in self._pool_folders}
        current = getattr(self, "_current_folder_id", "__all__")
        if current is None:
            current = "__all__"
        current = str(current)
        if current not in valid_ids and current != "__all__":
            current = "__all__"
            self._current_folder_id = current

        self.folder_list.blockSignals(True)
        # 增量同步：按 id 复用已有行、只增删/改文字，不再 clear() 整表。
        # 整表清空会让列表在那一帧短暂变空，用户看到的就是“闪一下”。
        wanted = [("__all__", "全部素材", sum(
            1 for i in range(len(self._results)) if not self._folder_of_row(i)),
            True, "显示所有已分析片段")]
        order = self._folder_order_key()
        for folder in sorted(self._pool_folders,
                             key=lambda f: order.get(f["id"], len(order))):
            wanted.append((folder["id"], folder["name"],
                           counts.get(folder["id"], 0),
                           bool(folder.get("checked", True)),
                           "勾选启用；取消勾选后整组片段不参与混剪"))
        self._sync_folder_list_items(wanted, current)
        self.folder_list.blockSignals(False)

        # 右侧网格顶部的文件夹卡片：和片段同一个网格，固定第一行。
        self._sync_folder_tiles()
        self._relayout_cards()

        rows = self._rows_in_current_folder()
        name = self._folder_display_name(current)
        if hasattr(self, "folder_path_label"):
            if current in ("", "__all__"):
                # 全部素材只显示未归类片段；括号里给出已分析库里可进入的总数。
                self.folder_path_label.setText(
                    "%s    %d 个片段（另有 %d 个在文件夹里）" % (
                        name, len(rows), len(self._results) - len(rows))
                    if len(rows) != len(self._results)
                    else "%s    %d 个片段" % (name, len(rows)))
            else:
                self.folder_path_label.setText(
                    "%s    %d 个片段" % (name, len(rows)))
        if hasattr(self, "folder_back_btn"):
            self.folder_back_btn.setEnabled(current != "__all__")

    def _sync_folder_list_items(self, wanted, current):
        """把左侧文件夹栏按 ``wanted`` 增量对齐，尽量不重建控件。

        wanted 的每项是 (folder_id, name, count, checked, tooltip)；
        「全部素材」在建栏时已经存在，这里只更新文字，不新增不删除。
        """
        wanted_ids = [w[0] for w in wanted]
        # 1) 删掉不再存在的文件夹行（「全部素材」永远保留）
        for i in range(self.folder_list.count() - 1, -1, -1):
            item = self.folder_list.item(i)
            fid = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if fid != "__all__" and fid not in wanted_ids:
                self.folder_list.takeItem(i)
        # 2) 按目标顺序放置各行；已存在就复用，不存在才新建
        for pos, (fid, name, count, checked, tip) in enumerate(wanted):
            text = ("全部素材    (%d)" % count) if fid == "__all__" \
                else ("%s    (%d)" % (name, count))
            item = self._folder_item_by_id(fid)
            if item is None:
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, fid)
                item.setForeground(QColor(theme.COLORS["text"]))
                if fid != "__all__":
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                                  | Qt.ItemFlag.ItemIsEnabled
                                  | Qt.ItemFlag.ItemIsSelectable)
                self.folder_list.insertItem(pos, item)
            elif self.folder_list.row(item) != pos:
                self.folder_list.takeItem(self.folder_list.row(item))
                self.folder_list.insertItem(pos, item)
            if item.text() != text:
                item.setText(text)
            item.setToolTip(tip)
            if fid != "__all__":
                state = (Qt.CheckState.Checked if checked
                         else Qt.CheckState.Unchecked)
                if item.checkState() != state:
                    item.setCheckState(state)
        # 3) 恢复当前选中行
        target = self._folder_item_by_id(current) or self._folder_item_by_id("__all__")
        if target is not None and self.folder_list.currentItem() is not target:
            self.folder_list.setCurrentItem(target)
        elif target is not None:
            self.folder_list.scrollToItem(target)

    def _folder_item_by_id(self, fid):
        fid = str(fid or "")
        want = "__all__" if fid in ("", "__all__") else fid
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == want:
                return item
        return None


    def _on_folder_toggled(self, folder_id, checked):
        self.set_mix_folder_checked(folder_id, checked)
        self._refresh_pool_ui()
        self._notify_selectors()

    def _on_folder_item_changed(self, item):
        folder_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if folder_id == "__all__":
            return
        checked = item.checkState() == Qt.CheckState.Checked
        frag_ids = [self._frag_id(it) for it in self._results
                    if self._pool_folder_of.get(self._frag_id(it), "")
                    == folder_id]
        if checked:
            self._pool_frag_ids.update(frag_ids)
        else:
            self._pool_frag_ids.difference_update(frag_ids)
        if folder_id:
            self._active_folder_id = folder_id
            self._set_combo_folder(folder_id)
            self.set_mix_folder_checked(folder_id, checked)
        self._refresh_pool_ui()
        self._notify_selectors()

    def _enter_folder(self, folder_id):
        folder_id = str(folder_id or "")
        if folder_id not in {f["id"] for f in self._pool_folders} \
                and folder_id not in ("__all__", ""):
            folder_id = "__all__"
        self._current_folder_id = folder_id
        self._active_folder_id = "" if folder_id == "__all__" else folder_id
        self._set_combo_folder(self._active_folder_id)
        # 切换文件夹后旧选区大多已不可见，清掉避免「看不见却还选中」。
        self._selected_rows = set()
        self._selected_row = -1
        self._anchor_row = -1
        self._rebuild_list_from_results()

    def _on_folder_row_selected(self, current, previous):
        if current is None:
            return
        fid = str(current.data(Qt.ItemDataRole.UserRole) or "")
        current_view = str(self._current_folder_id or "")
        if fid == "__all__":
            self._current_folder_id = "__all__"
            self._active_folder_id = ""
        else:
            self._current_folder_id = fid
            self._active_folder_id = fid
        self._set_combo_folder(self._active_folder_id)
        if fid != current_view:
            self._selected_rows = set()
            self._selected_row = -1
            self._anchor_row = -1
        self._rebuild_list_from_results()

    def _on_folder_frags_dropped(self, folder_id, frag_ids):
        """把片段拖进文件夹 / 文件夹卡片。

        只改归类，不切换当前视图，也不会自动打开目标文件夹。
        """
        self.move_frags_to_folder(frag_ids, folder_id)

    def _move_frags_to_folder(self, frag_ids, folder_id):
        """右键「加入文件夹」等入口复用：归类 → 同步所有计数与当前网格。"""
        return self.move_frags_to_folder(frag_ids, folder_id)

    def move_frags_to_folder(self, frag_ids, folder_id):
        folder_id = str(folder_id or "")
        if folder_id in ("", "__all__"):
            return 0
        moved_ids = [str(f) for f in (frag_ids or []) if f]
        moved = self.assign_frags_to_folder(frag_ids, folder_id)
        if moved:
            # 刚拖进去的片段排在文件夹最上面，而不是散在原来分析顺序里。
            self._touch_frag_order(moved_ids)
            self._rebuild_list_from_results(preserve_selection=True)
        self._refresh_pool_ui()
        self._notify_selectors()
        return moved

    def _on_folder_context_menu(self, pos):
        item = self.folder_list.itemAt(pos)
        if item is None:
            return
        folder_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if folder_id in ("__all__", ""):
            return
        self._show_folder_menu(folder_id, self.folder_list.mapToGlobal(pos))

    def _on_folder_tile_context_menu(self, folder_id, global_pos):
        if str(folder_id or "") in ("", "__all__"):
            return
        self._show_folder_menu(str(folder_id), global_pos)

    def _show_folder_menu(self, folder_id, global_pos):
        folder = self._pool_folder(folder_id)
        if folder is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:%s;border:1px solid %s;border-radius:6px;padding:4px;}"
            "QMenu::item{padding:6px 18px;border-radius:4px;color:%s;}"
            "QMenu::item:selected{background:%s;color:#101113;}"
            % (theme.COLORS["panel"], theme.COLORS["border"],
               theme.COLORS["text"], theme.COLORS["accent"]))
        rename_act = menu.addAction("重命名文件夹")
        delete_act = menu.addAction("删除文件夹")
        delete_act.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_TrashIcon))
        chosen = self._exec_menu(menu, global_pos)
        if chosen == rename_act:
            self._rename_folder_dialog(folder_id)
        elif chosen == delete_act:
            self._delete_folder_confirm(folder_id)

    def _rename_folder_dialog(self, folder_id):
        folder = self._pool_folder(folder_id)
        if folder is None:
            return
        name, ok = QInputDialog.getText(
            self, "重命名文件夹", "新名称：", text=folder["name"])
        name = str(name or "").strip()
        if not ok or not name:
            return
        folder["name"] = name
        self._refresh_folder_bar()
        self._refresh_pool_ui()
        self._notify_selectors()

    def _delete_folder_confirm(self, folder_id):
        folder = self._pool_folder(folder_id)
        if folder is None:
            return
        count = sum(1 for v in self._pool_folder_of.values() if v == folder_id)
        ok = QMessageBox.question(
            self, "删除文件夹",
            "确定删除文件夹「%s」（%d 个片段）？\n"
            "片段会回到已分析库，不会删除分析结果。" % (folder["name"], count))
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._pool_folders = [f for f in self._pool_folders
                              if f["id"] != folder_id]
        for fid, f in list(self._pool_folder_of.items()):
            if f == folder_id:
                self._pool_folder_of.pop(fid, None)
        if self._current_folder_id == folder_id:
            self._current_folder_id = "__all__"
        if self._active_folder_id == folder_id:
            self._active_folder_id = ""
        self._rebuild_list_from_results(preserve_selection=True)
        self._notify_selectors()

    def _on_pool_folder_combo_changed(self, _index):
        self._active_folder_id = str(self.pool_folder_combo.currentData() or "")

    def _set_combo_folder(self, folder_id):
        if not hasattr(self, "pool_folder_combo"):
            return
        idx = self.pool_folder_combo.findData(str(folder_id or ""))
        if idx >= 0:
            self.pool_folder_combo.blockSignals(True)
            self.pool_folder_combo.setCurrentIndex(idx)
            self.pool_folder_combo.blockSignals(False)

    def _select_folder_rows(self, folder_id):
        rows = set()
        for i, item in enumerate(self._results):
            fid = self._frag_id(item)
            if str(self._pool_folder_of.get(fid, "")) == str(folder_id or ""):
                rows.add(i)
        self._selected_rows = rows
        self._selected_row = min(rows) if rows else -1
        self._anchor_row = self._selected_row
        for row, card in self.cards.items():
            card.set_selected(row in rows)
        self._update_card_drag_ids()
        self._update_pool_btn()

    def _update_pool_btn(self):
        total = len(self._results)
        n = len(self.pool_fragment_ids())
        if hasattr(self, "pool_status_label"):
            sel = len(self._selection_items())
            text = ("混剪池  %d / %d 片段" % (n, total)
                    if n > 0 else "混剪池 -/-")
            if sel:
                text += "  ·  已选 %d" % sel
            # 把「自动判废」与「手动保留」说清楚：
            # 判废只是扣分标记，是否进池由 included 决定，两者不能混为一谈。
            scope = set(self._get_paths() or [])
            results = [r for r in self._results
                       if not scope or str(r.get("path") or "") in scope]
            auto_bad = sum(1 for r in results
                           if r.get("is_bad") and not r.get("manual_included"))
            kept_bad = sum(1 for r in results
                           if r.get("is_bad") and r.get("manual_included")
                           and r.get("included", True))
            if auto_bad:
                text += "  ·  自动判废 %d" % auto_bad
            if kept_bad:
                text += "  ·  手动保留 %d" % kept_bad
            self.pool_status_label.setText(text)
        if hasattr(self, "pool_toolbar"):
            self.pool_toolbar.setVisible(True)
        if hasattr(self, "pool_add_btn"):
            selected = len(self._selection_items())
            self.pool_add_btn.setEnabled(bool(selected))
            self.pool_remove_btn.setEnabled(bool(selected))
            self.pool_new_btn.setEnabled(True)
            self.pool_move_btn.setEnabled(bool(selected and self._pool_folders))
            self.pool_all_btn.setEnabled(bool(total))
            if hasattr(self, "pool_clear_btn"):
                self.pool_clear_btn.setEnabled(bool(selected))
        if hasattr(self, "pool_folder_combo"):
            current = self._active_folder_id or str(
                self.pool_folder_combo.currentData() or "")
            self.pool_folder_combo.blockSignals(True)
            self.pool_folder_combo.clear()
            self.pool_folder_combo.addItem("选择文件夹", "")
            for folder in self._pool_folders:
                self.pool_folder_combo.addItem(folder["name"], folder["id"])
            idx = self.pool_folder_combo.findData(current)
            self.pool_folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.pool_folder_combo.blockSignals(False)
        self._refresh_folder_bar()

    def _manage_mix_pool(self):
        # 兼容旧调用：不再弹独立窗口，直接聚焦页面上的混剪池工具条。
        self.pool_toolbar.setVisible(True)


    def _reset_preview(self):
        if self._preview_player is not None:
            self._preview_player.stop()
            self._preview_player.setSource(QUrl())
        self._preview_source = ""
        self._pending_pos = 0
        self.preview_placeholder.show()
        self.preview_info.setText("")

    def _locate_preview(self, item):
        path = item["path"]
        pos_ms = int(item.get("seg_start", 0.0) * 1000)
        self._pending_pos = pos_ms
        self.preview_placeholder.hide()
        # 预览代理：有缓存就播代理（时间轴一致），没有就用原片并后台生成
        src = path
        try:
            from mixcut import preview_proxy
            src = preview_proxy.cached_proxy(path) or path
            if src == path:
                preview_proxy.request(path)
        except Exception:
            pass
        if self._preview_source != src:
            self._preview_source = src
            self._preview_player.setSource(QUrl.fromLocalFile(src))
        else:
            self._preview_player.setPosition(pos_ms)
            self._preview_player.pause()

    def _on_preview_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._preview_player.setPosition(self._pending_pos)
            self._preview_player.pause()

    def _toggle_play(self):
        p = self._preview_player
        if p.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            p.pause()
        else:
            p.play()

    def _update_preview_info(self, item):
        state = "废片段" if item.get("is_bad") else "可用"
        include = "加入" if item.get("included", True) else "不加入"
        pool_state = "池内" if self._frag_in_pool(item) else "未入池"
        self.preview_info.setText(
            f"{item['name']} · 片段{item.get('seg_index', 0) + 1}\n"
            f"原视频时间：{item.get('seg_start', 0.0):.1f}-"
            f"{item.get('seg_end', 0.0):.1f} 秒　状态：{state}　"
            f"选择：{include}　混剪池：{pool_state}\n"
            f"{item.get('bad_reason', '')}")

    # -----------------------------------------------------
    def _toggle_preview_fullscreen(self):
        # 全屏的是预览容器本身：视频不搬家，退出后一定还在原位。
        # 主窗口有统一实现（记住原布局位置），优先交给它。
        win = self.window()
        fn = getattr(win, "_toggle_preview_fullscreen_for", None)
        if callable(fn):
            fn(self.preview_host)
            return
        if self.preview_host.isFullScreen():
            self._exit_preview_fullscreen()
            return
        parent = self.preview_host.parentWidget()
        lay = getattr(parent, "lay", None) if parent is not None else None
        if lay is None and parent is not None:
            lay = parent.layout()
        self._fs_home = (parent, lay,
                         lay.indexOf(self.preview_host) if lay is not None else -1)
        self.preview_host.showFullScreen()
        self.preview_host.setFocus()
        self.set_fullscreen_state(True)

    def _exit_preview_fullscreen(self):
        win = self.window()
        fn = getattr(win, "_exit_preview_fullscreen", None)
        if callable(fn) and fn():
            return
        if not self.preview_host.isFullScreen():
            return
        self.preview_host.showNormal()
        parent, lay, idx = getattr(self, "_fs_home", (None, None, -1))
        if parent is not None and lay is not None:
            if self.preview_host.parentWidget() is not parent:
                self.preview_host.setParent(parent)
            if lay.indexOf(self.preview_host) < 0:
                lay.insertWidget(max(0, idx), self.preview_host)
        self.preview_host.show()
        self.set_fullscreen_state(False)
        self._fs_home = (None, None, -1)

    def set_fullscreen_state(self, full):
        self.fs_btn.setText("退出全屏" if full else "全屏")
        if getattr(self, "fs_overlay", None) is not None:
            self.fs_overlay.setText("⤡" if full else "⤢")
            self.fs_overlay.setToolTip("退出全屏（P / Esc）" if full else "全屏（P）")

    def _place_fs_overlay(self):
        btn = getattr(self, "fs_overlay", None)
        if btn is None:
            return
        m = 10
        btn.move(max(0, self.preview_host.width() - btn.width() - m),
                 max(0, self.preview_host.height() - btn.height() - m))
        btn.raise_()

    # -----------------------------------------------------
    def _on_card_menu(self, row, pos):
        if row >= len(self._results):
            return
        item = self._results[row]

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:%s;border:1px solid %s;border-radius:6px;padding:4px;}"
            "QMenu::item{padding:6px 18px;border-radius:4px;color:%s;}"
            "QMenu::item:selected{background:%s;color:#101113;}"
            % (theme.COLORS["panel"], theme.COLORS["border"],
               theme.COLORS["text"], theme.COLORS["accent"]))

        action = (menu.addAction("✕  不加入这个片段")
                  if item.get("included", True)
                  else menu.addAction("✓  加入这个片段"))

        # 「为什么」入口：把这一段的判废算术摊开给用户看。
        # 放在最前面，因为它是最常被问的问题（「这段明明挺好，凭什么废了」）。
        why_act = menu.addAction("🔍  为什么这样判定")
        menu.addSeparator()

        # 废片被手动勾回时，把当前状态说清楚，避免用户困惑
        # 「明明标了废，怎么还进混剪了」。
        if item.get("is_bad") and item.get("manual_included") \
                and item.get("included", True):
            status_act = menu.addAction("⚠  当前：手动保留的废片段")
            status_act.setEnabled(False)
            menu.addSeparator()

        # 右键操作的作用对象在这里**一次性快照**：
        # 已经多选 → 作用于整组；只点了这一张 → 只作用于它。
        if row in self._selected_rows and len(self._selected_rows) > 1:
            target_rows = sorted(r for r in self._selected_rows
                                 if 0 <= r < len(self._results))
        else:
            target_rows = [row]
        frags = [self._frag_id(self._results[r]) for r in target_rows]
        frags = [f for f in frags if f]
        pool = menu.addAction("加入混剪池")
        unpool = menu.addAction("移出混剪池")

        # 右键直接归类：多选时对整组生效。
        folder_menu = menu.addMenu("加入文件夹")
        folder_actions = {}
        if self._pool_folders:
            for folder in self._pool_folders:
                act = folder_menu.addAction(folder["name"])
                folder_actions[act] = folder["id"]
        else:
            empty_act = folder_menu.addAction("还没有文件夹")
            empty_act.setEnabled(False)
        folder_menu.addSeparator()
        new_folder_act = folder_menu.addAction("新建文件夹并加入…")

        chosen = self._exec_menu(menu, pos)
        if chosen == why_act:
            self._show_verdict_detail(row)
            return
        if chosen in folder_actions:
            fid = folder_actions[chosen]
            self._move_frags_to_folder(list(set(frags)), fid)
            return
        if chosen == new_folder_act:
            name, ok = QInputDialog.getText(
                self, "新建文件夹", "文件夹名称：")
            name = str(name or "").strip()
            if ok and name:
                fid = self.create_mix_folder(name)
                self._move_frags_to_folder(list(set(frags)), fid)
            return
        if chosen == pool:
            for fid in set(frags):
                self.set_mix_member(fid, True)
            self._refresh_pool_ui()
            self._notify_selectors()
            return
        if chosen == unpool:
            for fid in set(frags):
                self.set_mix_member(fid, False)
            self._refresh_pool_ui()
            self._notify_selectors()
            return
        if chosen != action:
            return
        item["included"] = not item.get("included", True)
        item["manual_included"] = True
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.set_manual_included(
                item["path"], item["seg_index"], item["included"])
        self._refresh_list_row(row)
        if self._selected_row == row:
            self._on_card_clicked(row)
        rs = getattr(self.win, "refresh_selectors", None)
        if callable(rs):
            rs()

    def _show_verdict_detail(self, row):
        """弹出这段片段的判废明细（右键「为什么这样判定」）。"""
        if not (0 <= row < len(self._results)):
            return
        from mixcut.ui.demo.verdict_detail import VerdictDetailDialog
        from mixcut.verdict_advice import suggest

        cfg = getattr(self, "verdict_config", None)
        # 建议基于**整批结果**算：单看一段无法判断「改了会影响多少」，
        # 而用户真正想知道的是「这么改值不值」。
        advices, summary = ([], "")
        if cfg is not None:
            try:
                advices, summary = suggest(self._results, cfg)
            except Exception:
                advices, summary = [], ""

        dlg = VerdictDetailDialog(self._results[row], cfg, self,
                                  advices=advices)
        if summary:
            dlg.setWindowTitle("判废明细 · " + summary)
        if dlg.exec() == QDialog.Accepted:
            new_cfg = dlg.applied_config()
            if new_cfg is not None:
                self.verdict_config = new_cfg
                # ⚠ 关键：必须同时更新 worker 的配置。
                # 真正做判定的是 worker._assemble → worker._judge_frame，
                # 它读的是 **worker.verdict_config**。只改页面上的这一份，
                # 重新判定会用旧配置跑一遍，结论当然不变——用户看到的就是
                # 「点了应用但毫无反应」。见台账第二十六阶段。
                worker = getattr(self, "_worker", None)
                if worker is not None:
                    worker.verdict_config = new_cfg
                self.win.controller.log(
                    "分析中心 · 已应用判废建议，正在按新规则重新判定（无需重新分析）")
                self._reapply_verdict()

    def _reapply_verdict(self):
        """按当前判废规则重新聚合已有结果，**不重新解码视频**。

        判废规则只参与「实测 vs 阈值」的比较，而实测值早就在 cv_cache 里，
        所以改规则是纯计算的事。相比整轮重跑（要重新调 ffmpeg 解码），
        这里快几个数量级。

        ⚠ 2026-11-20 第二十五阶段修正
        -----------------------------
        上一版直接读 ``self._roster`` / ``self._items``，但这两个属性在
        **AnalysisWorker** 上，``AnalyzePage`` 自己没有——于是第一行就
        ``AttributeError``，函数直接中断，后面的卡片刷新和统计刷新
        **一次都没执行过**。

        用户看到的现象就是：在「可以试试」里点了应用，
        日志说「正在按新规则重新判定」，但卡片不变、右上角「废片段」
        条数也不动。

        现在改为从 worker 取数据，并且不再让异常静默吞掉：
        每一步失败都写日志，至少让问题可见。

        Returns:
            结论发生变化的片段数；无法执行时返回 -1。
        """
        worker = getattr(self, "_worker", None)
        if worker is None:
            self.win.controller.log(
                "分析中心 · 重新判定失败：后台分析器不存在")
            return -1
        # 保险：确保 worker 用的是页面当前这份配置。
        # 调用方可能只更新了一边（历史上就出过这个 bug），
        # 这里统一兜一次，避免「配置对了但判定用旧值」。
        # **必须在下面的提前 return 之前做**——否则没有可重算的片段时
        # 配置就同步不过去，下次分析还会拿着旧配置跑。
        page_cfg = getattr(self, "verdict_config", None)
        if page_cfg is not None and getattr(worker, "verdict_config", None) \
                is not page_cfg:
            worker.verdict_config = page_cfg

        roster = getattr(worker, "_roster", None) or []
        items = getattr(worker, "_items", None) or {}
        if not roster or not items:
            # 还没分析过任何片段：没有可重新判定的东西，不算错误
            return 0

        entries = [e for e in roster if e["key"] in items]
        if not entries:
            return 0

        changed = 0
        failed = 0
        for entry in entries:
            try:
                cv_frames = worker._required_frames(entry)
                item = worker._assemble(entry, cv_frames)
            except Exception:
                failed += 1
                continue
            before = items.get(entry["key"]) or {}
            if bool(before.get("is_bad")) != bool(item.get("is_bad")):
                changed += 1
            items[entry["key"]] = item

        if failed:
            self.win.controller.log(
                "分析中心 · 重新判定：%d 段跳过（聚合失败）" % failed)

        # 把 worker 的结果同步回页面
        self._results = [items[e["key"]] for e in roster
                         if e["key"] in items]
        # 逐行刷新已有卡片（不重建，避免丢选中态和滚动位置）
        for row in range(len(self._results)):
            try:
                self._refresh_list_row(row)
            except Exception as exc:
                self.win.controller.log(
                    "分析中心 · 刷新第 %d 行失败：%s" % (row + 1, exc))
        for fn_name in ("_refresh_stats", "_refresh_pool_ui"):
            fn = getattr(self, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception as exc:
                    self.win.controller.log(
                        "分析中心 · %s 失败：%s" % (fn_name, exc))
        rs = getattr(self.win, "refresh_selectors", None)
        if callable(rs):
            try:
                rs()
            except Exception:
                pass
        n_bad = sum(1 for r in self._results if r.get("is_bad"))
        self.win.controller.log(
            "分析中心 · 重新判定完成：%d 段结论有变化，当前废片段 %d 段"
            "（未重新解码素材）" % (changed, n_bad))
        return changed

    def _refresh_list_row(self, row):
        card = self.cards.get(row)
        if card is not None and row < len(self._results):
            card.set_item(self._results[row])
            card.set_pool_state(self._frag_in_pool(self._results[row]))

    def _on_error(self, msg):
        self.progress_label.setText(f"错误：{msg}")
        self._finished = False
        self.pause_btn.setText("暂停")
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
