"""Wykresy widgetowe: slupki/linia oraz heatmapa (bez matplotlib).

Kazda geometria liczona jest od biezacego ``self.rect()`` (nic nie jest
cache'owane w ``__init__``), dlatego widgety skaluja sie razem z oknem, a ich
mini-metody geometrii (``plot_rect()``, ``legend_rect()``) sa uzywane zarowno
w ``paintEvent``, jak i w testach regresyjnych.
"""
from __future__ import annotations

from typing import Callable, Sequence

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QFontMetrics, QPainter, QPainterPath
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..theme import FONT_SIZES, TYPO, c
from . import texture
from .paint import clamp01, dither_brush, fill_dither, gray_level, pen, qcolor, ui_font

#: Minimalna wysokosc, przy ktorej wykres jest jeszcze czytelny (podpisy osi
#: znikaja, ale slupki i linia zostaja).
MIN_CHART_HEIGHT = 56
#: Minimalna wysokosc heatmapy (naglowek kolumn + wiersz legendy + siatka).
MIN_HEATMAP_HEIGHT = 96
#: Zakres rozmiaru komorki heatmapy (w pikselach).
MIN_HEATMAP_CELL = 8.0
MAX_HEATMAP_CELL = 40.0


def _as_float(value: object, default: float = 0.0) -> float:
    """Bezpieczne rzutowanie na liczbe (dane z UI/DB bywaja smieciowe)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)


def _as_float_list(values: object) -> list[float]:
    """Zamienia cokolwiek iterowalnego na liste liczb (odporne na smieci)."""
    if values is None or isinstance(values, (str, bytes)):
        return []
    try:
        items = list(values)  # type: ignore[arg-type]
    except TypeError:
        return []
    return [_as_float(item) for item in items]


def _elide(metrics: QFontMetrics, text: str, width: float) -> str:
    """Przycina tekst wielokropkiem, gdy nie miesci sie w ``width``."""
    if width <= 1.0 or not text:
        return ""
    return metrics.elidedText(str(text), Qt.TextElideMode.ElideRight, int(max(1.0, width)))


class MonochromeChart(QWidget):
    """Wykres slupkowy albo liniowy w skali szarosci.

    Dane wejsciowe to dwie listy: wartosci i etykiety (opcjonalne).
    Podpisy osi X sa dobierane do szerokosci (mieszcza sie bez nachodzenia),
    a wartosc maksymalna trafia do prawego gornego rogu.
    """

    def __init__(self, parent: QWidget | None = None, kind: str = "bar", height: int = 190) -> None:
        super().__init__(parent)
        self._kind = kind if kind in ("bar", "line") else "bar"
        self._values: list[float] = []
        self._labels: list[str] = []
        self._highlight = -1
        self._unit = ""
        self._formatter: Callable[[float], str] = lambda value: str(int(round(value)))
        # `height` to preferencja (sizeHint), nie sztywne minimum: przy niskim
        # oknie wykres musi sie skurczyc i nadal byc czytelny.
        self._preferred_height = max(MIN_CHART_HEIGHT, int(height))
        self.setMinimumHeight(MIN_CHART_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    # ------------------------------------------------------------------ API
    def set_series(self, values: Sequence[float], labels: Sequence[str] | None = None, kind: str | None = None) -> None:
        self._values = [max(0.0, value) for value in _as_float_list(values)]
        try:
            raw_labels = [] if labels is None or isinstance(labels, (str, bytes)) else list(labels)
        except TypeError:
            raw_labels = []
        self._labels = [str(item) for item in raw_labels]
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

    # -------------------------------------------------------------- rozmiary
    def sizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(360, self._preferred_height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(120, MIN_CHART_HEIGHT)

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        # Geometria nie jest cache'owana — wystarczy odswiezenie.
        self.update()

    # ------------------------------------------------------------- geometria
    def _label_font(self):
        return ui_font(FONT_SIZES["xs"])

    def _text_band(self) -> float:
        """Wysokosc jednej linii podpisu (px)."""
        return float(QFontMetrics(self._label_font()).height())

    def _show_axis_text(self) -> bool:
        band = self._text_band()
        return bool(self._labels) and float(self.height()) >= 2.0 * (band + 2.0) + 12.0

    def _paddings(self) -> tuple[float, float, float, float]:
        """(lewo, gora, prawo, dol) — zawsze <= 1/4 wysokosci, wiec miesci sie w rect."""
        width = float(self.width())
        height = float(self.height())
        band = self._text_band()
        edge_x = min(2.0, width / 4.0)
        top = max(1.0, min(band + 2.0, height * 0.28))
        bottom = (band + 2.0) if self._show_axis_text() else max(1.0, min(3.0, height * 0.12))
        top = min(top, height * 0.25)
        bottom = min(bottom, height * 0.25)
        return edge_x, top, edge_x, bottom

    def plot_rect(self) -> QRectF:
        """Prostokat wykresu (slupki/linia), bez podpisow osi."""
        left, top, right, bottom = self._paddings()
        width = float(self.width())
        height = float(self.height())
        return QRectF(
            left,
            top,
            max(1.0, width - left - right),
            max(1.0, height - top - bottom),
        )

    def _tick_center(self, index: int, plot: QRectF) -> float:
        count = len(self._values)
        if count <= 1:
            return plot.center().x()
        if self._kind == "bar":
            return plot.left() + (index + 0.5) * plot.width() / count
        return plot.left() + index * plot.width() / (count - 1)

    def _label_layout(self) -> list[tuple[int, QRectF]]:
        """Podpisy osi X, ktore mieszcza sie bez nachodzenia (indeks + prostokat)."""
        count = len(self._values)
        if count <= 0 or not self._show_axis_text():
            return []
        plot = self.plot_rect()
        _, _, _, bottom = self._paddings()
        band = max(1.0, min(self._text_band(), bottom - 1.0))
        metrics = QFontMetrics(self._label_font())
        layout: list[tuple[int, QRectF]] = []
        last_right: float | None = None
        for index in range(count):
            text = self._label_at(index)
            if not text:
                continue
            width = max(4.0, float(metrics.horizontalAdvance(text)))
            center = self._tick_center(index, plot)
            left = min(max(center - width / 2.0, 0.0), max(0.0, float(self.width()) - width))
            if last_right is not None and left < last_right + 4.0:
                continue
            layout.append((index, QRectF(left, plot.bottom() + 2.0, width, band)))
            last_right = left + width
        return layout

    def axis_label_indices(self) -> list[int]:
        """Indeksy podpisow osi X widocznych w biezacym rozmiarze."""
        return [index for index, _ in self._label_layout()]

    def axis_label_rects(self) -> list[QRectF]:
        """Prostokaty podpisow osi X widocznych w biezacym rozmiarze."""
        return [rect for _, rect in self._label_layout()]

    # -------------------------------------------------------------- malowanie
    def _paint_plot_texture(self, painter: QPainter, plot: QRectF) -> None:
        """Tlo pola wykresu: dwa poziomy ziarna (bez linii pomocniczych)."""
        if plot.width() < 8.0 or plot.height() < 8.0:
            return
        texture.paint_stack(painter, plot, ("medium", "sand", "clump"), opacity=0.5)

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setFont(self._label_font())
        plot = self.plot_rect()
        self._paint_plot_texture(painter, plot)

        painter.setPen(pen("line"))
        painter.drawLine(int(plot.left()), int(plot.bottom()), int(plot.right()), int(plot.bottom()))
        painter.setPen(Qt.PenStyle.NoPen)

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
        self._paint_axis_labels(painter, plot)
        self._paint_max_label(painter, plot, maximum)
        painter.end()

    # ------------------------------------------------------------------ czastki
    def _paint_bars(self, painter: QPainter, plot: QRectF, maximum: float) -> None:
        count = len(self._values)
        slot = plot.width() / max(1, count)
        bar_width = min(max(2.0, slot * 0.62), 64.0)
        highlight_pen = pen("accent", 1.0)
        for index, value in enumerate(self._values):
            height = plot.height() * (value / maximum) if maximum else 0.0
            x = plot.left() + index * slot + (slot - bar_width) / 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            if value <= 0.0:
                painter.fillRect(QRectF(x, plot.bottom() - 1.0, bar_width, 1.0), qcolor("line"))
                continue
            bar = QRectF(x, plot.bottom() - max(1.0, height), bar_width, max(1.0, height))
            painter.fillRect(bar, gray_level(0.42, "surface2", "text"))
            if bar.height() > 5.0:
                cap = QRectF(bar.x(), bar.y(), bar.width(), min(3.0, bar.height()))
                fill_dither(painter, cap, "accent", 0.65, cell=4, bg="surface2")
            if index == self._highlight:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(highlight_pen)
                painter.drawRect(bar.adjusted(-1.0, -1.0, 1.0, 1.0).intersected(QRectF(self.rect())))
                painter.setPen(Qt.PenStyle.NoPen)

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

        painter.setPen(pen("accent_dim", 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for first, second in zip(points, points[1:]):
            painter.drawLine(first, second)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(
            QRectF(points[-1].x() - 2.0, points[-1].y() - 2.0, 4.0, 4.0).intersected(QRectF(self.rect())),
            qcolor("accent"),
        )

    def _paint_axis_labels(self, painter: QPainter, plot: QRectF) -> None:
        layout = self._label_layout()
        if not layout:
            return
        painter.setPen(qcolor("text_mute"))
        for index, rect in layout:
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                self._label_at(index),
            )

    def _paint_max_label(self, painter: QPainter, plot: QRectF, maximum: float) -> None:
        _, top, _, _ = self._paddings()
        if top < self._text_band() * 0.9:
            return
        top_value = self._formatter(maximum)
        label = f"MAX {top_value}{(' ' + self._unit) if self._unit else ''}".strip()
        rect = QRectF(plot.left(), 0.0, plot.width(), top)
        painter.setPen(qcolor("text_mute"))
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            _elide(painter.fontMetrics(), label, rect.width()),
        )

    def _label_at(self, index: int) -> str:
        if 0 <= index < len(self._labels):
            return self._labels[index]
        return str(index + 1)


class Heatmap(QWidget):
    """Siatka godzinowa: gestosc ditheringu koduje wartosc (0..1).

    Rozmiar komorki liczy sie z dostepnej szerokosci I wysokosci (8..24 px),
    siatka jest centrowana, a legenda ("mniej"/"wiecej" + 4 probki) ma
    zarezerwowane miejsce na dolnej krawedzi widgetu.
    """

    def __init__(self, parent: QWidget | None = None, cell: int = 14) -> None:
        super().__init__(parent)
        self._matrix: list[list[float]] = []
        self._row_labels: list[str] = []
        self._col_labels: list[str] = []
        # `cell` jest teraz preferencja (sizeHint) — realny rozmiar zalezy od miejsca.
        self._cell = max(int(MIN_HEATMAP_CELL), min(int(MAX_HEATMAP_CELL), int(cell)))
        self._legend = False
        self.setMinimumHeight(MIN_HEATMAP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    # ------------------------------------------------------------------ API
    def set_matrix(
        self,
        matrix: Sequence[Sequence[float]],
        row_labels: Sequence[str] | None = None,
        col_labels: Sequence[str] | None = None,
    ) -> None:
        rows: list[list[float]] = []
        if matrix is not None and not isinstance(matrix, (str, bytes)):
            try:
                raw_rows = list(matrix)
            except TypeError:
                raw_rows = []
            for row in raw_rows:
                if row is None or isinstance(row, (str, bytes)):
                    rows.append([])
                    continue
                try:
                    rows.append([clamp01(value) for value in _as_float_list(row)])
                except (TypeError, ValueError):
                    rows.append([])
        self._matrix = rows
        self._row_labels = self._as_labels(row_labels)
        self._col_labels = self._as_labels(col_labels)
        self._sync_minimum_height()
        self.updateGeometry()
        self.update()

    @staticmethod
    def _as_labels(labels: Sequence[str] | None) -> list[str]:
        if labels is None or isinstance(labels, (str, bytes)):
            return []
        try:
            return [str(item) for item in labels]
        except TypeError:
            return []

    def set_data(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self.set_matrix(data.get("matrix") or [], data.get("row_labels"), data.get("col_labels"))

    def set_legend(self, visible: bool) -> None:
        self._legend = bool(visible)
        self._sync_minimum_height()
        self.updateGeometry()
        self.update()

    def matrix(self) -> list[list[float]]:
        return [list(row) for row in self._matrix]

    def legend_visible(self) -> bool:
        return self._legend

    # -------------------------------------------------------------- rozmiary
    def _shape(self) -> tuple[int, int]:
        rows = len(self._matrix)
        cols = max((len(row) for row in self._matrix), default=0)
        return rows, cols

    def _label_font(self):
        return ui_font(FONT_SIZES["xs"])

    def _text_band(self) -> float:
        return float(QFontMetrics(self._label_font()).height())

    def _row_label_natural_width(self) -> float:
        if not self._row_labels:
            return 0.0
        metrics = QFontMetrics(self._label_font())
        width = max(
            (float(metrics.horizontalAdvance(str(label))) for label in self._row_labels if str(label)),
            default=0.0,
        )
        return width + 8.0 if width > 0 else 0.0

    def _row_label_width(self) -> float:
        """Szerokosc kolumny etykiet wierszy (liczona z tekstu, ograniczona do 28%)."""
        natural = self._row_label_natural_width()
        if natural <= 0.0:
            return 0.0
        return min(natural, max(12.0, float(self.width()) * 0.28))

    def row_label_width(self) -> float:
        """Szerokosc kolumny etykiet wierszy (liczona z tekstu)."""
        return self._row_label_width()

    def _col_label_height(self) -> float:
        if not self._col_labels:
            return 0.0
        return min(self._text_band() + 2.0, max(1.0, float(self.height()) - 4.0))

    def _legend_height(self) -> float:
        if not self._legend:
            return 0.0
        return min(self._text_band() + 6.0, max(1.0, float(self.height()) - 4.0))

    def _minimum_height(self, rows: int) -> int:
        bands = self._col_label_height() + self._legend_height()
        return int(max(MIN_HEATMAP_HEIGHT, bands + MIN_HEATMAP_CELL * max(0, rows) + 4.0))

    def _sync_minimum_height(self) -> None:
        rows, _ = self._shape()
        self.setMinimumHeight(self._minimum_height(rows))

    def sizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        rows, cols = self._shape()
        band = self._text_band()
        if rows <= 0 or cols <= 0:
            return QSize(240, MIN_HEATMAP_HEIGHT)
        width = int(self._row_label_natural_width() + self._cell * cols + 4.0)
        height = int(band + 2.0 + self._cell * rows + self._legend_height() + 4.0)
        return QSize(max(120, width), max(MIN_HEATMAP_HEIGHT, height))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        rows, cols = self._shape()
        if rows <= 0 or cols <= 0:
            return QSize(120, MIN_HEATMAP_HEIGHT)
        band = self._text_band()
        width = int(self._row_label_natural_width() + MIN_HEATMAP_CELL * cols + 4.0)
        height = int(band + 2.0 + MIN_HEATMAP_CELL * rows + self._legend_height() + 4.0)
        return QSize(max(120, width), max(MIN_HEATMAP_HEIGHT, height))

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self.update()

    # ------------------------------------------------------------- geometria
    def cell_size(self) -> float:
        """Rozmiar komorki z dostepnego miejsca (gorna granica 40 px)."""
        rows, cols = self._shape()
        if rows <= 0 or cols <= 0:
            return float(self._cell)
        available_w = max(1.0, float(self.width()) - self._row_label_width() - 4.0)
        available_h = max(1.0, float(self.height()) - self._col_label_height() - self._legend_height() - 4.0)
        fit = min(available_w / cols, available_h / rows)
        # Przy normalnych rozmiarach 8..40 px; w ekstremalnie malym widgecie
        # komorka moze byc mniejsza, byle siatka nie wyszla za self.rect().
        return max(1.0, min(MAX_HEATMAP_CELL, fit))

    def plot_rect(self) -> QRectF:
        """Prostokat siatki (wycentrowany miedzy etykietami a legenda)."""
        rows, cols = self._shape()
        if rows <= 0 or cols <= 0:
            return QRectF(0.0, 0.0, 0.0, 0.0)
        cell = self.cell_size()
        grid_w = cell * cols
        grid_h = cell * rows
        left_band = self._row_label_width()
        top_band = self._col_label_height()
        bottom_band = self._legend_height()
        free_w = max(0.0, float(self.width()) - left_band - 4.0)
        free_h = max(0.0, float(self.height()) - top_band - bottom_band - 4.0)
        x = left_band + 2.0 + max(0.0, (free_w - grid_w) / 2.0)
        y = top_band + max(0.0, (free_h - grid_h) / 2.0)
        return QRectF(x, y, grid_w, grid_h)

    def legend_rect(self) -> QRectF:
        """Pasek legendy na dolnej krawedzi (pusty, gdy legenda wylaczona)."""
        if not self._legend:
            return QRectF(0.0, 0.0, 0.0, 0.0)
        height = self._legend_height()
        left = self._row_label_width()
        top = max(0.0, float(self.height()) - height - 2.0)
        width = max(1.0, float(self.width()) - left - 4.0)
        return QRectF(left, top, width, height)

    def legend_parts(self) -> dict:
        """Geometria legendy: podpisy 'mniej'/'wiecej' i 4 probki ditheringu."""
        empty = {"mniej": QRectF(), "wiecej": QRectF(), "samples": []}
        rect = self.legend_rect()
        if rect.isEmpty() or rect.width() < 8.0 or rect.height() < 4.0:
            return empty
        metrics = QFontMetrics(self._label_font())
        text_h = min(float(metrics.height()), rect.height())
        less_w = float(metrics.horizontalAdvance("mniej"))
        more_w = float(metrics.horizontalAdvance("więcej"))
        gap = 4.0
        sample_w = min(
            18.0,
            max(3.0, (rect.width() - less_w - more_w - 4.0 * gap) / 4.0),
        )
        sample_h = max(3.0, min(8.0, rect.height() - 4.0))
        top = rect.top()
        y = top + (rect.height() - sample_h) / 2.0
        x = rect.left()
        mniej = QRectF(x, top, less_w, text_h)
        x += less_w + gap
        samples = []
        for _ in range(4):
            samples.append(QRectF(x, y, sample_w, sample_h))
            x += sample_w + gap
        wiecej = QRectF(x, top, more_w, text_h)
        clip = rect
        return {
            "mniej": mniej.intersected(clip),
            "wiecej": wiecej.intersected(clip),
            "samples": [sample.intersected(clip) for sample in samples],
        }

    def _column_label_layout(self) -> list[tuple[int, QRectF]]:
        rows, cols = self._shape()
        if not self._col_labels or rows <= 0 or cols <= 0 or self._col_label_height() <= 0.0:
            return []
        metrics = QFontMetrics(self._label_font())
        plot = self.plot_rect()
        cell = self.cell_size()
        band = self._col_label_height()
        shown: list[tuple[int, QRectF]] = []
        last_right: float | None = None
        for index in range(cols):
            text = str(self._col_labels[index]) if index < len(self._col_labels) else ""
            if not text:
                continue
            width = max(4.0, float(metrics.horizontalAdvance(text)))
            center = plot.left() + (index + 0.5) * cell
            left = min(max(center - width / 2.0, 0.0), max(0.0, float(self.width()) - width))
            if last_right is not None and left < last_right + 4.0:
                continue
            shown.append((index, QRectF(left, 0.0, width, band)))
            last_right = left + width
        return shown

    def column_label_indices(self) -> list[int]:
        """Indeksy podpisow kolumn, ktore mieszcza sie bez nachodzenia."""
        return [index for index, _ in self._column_label_layout()]

    def column_label_rects(self) -> list[QRectF]:
        """Prostokaty widocznych podpisow kolumn (bez nachodzenia)."""
        return [rect for _, rect in self._column_label_layout()]

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setFont(self._label_font())
        rows, cols = self._shape()
        if rows <= 0 or cols <= 0:
            painter.setPen(qcolor("text_mute"))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "brak danych")
            painter.end()
            return

        cell = self.cell_size()
        plot = self.plot_rect()
        # Miedzy komorkami zostaje 1 px przerwy - zamiast plaskiej plamy lezy tam
        # ziarno (z grudkami), wiec siatka heatmapy czyta sie jak material.
        if plot.width() >= 8.0 and plot.height() >= 8.0:
            texture.paint_stack(painter, plot, ("medium", "sand", "clump"), opacity=0.5)
        for row_index, row in enumerate(self._matrix):
            for col_index in range(cols):
                value = row[col_index] if col_index < len(row) else 0.0
                rect = QRectF(
                    plot.left() + col_index * cell + 0.5,
                    plot.top() + row_index * cell + 0.5,
                    max(1.0, cell - 1.0),
                    max(1.0, cell - 1.0),
                )
                if value <= 0.001:
                    painter.fillRect(rect, qcolor("surface2"))
                else:
                    fill_dither(painter, rect, "text", 0.12 + 0.88 * value, cell=4, bg="surface2")

        self._paint_row_labels(painter, plot, cell)
        self._paint_col_labels(painter)
        self._paint_legend(painter)
        painter.end()

    def _paint_row_labels(self, painter: QPainter, plot: QRectF, cell: float) -> None:
        if not self._row_labels or self._row_label_width() <= 0.0:
            return
        if cell < self._text_band() * 0.8:
            return  # wiersze za niskie na czytelny tekst — nie mazemy po sobie
        painter.setPen(qcolor("text_mute"))
        width = self._row_label_width() - 4.0
        for row_index in range(len(self._matrix)):
            if row_index >= len(self._row_labels):
                continue
            text = str(self._row_labels[row_index])
            if not text:
                continue
            rect = QRectF(0.0, plot.top() + row_index * cell, width, cell)
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _elide(painter.fontMetrics(), text, rect.width()),
            )

    def _paint_col_labels(self, painter: QPainter) -> None:
        layout = self._column_label_layout()
        if not layout:
            return
        painter.setPen(qcolor("text_mute"))
        for index, rect in layout:
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
                str(self._col_labels[index]),
            )

    def _paint_legend(self, painter: QPainter) -> None:
        if not self._legend:
            return
        parts = self.legend_parts()
        if not parts["samples"]:
            return
        painter.setPen(qcolor("text_mute"))
        painter.drawText(
            parts["mniej"],
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "mniej",
        )
        for index, sample in enumerate(parts["samples"]):
            if index == 0:
                painter.fillRect(sample, qcolor("surface2"))
            else:
                fill_dither(painter, sample, "text", index / 3.0, cell=4, bg="surface2")
        painter.drawText(
            parts["wiecej"],
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            "więcej",
        )


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
