"""Niskopoziomowe pomocniki QPainter dla widgetow.

Zasada twarda: kazdy odcien pochodzi z tokenow `focuslock.ui.theme.COLORS`
albo z interpolacji dwoch tokenow szarych, wiec wynik zawsze ma R == G == B.
Dithering realizujemy wzorem Bayera 4x4 (bez obrazkow z sieci).
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap

from ..theme import TYPO, c

BAYER4: tuple[tuple[int, ...], ...] = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)

_BRUSH_CACHE: dict[tuple, QBrush] = {}

#: Mapowanie wag numerycznych (CSS/QSS) na `QFont.Weight`.
_WEIGHTS: tuple[tuple[int, QFont.Weight], ...] = (
    (200, QFont.Weight.Light),
    (300, QFont.Weight.Light),
    (400, QFont.Weight.Normal),
    (500, QFont.Weight.Medium),
    (600, QFont.Weight.DemiBold),
    (700, QFont.Weight.Bold),
)


def _weight(value: int) -> QFont.Weight:
    return min(_WEIGHTS, key=lambda item: abs(item[0] - int(value)))[1]


def ui_font(size: int, weight: int = 400) -> QFont:
    """Czcionka interfejsu z pelna lista fallbackow (np. brak 'Segoe UI Variable')."""
    font = QFont()
    font.setFamilies([TYPO.ui_family, TYPO.ui_fallback, "Arial", "sans-serif"])
    font.setPixelSize(max(8, int(size)))
    font.setWeight(_weight(weight))
    return font


def mono_font(size: int, weight: int = 300) -> QFont:
    """Czcionka o stalej szerokosci (timer, wartosci) z fallbackiem (Consolas)."""
    font = QFont()
    font.setFamilies([TYPO.mono_family, TYPO.mono_fallback, "Courier New", "monospace"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(max(8, int(size)))
    font.setWeight(_weight(weight))
    return font


def qcolor(name: str, alpha: int = 255) -> QColor:
    """Kolor z tokenu motywu (z opcjonalna przezroczystoscia)."""
    color = QColor(c(name))
    color.setAlpha(int(alpha))
    return color


def gray_level(amount: float, low: str = "surface2", high: str = "text") -> QColor:
    """Poziom szarosci miedzy dwoma tokenami; wynik pozostaje monochromatyczny."""
    amount = max(0.0, min(1.0, float(amount)))
    a = QColor(c(low))
    b = QColor(c(high))
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    )


def dither_brush(color: str, density: float, cell: int = 4, bg: str | None = None) -> QBrush:
    """Pędzel z wzorem Bayera 4x4 o kryciu `density` (0..1)."""
    step = round(max(0.0, min(1.0, float(density))) * 16)
    size = max(2, int(cell))
    key = (color, step, size, bg)
    cached = _BRUSH_CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(c(bg)) if bg else QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(qcolor(color))
    threshold = step + 0.5
    for y in range(size):
        for x in range(size):
            if BAYER4[y % 4][x % 4] < threshold:
                painter.drawRect(x, y, 1, 1)
    painter.end()
    brush = QBrush(pixmap)
    _BRUSH_CACHE[key] = brush
    return brush


def fill_dither(
    painter: QPainter,
    shape: QPainterPath | QRectF,
    color: str,
    density: float,
    cell: int = 4,
    bg: str | None = None,
) -> None:
    """Wypelnia obszar ditheringiem o zadanym kryciu."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(dither_brush(color, density, cell, bg))
    if isinstance(shape, QPainterPath):
        painter.drawPath(shape)
    else:
        painter.drawRect(shape)
    painter.restore()


def pen(color: str, width: float = 1.0, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    pen_ = QPen(qcolor(color))
    pen_.setWidthF(float(width))
    pen_.setStyle(style)
    return pen_


def annulus_path(center: QPointF, r_out: float, r_in: float, start_deg: float, span_deg: float) -> QPainterPath:
    """Wyciek pierscienia (sektor annulusa) do malowania ditheringiem."""
    outer = QRectF(center.x() - r_out, center.y() - r_out, r_out * 2.0, r_out * 2.0)
    inner = QRectF(center.x() - r_in, center.y() - r_in, r_in * 2.0, r_in * 2.0)
    path = QPainterPath()
    path.arcMoveTo(outer, start_deg)
    path.arcTo(outer, start_deg, span_deg)
    path.arcMoveTo(inner, start_deg + span_deg)
    path.arcTo(inner, start_deg + span_deg, -span_deg)
    path.closeSubpath()
    return path


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
