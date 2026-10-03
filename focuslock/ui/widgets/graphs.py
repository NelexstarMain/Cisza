"""Wykresy widgetowe: slupki/linia oraz heatmapa (bez matplotlib)."""
from __future__ import annotations

from typing import Callable, Sequence

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter, QPainterPath
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..theme import FONT_SIZES, TYPO, c
from .paint import clamp01, dither_brush, fill_dither, gray_level, pen, qcolor, ui_font


class MonochromeChart(QWidget):
    """Wykres slupkowy albo liniowy w skali szarosci.

    Dane wejsciowe to dwie listy: wartosci i etykiety (opcjonalne).
    """

    def __init__(self, parent: QWidget | None = None, kind: str = "bar", height: int = 190) -> None:
        super().__init__(parent)
        self._kind = kind if kind in ("bar", "line") else "bar"
        self._values: list[float] = []
        self._labels: list[str] = []
        self._highlight = -1
        self._unit = ""
        self._formatter: Callable[[float], str] = lambda value: str(int(round(value)))
        self.setMinimumHeight(int(height))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    # ------------------------------------------------------------------ API
    def set_series(self, values: Sequence[float], labels: Sequence[str] | None = None, kind: str | None = None) -> None:
        self._values = [max(0.0, float(v)) for v in values]
        self._labels = [str(x) for x in (labels or [])]
        if kind in ("bar", "line"):
            self._kind = kind
        self.update()

    def set_data(self, data: dict) -> None:
        """Przyjmuje dict z `charts.py` (`values`, `labels`, `kind`) lub surowa liste."""
        if not isinstance(data, dict):
            return
        values = data.get("values") or data.get("series") or []
        labels = data.get("labels") or []
        kind = data.get("kind")
        self.set_series(values, labels, kind)

    def set_kind(self, kind: str) -> None:
        if kind in ("bar", "line"):
            self._kind = kind
            self.update()

    def kind(self) -> str:
        return self._kind

    def set_highlight(self, index: int) -> None:
        self._highlight = int(index)
        self.update()

    def set_unit(self, unit: str) -> None:
        self._unit = str(unit)
        self.update()

    def set_formatter(self, formatter: Callable[[float], str]) -> None:
        self._formatter = formatter
        self.update()

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        width = float(self.width())
        height = float(self.height())
        top_pad = 22.0
        bottom_pad = 20.0
        left_pad = 4.0
        right_pad = 4.0
        plot = QRectF(left_pad, top_pad, max(1.0, width - left_pad - right_pad), max(1.0, height - top_pad - bottom_pad))

        painter.setFont(ui_font(FONT_SIZES["xs"]))

        baseline_pen = pen("line")
        painter.setPen(baseline_pen)
        painter.drawLine(int(plot.left()), int(plot.bottom()), int(plot.right()), int(plot.bottom()))

        if not self._values:
            painter.setPen(qcolor("text_mute"))
            painter.drawText(plot, int(Qt.AlignmentFlag.AlignCenter), "brak danych")
            painter.end()
            return

        maximum = max(self._values) or 1.0
        if self._kind == "bar":
            self._paint_bars(painter, plot, maximum)
        else:
            self._paint_line(painter, plot, maximum)

        top_value = self._formatter(maximum)
        label = f"MAX {top_value}{(' ' + self._unit) if self._unit else ''}".strip()
        painter.setPen(qcolor("text_mute"))
        painter.drawText(
            QRectF(plot.left(), 0.0, plot.width(), top_pad),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            label,
        )
        painter.end()

    # ------------------------------------------------------------------ czastki
    def _paint_bars(self, painter: QPainter, plot: QRectF, maximum: float) -> None:
        count = len(self._values)
        slot = plot.width() / max(1, count)
        bar_width = min(max(2.0, slot * 0.62), 64.0)
        step = max(1, count // 8)
        for index, value in enumerate(self._values):
            height = plot.height() * (value / maximum) if maximum else 0.0
            x = plot.left() + index * slot + (slot - bar_width) / 2.0
            if value <= 0.0:
                painter.fillRect(QRectF(x, plot.bottom() - 1.0, bar_width, 1.0), qcolor("line"))
                continue
            bar = QRectF(x, plot.bottom() - max(1.0, height), bar_width, max(1.0, height))
            painter.fillRect(bar, gray_level(0.42, "surface2", "text"))
            if bar.height() > 5.0:
                cap = QRectF(bar.x(), bar.y(), bar.width(), 3.0)
                fill_dither(painter, cap, "accent", 0.65, cell=4, bg="surface2")
            if index == self._highlight:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(pen("accent", 1.0))
                painter.drawRect(bar.adjusted(-1.0, -1.0, 1.0, 1.0))
            if index % step == 0 or index == count - 1:
                painter.setPen(qcolor("text_mute"))
                painter.drawText(
                    QRectF(plot.left() + index * slot, plot.bottom() + 2.0, slot, 16.0),
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                    self._label_at(index),
                )

    def _paint_line(self, painter: QPainter, plot: QRectF, maximum: float) -> None:
        count = len(self._values)
        step = plot.width() / max(1, count - 1)
        points = [
            QPointF(plot.left() + index * step, plot.bottom() - (value / maximum) * (plot.height() - 4.0))
            for index, value in enumerate(self._values)
        ]
        area = QPainterPath()
        area.moveTo(points[0].x(), plot.bottom())
        for point in points:
            area.lineTo(point)
        area.lineTo(points[-1].x(), plot.bottom())
        area.closeSubpath()
        fill_dither(painter, area, "text", 0.18, cell=4, bg="surface")

        pen_line = pen("accent_dim", 1.4)
        painter.setPen(pen_line)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for first, second in zip(points, points[1:]):
            painter.drawLine(first, second)
        painter.fillRect(QRectF(points[-1].x() - 2.0, points[-1].y() - 2.0, 4.0, 4.0), qcolor("accent"))

        every = max(1, count // 8)
        for index in range(0, count, every):
            painter.setPen(qcolor("text_mute"))
            painter.drawText(
                QRectF(points[index].x() - step / 2.0, plot.bottom() + 2.0, max(step, 24.0), 16.0),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                self._label_at(index),
            )

    def _label_at(self, index: int) -> str:
        if 0 <= index < len(self._labels):
            return self._labels[index]
        return str(index + 1)


class Heatmap(QWidget):
    """Siatka godzinowa: gestosc ditheringu koduje wartosc (0..1)."""

    def __init__(self, parent: QWidget | None = None, cell: int = 14) -> None:
        super().__init__(parent)
        self._matrix: list[list[float]] = []
        self._row_labels: list[str] = []
        self._col_labels: list[str] = []
        self._cell = max(6, int(cell))
        self._legend = False
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    # ------------------------------------------------------------------ API
    def set_matrix(
        self,
        matrix: Sequence[Sequence[float]],
        row_labels: Sequence[str] | None = None,
        col_labels: Sequence[str] | None = None,
    ) -> None:
        self._matrix = [[clamp01(float(v)) for v in row] for row in matrix]
        self._row_labels = [str(x) for x in (row_labels or [])]
        self._col_labels = [str(x) for x in (col_labels or [])]
        rows = len(self._matrix)
        if rows:
            self.setMinimumHeight(int(self._cell * rows + 24))
        self.update()

    def set_data(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self.set_matrix(data.get("matrix") or [], data.get("row_labels"), data.get("col_labels"))

    def set_legend(self, visible: bool) -> None:
        self._legend = bool(visible)
        self.update()

    def matrix(self) -> list[list[float]]:
        return [list(row) for row in self._matrix]

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        if not self._matrix:
            painter.setPen(qcolor("text_mute"))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "brak danych")
            painter.end()
            return

        rows = len(self._matrix)
        cols = max(len(row) for row in self._matrix)
        if cols <= 0:
            painter.end()
            return
        left = 34.0 if self._row_labels else 0.0
        top = 14.0 if self._col_labels else 0.0
        available_w = max(1.0, self.width() - left - 2.0)
        available_h = max(1.0, self.height() - top - 2.0)
        cell = min(float(self._cell), available_w / cols, available_h / rows)

        for row_index, row in enumerate(self._matrix):
            for col_index in range(cols):
                value = row[col_index] if col_index < len(row) else 0.0
                rect = QRectF(left + col_index * cell, top + row_index * cell, cell - 1.0, cell - 1.0)
                if value <= 0.001:
                    painter.fillRect(rect, qcolor("surface2"))
                else:
                    fill_dither(painter, rect, "text", 0.12 + 0.88 * value, cell=4, bg="surface2")

        painter.setFont(ui_font(FONT_SIZES["xs"]))
        painter.setPen(qcolor("text_mute"))
        if self._row_labels:
            for row_index in range(rows):
                if row_index >= len(self._row_labels):
                    continue
                painter.drawText(
                    QRectF(0.0, top + row_index * cell, left - 6.0, cell),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    self._row_labels[row_index],
                )
        if self._col_labels:
            for col_index in range(cols):
                if col_index >= len(self._col_labels) or not self._col_labels[col_index]:
                    continue
                painter.drawText(
                    QRectF(left + col_index * cell, 0.0, cell, top),
                    int(Qt.AlignmentFlag.AlignCenter),
                    self._col_labels[col_index],
                )
        if self._legend:
            legend = QRectF(left, top + rows * cell + 4.0, 12.0, 8.0)
            painter.setPen(qcolor("text_mute"))
            painter.drawText(
                QRectF(left, top + rows * cell + 2.0, self.width() - left, 14.0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "mniej",
            )
            for index in range(4):
                rect = QRectF(legend.right() + 6.0 + index * 14.0, legend.top(), 12.0, 8.0)
                if index == 0:
                    painter.fillRect(rect, qcolor("surface2"))
                else:
                    fill_dither(painter, rect, "text", index / 3.0, cell=4, bg="surface2")
            painter.drawText(
                QRectF(legend.right() + 68.0, legend.top() - 3.0, 60.0, 14.0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "więcej",
            )
        painter.end()


def dot_matrix(values: Sequence[float], columns: int = 8) -> list[list[float]]:
    """Pomocnik: zamiana plaskiej listy na siatke (uzywana przez ekran statystyk)."""
    values = [clamp01(v) for v in values]
    rows: list[list[float]] = []
    for start in range(0, len(values), max(1, columns)):
        rows.append(values[start : start + max(1, columns)])
    return rows


def dither_preview_brush(density: float):
    """Pomocniczy pedzel (testy/wizualizacje) — zawsze szary."""
    _ = c("text")
    return dither_brush("text", density, 4, bg="surface2")
