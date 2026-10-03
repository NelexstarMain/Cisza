"""Wskazniki: pierscien postepu i pasek z ditheringiem."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QPainter, QPainterPath
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..theme import FONT_SIZES, TYPO
from .paint import annulus_path, clamp01, fill_dither, mono_font, pen, qcolor, ui_font


class RingProgress(QWidget):
    """Pierscien postepu: tor + luk postepu + dithering na granicy i wewnatrz."""

    def __init__(self, parent: QWidget | None = None, thickness: int = 10) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._thickness = max(2, int(thickness))
        self._text = "--:--"
        self._caption = ""
        self._overline = ""
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    # ------------------------------------------------------------------ API
    def set_value(self, value: float) -> None:
        value = clamp01(value)
        if value != self._value:
            self._value = value
            self.update()

    def value(self) -> float:
        return self._value

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.update()

    def set_caption(self, text: str) -> None:
        if text != self._caption:
            self._caption = text
            self.update()

    def set_overline(self, text: str) -> None:
        if text != self._overline:
            self._overline = text
            self.update()

    def set_thickness(self, thickness: int) -> None:
        self._thickness = max(2, int(thickness))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(220, 220)

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = float(min(self.width(), self.height()))
        thickness = float(self._thickness)
        margin = thickness / 2.0 + 2.0
        if side <= 2 * margin + 4:
            painter.end()
            return
        rect = QRectF(
            (self.width() - side) / 2.0 + margin,
            (self.height() - side) / 2.0 + margin,
            side - 2 * margin,
            side - 2 * margin,
        )
        center = rect.center()
        r_out = rect.width() / 2.0
        r_in = max(1.0, r_out - thickness)

        # wewnetrzny dysk z delikatnym ditheringiem (glebia, bez koloru)
        disc = QRectF(center.x() - r_in, center.y() - r_in, r_in * 2.0, r_in * 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("surface"))
        painter.drawEllipse(disc)
        disc_path = QPainterPath()
        disc_path.addEllipse(disc)
        fill_dither(painter, disc_path, "text", 0.04)

        # tor
        track_pen = pen("line", thickness)
        track_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        # postep (zgodnie z ruchem wskazowek zegara od godziny 12)
        if self._value > 0.0:
            arc_pen = pen("accent", thickness)
            arc_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(arc_pen)
            painter.drawArc(rect, 90 * 16, -int(round(360 * 16 * self._value)))

        # dithering na krawedzi postepu — wizualny "polowiczny" krok
        if 0.0 < self._value < 1.0:
            end_deg = 90.0 - 360.0 * self._value
            band = annulus_path(center, r_out, r_in, end_deg, -16.0)
            fill_dither(painter, band, "accent", 0.5, cell=4, bg="surface")

        # tekst srodkowy
        painter.setFont(mono_font(max(14, int(side * 0.17)), 300))
        painter.setPen(qcolor("accent"))
        text_rect = QRectF(center.x() - r_in, center.y() - r_in, r_in * 2.0, r_in * 2.0)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), self._text)

        if self._caption:
            painter.setFont(ui_font(FONT_SIZES["xs"]))
            cap_rect = QRectF(rect.left(), center.y() + side * 0.19, rect.width(), 18.0)
            # Podpis dostaje czyste tlo: na ditheringowanym dysku tekst ginal.
            text_width = min(
                cap_rect.width() - 8.0, painter.fontMetrics().horizontalAdvance(self._caption) + 16.0
            )
            backing = QRectF(center.x() - text_width / 2.0, cap_rect.top() - 2.0, text_width, 18.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("surface"))
            painter.drawRoundedRect(backing, 4.0, 4.0)
            painter.setPen(qcolor("text"))
            painter.drawText(cap_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), self._caption)

        if self._overline:
            painter.setFont(ui_font(FONT_SIZES["sm"]))
            painter.setPen(qcolor("text_dim"))
            over_rect = QRectF(rect.left(), center.y() - side * 0.24, rect.width(), 18.0)
            painter.drawText(over_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), self._overline)
        painter.end()


class DitheredBar(QWidget):
    """Pasek postepu z podzialem na segmenty; krok niepelny znaczy dithering."""

    def __init__(self, parent: QWidget | None = None, segments: int = 0, bar_height: int = 10) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._segments = max(0, int(segments))
        self._bar_height = max(4, int(bar_height))
        self.setFixedHeight(self._bar_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------------ API
    def set_value(self, value: float) -> None:
        value = clamp01(value)
        if value != self._value:
            self._value = value
            self.update()

    def value(self) -> float:
        return self._value

    def set_segments(self, segments: int) -> None:
        self._segments = max(0, int(segments))
        self.update()

    def set_bar_height(self, height: int) -> None:
        self._bar_height = max(4, int(height))
        self.setFixedHeight(self._bar_height)
        self.update()

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        width = float(self.width())
        height = float(self.height())
        if width < 3 or height < 3:
            painter.end()
            return
        track = QRectF(0.0, 0.0, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("surface2"))
        painter.drawRect(track)
        inner = QRectF(1.0, 1.0, width - 2.0, height - 2.0)

        if self._value > 0.0 and inner.width() > 0:
            if self._segments > 1:
                seg_w = inner.width() / self._segments
                scaled = self._value * self._segments
                full = int(scaled)
                for index in range(self._segments):
                    cell = QRectF(inner.left() + index * seg_w + 1.0, inner.top(), max(1.0, seg_w - 2.0), inner.height())
                    if index < full:
                        painter.fillRect(cell, qcolor("text_dim"))
                    elif index == full and (scaled - full) > 0.02:
                        fill_dither(painter, cell, "accent", scaled - full, cell=4, bg="surface2")
                tick_pen = pen("line")
                painter.setPen(tick_pen)
                for index in range(1, self._segments):
                    x = inner.left() + index * seg_w
                    painter.drawLine(int(x), int(inner.top()), int(x), int(inner.bottom()))
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                filled = inner.width() * self._value
                solid = max(0.0, filled - 3.0)
                if solid > 0:
                    painter.fillRect(QRectF(inner.left(), inner.top(), solid, inner.height()), qcolor("text_dim"))
                if filled > solid:
                    edge = QRectF(inner.left() + solid, inner.top(), max(1.0, filled - solid), inner.height())
                    fill_dither(painter, edge, "accent", 0.5, cell=4, bg="surface2")

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen("line"))
        painter.drawRect(track.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


class Sparkline(QWidget):
    """Miniatura przebiegu (lista wartosci 0..1) rysowana linia schodkowa."""

    def __init__(self, parent: QWidget | None = None, height: int = 28) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self.setFixedHeight(int(height))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values: list[float]) -> None:
        self._values = [clamp01(v) for v in values]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        width = float(self.width())
        height = float(self.height())
        if not self._values or width < 4 or height < 4:
            painter.setPen(pen("line"))
            painter.drawLine(0, int(height) - 1, int(width), int(height) - 1)
            painter.end()
            return
        count = len(self._values)
        step = width / max(1, count)
        painter.setPen(pen("text_dim", 1.0))
        previous = QPointF(0.0, height - self._values[0] * (height - 3.0) - 1.0)
        for index, value in enumerate(self._values):
            x = index * step
            point = QPointF(x, height - value * (height - 3.0) - 1.0)
            painter.drawLine(previous, point)
            previous = point
        painter.fillRect(QRectF(previous.x() - 2.0, previous.y() - 2.0, 4.0, 4.0), qcolor("accent"))
        painter.end()
