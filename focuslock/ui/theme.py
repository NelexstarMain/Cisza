"""Monochromatyczny system wygladu: tokeny, czcionki, arkusz QSS.

Zasada twarda: w calym UI wystepuje wylacznie skala szarosci (R == G == B).
Test tools/mono_lint.py oraz tests/test_theme_mono.py wymuszaja te regule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------- tokeny
COLORS: dict[str, str] = {
    "bg": "#000000",
    "surface": "#0B0B0B",
    "surface2": "#131313",
    "surface3": "#1B1B1B",
    "line": "#242424",
    "line_strong": "#3A3A3A",
    "text": "#F2F2F2",
    "text_dim": "#A8A8A8",
    # tekst pomocniczy: kontrast >= 4.5:1 na `surface` i `surface2`
    # (poprzednie #6B6B6B dawalo ok. 3.6:1 - drobne podpisy byly nieczytelne)
    "text_mute": "#828282",
    "disabled": "#4D4D4D",
    "accent": "#FFFFFF",
    "accent_dim": "#C8C8C8",
    "overlay": "#000000",
    "blocked": "#FFFFFF",
}

SPACING = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 40, "xxl": 64}
RADIUS = {"sm": 4, "md": 8, "lg": 14, "xl": 22}
FONT_SIZES = {
    "xs": 11,
    "sm": 12,
    "body": 13,
    "md": 14,
    "lg": 18,
    "xl": 24,
    "xxl": 34,
    "timer": 96,
    "timer_small": 48,
}

#: Wspolne wysokosci kontrolek - pola, przyciski i chipy w jednym rytmie.
SIZES = {"input": 38, "button": 38, "primary": 48, "chip": 32, "toggle": 28}

#: Rozmiar, rozstrzelenie liter i krój dla rol tekstu.
#: Te same wartosci sa w `qss()`, ale Qt nie widzi rozstrzelenia w metrykach
#: czcionki, wiec elidowanie tekstu (`widgets.labels.elide_text`) musi znac je
#: jawnie - inaczej dlugi tekst jest przycinany zamiast konczyc sie wielokropkiem.
#: (rozmiar_px, rozstrzelenie_px, czy_mono)
ROLE_FONTS: dict[str, tuple[int, float, bool]] = {
    "title": (FONT_SIZES["xxl"], 6.0, False),
    "subtitle": (FONT_SIZES["lg"], 2.0, False),
    "caption": (FONT_SIZES["sm"], 2.0, False),
    "body": (FONT_SIZES["body"], 0.0, False),
    "hint": (FONT_SIZES["sm"], 0.0, False),
    "field": (FONT_SIZES["sm"], 0.0, False),
    "empty": (FONT_SIZES["body"], 0.0, False),
    "emptyDetail": (FONT_SIZES["sm"], 0.0, False),
    "value": (FONT_SIZES["xl"], 1.0, True),
    "button": (FONT_SIZES["md"], 1.0, False),
    "primary": (FONT_SIZES["lg"], 3.0, False),
}

HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def is_gray(hex_color: str) -> bool:
    """True, gdy kolor ma zerowe nasycenie."""
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return False
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return r == g == b


def non_gray_colors(text: str) -> list[str]:
    """Zwraca liste kolorow naruszajacych monochromatycznosc (do testow)."""
    return [match for match in HEX_RE.findall(text) if not is_gray(match)]


@dataclass(frozen=True)
class Typography:
    ui_family: str = "Segoe UI Variable Display"
    ui_fallback: str = "Segoe UI"
    mono_family: str = "Cascadia Mono"
    mono_fallback: str = "Consolas"
    letter_spacing: float = 1.4

    def ui(self, size: int, weight: int = 400) -> str:
        return f'font-family: "{self.ui_family}", "{self.ui_fallback}", sans-serif; font-size: {size}px; font-weight: {weight};'

    def mono(self, size: int, weight: int = 300) -> str:
        return f'font-family: "{self.mono_family}", "{self.mono_fallback}", monospace; font-size: {size}px; font-weight: {weight};'


TYPO = Typography()


def c(name: str) -> str:
    return COLORS[name]


def qss() -> str:
    """Arkusz stylow dla calego UI (tylko szarosci)."""
    return f"""
QWidget {{
    background-color: {c('bg')};
    color: {c('text')};
    {TYPO.ui(FONT_SIZES['md'])}
}}
QMainWindow, QDialog {{ background-color: {c('bg')}; }}

QLabel[role="title"] {{
    {TYPO.ui(FONT_SIZES['xxl'], 600)}
    color: {c('text')};
    letter-spacing: 6px;
}}
QLabel[role="subtitle"] {{ {TYPO.ui(FONT_SIZES['lg'], 400)} color: {c('text_dim')}; letter-spacing: 2px; }}
QLabel[role="caption"] {{ {TYPO.ui(FONT_SIZES['sm'])} color: {c('text_mute')}; letter-spacing: 2px; }}
QLabel[role="body"] {{ {TYPO.ui(FONT_SIZES['body'])} color: {c('text_dim')}; }}
QLabel[role="hint"] {{ {TYPO.ui(FONT_SIZES['sm'])} color: {c('text_mute')}; letter-spacing: 0px; }}
QLabel[role="field"] {{ {TYPO.ui(FONT_SIZES['sm'])} color: {c('text_dim')}; letter-spacing: 0px; }}
QLabel[role="empty"] {{ {TYPO.ui(FONT_SIZES['body'])} color: {c('text_dim')}; }}
QLabel[role="emptyDetail"] {{ {TYPO.ui(FONT_SIZES['sm'])} color: {c('text_mute')}; letter-spacing: 0px; }}
QLabel[role="value"] {{ {TYPO.mono(FONT_SIZES['xl'], 300)} color: {c('text')}; letter-spacing: 1px; }}
QLabel[role="timer"] {{ {TYPO.mono(FONT_SIZES['timer'], 200)} color: {c('accent')}; letter-spacing: 2px; }}
QLabel[role="timerSmall"] {{ {TYPO.mono(FONT_SIZES['timer_small'], 200)} color: {c('accent')}; }}

QFrame[role="card"] {{
    background-color: {c('surface')};
    border: 1px solid {c('line')};
    border-radius: {RADIUS['lg']}px;
}}
QFrame[role="card"][state="active"] {{ border-color: {c('text_mute')}; }}
QFrame[role="panel"] {{
    background-color: {c('surface2')};
    border: 1px solid {c('line')};
    border-radius: {RADIUS['md']}px;
}}
QFrame[role="panel"][state="active"] {{ border-color: {c('text_mute')}; }}
QFrame[role="hairline"] {{ background-color: {c('line')}; border: none; max-height: 1px; }}
QFrame[role="emptyBox"] {{
    background-color: {c('surface2')};
    border: 1px dashed {c('line_strong')};
    border-radius: {RADIUS['md']}px;
}}

QPushButton {{
    background-color: {c('surface2')};
    color: {c('text')};
    border: 1px solid {c('line_strong')};
    border-radius: {RADIUS['md']}px;
    padding: 8px 16px;
    min-height: 20px;
    letter-spacing: 1px;
}}
QPushButton:hover {{ background-color: {c('surface3')}; border-color: {c('text_mute')}; }}
QPushButton:focus {{ border-color: {c('text_dim')}; }}
QPushButton:pressed {{ background-color: {c('line')}; }}
QPushButton:disabled {{ color: {c('disabled')}; border-color: {c('line')}; }}
QPushButton[role="primary"] {{
    background-color: {c('accent')};
    color: {c('bg')};
    border: 1px solid {c('accent')};
    {TYPO.ui(FONT_SIZES['lg'], 600)}
    padding: 12px 24px;
    letter-spacing: 3px;
}}
QPushButton[role="primary"]:hover {{ background-color: {c('accent_dim')}; border-color: {c('accent_dim')}; }}
QPushButton[role="primary"]:focus {{ border-color: {c('line_strong')}; }}
QPushButton[role="primary"]:disabled {{ background-color: {c('line_strong')}; border-color: {c('line_strong')}; color: {c('text_mute')}; }}
QPushButton[role="ghost"] {{ background: transparent; border: 1px solid {c('line')}; color: {c('text_dim')}; }}
QPushButton[role="ghost"]:hover {{ color: {c('text')}; border-color: {c('line_strong')}; }}
QPushButton[role="ghost"]:checked {{
    color: {c('text')};
    border-color: {c('text_dim')};
    background-color: {c('surface3')};
}}
QPushButton[role="ghost"]:disabled {{
    color: {c('disabled')};
    border-color: {c('line')};
    border-style: dashed;
    background: transparent;
}}
QPushButton[role="ghost"]:checked:disabled {{ color: {c('text_mute')}; border-style: solid; }}
QPushButton[role="danger"] {{ background: transparent; border: 1px solid {c('text')}; color: {c('text')}; }}
QPushButton[role="danger"]:hover {{ background-color: {c('surface2')}; }}
QPushButton[role="danger"]:disabled {{ color: {c('disabled')}; border-color: {c('line')}; }}
QPushButton[role="link"] {{ background: transparent; border: none; color: {c('text_dim')}; text-decoration: underline; }}
QPushButton[role="link"]:hover {{ color: {c('text')}; }}
QPushButton[role="link"]:disabled {{ color: {c('disabled')}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {c('surface2')};
    border: 1px solid {c('line')};
    border-radius: {RADIUS['sm']}px;
    padding: 8px 10px;
    min-height: 20px;
    selection-background-color: {c('text_mute')};
    selection-color: {c('bg')};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {c('text_dim')};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QPlainTextEdit:disabled, QTextEdit:disabled {{
    color: {c('disabled')};
    border-color: {c('line')};
}}
QComboBox QAbstractItemView {{
    background-color: {c('surface2')};
    border: 1px solid {c('line_strong')};
    padding: 4px;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}

QCheckBox, QRadioButton {{ spacing: 10px; color: {c('text_dim')}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {c('line_strong')};
    background-color: {c('surface2')};
}}
QCheckBox::indicator:checked {{ background-color: {c('accent')}; border-color: {c('accent')}; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QRadioButton::indicator:checked {{ background-color: {c('accent')}; }}

QListWidget, QTableWidget, QTreeWidget {{
    background-color: {c('surface')};
    border: 1px solid {c('line')};
    gridline-color: {c('line')};
    alternate-background-color: {c('surface2')};
}}
QListWidget::item, QTreeWidget::item {{ padding: 7px 10px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {c('surface2')}; }}
QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {c('surface3')};
    color: {c('accent')};
}}
QTableWidget::item {{ padding: 6px 8px; }}
QHeaderView::section {{
    background-color: {c('surface2')};
    color: {c('text_mute')};
    border: none;
    border-bottom: 1px solid {c('line')};
    padding: 8px 10px;
    letter-spacing: 2px;
}}
QTableCornerButton::section {{ background-color: {c('surface2')}; border: none; }}

QTabWidget::pane {{ border: 1px solid {c('line')}; top: -1px; }}
QTabBar::tab {{
    background: {c('surface')};
    color: {c('text_mute')};
    padding: 9px 16px;
    border: 1px solid {c('line')};
    border-bottom: none;
    letter-spacing: 2px;
}}
QTabBar::tab:hover {{ color: {c('text_dim')}; }}
QTabBar::tab:selected {{ background: {c('surface2')}; color: {c('text')}; border-color: {c('line_strong')}; }}
QTabBar QToolButton {{
    background: {c('surface2')};
    border: 1px solid {c('line')};
    color: {c('text_dim')};
    width: 16px;
}}
QTabBar QToolButton:hover {{ color: {c('text')}; border-color: {c('line_strong')}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QProgressBar {{
    background-color: {c('surface2')};
    border: 1px solid {c('line')};
    border-radius: {RADIUS['sm']}px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {c('text_dim')}; }}

QScrollBar:vertical {{ background: {c('bg')}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c('line_strong')}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c('text_mute')}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {c('bg')}; height: 12px; }}
QScrollBar::handle:horizontal {{ background: {c('line_strong')}; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {c('text_mute')}; }}

QToolTip {{ background-color: {c('surface2')}; color: {c('text')}; border: 1px solid {c('line_strong')}; }}
QMenu {{ background-color: {c('surface2')}; border: 1px solid {c('line_strong')}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; }}
QMenu::item:selected {{ background-color: {c('surface3')}; }}
QStatusBar {{ background: {c('bg')}; color: {c('text_mute')}; }}
QSlider::groove:horizontal {{ height: 2px; background: {c('line_strong')}; }}
QSlider::handle:horizontal {{ background: {c('accent')}; width: 12px; margin: -6px 0; border-radius: 6px; }}
QGroupBox {{ border: 1px solid {c('line')}; border-radius: {RADIUS['md']}px; margin-top: 14px; padding-top: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; color: {c('text_mute')}; letter-spacing: 2px; }}
"""


def apply_to(app) -> None:
    """Naklada motyw na QApplication."""
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    app.setStyleSheet(qss())


@dataclass
class Metrics:
    """Pomocnik odstepow dla ekranow."""

    spacing: dict = field(default_factory=lambda: dict(SPACING))
    radius: dict = field(default_factory=lambda: dict(RADIUS))
    fonts: dict = field(default_factory=lambda: dict(FONT_SIZES))
    sizes: dict = field(default_factory=lambda: dict(SIZES))


METRICS = Metrics()
