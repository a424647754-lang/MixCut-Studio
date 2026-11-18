# -*- coding: utf-8 -*-
"""MixCut Studio · 2026 macOS 深色设计令牌与全局 QSS。"""

from PySide6.QtGui import QColor, QFont

# ---------- 颜色 ----------
BG = "#141519"            # 主内容背景
BG_DEEP = "#101114"
SIDEBAR = "#1B1C21"
SIDEBAR_HI = "#22242A"
CARD = "#1E2026"
CARD_HI = "#24262D"
INSET = "#16171B"
LINE = "#2B2D35"
LINE_SOFT = "#24262C"
LINE_HI = "#3A3D47"

TEXT = "#F4F5F7"
TEXT_2 = "#A7AAB4"
TEXT_3 = "#71757F"
TEXT_INV = "#111214"

ACCENT = "#A78BFA"        # 仅用于选中/焦点，不铺大面积
ACCENT_SOFT = "#8B7CF6"
ACCENT_BG = "#2A2540"
ACCENT_LINE = "#4B3F7A"
OK = "#63D6A0"
BLUE = "#79A9FF"
WARN = "#E7B06B"
ERR = "#E58B84"

# ---------- 主题变体 ----------
# 每个变体只换「背景/卡片/边框/文字」这组底层令牌，强调色单独切换
PALETTES = {
    "石墨深色": {
        "BG": "#141519", "BG_DEEP": "#101114", "SIDEBAR": "#1B1C21",
        "SIDEBAR_HI": "#22242A", "CARD": "#1E2026", "CARD_HI": "#24262D",
        "INSET": "#16171B", "LINE": "#2B2D35", "LINE_SOFT": "#24262C",
        "LINE_HI": "#3A3D47", "TEXT": "#F4F5F7", "TEXT_2": "#A7AAB4",
        "TEXT_3": "#71757F", "TEXT_INV": "#111214",
    },
    "深空黑": {
        "BG": "#0C0D10", "BG_DEEP": "#08090B", "SIDEBAR": "#131418",
        "SIDEBAR_HI": "#1A1C21", "CARD": "#16171C", "CARD_HI": "#1C1E24",
        "INSET": "#101115", "LINE": "#24262C", "LINE_SOFT": "#1E2025",
        "LINE_HI": "#33363E", "TEXT": "#F2F3F5", "TEXT_2": "#9DA1AA",
        "TEXT_3": "#676B74", "TEXT_INV": "#0C0D10",
    },
    "暖灰": {
        "BG": "#171512", "BG_DEEP": "#12100E", "SIDEBAR": "#1F1C18",
        "SIDEBAR_HI": "#272320", "CARD": "#23201B", "CARD_HI": "#2A2621",
        "INSET": "#1A1814", "LINE": "#332E27", "LINE_SOFT": "#2A2620",
        "LINE_HI": "#453F36", "TEXT": "#F3F0EA", "TEXT_2": "#ADA59A",
        "TEXT_3": "#786F63", "TEXT_INV": "#171512",
    },
}

# 强调色：(主色, 柔和色, 选中底色, 选中描边)
ACCENTS = {
    "白色": ("#F4F5F7", "#D9DCE3", "#2C2E33", "#4A4E59"),
    "紫色": ("#A78BFA", "#8B7CF6", "#2A2540", "#4B3F7A"),
    "蓝色": ("#79A9FF", "#5E8FE8", "#1F2A3D", "#37507A"),
    "绿色": ("#63D6A0", "#4FBF8C", "#1D2E28", "#2F6A55"),
}

_current_palette = "石墨深色"
_current_accent = "紫色"

# ---------- 尺寸 ----------
R_CARD = 16
R_CTRL = 10
R_CHIP = 8
SIDEBAR_W = 238
TOPBAR_H = 56
PAGE_PAD = 30

# QSS 里的 "Microsoft YaHei UI" 这个名字解析不到真实字族，中文会变成方框；
# 统一用字族名 "Microsoft YaHei"（同一套雅黑，中文/Latin 都正常）。
FONT_UI = "Microsoft YaHei"
FONT_FALLBACK = "Microsoft YaHei"
FONT_MONO = "Cascadia Mono"


def font(size: int, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False) -> QFont:
    f = QFont(FONT_MONO if mono else FONT_UI)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def title_font() -> QFont:
    return font(30, QFont.Weight.DemiBold)


def qss() -> str:
    return f"""
* {{
    font-family: "{FONT_UI}";
    color: {TEXT};
    outline: none;
}}
QWidget#Root {{ background: {BG}; }}
QWidget#Content {{ background: {BG}; }}
QWidget#Sidebar {{ background: {SIDEBAR}; border-right: 1px solid {LINE_SOFT}; }}
QWidget#TopBar {{ background: {BG}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea::viewport {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #3A3D46; min-height: 32px; border-radius: 4px; }}
QScrollBar::handle:vertical:hover {{ background: #4A4E59; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: #3A3D46; min-width: 32px; border-radius: 4px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QLineEdit {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {R_CTRL}px;
    padding: 0 12px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: #FFFFFF;
    font-size: 13px;
}}
QLineEdit:focus {{ border: 1px solid {ACCENT_SOFT}; background: {CARD_HI}; }}
QLineEdit::placeholder {{ color: {TEXT_3}; }}

QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {R_CTRL}px;
    padding: 0 10px;
    font-size: 12px;
    color: {TEXT};
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT_SOFT};
    background: {CARD_HI};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: {CARD_HI};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {ACCENT_BG};
    selection-color: {TEXT};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QPushButton#FolderRow {{
    background: transparent;
    border: none;
    border-radius: 7px;
    color: {TEXT_2};
    text-align: left;
    padding: 0 8px;
    font-size: 12px;
}}
QPushButton#FolderRow:hover {{ background: {CARD_HI}; color: {TEXT}; }}
QPushButton#FolderRow:checked {{
    background: {ACCENT_BG};
    color: #FFFFFF;
}}

QSplitter#FolderSplitter::handle {{
    background: {BG_DEEP};
    border-left: 1px solid {LINE_SOFT};
}}
QSplitter#FolderSplitter::handle:hover {{
    background: {LINE_HI};
    border-left: 1px solid {LINE_HI};
    border-radius: 3px;
}}

QSlider::groove:horizontal {{ height: 3px; background: #32343C; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #D9DCE3; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
    background: #FFFFFF; border: 3px solid {BG};
}}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}

QToolTip {{
    background: {CARD_HI}; color: {TEXT}; border: 1px solid {LINE_HI};
    border-radius: 8px; padding: 6px 9px;
}}
"""


def c(hex_color: str, alpha: int = 255) -> QColor:
    col = QColor(hex_color)
    col.setAlpha(alpha)
    return col


# ---------------------------------------------------------
# 主题变体切换 + 持久化
# ---------------------------------------------------------

def current_palette() -> str:
    return _current_palette


def current_accent() -> str:
    return _current_accent


def _apply_tokens(palette: str, accent: str):
    global _current_palette, _current_accent
    if palette in PALETTES:
        globals().update(PALETTES[palette])
        _current_palette = palette
    if accent in ACCENTS:
        main, soft, bg, line = ACCENTS[accent]
        globals().update({"ACCENT": main, "ACCENT_SOFT": soft,
                          "ACCENT_BG": bg, "ACCENT_LINE": line})
        _current_accent = accent


def set_variant(palette: str = None, accent: str = None, save: bool = True) -> dict:
    """切主题/强调色；save=True 时写入 config/ui.json。返回最新偏好。"""
    from . import prefs

    _apply_tokens(palette or _current_palette, accent or _current_accent)
    if save:
        data = prefs.load()
        data["palette"] = _current_palette
        data["accent"] = _current_accent
        prefs.save(data)
        return data
    return {"palette": _current_palette, "accent": _current_accent}


def apply_tokens_to_app(app):
    """把当前令牌刷成全局 QSS（只换颜色，不重建控件）。"""
    if app is None:
        return
    app.setStyleSheet(qss())
    for widget in app.allWidgets():
        try:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        except Exception:
            pass


def load_prefs():
    """启动时按上次选择恢复主题（在构建界面前调用）。"""
    from . import prefs

    data = prefs.load()
    _apply_tokens(data.get("palette", "石墨深色"), data.get("accent", "紫色"))
    return data
