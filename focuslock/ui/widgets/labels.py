"""Fabryki etykiet i prostych layoutow (role zgodne z theme.qss).

Zasady czytelnosci wypracowane dla calego UI:
* `caption` - krotki, wersalikowy podpis nad wartoscia (mala litera + rozstrzelenie),
* `body`    - zdanie dla czlowieka (13 px, `text_dim`), zawija sie,
* `hint`    - dluzsza nota pomocnicza (12 px, `text_mute`), bez rozstrzelenia,
* `field`   - etykieta pola formularza (12 px, `text_dim`), zawija sie,
* `ElidedLabel` - dlugie nazwy (tagi, hosty, notatki) przycinane zamiast rozpychac layout.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..theme import FONT_SIZES, ROLE_FONTS, TYPO


#: Role, ktore rysujemy krojem naglowkowym (Display) - spojne z `theme.qss()`.
DISPLAY_ROLES = frozenset({"title", "subtitle", "primary"})


def _role_font(role: str) -> QFont:
    """Czcionka zgodna z QSS dla roli tekstu (rozmiar + rozstrzelenie + krój)."""
    from .paint import display_font, mono_font, ui_font

    size, spacing, mono = ROLE_FONTS.get(role, ROLE_FONTS["button"])
    if mono:
        font = mono_font(size, 300)
    elif role in DISPLAY_ROLES:
        font = display_font(size, 600)
    else:
        font = ui_font(size)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(spacing))
    return font


def elide_text(
    widget: QWidget,
    text: str,
    width: int,
    *,
    role: str = "button",
    mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
) -> str:
    """Skraca tekst do szerokosci tak, jak zrobi to Qt (z rozstrzeleniem liter)."""
    _ = widget
    metrics = QFontMetrics(_role_font(role))
    return metrics.elidedText(str(text), mode, max(20, int(width)))


class ElidedLabel(QLabel):
    """Etykieta jednowierszowa: pelny tekst w tooltipie, na ekranie wielokropek.

    `Ignored` w polityce rozmiaru lamie wyrownanie w `QHBoxLayout` (widget potrafi
    wyjsc za prawy margines), dlatego `sizeHint` jest ograniczany do `maximumWidth`,
    a `minimumSizeHint` jest maly - dzieki temu etykieta kurczy sie razem z karta.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        role: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ) -> None:
        super().__init__("", parent)
        self._full_text = ""
        self._mode = mode
        if role:
            self.setProperty("role", role)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.set_full_text(text)

    # ------------------------------------------------------------------ API
    def set_full_text(self, text: str) -> None:
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self.updateGeometry()
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def set_elide_mode(self, mode: Qt.TextElideMode) -> None:
        self._mode = mode
        self._apply_elide()

    # ------------------------------------------------------------- rozmiary
    def _metrics(self) -> QFontMetrics:
        return QFontMetrics(_role_font(str(self.property("role") or "")))

    def sizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        metrics = self._metrics()
        width = metrics.horizontalAdvance(self._full_text) + 2
        cap = self.maximumWidth()
        if 0 < cap < 16777215:
            width = min(width, cap)
        return QSize(max(24, width), max(metrics.height(), 16))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        metrics = self._metrics()
        return QSize(24, max(metrics.height(), 16))

    # ------------------------------------------------------------- wewnetrzne
    def _apply_elide(self) -> None:
        available = max(40, self.width() - 2) if self.width() > 0 else 0
        if available <= 0:
            super().setText(self._full_text)
            return
        super().setText(self._metrics().elidedText(self._full_text, self._mode, available))

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._apply_elide()

    def setText(self, text: str) -> None:  # noqa: N802 (API Qt) - zgodnosc z QLabel
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._apply_elide()


def elided(text: str = "", parent: QWidget | None = None, *, role: str = "caption") -> ElidedLabel:
    """Jednowierszowy tekst z elidowaniem (tag, host, nazwa aplikacji)."""
    return ElidedLabel(text, parent, role=role)


def _single_line(label: QLabel) -> QLabel:
    """Jednowierszowa etykieta nie rosnie w pionie (tekst nie 'ucieka' na srodek karty)."""
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return label


def styled(role: str, text: str = "", parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", role)
    label.setStyleSheet("background: transparent;")
    return label


def title(text: str = "", parent: QWidget | None = None) -> QLabel:
    return _single_line(styled("title", text, parent))


def subtitle(text: str = "", parent: QWidget | None = None) -> QLabel:
    label = styled("subtitle", text, parent)
    label.setWordWrap(True)
    return _single_line(label)


def caption(text: str = "", parent: QWidget | None = None) -> QLabel:
    return _single_line(styled("caption", text, parent))


def value_label(text: str = "-", parent: QWidget | None = None) -> QLabel:
    label = _single_line(styled("value", text, parent))
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return label


def timer_label(text: str = "00:00", small: bool = False, parent: QWidget | None = None) -> QLabel:
    return _single_line(styled("timerSmall" if small else "timer", text, parent))


def body(text: str = "", parent: QWidget | None = None) -> QLabel:
    """Zdanie dla czlowieka: 13 px, `text_dim`, zawija sie."""
    label = styled("body", text, parent)
    label.setWordWrap(True)
    return label


def hint(text: str = "", parent: QWidget | None = None) -> QLabel:
    """Dluzsza nota pomocnicza: mala litera, bez rozstrzelenia, zawija sie."""
    label = styled("hint", text, parent)
    label.setWordWrap(True)
    return label


def field(text: str = "", parent: QWidget | None = None) -> QLabel:
    """Etykieta pola formularza: czytelna, zawijana, wyrownana do gory."""
    label = styled("field", text, parent)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return label


def empty(text: str = "", parent: QWidget | None = None) -> QLabel:
    """Krotki komunikat pustego stanu (w ramce `EmptyState`)."""
    label = styled("empty", text, parent)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def empty_detail(text: str = "", parent: QWidget | None = None) -> QLabel:
    """Druga linia pustego stanu: podpowiedz, co zrobic."""
    label = styled("emptyDetail", text, parent)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def _dim() -> str:
    from ..theme import c

    return c("text_dim")


def hbox(*widgets: QWidget, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for widget in widgets:
        layout.addWidget(widget)
    return layout


def vbox(*widgets: QWidget, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for widget in widgets:
        layout.addWidget(widget)
    return layout


def stretch_spacer() -> QWidget:
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return spacer


def grow(layout) -> None:
    layout.addStretch(1)


def align_right(label: QLabel) -> QLabel:
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return label


def align_center(label: QLabel) -> QLabel:
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


#: `_dim` i `TYPO`/`FONT_SIZES` zostaja publiczne dla ekranow piszacych wlasne etykiety.
__all__ = [
    "ElidedLabel",
    "align_center",
    "align_right",
    "body",
    "caption",
    "elide_text",
    "elided",
    "empty",
    "empty_detail",
    "field",
    "grow",
    "hbox",
    "hint",
    "stretch_spacer",
    "styled",
    "subtitle",
    "timer_label",
    "title",
    "value_label",
    "vbox",
    "FONT_SIZES",
    "TYPO",
]
