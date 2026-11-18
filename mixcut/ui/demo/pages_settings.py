# -*- coding: utf-8 -*-
"""设置：外观（主题/强调色）、动效、运行环境、缓存与草稿。

运行环境与缓存全部本地读取：
- 显卡/CUDA 走 nvidia-smi
- 预览分辨率取参数页的画布设置
- 色彩空间取素材真实 bit_depth
- 草稿目录自动探测剪映草稿目录，可手动改
- 缓存直接落在 MixCut 源文件夹 / 安装目录（不可写时退回 LOCALAPPDATA）
"""

import os
import re
import shutil
import subprocess
import time

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ... import app_paths
from . import prefs, theme as T
from .ui_kit import Card, SoftButton

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _gpu_info() -> str:
    """nvidia-smi 读显卡名 + 驱动 CUDA 版本；没有就退回 CPU。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=8, creationflags=NO_WINDOW,
        )
        name = (out.stdout or "").strip().splitlines()
        name = name[0].strip() if name else ""
        cuda = ""
        head = subprocess.run(["nvidia-smi"], capture_output=True, text=True,
                              encoding="utf-8", errors="ignore",
                              timeout=8, creationflags=NO_WINDOW).stdout or ""
        match = re.search(r"CUDA\s+(?:UMD\s+)?Version:\s*([0-9.]+)", head)
        if match:
            cuda = match.group(1).rstrip(".")
        if name:
            return "%s · CUDA %s" % (name, cuda) if cuda else name
    except Exception:
        pass
    return "CPU 软解 · 未检测到 NVIDIA 显卡"


def _dir_size(path) -> int:
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return ("%.1f %s" % (value, unit)) if unit != "B" else "%d B" % num
        value /= 1024
    return "%.1f TB" % value


def _count_drafts(path: str) -> int:
    if not path or not os.path.isdir(path):
        return 0
    try:
        return sum(1 for name in os.listdir(path)
                   if os.path.isdir(os.path.join(path, name)))
    except OSError:
        return 0


class Swatch(QWidget):
    clicked = Signal(str)

    def __init__(self, name: str, base: str, side: str, active: bool = False, parent=None):
        super().__init__(parent)
        self.name = name
        self.base = base
        self.side = side
        self.active = active
        self.setFixedSize(150, 104)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击切换主题：%s" % name)

    def set_active(self, on: bool):
        if self.active != on:
            self.active = on
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.name)
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -16)
        p.setPen(QPen(QColor(T.ACCENT_LINE) if self.active else QColor(T.LINE),
                      2 if self.active else 1))
        p.setBrush(QColor(self.base))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.side))
        p.drawRoundedRect(QRectF(r.left(), r.top(), 40, r.height()), 9, 9)
        p.drawRect(QRectF(r.left() + 30, r.top(), 12, r.height()))
        p.setBrush(QColor(T.CARD_HI))
        p.drawRoundedRect(QRectF(r.left() + 50, r.top() + 12, 70, 16), 5, 5)
        p.setBrush(QColor(self.base).lighter(112))
        p.drawRoundedRect(QRectF(r.left() + 50, r.top() + 36, 86, 34), 6, 6)
        p.setBrush(QColor(T.ACCENT))
        p.drawRoundedRect(QRectF(r.left() + 58, r.top() + 43, 26, 5), 2, 2)
        p.setPen(QColor(T.TEXT if self.active else T.TEXT_2))
        p.setFont(T.font(11))
        p.drawText(QRectF(0, self.height() - 15, self.width(), 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.name)
        p.end()


class AccentDot(QWidget):
    clicked = Signal(str)

    def __init__(self, name: str, color: str, active: bool, parent=None):
        super().__init__(parent)
        self.name = name
        self.color = color
        self.active = active
        self.setFixedSize(16, 16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("强调色：%s" % name)

    def set_active(self, on: bool):
        if self.active != on:
            self.active = on
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.name)
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(T.TEXT) if self.active else QColor(255, 255, 255, 40),
                      2 if self.active else 1))
        p.setBrush(QColor(self.color))
        p.drawEllipse(r)
        p.end()


class InfoRow(QWidget):
    def __init__(self, key: str, value: str, parent=None, tooltip: str = ""):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(10)
        k = QLabel(key)
        k.setFont(T.font(12))
        k.setStyleSheet("color:%s;" % T.TEXT_2)
        self.value_label = QLabel(value)
        self.value_label.setFont(T.font(11, mono=True))
        self.value_label.setStyleSheet("color:%s;" % T.TEXT_3)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lay.addWidget(k, 1)
        lay.addWidget(self.value_label)
        self.setFixedHeight(34)
        if tooltip:
            self.setToolTip(tooltip)

    def set_value(self, text: str):
        self.value_label.setText(text)


class MotionRow(InfoRow):
    """点一下切换 开/关，立即写入 config/ui.json 并生效。"""

    def __init__(self, key: str, title: str, hint: str, parent=None):
        value = "开" if prefs.motion(key) else "关"
        super().__init__(title, "%s · %s" % (hint, value), parent)
        self.key = key
        self.hint = hint
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击切换：%s" % title)
        self.value_label.setStyleSheet("color:%s;" % T.TEXT_2)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        data = prefs.data()
        motion = dict(data.get("motion") or {})
        motion[self.key] = not bool(motion.get(self.key, True))
        data["motion"] = motion
        prefs.save(data)
        self.set_value("%s · %s" % (self.hint, "开" if motion[self.key] else "关"))
        win = getattr(self.window(), "win", None)
        if win is not None and hasattr(win, "controller"):
            win.controller.log("设置 · 动效「%s」已%s" % (
                self.key, "开启" if motion[self.key] else "关闭"))
        super().mousePressEvent(e)


class SettingsPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._data = prefs.data()
        self._clean_days = int(self._data.get("clean_days", 7))
        self._clean_days = max(1, min(14, self._clean_days))
        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAGE_PAD, 18, T.PAGE_PAD, 22)
        root.setSpacing(6)
        h = QLabel("设置")
        h.setFont(T.font(30, QFont.Weight.DemiBold))
        s = QLabel("外观、动效与运行环境")
        s.setFont(T.font(12))
        s.setStyleSheet("color:%s;" % T.TEXT_3)
        head = QHBoxLayout()
        head.addWidget(h)
        head.addStretch(1)
        self.btn_new = SoftButton("新建工程", "ghost", height=32, min_width=86)
        self.btn_open = SoftButton("打开工程", "ghost", height=32, min_width=86)
        self.btn_save = SoftButton("保存工程", "ghost", height=32, min_width=86)
        self.btn_recheck = SoftButton("重新检测", "secondary", height=32, min_width=86)
        for b in (self.btn_new, self.btn_open, self.btn_save, self.btn_recheck):
            head.addWidget(b)
        root.addLayout(head)
        root.addWidget(s)
        root.addSpacing(12)

        cols = QHBoxLayout()
        cols.setSpacing(16)
        root.addLayout(cols, 1)

        left = QVBoxLayout()
        left.setSpacing(16)
        cols.addLayout(left, 1)

        # ---------------- 外观 ----------------
        look = Card(padding=20, spacing=14)
        lt = QLabel("外观")
        lt.setFont(T.font(14, QFont.Weight.DemiBold))
        lsub = QLabel("主题、卡片、边框与文字层级由统一令牌控制，点选立即生效并记住")
        lsub.setFont(T.font(11))
        lsub.setStyleSheet("color:%s;" % T.TEXT_3)
        look.lay.addWidget(lt)
        look.lay.addWidget(lsub)
        sw = QHBoxLayout()
        sw.setSpacing(12)
        self.swatches = {}
        for name, palette in T.PALETTES.items():
            widget = Swatch(name, palette["BG"], palette["SIDEBAR"],
                            name == T.current_palette())
            widget.clicked.connect(self._on_palette)
            self.swatches[name] = widget
            sw.addWidget(widget)
        sw.addStretch(1)
        look.lay.addLayout(sw)
        accent = QHBoxLayout()
        accent.setSpacing(10)
        al = QLabel("强调色")
        al.setFont(T.font(11))
        al.setStyleSheet("color:%s;" % T.TEXT_2)
        accent.addWidget(al)
        self.dots = {}
        for name, (main, _soft, _bg, _line) in T.ACCENTS.items():
            dot = AccentDot(name, main, name == T.current_accent())
            dot.clicked.connect(self._on_accent)
            self.dots[name] = dot
            lab = QLabel(name)
            lab.setFont(T.font(10))
            lab.setStyleSheet("color:%s;" % T.TEXT_3)
            accent.addWidget(dot)
            accent.addWidget(lab)
            accent.addSpacing(6)
        accent.addStretch(1)
        look.lay.addLayout(accent)
        left.addWidget(look)

        # ---------------- 动效 ----------------
        motion = Card(padding=20, spacing=10)
        mt = QLabel("动效")
        mt.setFont(T.font(14, QFont.Weight.DemiBold))
        msub = QLabel("点击任意一行可以开关对应动效，跟随系统「减弱动态效果」时自动降级")
        msub.setFont(T.font(11))
        msub.setStyleSheet("color:%s;" % T.TEXT_3)
        motion.lay.addWidget(mt)
        motion.lay.addWidget(msub)
        self.motion_rows = [
            MotionRow("page", "页面切换", "淡入 180 ms · OutCubic"),
            MotionRow("button", "按钮反馈", "缩放 0.97 · 110 ms"),
            MotionRow("number", "数字滚动", "260 ms 插值"),
            MotionRow("reduce_follow", "动效降级", "跟随系统「减弱动态效果」"),
        ]
        for row in self.motion_rows:
            motion.lay.addWidget(row)
        left.addWidget(motion)

        # ---------------- 快捷键（保持只读说明） ----------------
        keys = Card(padding=20, spacing=8)
        kt = QLabel("快捷键")
        kt.setFont(T.font(14, QFont.Weight.DemiBold))
        keys.lay.addWidget(kt)
        for k, v in (("导入素材", "Ctrl + I"), ("运行全流程", "Ctrl + R"),
                     ("暂停 / 继续", "Space"), ("切换页面", "Ctrl + 1 … 5"),
                     ("预览全屏", "P / Esc")):
            keys.lay.addWidget(InfoRow(k, v))
        left.addWidget(keys)
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(16)
        right_wrap = QWidget()
        right_wrap.setFixedWidth(360)
        right_wrap.setLayout(right)
        cols.addWidget(right_wrap)

        # ---------------- 运行环境 ----------------
        run = Card(padding=20, spacing=10)
        rt = QLabel("运行环境")
        rt.setFont(T.font(14, QFont.Weight.DemiBold))
        run.lay.addWidget(rt)
        self.row_gpu = InfoRow("渲染设备", "检测中…")
        self.row_res = InfoRow("预览分辨率", "—")
        self.row_color = InfoRow("色彩空间", "—")
        self.row_draft = InfoRow("草稿目录", "检测中…", tooltip="点击选择草稿目录")
        self.row_draft.setCursor(Qt.CursorShape.PointingHandCursor)
        self.row_draft.mousePressEvent = self._pick_draft_dir
        for row in (self.row_gpu, self.row_res, self.row_color, self.row_draft):
            run.lay.addWidget(row)
        right.addWidget(run)

        # ---------------- 缓存与草稿 ----------------
        store = Card(padding=20, spacing=8)
        st = QLabel("缓存与草稿")
        st.setFont(T.font(14, QFont.Weight.DemiBold))
        store.lay.addWidget(st)
        self.row_drafts = InfoRow("草稿数量", "—")
        self.row_cache = InfoRow("缓存占用", "—")
        self.row_policy = InfoRow("清理策略", "保留最近 %d 天（上限 14 天）" % self._clean_days,
                                  tooltip="点击切换 3 / 7 / 14 天")
        self.row_policy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.row_policy.mousePressEvent = self._cycle_policy
        for row in (self.row_drafts, self.row_cache, self.row_policy):
            store.lay.addWidget(row)
        eng = QHBoxLayout()
        eng.setSpacing(6)
        self.btn_engine_refresh = SoftButton("刷新状态", "ghost", height=28, radius=7)
        self.btn_engine_cache = SoftButton("打开缓存目录", "ghost", height=28, radius=7)
        for b in (self.btn_engine_refresh, self.btn_engine_cache):
            eng.addWidget(b)
        eng.addStretch(1)
        store.lay.addLayout(eng)

        docs = QHBoxLayout()
        docs.setSpacing(6)
        self.btn_user_manual = SoftButton("用户手册", "secondary", height=30, radius=7)
        self.btn_user_manual.setToolTip("打开完整功能参考：页面、参数、代理、故障排查")
        self.btn_operation_guide = SoftButton("操作指南", "secondary", height=30, radius=7)
        self.btn_operation_guide.setToolTip("打开从导入到出片的快速操作步骤")
        docs.addWidget(self.btn_user_manual)
        docs.addWidget(self.btn_operation_guide)
        docs.addStretch(1)
        store.lay.addLayout(docs)

        eng2 = QHBoxLayout()
        eng2.setSpacing(6)
        self.btn_engine_reload = SoftButton("重新加载模型", "ghost", height=28, radius=7)
        eng2.addWidget(self.btn_engine_reload)
        eng2.addStretch(1)
        store.lay.addLayout(eng2)
        right.addWidget(store)
        right.addStretch(1)

        self.btn_new.clicked.connect(self.win.new_project)
        self.btn_open.clicked.connect(self._open_project)
        self.btn_save.clicked.connect(self._save_project)
        self.btn_recheck.clicked.connect(self._recheck)
        self.btn_engine_refresh.clicked.connect(self._engine_refresh)
        self.btn_engine_cache.clicked.connect(self._engine_cache)
        self.btn_engine_reload.clicked.connect(self._engine_reload)
        self.btn_user_manual.clicked.connect(self._open_user_manual)
        self.btn_operation_guide.clicked.connect(self._open_operation_guide)

        self.refresh_env()

    # -----------------------------------------------------
    # 外观
    # -----------------------------------------------------
    def _on_palette(self, name):
        data = T.set_variant(palette=name)
        self._data = data
        for key, widget in self.swatches.items():
            widget.set_active(key == name)
        T.apply_tokens_to_app(QApplication.instance())
        self.win.controller.log("设置 · 主题已切换：%s" % name)

    def _on_accent(self, name):
        data = T.set_variant(accent=name)
        self._data = data
        for key, widget in self.dots.items():
            widget.set_active(key == name)
        T.apply_tokens_to_app(QApplication.instance())
        self.win.controller.log("设置 · 强调色已切换：%s" % name)

    # -----------------------------------------------------
    # 运行环境 / 缓存
    # -----------------------------------------------------
    def on_shown(self):
        self.refresh_env()

    def refresh_env(self):
        self.row_gpu.set_value(_gpu_info())
        try:
            width, height, _fps = self.win.demo_params.canvas_size()
            self.row_res.set_value("%d × %d" % (width, height))
        except Exception:
            self.row_res.set_value("读取失败")
        depth = 8
        try:
            from . import mock
            depths = [c.get("bit_depth") for c in mock.CLIPS if c.get("bit_depth")]
            depth = max(depths) if depths else 8
        except Exception:
            pass
        self.row_color.set_value("sRGB · %d-bit" % depth)

        draft = self._data.get("draft_dir") or app_paths.detect_draft_dir()
        if draft:
            data = prefs.data()
            if data.get("draft_dir") != draft:
                data["draft_dir"] = draft
                prefs.save(data)
            self._data = data
        self.row_draft.set_value(self._short_path(draft) if draft else "未检测到，点击选择")
        self.row_drafts.set_value("%d 个" % _count_drafts(draft))

        cache = app_paths.cache_dir()
        self.row_cache.set_value("%s · %s" % (_human_size(_dir_size(cache)),
                                              self._short_path(str(cache))))
        self.row_policy.set_value("保留最近 %d 天（上限 14 天）" % self._clean_days)

    def _short_path(self, path):
        if not path:
            return ""
        if len(path) <= 34:
            return path
        return path[:12] + "…" + path[-18:]

    def _pick_draft_dir(self, _event=None):
        start = self._data.get("draft_dir") or app_paths.detect_draft_dir() or ""
        path = QFileDialog.getExistingDirectory(self, "选择剪映草稿目录", start)
        if not path:
            return
        data = prefs.data()
        data["draft_dir"] = path
        prefs.save(data)
        self._data = data
        self.refresh_env()
        self.win.controller.log("设置 · 草稿目录：%s" % path)

    def _cycle_policy(self, _event=None):
        # 3 → 7 → 14 → 7 → 3，上限固定 14 天
        if self._clean_days >= 14:
            self._clean_days = 7
        elif self._clean_days >= 7:
            self._clean_days = 14
        else:
            self._clean_days = 7
        data = prefs.data()
        data["clean_days"] = self._clean_days
        prefs.save(data)
        self._data = data
        self._purge_cache()
        self.refresh_env()
        self.win.controller.log("设置 · 清理策略改为保留 %d 天" % self._clean_days)

    def _purge_cache(self):
        """按清理策略删掉过期缓存（只动 cache 目录里的文件，不碰素材/草稿）。"""
        cache = app_paths.cache_dir()
        limit = time.time() - self._clean_days * 86400
        removed = 0
        try:
            for root, _dirs, files in os.walk(cache):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        if os.path.getmtime(path) < limit:
                            os.remove(path)
                            removed += 1
                    except OSError:
                        continue
        except OSError:
            pass
        if removed:
            self.win.controller.log("设置 · 清理缓存 %d 个文件（超过 %d 天）"
                                    % (removed, self._clean_days))
        return removed

    # -----------------------------------------------------
    # 工程 / 引擎
    # -----------------------------------------------------
    def _open_project(self):
        b = self.win.backend
        if b.load_error and not b.ready:
            self.win.controller.log("设置 · 后台桥不可用，打开工程未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", getattr(self.win, "_project_path", "") or "",
            "MixCut 工程 (*.mixcut)")
        if not path:
            return
        if b.open_project_from(path):
            self.win.controller.log("设置 · 已打开工程：%s" % path)
        else:
            self.win.controller.log("设置 · 打开工程失败：%s" % path)

    def _save_project(self):
        b = self.win.backend
        if b.load_error and not b.ready:
            self.win.controller.log("设置 · 后台桥不可用，保存工程未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        current = getattr(self.win, "_project_path", "") or ""
        path, _ = QFileDialog.getSaveFileName(
            self, "保存工程", current, "MixCut 工程 (*.mixcut)")
        if not path:
            return
        if not path.lower().endswith(".mixcut"):
            path += ".mixcut"
        if b.save_project_to(path):
            self.win.controller.log("设置 · 工程已保存：%s" % path)
        else:
            self.win.controller.log("设置 · 工程保存失败：%s" % path)

    def _engine_refresh(self):
        self._purge_cache()
        self.refresh_env()
        b = self.win.backend
        if b.ready or not b.load_error:
            text = b.refresh_engine()
            self.win.controller.log("MIX 引擎 · 刷新状态：%s" %
                                    (text.splitlines()[0] if text else "后台无返回"))
            return
        self.win.controller.log("MIX 引擎 · 刷新状态未执行：" +
                                b.load_error.splitlines()[-1])

    def _open_user_manual(self):
        self._open_local_doc("user_manual.html", "用户手册")

    def _open_operation_guide(self):
        self._open_local_doc("operation_guide.html", "操作指南")

    def _open_local_doc(self, filename, title):
        roots = (app_paths.resource_dir(), app_paths.app_dir())
        for root in roots:
            path = root / "docs" / filename
            if path.is_file():
                ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                self.win.controller.log(
                    "设置 · %s：%s" % (title, "已打开" if ok else "打开失败"))
                if not ok:
                    self.win.controller.log("设置 · 文档路径：%s" % path)
                return
        self.win.controller.log("设置 · 未找到「%s」，请重装或检查 docs 目录" % title)

    def _engine_cache(self):
        path = app_paths.cache_dir()
        try:
            os.startfile(str(path))
        except OSError as exc:
            self.win.controller.log("MIX 引擎 · 打开缓存目录失败：%s" % exc)

    def _engine_reload(self):
        b = self.win.backend
        if b.load_error and not b.ready:
            self.win.controller.log("MIX 引擎 · 重新加载未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        ok = b.reload_engine()
        self.win.controller.log("MIX 引擎 · 重新加载模型：%s" %
                                ("已调用后台" if ok else "后台未返回"))

    def _recheck(self):
        b = self.win.backend
        self.refresh_env()
        if b.load_error and not b.ready:
            self.win.controller.log("设置 · 重新检测未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        ok = b.force_self_check(show_dialog=False)
        self.win.controller.log("设置 · 重新检测：%s" %
                                ("后台组件就绪" if ok else "后台检测失败"))
