"""Wskazniki: pierscien postepu, pasek z ditheringiem i sparkline.

Wszystkie geometrie licza sie od biezacego ``self.rect()`` — widgety skaluja sie
razem z oknem, a pomocnicze metody (``ring_rect()``, ``plot_rect()``) sa uzywane
zarowno w ``paintEvent``, jak i w testach.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QFontMetrics, QPainter, QPainterPath
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..theme import FONT_SIZES
from .paint import clamp01, fill_dither, mono_font, pen, qcolor, ui_font

#: Minimalna srednica, przy ktorej pierscien jest jeszcze czytelny.
MIN_RING_SIDE = 96.0
#: Minimalna wysokosc paska postepu / sparkline.
MIN_BAR_HEIGHT = 4


def _as_float(value: object, default: float = 0.0) -> float:
    """Bezpieczne rzutowanie na liczbe (dane z UI bywaja smieciowe)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)


def _sector_path(center: QPointF, r_out: float, r_in: float, start_deg: float, span_deg: float) -> QPainterPath:
    """Wyciek pierscienia (annulus) poprawny takze dla ujemnego ``span_deg``.

    ``QPainterPath.arcMoveTo`` zaczyna nowa podsciezke, wiec naiwne zlozenie
    dwoch lukow zostawia otwarty luk zewnetrzny — Qt domyka go cieciwa, co
    rysowalo "smuge" przez srodek pierscienia. Tutaj luki sa jawnie spiete
    promieniami.
    """
    outer = QRectF(center.x() - r_out, center.y() - r_out, r_out * 2.0, r_out * 2.0)
    inner = QRectF(center.x() - r_in, center.y() - r_in, r_in * 2.0, r_in * 2.0)
    end_deg = start_deg + span_deg
    end_rad = math.radians(end_deg)
    path = QPainterPath()
    path.arcMoveTo(outer, start_deg)
    path.arcTo(outer, start_deg, span_deg)
    path.lineTo(center.x() + r_in * math.cos(end_rad), center.y() - r_in * math.sin(end_rad))
    path.arcTo(inner, end_deg, -span_deg)
    path.closeSubpath()
    return path


class RingProgress(QWidget):
    """Pierscien postepu: tor + znaczniki co 10% + luk postepu + tekst w srodku.

    Wewnatrz pierscienia jest czysty dysk (bez tekstury pod tekstem), a faktura
    ditheringu zostaje na samym torze — drobna (``cell=2``), wiec nie zlepia sie
    w "brudna" plame. Wartosc i podpis skaluja sie od srednicy (min. 9 px).
    """

    def __init__(self, parent: QWidget | None = None, thickness: int = 10) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._thickness = max(2, int(thickness))
        self._text = "--:--"
        self._caption = ""
        self._overline = ""
        self.setMinimumSize(int(MIN_RING_SIDE), int(MIN_RING_SIDE))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    # ------------------------------------------------------------------ API
    def set_value(self, value: float) -> None:
        value = clamp01(_as_float(value))
        if value != self._value:
            self._value = value
            self.update()

    def value(self) -> float:
        return self._value

    def set_text(self, text: str) -> None:
        text = str(text)
        if text != self._text:
            self._text = text
            self.update()

    def set_caption(self, text: str) -> None:
        text = str(text)
        if text != self._caption:
            self._caption = text
            self.update()

    def set_overline(self, text: str) -> None:
        text = str(text)
        if text != self._overline:
            self._overline = text
            self.update()

    def set_thickness(self, thickness: int) -> None:
        self._thickness = max(2, int(thickness))
        self.update()

    # -------------------------------------------------------------- rozmiary
    def sizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        # 220 px jak dotad (ekrany licza na ten rozmiar), ale minimum spadlo do 96,
        # zeby pierscien mogl sie kurczyc razem z oknem.
        return QSize(220, 220)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(int(MIN_RING_SIDE), int(MIN_RING_SIDE))

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self.update()

    # ------------------------------------------------------------- geometria
    def _side(self) -> float:
        return float(min(self.width(), self.height()))

    def _track_thickness(self) -> float:
        """Grubosc toru: z API, ale nigdy wieksza niz 13% srednicy."""
        side = self._side()
        return max(2.0, min(float(self._thickness), max(3.0, side * 0.13)))

    def ring_rect(self) -> QRectF:
        """Kwadrat toru pierscienia (zawsze wewnatrz ``self.rect()``)."""
        side = self._side()
        thickness = self._track_thickness()
        margin = thickness / 2.0 + 2.0
        radius = side / 2.0 - margin
        if radius < 1.0:
            radius = max(0.0, min(float(self.width()), float(self.height())) / 2.0 - 0.5)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        return QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)

    def inner_rect(self) -> QRectF:
        """Wewnetrzny dysk — czyste tlo pod wartoscia i podpisem."""
        ring = self.ring_rect()
        radius = max(0.0, ring.width() / 2.0 - self._track_thickness())
        center = ring.center()
        return QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)

    def value_font_size(self) -> int:
        """Stopien pisma wartosci — skaluje sie od srednicy (min. 9 px)."""
        side = self._side()
        inner = max(1.0, self.inner_rect().width())
        text = str(self._text or "")
        candidate = int(max(9, min(120, round(side * 0.30))))
        while candidate > 9:
            metrics = QFontMetrics(mono_font(candidate, 300))
            if metrics.horizontalAdvance(text) <= inner * 0.62 and metrics.height() <= inner * 0.52:
                break
            candidate -= 1
        return max(9, candidate)

    def caption_font_size(self) -> int:
        """Stopien pisma podpisu (min. 9 px, nigdy wiekszy niz naglowek karty)."""
        return int(max(9, min(FONT_SIZES["sm"] + 2, round(self._side() * 0.075))))

    def overline_font_size(self) -> int:
        return int(max(9, min(FONT_SIZES["sm"], round(self._side() * 0.075))))

    def _text_layout(self) -> tuple[QRectF, QRectF, QRectF]:
        """(wartosc, podpis, overline) — blok wycentrowany w dysku pierscienia."""
        inner = self.inner_rect()
        center = inner.center()
        metrics_value = QFontMetrics(mono_font(self.value_font_size(), 300))
        metrics_caption = QFontMetrics(ui_font(self.caption_font_size(), 500))
        metrics_overline = QFontMetrics(ui_font(self.overline_font_size()))
        value_h = float(metrics_value.height())
        caption_h = float(metrics_caption.height()) if self._caption else 0.0
        overline_h = float(metrics_overline.height()) if self._overline else 0.0
        gap = 2.0 if self._caption else 0.0
        block_h = overline_h + (2.0 if overline_h else 0.0) + value_h + gap + caption_h
        top = center.y() - block_h / 2.0
        left = inner.left()
        width = max(1.0, inner.width())
        overline = QRectF(left, top, width, overline_h)
        value = QRectF(left, top + overline_h + (2.0 if overline_h else 0.0), width, value_h)
        caption = QRectF(left, value.bottom() + gap, width, caption_h)
        return value, caption, overline

    def value_rect(self) -> QRectF:
        return self._text_layout()[0]

    def caption_rect(self) -> QRectF:
        return self._text_layout()[1]

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ring = self.ring_rect()
        if ring.width() < 6.0 or ring.height() < 6.0:
            painter.end()
            return

        thickness = self._track_thickness()
        center = ring.center()
        r_out = ring.width() / 2.0
        r_in = max(1.0, r_out - thickness)
        r_mid = (r_out + r_in) / 2.0
        mid_rect = QRectF(center.x() - r_mid, center.y() - r_mid, r_mid * 2.0, r_mid * 2.0)
        outer_rect = QRectF(center.x() - r_out, center.y() - r_out, r_out * 2.0, r_out * 2.0)
        inner_rect = QRectF(center.x() - r_in, center.y() - r_in, r_in * 2.0, r_in * 2.0)
        span = 360.0 * self._value

        # 1. Czysty dysk — zaden dithering nie lezy pod tekstem.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("surface"))
        painter.drawEllipse(inner_rect)

        # 2. Tor dokladnie na swoim pasie [r_in, r_out].
        track = pen("line", thickness)
        track.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(track)
        painter.drawArc(mid_rect, 0, 360 * 16)

        # 3. Drobna faktura na torze (cell=2); luk postepu przykrywa ja pozniej,
        #    wiec tekstura zostaje tylko na czesci niepostepowej.
        texture = QPainterPath()
        texture.setFillRule(Qt.FillRule.OddEvenFill)
        texture.addEllipse(outer_rect)
        texture.addEllipse(inner_rect)
        fill_dither(painter, texture, "text_mute", 0.12, cell=2)

        # 4. Luk postepu (zgodnie z ruchem wskazowek zegara od godziny 12).
        if self._value > 0.0:
            arc = pen("accent", thickness)
            arc.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(arc)
            painter.drawArc(mid_rect, 90 * 16, -int(round(360 * 16 * self._value)))

        # 5. Miekkie przejscie na koncu postepu — drobna faktura, nie plama.
        if 0.0 < self._value < 1.0:
            band = _sector_path(center, r_out, r_in, 90.0 - span, -14.0)
            fill_dither(painter, band, "accent", 0.5, cell=2)

        # 5. Znaczniki co 10% — ciemne naciecia na torze, czytelne na bialym luku.
        painter.setPen(pen("bg", 1.0))
        for step in range(1, 10):
            angle = math.radians(90.0 - 36.0 * step)
            dx, dy = math.cos(angle), -math.sin(angle)
            painter.drawLine(
                QPointF(center.x() + r_in * dx, center.y() + r_in * dy),
                QPointF(center.x() + r_out * dx, center.y() + r_out * dy),
            )

        # 6. Tekst: duza wartosc, pod nia podpis (bez prostokatnej podkladki).
        value_rect, caption_rect, overline_rect = self._text_layout()
        painter.setFont(mono_font(self.value_font_size(), 300))
        painter.setPen(qcolor("accent"))
        painter.drawText(value_rect, int(Qt.AlignmentFlag.AlignCenter), self._text)

        if self._caption and caption_rect.height() > 0.0:
            painter.setFont(ui_font(self.caption_font_size(), 500))
            painter.setPen(qcolor("text_dim"))
            text = painter.fontMetrics().elidedText(
                self._caption, Qt.TextElideMode.ElideRight, int(caption_rect.width())
            )
            painter.drawText(
                caption_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                text,
            )

        if self._overline and overline_rect.height() > 0.0:
            painter.setFont(ui_font(self.overline_font_size()))
            painter.setPen(qcolor("text_mute"))
            text = painter.fontMetrics().elidedText(
                self._overline, Qt.TextElideMode.ElideRight, int(overline_rect.width())
            )
            painter.drawText(
                overline_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                text,
            )
        painter.end()


class DitheredBar(QWidget):
    """Pasek postepu z podzialem na segmenty; krok niepelny znaczy dithering.

    Skaluje sie z szerokoscia (segmenty ponizej 4 px przechodza w plynny pasek),
    a wysokosc bierze z biezacego ``self.rect()`` — nic nie jest ucinane.
    """

    def __init__(self, parent: QWidget | None = None, segments: int = 0, bar_height: int = 10) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._segments = max(0, int(segments))
        self._bar_height = max(MIN_BAR_HEIGHT, int(bar_height))
        self.setMinimumHeight(self._bar_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------------ API
    def set_value(self, value: float) -> None:
        value = clamp01(_as_float(value))
        if value != self._value:
            self._value = value
            self.update()

    def value(self) -> float:
        return self._value

    def set_segments(self, segments: int) -> None:
        self._segments = max(0, int(segments))
        self.update()

    def set_bar_height(self, height: int) -> None:
        self._bar_height = max(MIN_BAR_HEIGHT, int(height))
        self.setMinimumHeight(self._bar_height)
        self.updateGeometry()
        self.update()

    # -------------------------------------------------------------- rozmiary
    def sizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(180, self._bar_height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(48, max(MIN_BAR_HEIGHT, self._bar_height))

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self.update()

    def plot_rect(self) -> QRectF:
        """Wewnetrzny prostokat paska (bez 1 px ramki)."""
        return QRectF(
            1.0,
            1.0,
            max(1.0, float(self.width()) - 2.0),
            max(1.0, float(self.height()) - 2.0),
        )

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        width = float(self.width())
        height = float(self.height())
        if width < 3.0 or height < 3.0:
            painter.end()
            return
        track = QRectF(0.0, 0.0, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("surface2"))
        painter.drawRect(track)
        inner = self.plot_rect()

        if self._value > 0.0 and inner.width() > 0.0 and inner.height() > 0.0:
            segments = self._segments
            if 1 < segments and inner.width() / segments < 4.0:
                segments = 0  # za waski na czytelne segmenty — plynny pasek
            if segments > 1:
                seg_w = inner.width() / segments
                scaled = self._value * segments
                full = int(scaled)
                for index in range(segments):
                    cell = QRectF(
                        inner.left() + index * seg_w + 1.0,
                        inner.top(),
                        max(1.0, seg_w - 2.0),
                        inner.height(),
                    )
                    if index < full:
                        painter.fillRect(cell, qcolor("text_dim"))
                    elif index == full and (scaled - full) > 0.02:
                        fill_dither(painter, cell, "accent", scaled - full, cell=4, bg="surface2")
                painter.setPen(pen("line"))
                for index in range(1, segments):
                    x = inner.left() + index * seg_w
                    painter.drawLine(int(x), int(inner.top()), int(x), int(inner.bottom()))
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                filled = inner.width() * self._value
                solid = max(0.0, filled - 3.0)
                if solid > 0.0:
                    painter.fillRect(QRectF(inner.left(), inner.top(), solid, inner.height()), qcolor("text_dim"))
                if filled > solid:
                    edge = QRectF(
                        inner.left() + solid,
                        inner.top(),
                        max(1.0, filled - solid),
                        inner.height(),
                    )
                    fill_dither(painter, edge, "accent", 0.5, cell=4, bg="surface2")

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen("line"))
        painter.drawRect(track.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


class Sparkline(QWidget):
    """Miniatura przebiegu (lista wartosci 0..1) rysowana linia lamana.

    Rozciaga sie na cala szerokosc widgetu, a wysokosc bierze z ``self.rect()``
    (bez sztywnego ``setFixedHeight``).
    """

    def __init__(self, parent: QWidget | None = None, height: int = 28) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._preferred_height = max(8, int(height))
        self.setMinimumHeight(self._preferred_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values: list[float]) -> None:
        if values is None or isinstance(values, (str, bytes)):
            self._values = []
        else:
            try:
                items = list(values)
            except TypeError:
                items = []
            self._values = [clamp01(_as_float(value)) for value in items]
        self.update()

    # -------------------------------------------------------------- rozmiary
    def sizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(160, self._preferred_height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(32, self._preferred_height)

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self.update()

    def plot_rect(self) -> QRectF:
        """Obszar rysowania linii (z zapasem na znacznik ostatniej wartosci)."""
        return QRectF(
            0.5,
            1.0,
            max(1.0, float(self.width()) - 4.0),
            max(1.0, float(self.height()) - 2.5),
        )

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        plot = self.plot_rect()
        if not self._values:
            painter.setPen(pen("line"))
            painter.drawLine(
                int(plot.left()),
                int(plot.bottom()),
                int(plot.right()),
                int(plot.bottom()),
            )
            painter.end()
            return
        count = len(self._values)
        step = plot.width() / max(1, count - 1)
        painter.setPen(pen("text_dim", 1.0))
        previous = QPointF(plot.left(), plot.bottom() - self._values[0] * plot.height())
        for index, value in enumerate(self._values):
            point = QPointF(plot.left() + index * step, plot.bottom() - value * plot.height())
            painter.drawLine(previous, point)
            previous = point
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(
            QRectF(previous.x() - 2.0, previous.y() - 2.0, 4.0, 4.0).intersected(QRectF(self.rect())),
            qcolor("accent"),
        )
        painter.end()
