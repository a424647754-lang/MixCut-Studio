"""MixCut Editor — 设计令牌与全局主题。

设计参考：DaVinci Resolve（视觉）+ Premiere Pro（交互逻辑）。
原则：扁平、极简、精密、工业感。
禁止：渐变、大面积圆角、玻璃拟态、装饰性阴影。

所有颜色/尺寸集中在此模块，其它 UI 模块只引用常量，不写字面值。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen

APP_NAME = "MixCut Editor"
APP_NAME_EN = "MixCut"
APP_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
BG_APP = "#0D0D0F"          # 应用最底层
BG_PANEL = "#151518"        # 面板底色
BG_PANEL_ALT = "#1A1A1E"    # 交替行 / 次级面板
BG_RAISED = "#1F1F24"       # 悬浮控件（按钮）
BG_INPUT = "#0F0F12"        # 输入类控件
BG_VIEWER = "#08080A"       # 播放器画布

BORDER = "#2A2A30"          # 常规分隔线
BORDER_STRONG = "#3A3A42"   # 焦点 / 强调分隔
BORDER_SUBTLE = "#202026"   # 极弱分隔（行间）

TEXT = "#D6D6DB"            # 主文字
TEXT_DIM = "#8A8A94"        # 次文字
TEXT_MUTED = "#5C5C66"      # 禁用 / 提示
TEXT_STRONG = "#F0F0F4"     # 强调文字

ACCENT = "#3B82F6"          # 主强调（选择、播放头之外）
ACCENT_HOVER = "#4C8FF8"
ACCENT_DIM = "#2B5FB8"
ACCENT_SOFT = "#1C3255"     # 选中背景

WARN = "#D98E3A"
ERROR = "#D9534F"
OK = "#4CAF7D"

# 时间线专用
TL_BG = "#0B0B0D"
TL_LANE = "#141418"
TL_LANE_ALT = "#111114"
TL_HEADER = "#17171B"
TL_RULER = "#1A1A1F"
TL_CLIP_VIDEO = "#25456E"
TL_CLIP_VIDEO_SEL = "#3B6EA8"
TL_CLIP_AUDIO = "#1F5642"
TL_CLIP_AUDIO_SEL = "#2C7A5E"
TL_PLAYHEAD = "#E5484D"
TL_WAVE = "#5FBE8F"
TL_MARKER = "#D98E3A"
TL_GRID = "#1E1E24"

# ---------------------------------------------------------------------------
# 尺寸（像素）
# ---------------------------------------------------------------------------
MENUBAR_HEIGHT = 48
STATUSBAR_HEIGHT = 24
PAGEBAR_HEIGHT = 46           # 页面切换条（媒体/剪辑/调色/导出）
PANEL_LEFT_MIN = 260
PANEL_LEFT_MAX = 320
PANEL_RIGHT_MIN = 280
PANEL_RIGHT_MAX = 320
MEDIA_PANEL_WIDTH = 344       # 编辑页媒体池
INSPECTOR_WIDTH = 318         # 剪辑页 Inspector
GRADE_PANEL_WIDTH = 306       # 调色页调色板
CLIP_STRIP_WIDTH = 208        # 调色页左侧片段条
TIMELINE_RATIO = 0.40        # 时间线占窗口高度比例
PANEL_HEADER_HEIGHT = 32
TRANSPORT_HEIGHT = 46
TOOLBAR_HEIGHT = 36
RADIUS = 2                   # 极克制的圆角
BORDER_W = 1

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------
FONT_UI_FAMILIES = ["Microsoft YaHei UI", "Segoe UI", "Inter", "Noto Sans SC", "sans-serif"]
FONT_MONO_FAMILIES = ["Consolas", "Cascadia Mono", "JetBrains Mono", "Menlo", "monospace"]

FONT_SIZE_BASE = 9.0
FONT_SIZE_SMALL = 8.0
FONT_SIZE_TITLE = 10.0
FONT_SIZE_HEADLINE = 13.0


def ui_font(size: float = FONT_SIZE_BASE, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(resolve_families(FONT_UI_FAMILIES))
    font.setPointSizeF(size)
    font.setBold(bold)
    return font


def mono_font(size: float = FONT_SIZE_BASE, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(resolve_families(FONT_MONO_FAMILIES))
    font.setPointSizeF(size)
    font.setBold(bold)
    return font


_family_cache: dict[str, list[str]] = {}


def resolve_families(candidates: list[str]) -> list[str]:
    """从候选列表里挑出系统真实存在的字体，避免出现方框字。

    未安装首选字体（例如非中文 Windows 没有「Microsoft YaHei UI」）时，
    保留候选列表交给 Qt 做系统回退，而不是硬编码一个不存在的名字。
    """
    key = "|".join(candidates)
    cached = _family_cache.get(key)
    if cached is not None:
        return cached

    try:
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            return candidates
        available = set(QFontDatabase.families())
    except Exception:  # noqa: BLE001 - 取不到字体库时不影响启动
        return candidates

    chosen = [name for name in candidates if name in available]
    result = chosen or candidates
    _family_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# 样式表
# ---------------------------------------------------------------------------
def stylesheet() -> str:
    return f"""
/* ===== 基础 ===== */
QMainWindow, QDialog {{
    background: {BG_APP};
}}
QWidget {{
    background: transparent;
    color: {TEXT};
    font-size: {FONT_SIZE_BASE}pt;
}}
QWidget#PanelRoot, QWidget#PanelBody {{
    background: {BG_PANEL};
}}
QWidget#ViewerRoot {{
    background: {BG_VIEWER};
}}
QWidget#TransportBar {{
    background: {BG_PANEL_ALT};
    border-top: {BORDER_W}px solid {BORDER};
}}
QWidget#PanelHeader {{
    background: {BG_PANEL_ALT};
    border-bottom: {BORDER_W}px solid {BORDER};
}}
QLabel#PanelTitle {{
    color: {TEXT_DIM};
    font-size: {FONT_SIZE_SMALL}pt;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#Muted {{
    color: {TEXT_MUTED};
    font-size: {FONT_SIZE_SMALL}pt;
}}
QLabel#FieldLabel {{
    color: {TEXT_DIM};
    font-size: {FONT_SIZE_SMALL}pt;
}}
QLabel#Timecode {{
    color: {TEXT_STRONG};
    font-family: {", ".join(resolve_families(FONT_MONO_FAMILIES))};
    font-size: {FONT_SIZE_TITLE}pt;
    font-weight: 600;
}}
QLabel#SectionTitle {{
    color: {TEXT_DIM};
    font-size: {FONT_SIZE_SMALL}pt;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 6px 0 4px 0;
}}
QToolTip {{
    background: {BG_RAISED};
    color: {TEXT};
    border: {BORDER_W}px solid {BORDER_STRONG};
    padding: 4px 6px;
}}

/* ===== 菜单栏（高度由 MainWindow 用 setFixedHeight 精确锁定）===== */
QMenuBar {{
    background: {BG_PANEL};
    border-bottom: {BORDER_W}px solid {BORDER};
    padding: 0 8px;
    spacing: 2px;
}}
QMenuBar::item {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 6px 12px;
    margin: 8px 1px;
    border-radius: {RADIUS}px;
}}
QMenuBar::item:selected {{
    background: {BG_RAISED};
    color: {TEXT_STRONG};
}}
QMenuBar::item:pressed {{
    background: {ACCENT_SOFT};
    color: {TEXT_STRONG};
}}
QMenu {{
    background: {BG_PANEL_ALT};
    border: {BORDER_W}px solid {BORDER_STRONG};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 28px 6px 20px;
    color: {TEXT};
    border-radius: {RADIUS}px;
}}
QMenu::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT_STRONG};
}}
QMenu::item:disabled {{
    color: {TEXT_MUTED};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 8px;
}}

/* ===== 停靠面板 ===== */
QDockWidget {{
    color: {TEXT};
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background: {BG_PANEL_ALT};
    min-height: 28px;
    padding: 0px 8px 0px 10px;
    border-bottom: {BORDER_W}px solid {BORDER};
    border-left: 2px solid {ACCENT};
    color: {TEXT_DIM};
    font-size: {FONT_SIZE_SMALL}pt;
    font-weight: 600;
    letter-spacing: 1px;
    text-align: left;
}}
QDockWidget::close-button, QDockWidget::float-button {{
    background: transparent;
    border: 0;
    width: 14px;
    height: 14px;
}}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{
    background: {BG_RAISED};
}}
QMainWindow::separator {{
    background: {BG_APP};
    width: 4px;
    height: 4px;
}}
QMainWindow::separator:hover {{
    background: {ACCENT_DIM};
}}

/* ===== 按钮 ===== */
QPushButton {{
    background: {BG_RAISED};
    color: {TEXT};
    border: {BORDER_W}px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 5px 12px;
    min-height: 18px;
}}
QPushButton:hover {{
    background: {BG_PANEL_ALT};
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed {{
    background: {ACCENT_SOFT};
    border-color: {ACCENT_DIM};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background: {BG_PANEL};
    border-color: {BORDER_SUBTLE};
}}
QPushButton#Primary {{
    background: {ACCENT_DIM};
    border-color: {ACCENT};
    color: {TEXT_STRONG};
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {ACCENT};
}}
QPushButton#Flat {{
    background: transparent;
    border: {BORDER_W}px solid transparent;
    padding: 4px 8px;
    color: {TEXT_DIM};
}}
QPushButton#Flat:hover {{
    background: {BG_RAISED};
    color: {TEXT_STRONG};
}}
QPushButton#Danger {{
    color: {ERROR};
    border-color: {BORDER};
}}
QPushButton#Danger:hover {{
    background: #2A1717;
    border-color: {ERROR};
}}
QToolButton {{
    background: transparent;
    border: {BORDER_W}px solid transparent;
    border-radius: {RADIUS}px;
    padding: 4px 6px;
    color: {TEXT_DIM};
}}
QToolButton:hover {{
    background: {BG_RAISED};
    color: {TEXT_STRONG};
}}
QToolButton:checked {{
    background: {ACCENT_SOFT};
    color: {TEXT_STRONG};
    border-color: {ACCENT_DIM};
}}

/* ===== 输入控件 ===== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {BG_INPUT};
    color: {TEXT};
    border: {BORDER_W}px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 4px 6px;
    selection-background-color: {ACCENT_DIM};
    selection-color: {TEXT_STRONG};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT_DIM};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    color: {TEXT_MUTED};
}}
QComboBox::drop-down {{
    border: 0;
    width: 16px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {TEXT_DIM};
    margin-right: 4px;
}}
QComboBox QAbstractItemView {{
    background: {BG_PANEL_ALT};
    border: {BORDER_W}px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT_STRONG};
    outline: 0;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {BG_PANEL_ALT};
    border: 0;
    width: 12px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {BORDER};
}}
QCheckBox {{
    spacing: 6px;
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 12px;
    height: 12px;
    border: {BORDER_W}px solid {BORDER_STRONG};
    border-radius: 1px;
    background: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QRadioButton {{
    spacing: 6px;
}}
QRadioButton::indicator {{
    width: 12px;
    height: 12px;
    border: {BORDER_W}px solid {BORDER_STRONG};
    border-radius: 6px;
    background: {BG_INPUT};
}}
QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

/* ===== 列表 / 树 / 表格 ===== */
QListWidget, QTreeWidget, QTreeView, QListView, QTableWidget, QTableView {{
    background: {BG_PANEL};
    border: 0;
    border-top: {BORDER_W}px solid {BORDER_SUBTLE};
    outline: 0;
    padding: 4px;
    alternate-background-color: {BG_PANEL_ALT};
}}
QListWidget::item, QTreeWidget::item, QTreeView::item, QTableWidget::item {{
    padding: 6px 8px;
    border-radius: {RADIUS}px;
    color: {TEXT};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {BG_PANEL_ALT};
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected,
QTableWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT_STRONG};
}}
QHeaderView::section {{
    background: {BG_PANEL_ALT};
    color: {TEXT_DIM};
    border: 0;
    border-bottom: {BORDER_W}px solid {BORDER};
    padding: 6px 8px;
    font-size: {FONT_SIZE_SMALL}pt;
    font-weight: 600;
}}
QTableWidget {{
    gridline-color: {BORDER_SUBTLE};
}}

/* ===== 滚动条 ===== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    min-height: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG};
    min-width: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ===== 进度条 ===== */
QProgressBar {{
    background: {BG_INPUT};
    border: {BORDER_W}px solid {BORDER};
    border-radius: {RADIUS}px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 1px;
}}

/* ===== 滑块 ===== */
QSlider::groove:horizontal {{
    height: 3px;
    background: {BORDER};
    border-radius: 1px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT_DIM};
    border-radius: 1px;
}}
QSlider::handle:horizontal {{
    width: 10px;
    height: 10px;
    margin: -4px 0;
    border-radius: 5px;
    background: {TEXT_DIM};
}}
QSlider::handle:horizontal:hover {{
    background: {TEXT_STRONG};
}}

/* ===== 状态栏（高度由 MainWindow 用 setFixedHeight 精确锁定）===== */
QStatusBar {{
    background: {BG_PANEL};
    border-top: {BORDER_W}px solid {BORDER};
    color: {TEXT_DIM};
    font-size: {FONT_SIZE_SMALL}pt;
}}
QStatusBar::item {{
    border: 0;
}}
QStatusBar QLabel {{
    color: {TEXT_DIM};
    padding: 0 8px;
    font-size: {FONT_SIZE_SMALL}pt;
}}

/* ===== 分栏 ===== */
QSplitter::handle {{
    background: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}
QSplitter::handle:hover {{
    background: {ACCENT_DIM};
}}

/* ===== 选项卡 ===== */
QTabWidget::pane {{
    border: 0;
    border-top: {BORDER_W}px solid {BORDER};
    background: {BG_PANEL};
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 7px 16px;
    margin-right: 2px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {TEXT_STRONG};
    border-bottom-color: {ACCENT};
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}

/* ===== 分组框 ===== */
QGroupBox {{
    border: {BORDER_W}px solid {BORDER};
    border-radius: {RADIUS}px;
    margin-top: 14px;
    padding: 8px 6px 6px 6px;
    color: {TEXT_DIM};
    font-size: {FONT_SIZE_SMALL}pt;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {TEXT_DIM};
}}

/* ===== 工具栏 ===== */
QToolBar {{
    background: {BG_PANEL};
    border: 0;
    border-bottom: {BORDER_W}px solid {BORDER};
    spacing: 4px;
    padding: 2px 6px;
    min-height: {TOOLBAR_HEIGHT}px;
}}
QToolBar::separator {{
    background: {BORDER};
    width: 1px;
    margin: 6px 6px;
}}

/* ===== 页面切换条（达芬奇式页面标签）===== */
QWidget#PageBar {{
    background: {BG_PANEL};
    border-bottom: {BORDER_W}px solid {BORDER};
}}
QPushButton#Page {{
    background: transparent;
    border: 0;
    border-bottom: 2px solid transparent;
    color: {TEXT_MUTED};
    padding: 0 18px;
    font-size: {FONT_SIZE_TITLE}pt;
    font-weight: 600;
    letter-spacing: 1px;
}}
QPushButton#Page:hover {{
    color: {TEXT};
    background: {BG_PANEL_ALT};
}}
QPushButton#Page:checked {{
    color: {TEXT_STRONG};
    border-bottom-color: {ACCENT};
    background: {BG_PANEL_ALT};
}}
QLabel#PageHint {{
    color: {TEXT_MUTED};
    font-size: {FONT_SIZE_SMALL}pt;
    padding-right: 8px;
}}

/* ===== 时间线工具条 ===== */
QWidget#TimelineToolbar {{
    background: {BG_PANEL_ALT};
    border-top: {BORDER_W}px solid {BORDER};
    border-bottom: {BORDER_W}px solid {BORDER};
}}
QPushButton#Chip {{
    background: transparent;
    border: {BORDER_W}px solid {BORDER};
    color: {TEXT_DIM};
    padding: 3px 9px;
    font-size: {FONT_SIZE_SMALL}pt;
}}
QPushButton#Chip:hover {{
    color: {TEXT_STRONG};
    border-color: {BORDER_STRONG};
}}
QPushButton#Chip:checked {{
    background: {ACCENT_SOFT};
    border-color: {ACCENT_DIM};
    color: {TEXT_STRONG};
}}

/* 小图标按钮：源监视器底部、面板操作区共用 */
QPushButton#IconButton {{
    background: {BG_RAISED};
    border: {BORDER_W}px solid {BORDER};
    padding: 0;
}}
QPushButton#IconButton:hover {{
    background: {BG_PANEL_ALT};
    border-color: {BORDER_STRONG};
}}
QPushButton#IconButton:pressed {{
    background: {ACCENT_SOFT};
}}
QPushButton#IconButton:disabled {{
    background: {BG_PANEL};
    border-color: {BORDER_SUBTLE};
}}

/* ===== 调色板 ===== */
QWidget#GradePanel {{
    background: {BG_PANEL};
    border-left: {BORDER_W}px solid {BORDER};
}}
QLabel#GradeValue {{
    color: {TEXT_STRONG};
    font-family: {", ".join(resolve_families(FONT_MONO_FAMILIES))};
    font-size: {FONT_SIZE_SMALL}pt;
}}
QSlider#GradeSlider::groove:horizontal {{
    height: 4px;
    background: {BG_INPUT};
    border: {BORDER_W}px solid {BORDER};
    border-radius: 1px;
}}
QSlider#GradeSlider::sub-page:horizontal {{
    background: {ACCENT_DIM};
}}
QSlider#GradeSlider::handle:horizontal {{
    width: 8px;
    height: 14px;
    margin: -6px 0;
    border-radius: 1px;
    background: {TEXT};
}}
QListWidget#ClipStrip {{
    background: {TL_BG};
    border-right: {BORDER_W}px solid {BORDER};
}}
"""


def apply_theme(app) -> None:
    """应用全局主题：调色板 + 字体 + 样式表。"""
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG_APP))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(BG_INPUT))
    palette.setColor(QPalette.AlternateBase, QColor(BG_PANEL_ALT))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(BG_RAISED))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT_DIM))
    palette.setColor(QPalette.HighlightedText, QColor(TEXT_STRONG))
    palette.setColor(QPalette.ToolTipBase, QColor(BG_RAISED))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.PlaceholderText, QColor(TEXT_MUTED))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_MUTED))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_MUTED))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(TEXT_MUTED))
    app.setPalette(palette)
    app.setFont(ui_font(FONT_SIZE_BASE))
    app.setStyleSheet(stylesheet())


# ---------------------------------------------------------------------------
# 程序化 Logo（无位图资源依赖）
# ---------------------------------------------------------------------------
def paint_logo(painter: QPainter, rect: QRectF, color: str = ACCENT,
               secondary: str = TEXT_DIM) -> None:
    """绘制 MixCut 标识：方形取景框 + 播放三角 + 左侧胶片孔。

    纯几何、无渐变，符合工业极简风格。
    """
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)

    side = min(rect.width(), rect.height())
    x = rect.center().x() - side / 2
    y = rect.center().y() - side / 2
    box = QRectF(x, y, side, side)

    stroke = max(1.0, side * 0.055)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(color), stroke))
    painter.drawRect(box)

    # 左侧胶片孔：三个短竖条
    hole_w = side * 0.055
    hole_h = side * 0.13
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(secondary))
    for index in range(3):
        top = y + side * (0.20 + index * 0.23)
        painter.drawRect(QRectF(x + side * 0.10, top, hole_w, hole_h))

    # 播放三角
    triangle = [
        (x + side * 0.36, y + side * 0.28),
        (x + side * 0.36, y + side * 0.72),
        (x + side * 0.76, y + side * 0.50),
    ]
    from PySide6.QtGui import QPolygonF
    from PySide6.QtCore import QPointF

    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawPolygon(QPolygonF([QPointF(px, py) for px, py in triangle]))

    painter.restore()


def paint_panel_header(painter: QPainter, rect: QRect, title: str) -> None:
    """面板标题栏：左侧 2px 强调竖线 + 大写标题。"""
    painter.save()
    painter.fillRect(rect, QColor(BG_PANEL_ALT))
    painter.fillRect(QRect(rect.left(), rect.top(), 2, rect.height()), QColor(ACCENT))
    painter.setPen(QColor(TEXT_DIM))
    painter.setFont(ui_font(FONT_SIZE_SMALL, bold=True))
    painter.drawText(rect.adjusted(12, 0, -8, 0), Qt.AlignLeft | Qt.AlignVCenter, title.upper())
    painter.restore()
