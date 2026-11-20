"""
MixCut · 后台线程

把耗时的素材分析、混剪装配与剪映工程生成放到 QThread，
界面保持响应并可取消。

约定：
  · 任何结局（成功 / 失败 / 取消）都会经 QThread 的 finished 信号回到界面，
    界面据此恢复按钮，不会出现“卡在运行中”的状态；
  · 线程自己持有 worker 引用，避免界面对象先于线程释放；
  · 每次取片、每个工程之间都会检查取消请求。
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
import traceback

from PySide6.QtCore import QThread, Signal

from . import analysis as analysis_core
from . import analysis_schema
from . import mixbuilder as mb
from . import musicstudio as ms
from . import transitions as tr
from . import project_builder as pb
from . import filter_engine as fe
from . import ffmpeg_tools as ft
from . import gpu_engine as ge
from . import audio_dedup as adedup
from . import sentence_cut as scut
from . import openclip_embed as oce
from pathlib import Path
from .analysis_cache import DEFAULT_SOURCE, LEGACY_SOURCE


class BaseWorker(QThread):
    progress = Signal(int, str)
    failed = Signal(str)
    # 真实暂停状态：(是否处于暂停, 提示文字)
    paused_changed = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 暂停闸门：检测/装配的检查点会在这里等待，直到继续或取消
        self._pause_event = threading.Event()
        self._pause_event.set()     # set = 继续运行
        self._paused = False

    def _emit(self, idx, total, message):
        pct = int(idx / max(1, total) * 100)
        self.progress.emit(pct, message)

    def _cancel(self):
        return self.isInterruptionRequested()

    def shutdown(self):
        self.requestInterruption()
        # 取消必须能打断暂停：放行闸门，让工作线程走到取消检查点
        self._pause_event.set()

    # ---- 暂停 / 继续（真实闸门，不是界面假状态）----
    def pause(self):
        if self.isInterruptionRequested():
            return False
        self._paused = True
        self._pause_event.clear()
        self.paused_changed.emit(True, "已暂停")
        return True

    def resume(self):
        if not self._paused:
            return False
        self._paused = False
        self._pause_event.set()
        self.paused_changed.emit(False, "继续运行")
        return True

    def is_paused(self):
        return self._paused

    def _checkpoint(self):
        """检测/装配过程中的检查点：先看取消，再在暂停时等待。

        返回 True 表示应当中断（已取消）。
        """
        if self.isInterruptionRequested():
            return True
        if not self._pause_event.is_set():
            # 等待期间每 50ms 检查一次取消，保证暂停中也能立刻取消
            while not self._pause_event.wait(0.05):
                if self.isInterruptionRequested():
                    return True
        return self.isInterruptionRequested()

    def run(self):
        # 子类只需要实现 _work()；这里统一兜底，保证异常不会吞掉 finished。
        try:
            self._work()
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            self._paused = False
            self._pause_event.set()
            try:
                self._cleanup()
            except Exception:
                pass

    def _work(self):
        raise NotImplementedError

    def _cleanup(self):
        """子类的收尾钩子（可选）。"""


class MixBuildWorker(BaseWorker):
    """智能混剪：装配时间轴 → 配乐/闪避 → 转场 → 生成工程，一条龙。"""
    built = Signal(dict)
    # 逐素材分析进度：(路径, 状态, 详情)
    # status: analyzing / done / cached / error
    source_analyzed = Signal(str, str, dict)

    def __init__(self, paths, canvas, detect_options, mix_options,
                 music_settings, transition_options, analysis_cache=None,
                 filter_config=None, fragment_pool=None):
        super().__init__()
        self.paths = list(paths)
        self.canvas = canvas
        self.detect_options = dict(detect_options or {})
        self.mix_options = dict(mix_options or {})
        self.music_settings = dict(music_settings or {})
        self.transition_options = dict(transition_options or {})
        self.analysis_cache = analysis_cache
        self.filter_config = dict(filter_config) if filter_config else None
        # 分析页确认过的片段池：非空时混剪直接使用它，不再自行分析素材。
        self.fragment_pool = [dict(f) for f in (fragment_pool or [])] or None
        # 分析过程中的逐素材记录：path -> status
        self._source_status = {}

    def _emit_source(self, path, status, detail=None):
        self._source_status[str(path)] = status
        self.source_analyzed.emit(str(path), status, detail or {})

    # -------------------------------------------------
    # 主流程
    # -------------------------------------------------

    def _cancel_or_pause(self):
        """传给检测/装配循环的回调：已取消返回 True，暂停时就地等待。

        暂停期间仍然每 50ms 检查一次取消，保证“暂停中取消”能立刻生效。
        """
        if self.isInterruptionRequested():
            return True
        if not self._pause_event.is_set():
            while not self._pause_event.wait(0.05):
                if self.isInterruptionRequested():
                    return True
        return self.isInterruptionRequested()

    def _work(self):
        project_count = max(
            1, int(self.mix_options.get("project_count", 1))
        )
        beats = None
        downbeats = None
        bgm_info = None
        music_on = self.music_settings.get("enabled")
        if isinstance(music_on, str):
            music_on = music_on.strip().lower() in ("1", "true", "yes", "on")
        bgm_path = str(self.music_settings.get("path") or "").strip()
        # 没有勾选/没有有效 BGM 文件时不挂音频轨，避免生成一条离线音频。
        if not music_on or not bgm_path or not Path(bgm_path).is_file():
            music_on = False
            bgm_path = ""

        if music_on and bgm_path:
            self.progress.emit(2, "分析 BGM 节拍…")
            bgm_info = ms.analyze_bgm(bgm_path)
            if self._cancel():
                return
            if self.mix_options.get("beat_sync"):
                beats = bgm_info["beats"]
                downbeats = bgm_info.get("downbeats") or []
                if not beats:
                    self.progress.emit(
                        3, "这首音乐没有检测到鼓点，本次按普通时长切分。"
                    )
                elif not downbeats:
                    self.progress.emit(
                        3, "未能可靠识别强拍，按普通拍点卡点。"
                    )

        # 片段级唯一来源：有分析页片段池就直接用，绝不自行重建另一套区间。
        if self.fragment_pool:
            stubs = self._pool_stubs(self.fragment_pool)
            stubs = self._gpu_enrich(stubs)
            if self._cancel():
                return
            fragment_pool = self._apply_pool_enrich(self.fragment_pool, stubs)
            fragment_pool = self._openclip_enrich_pool(fragment_pool)
            if self._cancel():
                return
            analysis = None
            excluded = sum(
                1 for f in self.fragment_pool if not f.get("included", True))
            self.progress.emit(
                6,
                f"使用分析页片段：{len(fragment_pool)} 段可用"
                + (f"，已按人工标记排除 {excluded} 段" if excluded else ""),
            )
        else:
            fragment_pool = None
            analysis = self._analysis()
            if self._cancel():
                return
            analysis = self._gpu_enrich(analysis)
            if self._cancel():
                return

        stamp = int(time.time() * 1000)
        base_name = f"MixCut_Mix_{stamp}"
        results = []

        # 每个工程占用 [8, 95] 内的一段进度
        span_start, span_end = 8, 95
        span = (span_end - span_start) / project_count

        for i in range(project_count):
            if self._cancel():
                return
            p_start = span_start + i * span
            p_end = span_start + (i + 1) * span

            def scaler(idx, total, msg, ps=p_start, pe=p_end, n=i):
                frac = idx / max(1, total)
                self.progress.emit(
                    int(ps + frac * (pe - ps)),
                    f"[{n + 1}/{project_count}] {msg}",
                )

            self.progress.emit(
                int(p_start),
                f"[{i + 1}/{project_count}] 装配混剪时间线…",
            )
            # 多工程时按版本策略轮换（均衡 / 快节奏 / 多样性 / 长短交替 / 音乐优先），
            # 不同工程不只是换个随机种子，算法倾向也不同。
            # 用户在界面里选了固定版本时（_rotate_versions=False），所有工程用同一套倾向。
            rotate = bool(self.mix_options.get("_rotate_versions", True))
            if rotate:
                version_opts, style_key = mb.resolve_version_options(
                    self.mix_options, i, project_count)
            else:
                fixed = str(self.mix_options.get("version_style") or "balanced")
                version_opts, style_key = mb.resolve_version_options(
                    self.mix_options, 0, 1)
                version_opts["version_style"] = fixed
            mix_opts = dict(version_opts)
            mix_opts["beats"] = beats
            mix_opts["downbeats"] = downbeats
            style = mb.VERSION_STYLES[style_key]
            mix_opts["prefer_strong_beat"] = bool(version_opts.get(
                "prefer_strong_beat", style.get("prefer_strong_beat", False)))
            self.progress.emit(
                int(p_start),
                f"[{i + 1}/{project_count}] 版本：{style['label']}"
                f"（节奏 {mix_opts.get('rhythm')}"
                f" / 重复控制 {mix_opts.get('diversity')}）",
            )
            # 每个工程一个独立的随机种子：批量生成的草稿排列各不相同。
            seed = ((stamp + i * 7919 + style["seed_offset"]) & 0x7FFFFFFF)
            project = mb.build_mix_timeline(
                self.paths, self.canvas,
                detect_options=self.detect_options,
                mix_options=mix_opts,
                progress_cb=scaler,
                cancel_check=self._cancel_or_pause,
                analysis=analysis,
                seed=seed,
                fragment_pool=fragment_pool,
            )
            if self._cancel():
                return
            if not project["clips"]:
                raise RuntimeError("混剪没有生成任何片段，已停止。")

            if music_on and bgm_path:
                ms.attach_music(
                    project, bgm_path,
                    do_ducking=bool(self.music_settings.get("ducking")),
                    base_volume=float(self.music_settings.get("base_volume", 1.0)),
                    duck_volume=float(self.music_settings.get("duck_volume", 0.35)),
                    energy_threshold=float(self.music_settings.get("energy_threshold", 0.08)),
                    bgm_info=bgm_info,
                )

            if self.transition_options.get("enabled"):
                tr.apply_transition_rules(project, self.transition_options)

            # ---- 成片自检（第二十二阶段）----
            # 放在「最终时间线成形之后（含配乐/转场）、写草稿之前」：
            # 此时 project 就是即将落盘的那一份，检查的是真结果。
            # error 级问题会抛 RuntimeError 阻止落盘（见 _run_delivery_check），
            # warn 只警告不阻断。
            self._run_delivery_check(project, i + 1, project_count)

            name_i = (
                base_name if project_count == 1
                else f"{base_name}_{i + 1}"
            )
            self.progress.emit(
                int(p_end) - 1,
                f"[{i + 1}/{project_count}] 生成剪映工程…",
            )
            use_custom = bool(
                self.music_settings.get("draft_dir_custom", False)
            )
            target_dir = (
                self.music_settings.get("draft_dir", "")
                if use_custom
                else self.music_settings.get("output", "")
            )
            result = pb.build_draft(
                project,
                target_dir,
                draft_name=name_i,
                force_target=use_custom,
            )
            results.append(result)
            self.progress.emit(
                int(p_end),
                f"[{i + 1}/{project_count}] 完成：{name_i}"
                f"（{result['clip_count']} 片段 / {result.get('duration', 0):.2f} 秒）",
            )

        if not results:
            raise RuntimeError("没有生成任何剪映工程。")
        if self._cancel():
            return

        # 汇总：保留首个结果字段，另附全部工程列表
        summary = dict(results[0])
        summary["results"] = results
        summary["project_count"] = len(results)
        summary["total_clip_count"] = sum(
            r["clip_count"] for r in results
        )
        summary["total_duration"] = round(
            sum(float(r.get("duration") or 0.0) for r in results), 3
        )
        summary["target_duration"] = float(
            self.mix_options.get("target_duration", 0.0) or 0.0
        )
        summary["beat_sync"] = bool(beats)
        # 成片自检结果随 summary 带出（第二十一阶段 21.7）
        reports = getattr(self, "delivery_reports", None) or []
        if reports:
            summary["delivery_reports"] = reports
            agg = {ERROR: 0 for ERROR in ("error", "warn", "info")}
            for rep in reports:
                for k, v in (rep.get("summary") or {}).items():
                    if k in agg:
                        agg[k] += v
            summary["delivery_summary"] = agg
            summary["delivery_ok"] = (agg["error"] == 0 and agg["warn"] == 0)
        self.progress.emit(100, f"批量生成完成，共 {len(results)} 个工程")
        self.built.emit(summary)

    # -------------------------------------------------
    # 成片自检（第二十一阶段 21.7）
    # -------------------------------------------------
    def _run_delivery_check(self, project, index, total):
        """对刚生成的时间线做一次自检，返回是否允许继续生成草稿。

        分级处理（2026-11-20 第二十二阶段）
        ----------------------------------
        · **error**（会让剪映打不开/黑帧/时间线错乱的）：**阻止落盘**，
          直接抛 RuntimeError 让任务失败，并由上层把原因告诉用户。
          这样不会把一份注定有问题的草稿写进剪映目录。
        · **warn / info**（观感问题：重复、节奏、来源集中）：**只警告**，
          照常生成草稿。用户可能就想要这个效果，不能替他做决定。

        自检自身出错（代码 bug）时**不阻断**：记一条日志后放行，
        已经算好的混剪结果不应该因为自检崩溃而作废。

        Args:
            project: 时间线字典
            index: 第几个工程（1 基）
            total: 工程总数

        Returns:
            True = 允许继续生成草稿

        Raises:
            RuntimeError: 存在 error 级问题时
        """
        try:
            from . import delivery_check as dc
            target = float(self.mix_options.get("target_duration", 0.0) or 0.0)
            tolerance = float(
                self.mix_options.get("duration_tolerance", 2.0) or 2.0)
            report = dc.check_timeline(
                project,
                target_duration=target if target > 0 else None,
                tolerance=tolerance,
            )
        except Exception:
            self.progress.emit(
                0, "成片自检未能执行（不影响草稿生成）：%s"
                % traceback.format_exc().splitlines()[-1])
            return True

        if not hasattr(self, "delivery_reports"):
            self.delivery_reports = []
        self.delivery_reports.append(report)

        summary = report.get("summary") or {}
        errors = summary.get("error", 0)
        warns = summary.get("warn", 0)
        prefix = "[%d/%d] " % (index, total) if total > 1 else ""

        if errors == 0 and warns == 0:
            self.progress.emit(0, "%s成片自检通过（无异常）" % prefix)
            return True

        level_text = ("%d 个错误 / %d 个提醒" % (errors, warns)
                      if errors else "%d 个提醒" % warns)
        self.progress.emit(0, "%s成片自检：%s" % (prefix, level_text))

        # 输出前 5 条，避免日志被刷屏
        for it in (report.get("issues") or [])[:5]:
            self.progress.emit(
                0, "  · [%s] %s" % (it.get("level", ""), it.get("title", "")))
        fixes = dc.suggest_fixes(report)
        if fixes:
            for f in fixes[:3]:
                self.progress.emit(0, "  → 建议：%s" % f.get("action", ""))

        if errors:
            # 只列 error 级的，避免用户被 warn 淹没
            err_titles = [it.get("title", "") for it in
                          (report.get("issues") or [])
                          if it.get("level") == dc.ERROR][:3]
            raise RuntimeError(
                "成片自检未通过，已中止生成草稿（避免写出有问题的工程）：%s"
                % "；".join(err_titles))

        self.progress.emit(
            0, "%s这些问题只影响观感，草稿照常生成" % prefix)
        return True

    # -------------------------------------------------
    # 素材分析（带缓存）
    # -------------------------------------------------

    def _openclip_enrich_pool(self, pool):
        """为分析页候选片段生成 OpenCLIP 向量，供混剪评分做语义去重。"""
        if not pool or not self.mix_options.get("openclip_enabled", True):
            return pool
        included = [f for f in pool if f.get("included", True)]
        if not included:
            return pool
        if oce.venv_python() is None or not oce.CKPT.is_file():
            self.progress.emit(7, "OpenCLIP 环境或权重未就绪，语义多样性评分跳过；继续使用原混剪算法")
            return pool
        self.progress.emit(7, f"OpenCLIP 正在分析 {len(included)} 个候选片段的画面语义…")
        vectors = oce.encode_video_segments(
            included,
            progress_cb=lambda i, n, msg: self.progress.emit(
                7, f"{msg}（{i}/{n}）"),
            cancel_check=self._cancel_or_pause,
        )
        if self._cancel():
            return pool
        if vectors is None:
            self.progress.emit(7, "OpenCLIP 本轮分析未完成，已回退原混剪评分")
            return pool
        for frag, vector in zip(included, vectors):
            frag["_clip_embedding"] = vector
        valid = sum(v is not None for v in vectors)
        self.progress.emit(
            7, f"OpenCLIP 语义向量就绪：{valid}/{len(included)} 个候选片段；"
               "混剪将优先避免连续相似画面")
        return pool

    def _gpu_enrich(self, analysis):
        """可选：CUDA 查重 + 语音转写（整句切割用）。

        两个开关都关着时直接返回，不碰引擎。引擎缺失时只提示一次，
        不改变既有分析结果，保证没装引擎的人照常出片。
        """
        want_dedup = bool(self.detect_options.get("audio_dedup"))
        want_asr = bool(self.detect_options.get("sentence_snap"))
        if not analysis or not (want_dedup or want_asr):
            return analysis

        info = ge.describe()
        if not info["available"]:
            self.progress.emit(
                6, "CUDA 引擎未就位，本次跳过查重/语音转写：" + info["hint"]
            )
            return analysis

        # 1) 查重：指纹走 CUDA 引擎，比较在本地做
        if want_dedup:
            paths = [item["source"]["path"] for item in analysis]
            self.progress.emit(6, f"查重：提取 {len(paths)} 条素材指纹（CUDA）")
            groups, scores = adedup.find_duplicates(
                paths,
                threshold=self.detect_options.get("dedup_threshold"),
                progress_cb=lambda i, t, m: self.progress.emit(
                    6, f"{m} {i}/{t}"),
                cancel_check=self._cancel_or_pause,
            )
            if self._cancel():
                return analysis
            if scores:
                # scores 的键就是"被判定为重复拷贝"的素材，其余全部保留
                analysis = [
                    item for item in analysis
                    if item["source"]["path"] not in scores
                ]
                dropped = sorted({
                    Path(path).name for path in scores
                })
                self.progress.emit(
                    6,
                    f"查重：跳过 {len(dropped)} 条重复素材"
                    f"（{'、'.join(dropped[:5])}"
                    f"{' 等' if len(dropped) > 5 else ''}）",
                )
            else:
                self.progress.emit(6, "查重：未发现重复素材")

        # 2) 语音转写：整句切割的时间轴来源
        if want_asr:
            model = str(self.detect_options.get("asr_model") or "small")
            language = str(self.detect_options.get("asr_language") or "zh")
            total = len(analysis)
            done = 0
            for idx, item in enumerate(analysis):
                if self._cancel():
                    return analysis
                path = item["source"]["path"]
                self.progress.emit(
                    7, f"语音转写 {idx + 1}/{total}：{Path(path).name}")
                ok, segments, _srt, msg = ge.transcribe(
                    path, model=model, language=language)
                if ok:
                    # 压缩成 (start, end) 元组，供切点吸附使用
                    item["_sentences"] = [
                        (float(s["start"]), float(s["end"])) for s in segments
                    ]
                    item["_sentence_ends"] = scut.sentence_ends(segments)
                    done += 1
                else:
                    self.progress.emit(
                        7, f"语音转写跳过 {Path(path).name}：{msg}")
            self.progress.emit(
                7,
                f"语音转写完成：{done}/{total} 条素材可用于整句切割",
            )
        return analysis

    # ---- 片段池模式 ----
    def _pool_stubs(self, pool):
        """按素材路径造最小条目，仅供查重/转写增强使用（不参与分段）。"""
        stubs = []
        seen = set()
        for frag in pool:
            path = str(frag.get("path") or "")
            if not path or path in seen:
                continue
            seen.add(path)
            duration = float(frag.get("source_duration") or 0.0)
            stub = analysis_schema.make_item(
                analysis_schema.make_source(len(seen), path, duration),
                junk=[],
                junk_merged=[],
                # 本路径不做画质窗口与镜头检测：明确 None（没跑过），
                # 而不是 []（跑了但结果为空）。
                _quality_segments=None,
                _scenes=None,
            )
            # 生产出口强制校验（第二十二阶段）
            problems = analysis_schema.check(stub)
            if problems:
                raise RuntimeError(
                    "片段池条目的分析结构不合法（%s）：%s"
                    % (Path(path).name, "；".join(problems)))
            stubs.append(stub)
            self._emit_source(
                path, "cached",
                {"valid_segments": sum(
                    1 for f in pool
                    if f.get("path") == path and f.get("included", True)),
                 "valid_seconds": round(sum(
                    float(f["end"]) - float(f["start"]) for f in pool
                    if f.get("path") == path and f.get("included", True)), 3),
                 "waste_segments": 0, "errors": []})
        return stubs

    def _apply_pool_enrich(self, pool, stubs):
        """把查重结果与转写时间轴套回片段池；被查重剔除的素材整条跳过。"""
        by_path = {s["source"]["path"]: s for s in stubs}
        out = []
        for frag in pool:
            stub = by_path.get(str(frag.get("path") or ""))
            if stub is None:
                continue
            item = dict(frag)
            item["_sentences"] = stub.get("_sentences") or []
            item["_sentence_ends"] = stub.get("_sentence_ends") or []
            out.append(item)
        return out

    def _analysis(self):
        """返回整批素材的分析结果；有缓存就用缓存。"""
        cache = self.analysis_cache
        if self.filter_config is not None:
            return self._analysis_with_engine(cache)
        return self._analysis_legacy(cache)

    def _analysis_with_engine(self, cache):
        """逐素材增量检测：命中缓存直接用，只检测没缓存的素材。

        这样“58 个素材里只改了最后 1 个”时，前 57 个秒过，只检测 1 个；
        任务中途失败或取消，已经检测完的素材结果也都会留在缓存里。
        """
        cache_options = fe.cache_key_config(self.filter_config)

        hits, misses = {}, []
        if cache is not None:
            hits, misses = cache.get_many(
                self.paths, cache_options, source=DEFAULT_SOURCE)
        else:
            misses = list(self.paths)

        for path in hits:
            self._emit_source(path, "cached", self._summary_of(hits[path]))
        if hits:
            self.progress.emit(
                5, f"复用 {len(hits)} 个素材的缓存结果，"
                   f"本次只需检测 {len(misses)} 个")

        if not misses:
            return self._ordered_analysis(hits, misses_analysis={})

        self.progress.emit(5, "智能筛选引擎检测中…")

        def _engine_progress(frac, msg):
            self.progress.emit(
                int(5 + max(0.0, min(1.0, frac)) * 35), msg
            )

        for path in misses:
            self._emit_source(path, "analyzing", {})

        # 素材级并发 2 路：瓶颈在 ffmpeg 子进程，Python 线程只是等待；
        # 2 路是 NVDEC 会话数与 SSD 带宽的安全值，不盲目开满。
        filter_result = self._run_filter_concurrent(
            misses, self.filter_config, _engine_progress)
        # 取消或出错时，已完成的素材照样入缓存，不浪费
        fresh = fe.to_mix_analysis(misses, filter_result) \
            if filter_result else []
        by_path = {str((it.get("source") or {}).get("path")): it
                   for it in fresh}

        failed = 0
        for path in misses:
            item = by_path.get(str(path))
            src = (filter_result.get("sources") or {}).get(str(path)) or {}
            if item is None:
                failed += 1
                self._emit_source(path, "error", {
                    "error": "; ".join(src.get("errors") or [])
                             or "无法读取或解码该素材",
                })
                continue
            valid = analysis_core.valid_segments_for(item)
            info = {
                "valid_segments": len(valid),
                "valid_seconds": round(sum(e - s for s, e in valid), 3),
                "waste_segments": len(item.get("_junk_merged") or []),
                "errors": list(src.get("errors") or []),
            }
            self._emit_source(path, "done", info)
            if cache is not None:
                cache.set_entry(path, cache_options, item,
                                source=DEFAULT_SOURCE)

        for err in fe.collect_errors(filter_result):
            self.progress.emit(6, f"检测组件异常（已跳过）：{err}")
        if failed:
            self.progress.emit(
                7, f"{failed} 个素材无法读取，已在素材卡片标红；"
                   "取消勾选这些素材后重跑即可，其余素材不会重新检测。")

        if self._cancel():
            return []
        return self._ordered_analysis(hits, by_path)

    def _run_filter_concurrent(self, paths, config, progress_cb):
        """素材级 2 路并发跑筛选引擎，合并结果。

        瓶颈在 ffmpeg 子进程，Python 线程只是等待；2 路是 NVDEC 会话数
        与 SSD 带宽的安全值。素材 ≤2 个时直接串行，不起线程池。
        """
        if not paths:
            return {"detect_engine": {}, "waste_segments": [],
                    "shot_cuts": [], "sources": {}}
        if len(paths) <= 2:
            return fe.run_filter(
                paths, config,
                progress_cb=progress_cb,
                cancel_check=self._cancel_or_pause)

        half = len(paths) // 2
        groups = [paths[:half], paths[half:]]
        results = [None, None]

        def _run(idx, subset):
            results[idx] = fe.run_filter(
                subset, config,
                progress_cb=None,  # 并发时不分子进度，由外层汇总
                cancel_check=self._cancel_or_pause)

        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(_run, i, g) for i, g in enumerate(groups)]
            for i, f in enumerate(futs):
                f.result()
                if progress_cb:
                    progress_cb((i + 1) / len(groups), f"分组 {i + 1}/{len(groups)} 完成")

        a, b = results[0] or {}, results[1] or {}
        merged = dict(a)
        merged["sources"] = {**a.get("sources", {}), **b.get("sources", {})}
        merged["waste_segments"] = (a.get("waste_segments", []) +
                                    b.get("waste_segments", []))
        merged["shot_cuts"] = (a.get("shot_cuts", []) +
                               b.get("shot_cuts", []))
        return merged


    def _analysis_legacy(self, cache):
        """旧检测路径：同样按素材逐条缓存。"""
        cache_options = dict(self.detect_options)

        hits, misses = {}, []
        if cache is not None:
            hits, misses = cache.get_many(
                self.paths, cache_options, source=LEGACY_SOURCE)
        else:
            misses = list(self.paths)

        for path in hits:
            self._emit_source(path, "cached", self._summary_of(hits[path]))
        if hits:
            self.progress.emit(
                5, f"复用 {len(hits)} 个素材的缓存结果，"
                   f"本次只需检测 {len(misses)} 个")
        if not misses:
            return self._ordered_analysis(hits, {})

        self.progress.emit(5, "检测废镜头…")
        fresh = {}
        total = len(misses)
        for idx, path in enumerate(misses):
            if self._checkpoint():
                break
            self._emit_source(path, "analyzing", {})
            self.progress.emit(
                int(5 + (idx / max(1, total)) * 3),
                f"检测废镜头 {idx + 1}/{total}：{Path(path).name}")
            try:
                item = analysis_core.analyze_source(
                    idx, path, analysis_core.DEFAULT_OPTIONS,
                    cancel_check=self._cancel)
            except Exception as exc:
                self._emit_source(path, "error", {
                    "error": f"{type(exc).__name__}: {exc}"})
                continue
            fresh[str(path)] = item
            self._emit_source(path, "done", self._summary_of(item))
            if cache is not None:
                cache.set_entry(path, cache_options, item,
                                source=LEGACY_SOURCE)

        if self._cancel():
            return []
        return self._ordered_analysis(hits, fresh)

    # ---- 工具 ----
    @staticmethod
    def _summary_of(item):
        valid = analysis_core.valid_segments_for(item)
        return {
            "valid_segments": len(valid),
            "valid_seconds": round(sum(e - s for s, e in valid), 3),
            "waste_segments": len(item.get("_junk_merged") or []),
            "errors": [],
        }

    def _ordered_analysis(self, hits, misses_analysis):
        """按用户选择顺序返回分析结果；缺失的素材跳过。"""
        out = []
        for path in self.paths:
            item = hits.get(str(path)) or misses_analysis.get(str(path))
            if item is not None:
                out.append(item)
        return out

    def _analysis_progress(self, idx, total, message):
        frac = float(idx) / max(1.0, float(total))
        self.progress.emit(int(5 + frac * 3), message)


class ProbeWorker(QThread):
    """素材参数探测 + 缩略图提取线程。

    界面点击素材时不再同步跑 ffprobe/ffmpeg（会卡住界面），统一交给这个
    线程逐个处理，结果通过信号回到主线程。所有结果都来自真实的 ffprobe /
    ffmpeg 输出，没有任何占位图。
    """

    probed = Signal(str, dict)          # path, details
    probe_failed = Signal(str, str)     # path, 错误信息
    thumbnail_ready = Signal(str, str)  # path, 缩略图文件（裁剪成竖屏后的路径）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = []
        self._pending = set()
        self._thumb_queue = []
        self._thumb_pending = set()
        self._thumb_done = set()
        self._lock = threading.Lock()
        self._stop = False

    def request(self, paths):
        """把还没有探测过的路径加入队列；线程空闲时自动启动。"""
        with self._lock:
            for path in paths:
                p = str(path)
                if p in self._pending:
                    continue
                self._pending.add(p)
                self._queue.append(p)
            if not self.isRunning() and (self._queue or self._thumb_queue):
                self._stop = False
                self.start()

    def request_thumbnails(self, paths):
        """单独请求缩略图（例如换了存储目录）。"""
        with self._lock:
            for path in paths:
                p = str(path)
                if p in self._thumb_pending:
                    continue
                self._thumb_pending.add(p)
                self._thumb_queue.append(p)
            if not self.isRunning() and (self._queue or self._thumb_queue):
                self._stop = False
                self.start()

    def shutdown(self):
        with self._lock:
            self._stop = True

    def _next(self):
        with self._lock:
            if self._stop:
                return None, None
            if self._queue:
                return "probe", self._queue.pop(0)
            if self._thumb_queue:
                return "thumb", self._thumb_queue.pop(0)
            return None, None

    def run(self):
        while True:
            kind, path = self._next()
            if kind is None:
                return
            if kind == "probe":
                try:
                    details = ft.probe_details(path)
                    self.probed.emit(path, details)
                except Exception as exc:
                    self.probe_failed.emit(path, f"{type(exc).__name__}: {exc}")
                    continue
                if path not in self._thumb_done:
                    self._thumb_queue.append(path)
                    self._thumb_pending.add(path)
            else:
                try:
                    duration = ft.probe_duration(path)
                    seek = min(1.0, max(0.0, duration * 0.15))
                    thumb = ft.make_thumbnail(path, seek_seconds=seek)
                    if thumb:
                        self._thumb_done.add(path)
                        self.thumbnail_ready.emit(path, thumb)
                except Exception:
                    # 抽帧失败：卡片保持“无缩略图”，不伪造图片
                    pass
