# -*- coding: utf-8 -*-
"""智能混剪任务（从 main.py 原样搬出）。

包含 BGM 解析、素材标签收集、探测线程、混剪启动与进度/完成/失败回调，
共 24 个方法，其中 ``start_mix`` 单个方法约 225 行。

拆分方式说明
------------
Mixin：这批方法高度依赖主窗口上的 ``mix_*`` 参数控件（约 40 个），
用 Mixin 让 ``self.mix_xxx`` 保持原样，不引入参数对象或回调。

宿主契约
--------
**参数控件**（由 ``_build_mix_tab`` 创建）
    mix_resolution / mix_fps / mix_target / mix_tolerance /
    mix_clip_min / mix_clip_max / mix_short / mix_long / mix_reuse /
    mix_shuffle / mix_rhythm / mix_diversity / mix_weak_chance /
    mix_trans_enable / mix_dissolve_dur / mix_blacklist /
    mix_beat_sync / mix_beat_step / mix_beat_tolerance /
    mix_sentence_snap / mix_ducking / mix_base_vol / mix_duck_vol /
    mix_openclip / mix_version / mix_selector / mix_start_btn /
    mix_cancel_btn / mix_progress / mix_log / mix_draft_dir /
    mix_bgm / music_mode / music_folder

**状态**
    self.mix_worker  self._mix_canceled  self._mix_last_failed
    self._last_results  self._probe_cache  self.probe_worker
    self.material_confirmed  self._confirmed_paths

**宿主方法**
    self.active_paths  self._collect_filter_config  self.run_force_self_check
    self._update_demo_metadata  self._update_demo_thumbnail
    self.log  self.controller  self.backend  self.analysis_cache  self.tree

依赖方向：导入 PySide6、标准库与 mixcut 的业务模块（workers / analysis /
mixbuilder / filter_engine / ffmpeg_tools / timeline），
不导入 main.py，不导入 mixcut.ui。
"""
import os
import random
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMessageBox, QFileDialog, QTreeWidgetItemIterator,
)

from mixcut import workers as mc_workers
from mixcut import analysis as analysis_core
from mixcut import mixbuilder as mixbuilder_module
from mixcut import filter_engine as fe
from mixcut import ffmpeg_tools as ft
from mixcut import timeline as tl_mod

from .constants import MIX_RESOLUTION_PRESETS, MIX_FPS_OPTIONS


class MixTaskMixin:
    """智能混剪任务。"""

    def start_real_mix(self):
        paths = self.active_paths()
        if not paths:
            self.controller.log("开始混剪 · 素材库为空")
            return False
        self._confirmed_paths = list(paths)
        self.material_confirmed = True
        # 走桥的启动入口：先把新 UI 参数同步进旧后台控件，再启动旧混剪线程。
        # 直接调旧 start_mix() 会绕过 sync_params，界面上改的参数不会生效。
        params = getattr(self, "demo_params", None)
        return bool(self.backend.start_mix(paths, params))

    def choose_draft_dir(self):
        start = self.mix_draft_dir.text() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "选择草稿保存位置", start
        )
        if folder:
            self.mix_draft_dir.setText(folder)

    def choose_bgm(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 BGM", "",
            "音频 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
        )
        if path:
            self.mix_bgm.setText(path)

    def choose_music_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择本地音乐文件夹", self.music_folder.text()
        )
        if folder:
            self.music_folder.setText(folder)

    def _on_music_mode(self):
        mode = self.music_mode.currentIndex()
        # 0 手动 / 1 本地随机 / 2 云端占位
        self.mix_bgm.setEnabled(mode == 0)
        self.music_folder.setEnabled(mode == 1)

    AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

    def _resolve_bgm(self):
        """按配乐方式返回 (bgm_path, error_message)。"""
        mode = self.music_mode.currentIndex()
        if mode == 0:
            path = self.mix_bgm.text().strip()
            if not path:
                return "", None
            audio_path = Path(path)
            if not audio_path.is_file():
                return "", "所选 BGM 文件不存在，请重新选择。"
            if audio_path.suffix.lower() not in self.AUDIO_EXTS:
                return "", "BGM 格式不受支持，请选择 MP3、WAV、M4A、AAC、FLAC 或 OGG。"
            return str(audio_path.resolve()), None
        if mode == 2:
            return "", None  # 云端暂未开放，不加载音乐
        # 本地随机
        folder = self.music_folder.text().strip()
        if not folder or not Path(folder).is_dir():
            return "", "本地随机配乐：请先选择有效的音乐文件夹。"
        candidates = [
            str(p) for p in Path(folder).rglob("*")
            if p.is_file() and p.suffix.lower() in self.AUDIO_EXTS
        ]
        if not candidates:
            return "", "该文件夹里没有可用的音频文件。"
        return random.choice(candidates), None

    def collect_source_tags(self, paths):
        """把素材所在的虚拟分类名映射成 {path: [分类名]}。

        素材树里的顶层节点是用户新建的分类，子节点才是素材；只取分类名，
        不生成任何内容语义标签。没有归类的素材不出现在结果里。

        注意：QTreeWidgetItemIterator 不能用来判定“属于哪个分类”——它同一
        时间只维护一个包装对象，取到父节点后子节点的包装会被复用。因此这里
        用 parent() 链向上找分类节点。
        """
        wanted = {str(p) for p in paths}
        result = {}
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            path = item.data(1, Qt.UserRole)
            if isinstance(path, str) and path in wanted:
                category = self._category_of(item)
                if category:
                    tags = result.setdefault(path, [])
                    if category not in tags:
                        tags.append(category)
            iterator += 1
        return result

    @staticmethod

    def _category_of(item):
        """沿父链找到最近的分类节点名（顶层、没有素材路径的节点）。"""
        node = item.parent()
        while node is not None:
            if not node.data(1, Qt.UserRole):
                name = node.text(0).strip()
                if name:
                    return name
            node = node.parent()
        return None

    def _mix_detect_options(self):
        """传给混剪时间轴的检测选项（与筛选引擎保持一致）。

        背景检测在引擎里已经完成，这里主要影响有效区间计算（min_keep 等）
        与旧检测路径；画面/静音开关和阈值必须与用户勾选的一致，不能写死。
        """
        config = self._collect_filter_config()
        return {
            **analysis_core.DEFAULT_OPTIONS,
            **config["ffmpeg_options"],
        }

    def start_mix(self):
        ap = getattr(self, "page_analyze", None)
        has_analysis = bool(ap is not None and getattr(ap, "_results", None))
        pool = ap.mix_pool_paths() if hasattr(ap, "mix_pool_paths") else []
        if has_analysis:
            # 分析页有结果时，混剪池是唯一入口：池里没片段就不能开混剪。
            paths = pool
            if not paths:
                QMessageBox.warning(
                    self, "混剪池为空",
                    "当前没有片段被加入混剪池。\n"
                    "请回到「素材分析」页，在多选片段后点「加入混剪池」，"
                    "或勾选片段卡片右上角的「池」。")
                return
        else:
            paths = self.mix_selector.selected_paths()
        if not paths:
            QMessageBox.warning(self, "提示", "请先在素材选择框架勾选要混剪的视频。")
            return

        # 片段级唯一来源：混剪只使用分析页已确认的片段，不再自行分析素材。
        fragment_pool = (ap.fragment_pool(paths, pool_only=True)
                         if ap is not None else [])
        analyzed_paths = {f.get("path") for f in fragment_pool}
        missing = [p for p in paths if p not in analyzed_paths]
        if missing:
            names = "、".join(os.path.basename(p) for p in missing[:5])
            more = f" 等 {len(missing)} 条" if len(missing) > 5 else ""
            QMessageBox.warning(
                self, "缺少分析结果",
                f"以下素材还没有分析结果：{names}{more}。\n\n"
                "混剪只使用“分析”页确认过的片段，请先完成分析后再开始混剪。")
            return
        if not any(bool(f.get("included", True)) for f in fragment_pool):
            QMessageBox.warning(
                self, "没有可用片段",
                "所选素材的片段全部被标记为“不加入”。\n\n"
                "请在“分析”页把要用的片段加回，或勾选其他素材。")
            return

        # 参数校验必须发生在创建后台线程之前，避免无效任务进入引擎。
        clip_min = self.mix_clip_min.value()
        clip_max = self.mix_clip_max.value()
        if clip_min > clip_max:
            QMessageBox.warning(
                self, "参数错误", "最短片段不能大于最长片段。"
            )
            return

        short_threshold = self.mix_short.value()
        long_threshold = self.mix_long.value()
        if short_threshold > long_threshold:
            QMessageBox.warning(
                self, "参数错误",
                "短镜头阈值不能大于长镜头阈值，请重新调整。"
            )
            return

        # 启动前强制自检：重新导入组件、刷新状态；ffmpeg 缺失则禁止启动
        if not self.run_force_self_check(show_dialog=False):
            QMessageBox.critical(
                self, "环境自检失败",
                "找不到 ffmpeg，无法进行废镜头检测，请先修复环境。"
            )
            return
        # 若有勾选但依赖缺失的组件，提示一次（不阻断，引擎会自动跳过）
        missing = fe.missing_modules(self._collect_filter_config())
        if missing:
            QMessageBox.warning(
                self, "部分模块不可用",
                "以下模块未安装，本次将自动跳过：\n" + "\n".join(missing)
            )

        # 素材总数不足目标时长、且不允许重复使用：提前说清楚，而不是悄悄生成一条更短的片子
        if not self.mix_reuse.isChecked():
            available = self._available_footage(paths)
            need = self.mix_target.value() - self.mix_tolerance.value()
            if available is not None and available < need:
                QMessageBox.warning(
                    self, "素材可能不足",
                    f"所选素材合计可用时长约 {available:.1f} 秒，"
                    f"低于目标时长下限 {need:.1f} 秒，且未勾选“允许素材重复使用”。\n\n"
                    "成片将会比目标时长短。可勾选重复使用、增加素材，或降低目标时长。"
                )

        bgm_path, bgm_error = self._resolve_bgm()
        if bgm_error:
            QMessageBox.warning(self, "提示", bgm_error)
            return
        music_enabled = bool(bgm_path)

        # 新 UI（demo 参数页）会直接给画布尺寸：横屏/4K 在旧预设里没有对应项，
        # 用覆盖值避免按索引套预设把画幅选错。旧界面不走这里，保持原逻辑。
        canvas_override = getattr(self, "_mix_canvas_override", None)
        if canvas_override:
            width, height, fps = canvas_override
        else:
            _, width, height = MIX_RESOLUTION_PRESETS[
                self.mix_resolution.currentIndex()
            ]
            fps = MIX_FPS_OPTIONS[self.mix_fps.currentIndex()]
        canvas = tl_mod.create_canvas(width, height, fps)

        mix_options = {
            "target_duration": self.mix_target.value(),
            "duration_tolerance": self.mix_tolerance.value(),
            "project_count": self.mix_clip_count.value(),
            "clip_min": self.mix_clip_min.value(),
            "clip_max": self.mix_clip_max.value(),
            "shuffle": self.mix_shuffle.isChecked(),
            "reuse": self.mix_reuse.isChecked(),
            "beat_sync": self.mix_beat_sync.isChecked(),
            # 整句切割：切点吸附到语音句尾（mixbuilder 读取）
            "sentence_snap": bool(
                getattr(self, "mix_sentence_snap", None)
                and self.mix_sentence_snap.isChecked()
            ),
            # 用户自建的虚拟分类作为素材标签，供相邻重复控制使用。
            # 没有分类的素材不带标签（不虚构内容语义）。
            "tags_by_source": self.collect_source_tags(paths),
            "diversity": self.mix_diversity.currentData()
            if hasattr(self, "mix_diversity") else "medium",
            "rhythm": self.mix_rhythm.currentData()
            if hasattr(self, "mix_rhythm") else "balance",
            "beat_step": self.mix_beat_step.currentData()
            if hasattr(self, "mix_beat_step") else 0,
            "weak_beat_chance": self.mix_weak_chance.value()
            if hasattr(self, "mix_weak_chance") else 0.25,
            "openclip_enabled": bool(
                getattr(self, "mix_openclip", None)
                and self.mix_openclip.isChecked()),
            "openclip_weight": 2.0,
            "beat_tolerance": self.mix_beat_tolerance.value()
            if hasattr(self, "mix_beat_tolerance") else 0.12,
        }
        # 版本策略：单个工程走固定策略；多工程可轮换不同算法倾向
        version_choice = (
            self.mix_version.currentData()
            if hasattr(self, "mix_version") else "balanced"
        )
        if version_choice and version_choice != "rotate":
            mix_options["version_style"] = version_choice
            mix_options["_rotate_versions"] = False
        else:
            mix_options["_rotate_versions"] = True
        custom_draft_dir = self.mix_draft_dir.text().strip()
        music_settings = {
            "enabled": music_enabled,
            "path": bgm_path,
            "ducking": self.mix_ducking.isChecked(),
            "base_volume": self.mix_base_vol.value(),
            "duck_volume": self.mix_duck_vol.value(),
            "output": "",
            # 自定义草稿位置：非空时草稿输出到该目录而非默认剪映目录
            "draft_dir": custom_draft_dir,
            "draft_dir_custom": bool(custom_draft_dir),
        }
        blacklist = [
            x.strip() for x in self.mix_blacklist.text().split(",") if x.strip()
        ]
        transition_options = {
            "enabled": self.mix_trans_enable.isChecked(),
            "short_threshold": self.mix_short.value(),
            "long_threshold": self.mix_long.value(),
            "dissolve_duration": self.mix_dissolve_dur.value(),
            "blacklist": blacklist,
        }

        self.mix_start_btn.setEnabled(False)
        self.mix_cancel_btn.setEnabled(True)
        self.mix_progress.setValue(0)
        self.mix_log.clear()
        self.mix_log.appendPlainText("初始化智能混剪任务…")
        if self.music_mode.currentIndex() == 2:
            self.mix_log.appendPlainText(
                "云端随机配乐暂未开放，本次不加载音乐。"
            )
        if music_enabled:
            self.mix_log.appendPlainText(f"使用配乐：{Path(bgm_path).name}")
        if custom_draft_dir:
            self.mix_log.appendPlainText(f"草稿输出到自定义位置：{custom_draft_dir}")
        else:
            self.mix_log.appendPlainText("草稿输出到剪映默认草稿目录。")

        self._mix_canceled = False
        self._mix_last_failed = False
        worker = mc_workers.MixBuildWorker(
            paths, canvas, self._mix_detect_options(),
            mix_options, music_settings, transition_options,
            analysis_cache=self.analysis_cache,
            filter_config=self._collect_filter_config(),
            fragment_pool=fragment_pool,
        )
        # 线程归属主窗口：窗口析构前会先走 closeEvent 里的停止流程
        worker.setParent(self)
        worker.progress.connect(self._mix_on_progress)
        worker.built.connect(self.mix_on_built)
        worker.failed.connect(self.mix_on_failed)
        worker.finished.connect(self._mix_on_finished)
        # 逐素材分析结果 → 素材卡片状态（真实检测结果）
        worker.source_analyzed.connect(self._on_source_analyzed)
        # 真实暂停状态 → 任务卡按钮与导航状态
        worker.paused_changed.connect(self._on_worker_paused)
        self.mix_worker = worker

        page = getattr(self, "page_library", None)
        if page is not None:
            page.reset_analysis(paths)

        # 任务卡开始记录（时间来自真实开始时刻）
        shell = getattr(self, "shell", None)
        if shell is not None:
            shell.nav.set_status("运行中", "warning")
        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            card = tasks.start_task(f"智能混剪 · {len(paths)} 个素材")
            tasks.log(f"开始混剪：{len(paths)} 个素材，"
                      f"{mix_options['project_count']} 个工程", "info")
            # 任务卡上的暂停/继续/取消 → worker 的真实接口
            card.pause_requested.connect(self._pause_mix)
            card.resume_requested.connect(self._resume_mix)
            card.cancel_requested.connect(self.cancel_mix)
        worker.start()

    # ---- 新界面：素材参数探测线程 ----

    def _ensure_probe_worker(self):
        worker = getattr(self, "probe_worker", None)
        if worker is None:
            worker = mc_workers.ProbeWorker(self)
            worker.probed.connect(self._on_probe_result)
            worker.probe_failed.connect(self._on_probe_failed)
            worker.thumbnail_ready.connect(self._on_thumbnail_ready)
            self.probe_worker = worker
        return worker

    def _on_probe_result(self, path, details):
        page = getattr(self, "page_library", None)
        if page is not None:
            page.set_probe_result(path, details)
        # 顺便在主线程里缓存一份，详情面板可以复用（不再重复调 ffprobe）
        cache = getattr(self, "_probe_cache", None)
        if cache is None:
            cache = self._probe_cache = {}
        cache[path] = details
        if hasattr(self, "demo_library"):
            self._update_demo_metadata(path)

    def _on_probe_failed(self, path, error):
        page = getattr(self, "page_library", None)
        if page is not None:
            page.set_probe_result(path, None, error)

    def _on_thumbnail_ready(self, path, image_path):
        page = getattr(self, "page_library", None)
        if page is not None:
            page.set_thumbnail(path, image_path)
        if hasattr(self, "demo_library"):
            self._update_demo_thumbnail(path, image_path)

    def _on_source_analyzed(self, path, status, detail):
        """MixBuildWorker 的逐素材分析事件 → 素材卡片状态。"""
        page = getattr(self, "page_library", None)
        if page is not None:
            page.set_analysis_status(path, status, detail)
        _ = detail  # 详情目前只用于卡片提示

    def _available_footage(self, paths):
        """粗估所选素材的可用秒数；任一路径出错就返回 None（不做提示）。"""
        try:
            total = 0.0
            for path in paths:
                total += float(ft.probe_duration(path))
        except Exception:
            return None
        return total

    def _mix_on_progress(self, value, message):
        self.mix_progress.setValue(value)
        self.mix_log.appendPlainText(message)
        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            tasks.update_progress(value, message)

    def cancel_mix(self):
        """取消：任何状态下都能用（运行中 / 暂停中 / 检测阶段）。"""
        worker = self.mix_worker
        if worker is not None and worker.isRunning():
            self._mix_canceled = True
            worker.shutdown()
            self.mix_log.appendPlainText("正在取消…")
            if getattr(self, "_log_sink", None):
                self._log_sink("正在取消…", "warn")
            tasks = getattr(self, "demo_tasks", None)
            if tasks is not None and tasks.current_card() is not None:
                tasks.current_card().step.setText("正在取消…")

    def _pause_mix(self):
        worker = self.mix_worker
        if worker is not None and worker.isRunning():
            worker.pause()
            self.mix_log.appendPlainText("任务已暂停")
            if getattr(self, "_log_sink", None):
                self._log_sink("任务已暂停", "warn")

    def _resume_mix(self):
        worker = self.mix_worker
        if worker is not None and worker.isRunning():
            worker.resume()
            self.mix_log.appendPlainText("任务已继续")
            if getattr(self, "_log_sink", None):
                self._log_sink("任务已继续", "info")

    def _on_worker_paused(self, paused, message):
        """worker 真实暂停状态 → 任务卡与导航状态。"""
        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            tasks.set_paused(paused, message)
        shell = getattr(self, "shell", None)
        if shell is not None:
            if paused:
                shell.nav.set_status("已暂停", "warning")
            else:
                shell.nav.set_status("运行中", "warning")

    def _mix_on_finished(self):
        """QThread 结束（成功/失败/取消）必触发：恢复按钮，取消时给出提示。"""
        worker = self.mix_worker
        was_canceled = bool(self._mix_canceled) or (
            worker is not None and worker.isInterruptionRequested()
        )
        self.mix_start_btn.setEnabled(True)
        self.mix_cancel_btn.setEnabled(False)
        if was_canceled:
            self.mix_progress.setValue(0)
            self.mix_log.appendPlainText(
                "已取消，可重新调整参数后再次点击开始。"
            )
        # 导航栏运行状态 + 任务卡状态跟真实结束信号同步
        shell = getattr(self, "shell", None)
        if shell is not None:
            if was_canceled:
                shell.nav.set_status("已取消", "idle")
            elif getattr(self, "_mix_last_failed", False):
                shell.nav.set_status("任务失败", "error")
            else:
                shell.nav.set_status("就绪", "success")
        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            if was_canceled:
                tasks.finish_task("canceled", "已取消")
            elif getattr(self, "_mix_last_failed", False):
                tasks.finish_task("failed", "处理失败，详情见日志")
            else:
                tasks.finish_task("done", "已完成")
        self._mix_last_failed = False
        self._mix_canceled = False
        # 线程已结束，释放对象（下次开始会重新创建）
        if worker is not None:
            worker.deleteLater()
            self.mix_worker = None

    def mix_on_built(self, result):
        self.mix_start_btn.setEnabled(True)
        self.mix_cancel_btn.setEnabled(False)
        self.mix_progress.setValue(100)

        items = result.get("results", [result])
        for item in items:
            message = (
                f"工程完成：{item['draft_name']}"
                f"（{item['clip_count']} 片段 / {item.get('duration', 0):.2f} 秒）"
            )
            self.mix_log.appendPlainText(message)
            if getattr(self, "_log_sink", None):
                self._log_sink(message, "info")

        tasks = getattr(self, "demo_tasks", None)
        if tasks is not None:
            tasks.set_last_outputs(
                items, str(Path(items[0]["draft_path"]).parent) if items else "")
        self._last_results = items

        voice = ""
        if self.mix_beat_sync.isChecked() and result.get("beat_sync") is False:
            voice = "\n注意：BGM 未检测到可用鼓点，本次未卡点。"

        n = result.get("project_count", len(items))
        names = "\n".join(f"{i+1}. {r['draft_name']}"
                          for i, r in enumerate(items))
        target = float(result.get("target_duration") or 0.0)
        made = float(result.get("duration") or 0.0)
        gap_hint = ""
        if target > 0 and made + 1e-6 < target - self.mix_tolerance.value():
            gap_hint = (
                f"\n注意：成片约 {made:.1f} 秒，短于目标 {target:.0f} 秒。"
                "不可重复使用素材时受可用素材总量限制。"
            )
            if getattr(self, "_log_sink", None):
                self._log_sink(gap_hint.strip(), "warn")
        QMessageBox.information(
            self, "完成",
            f"已生成 {n} 个独立剪映工程，可在剪映中分别二次编辑。\n\n"
            f"{names}\n\n保存目录：\n{Path(items[0]['draft_path']).parent}"
            f"{gap_hint}{voice}",
        )

    def mix_on_failed(self, message):
        self.mix_start_btn.setEnabled(True)
        self.mix_cancel_btn.setEnabled(False)
        self.mix_progress.setValue(0)
        self._mix_last_failed = True
        self.mix_log.appendPlainText(f"发生错误：\n{message}")
        if getattr(self, "_log_sink", None):
            self._log_sink(f"发生错误：\n{message}", "error")
        QMessageBox.critical(self, "失败", "智能混剪出错，请查看日志。")


    # =================================================
    # 工作站占位页
    # =================================================

