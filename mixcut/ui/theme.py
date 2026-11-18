# -*- coding: utf-8 -*-
"""
MixCut · 界面主题

设计方向：DaVinci Resolve 风格专业黑白灰视频工作站。
只负责颜色、字号、间距、圆角与全局 QSS，不含任何业务逻辑。
"""

# ---------------------------------------------------------
# 颜色（MixCut Studio demo 风格：深灰底 + 低饱和紫强调）
# ---------------------------------------------------------
COLORS = {
    "bg": "#141519",            # 主背景
    "bg_deep": "#101114",
    "sidebar": "#1B1C21",       # 侧边栏
    "panel": "#1E2026",         # 一级面板
    "panel_alt": "#24262D",     # 二级面板
    "inset": "#16171B",
    "hover": "#26282F",         # 悬停
    "selected": "#2A2540",      # 选中
    "selected_hi": "#332B50",
    "text": "#F4F5F7",          # 主要文字
    "muted": "#A7AAB4",         # 次级文字
    "faint": "#71757F",         # 弱化文字
    "border": "#2B2D35",        # 边框
    "border_soft": "#24262C",   # 更淡的分隔线
    "border_hi": "#3A3D47",
    "accent": "#FFFFFF",        # 主要按钮：白
    "accent_hover": "#E9EAF0",
    "accent_dim": "#4A4E55",
    "focus": "#A78BFA",         # 焦点 / 选中强调
    "focus_soft": "#8B7CF6",
    "accent_bg": "#2A2540",
    "success": "#63D6A0",
    "warning": "#E7B06B",
    "danger": "#E58B84",
    "info": "#79A9FF",
}

# ---------------------------------------------------------
# 尺寸 / 字体 / 动效
# ---------------------------------------------------------
# QSS 里的 "Microsoft YaHei UI" 解析不到真实字族（中文会显示方框），
# 统一用 "Microsoft YaHei"，demo 页面也改成同一字族，保证新旧页面一致。
FONT_FAMILY = "Microsoft YaHei"
FONT_FALLBACK = "Microsoft YaHei"
FONT_MONO = "Cascadia Mono"
FONT_SIZE = 13
FONT_SIZE_SMALL = 12
FONT_SIZE_TITLE = 15
FONT_SIZE_HERO = 30

NAV_WIDTH = 200
NAV_WIDTH_COLLAPSED = 64
TOPBAR_HEIGHT = 56
PANEL_HEIGHT = 180
RADIUS = 10
RADIUS_CARD = 14
GAP = 14

# 动效时长（毫秒），只用于轻量过渡，不参与后台任务
ANIM_PAGE = 160
ANIM_HOVER = 140
ANIM_PANEL = 180


def _preferred_font_family():
    """返回当前系统可用的首选中文界面字体。

    说明（重要）：本项目**不再自带字体文件**。
    · 微软雅黑是微软的商业字体，不适合随开源仓库分发；
    · 界面一直使用的是系统字体 ``Microsoft YaHei``（见 FONT_FAMILY），
      以前那个 ``fonts/msyh.ttf`` 与加载函数其实从未被调用过；
    · 若某些精简系统缺少雅黑，这里会依次回退到其它常见中文字体。
    """
    from PySide6.QtGui import QFontDatabase
    candidates = [FONT_FAMILY, "Microsoft YaHei UI", "Microsoft YaHei",
                  "PingFang SC", "Source Han Sans SC", "Noto Sans CJK SC",
                  "SimHei", "SimSun", FONT_FALLBACK]
    try:
        available = set(QFontDatabase.families())
    except Exception:
        return FONT_FAMILY
    for name in candidates:
        if name and name in available:
            return name
    return FONT_FAMILY


def apply(app):
    """把主题 QSS 应用到整个应用。"""
    # demo 风格优先使用系统 Segoe UI Variable Display；
    # 中文自动回退到 Microsoft YaHei UI，不依赖随包字体文件。
    app.setStyleSheet(build_qss())
    family = _preferred_font_family()
    font = app.font()
    font.setFamilies([family, "Microsoft YaHei UI", FONT_FALLBACK])
    font.setPointSize(FONT_SIZE)
    app.setFont(font)


def build_qss():
    c = COLORS
    return f"""
    * {{
        font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}";
        font-size: {FONT_SIZE}px;
        outline: none;
    }}

    QMainWindow, QWidget#Root {{
        background: {c['bg']};
        color: {c['text']};
    }}

    /* ---------- 面板 / 卡片 ---------- */
    QFrame#Panel, QWidget#Panel {{
        background: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: {RADIUS_CARD}px;
    }}
    QFrame#Card {{
        background: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS_CARD}px;
    }}
    QLabel#Muted {{ color: {c['muted']}; }}
    QLabel#Faint {{ color: {c['faint']}; }}
    QLabel#Title {{ color: {c['text']}; font-size: {FONT_SIZE_TITLE}px; font-weight: 600; }}
    QLabel#Hero {{ color: {c['text']}; font-size: {FONT_SIZE_HERO}px; font-weight: 700; }}
    QLabel#SectionTitle {{ color: {c['muted']}; font-size: {FONT_SIZE_SMALL}px; font-weight: 600; }}

    /* ---------- 左侧导航 ---------- */
    QFrame#Sidebar {{
        background: {c['sidebar']};
        border-right: 1px solid {c['border_soft']};
    }}
    QPushButton#NavItem {{
        background: transparent;
        border: none;
        border-radius: {RADIUS}px;
        color: {c['muted']};
        text-align: left;
        padding: 10px 12px;
        font-size: {FONT_SIZE}px;
    }}
    QPushButton#NavItem:hover {{
        background: {c['hover']};
        color: {c['text']};
    }}
    QPushButton#NavItem:checked {{
        background: {c['selected']};
        color: {c['text']};
        font-weight: 600;
        border-left: 2px solid {c['focus']};
    }}

    /* ---------- 顶部工具栏 ---------- */
    QFrame#TopBar {{
        background: {c['bg']};
        border-bottom: 1px solid {c['border_soft']};
    }}

    /* ---------- 按钮 ---------- */
    QPushButton {{
        background: {c['panel_alt']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS}px;
        color: {c['text']};
        padding: 7px 14px;
    }}
    QPushButton:hover {{ background: {c['hover']}; border-color: {c['border_hi']}; }}
    QPushButton:pressed {{ background: {c['selected']}; }}
    QPushButton:disabled {{
        background: {c['panel']};
        color: {c['faint']};
        border-color: {c['border_soft']};
    }}
    QPushButton#Primary {{
        background: {c['text']};
        border: none;
        color: {c['bg_deep']};
        font-weight: 600;
        padding: 8px 18px;
    }}
    QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
    QPushButton#Primary:disabled {{ background: {c['accent_dim']}; color: {c['muted']}; }}
    QPushButton#Danger {{ color: {c['danger']}; }}
    QPushButton#Danger:hover {{ border-color: {c['danger']}; }}
    QPushButton#Ghost {{
        background: transparent;
        border: 1px solid {c['border']};
        color: {c['muted']};
    }}
    QPushButton#Ghost:hover {{ color: {c['text']}; border-color: {c['muted']}; }}

    /* ---------- 输入 ---------- */
    QLineEdit, QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget,
    QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c['inset']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS}px;
        color: {c['text']};
        padding: 6px 9px;
        selection-background-color: {c['focus']};
        selection-color: {c['bg_deep']};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {c['focus']};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox::down-arrow {{
        image: none;
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background: {c['panel']};
        border: 1px solid {c['border']};
        selection-background-color: {c['selected']};
        outline: none;
    }}

    /* ---------- 列表 / 树 ---------- */
    QTreeWidget, QListWidget {{ background: {c['panel']}; border: 1px solid {c['border_soft']}; }}
    QTreeWidget::item, QListWidget::item {{
        padding: 5px 4px;
        border-radius: 2px;
    }}
    QTreeWidget::item:hover, QListWidget::item:hover {{ background: {c['hover']}; }}
    QTreeWidget::item:selected, QListWidget::item:selected {{
        background: {c['selected']};
        color: {c['text']};
    }}
    QHeaderView::section {{
        background: {c['panel_alt']};
        color: {c['muted']};
        border: none;
        border-right: 1px solid {c['border_soft']};
        padding: 6px 8px;
        border-bottom: 1px solid {c['border']};
        font-weight: 600;
    }}

    /* ---------- 进度 ---------- */
    QProgressBar {{
        background: {c['inset']};
        border: 1px solid {c['border_soft']};
        border-radius: 5px;
        text-align: center;
        color: {c['muted']};
        height: 16px;
    }}
    QProgressBar::chunk {{
        background: {c['focus']};
        border-radius: 5px;
    }}

    /* ---------- 滑块 ---------- */
    QSlider::groove:horizontal {{
        background: {c['border']};
        height: 2px;
        border-radius: 1px;
    }}
    QSlider::handle:horizontal {{
        background: {c['text']};
        width: 12px;
        height: 12px;
        margin: -6px 0;
        border-radius: 6px;
    }}
    QSlider::handle:horizontal:hover {{ background: {c['accent']}; }}
    QSlider::sub-page:horizontal {{
        background: {c['muted']};
        height: 2px;
    }}

    /* ---------- Tab ---------- */
    QTabWidget::pane {{
        border: 1px solid {c['border_soft']};
        background: {c['panel']};
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {c['muted']};
        padding: 8px 16px;
        border: none;
        border-right: 1px solid {c['border_soft']};
    }}
    QTabBar::tab:selected {{
        background: {c['panel']};
        color: {c['text']};
        border-bottom: 2px solid {c['accent']};
    }}
    QTabBar::tab:hover:!selected {{
        color: {c['text']};
    }}

    /* ---------- 滚动条 ---------- */
    QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']}; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {c['border']}; border-radius: 4px; min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {c['muted']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ---------- 复选 / 单选 ---------- */
    QCheckBox, QRadioButton {{ color: {c['text']}; spacing: 6px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {c['border_hi']};
        background: {c['inset']};
        border-radius: 4px;
    }}
    QCheckBox::indicator:checked {{
        background: {c['focus']};
        border-color: {c['focus']};
    }}
    QRadioButton::indicator {{ border-radius: 7px; }}
    QRadioButton::indicator:checked {{
        background: {c['focus']}; border-color: {c['focus']};
    }}

    /* ---------- 滚动区域 ---------- */
    QScrollArea {{ background: transparent; border: none; }}

    /* ---------- 分割线 / 提示 ---------- */
    QFrame#Divider {{ background: {c['border_soft']}; max-height: 1px; }}
    QToolTip {{
        background: {c['panel']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px 6px;
    }}

    /* ---------- 日志/终端 ---------- */
    QPlainTextEdit {{
        font-family: "{FONT_MONO}";
        font-size: 12px;
    }}
    """
