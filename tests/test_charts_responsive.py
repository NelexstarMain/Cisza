"""Testy responsywnosci wykresow i wskaznikow (offscreen, bez GUI).

Pilnuje czterech rzeczy:

1. geometria (siatka, legenda, podpisy osi, pierscien, tekst) zawsze miesci sie
   w ``self.rect()`` i jest liczona od nowa po kazdej zmianie rozmiaru
   (zadnych stałych zapisanych w ``__init__``),
2. legenda heatmapy ma zarezerwowane miejsce i jest widoczna po ``set_legend(True)``,
3. podpisy osi/kolumn gescieja wraz ze zwezaniem widgetu (bez nachodzenia),
4. smieciowe dane nie wywracaja malowania.

Wszystko dziala na ``QT_QPA_PLATFORM=offscreen``.
"""
from __future__ import annotations

import inspect
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SIZES = [(240, 120), (520, 200), (900, 380)]
BAR_VALUES = [3.0, 7.0, 1.0, 9.0, 4.0, 0.0, 6.0, 2.0, 8.0, 5.0] * 3
BAR_LABELS = [f"{index + 1:02d}.09" for index in range(len(BAR_VALUES))]
WEEKDAYS = ["PON", "WTO", "ŚR", "CZW", "PT", "SOB", "NDZ"]
HOURS = [f"{hour:02d}" for hour in range(24)]
MATRIX = [[((row * 24 + col) % 9) / 8.0 for col in range(24)] for row in range(7)]


@pytest.fixture(scope="module")
def app():
    qt = pytest.importorskip("PyQt6.QtWidgets")
    return qt.QApplication.instance() or qt.QApplication([])


def inside(rect, widget, tolerance: float = 0.5) -> bool:
    """Czy prostokat miesci sie w ``widget.rect()`` (z tolerancja podpikselowa)."""
    return (
        rect.left() >= -tolerance
        and rect.top() >= -tolerance
        and rect.right() <= widget.width() + tolerance
        and rect.bottom() <= widget.height() + tolerance
    )


def shown(app, widget, width: int, height: int):
    """Ustawia rozmiar, pokazuje widget i sprawdza, ze cos sie namalowalo."""
    widget.resize(width, height)
    widget.show()
    app.processEvents()
    assert (widget.width(), widget.height()) == (width, height), (
        f"widget nie przyjal rozmiaru {width}x{height} (jest {widget.width()}x{widget.height()})"
    )
    assert not widget.grab().isNull(), "widget nie namalowal sie"
    return widget


def chart(values=None, labels=None, kind: str = "bar", height: int = 200):
    from focuslock.ui.widgets.graphs import MonochromeChart

    widget = MonochromeChart(kind=kind, height=height)
    widget.set_series(BAR_VALUES if values is None else values, BAR_LABELS if labels is None else labels)
    return widget


def heatmap(legend: bool = True, cell: int = 16):
    from focuslock.ui.widgets.graphs import Heatmap

    widget = Heatmap(cell=cell)
    widget.set_matrix(MATRIX, WEEKDAYS, HOURS)
    widget.set_legend(legend)
    return widget


# --------------------------------------------------------------- wykres slupkow
@pytest.mark.parametrize("width,height", SIZES, ids=lambda value: str(value))
def test_monochrome_chart_fits_its_rect(app, width: int, height: int) -> None:
    widget = shown(app, chart(), width, height)
    plot = widget.plot_rect()
    assert inside(plot, widget), f"plot_rect poza widgetem: {plot}"
    labels = widget.axis_label_rects()
    assert labels, "brak jakiegokolwiek podpisu osi X"
    for rect in labels:
        assert inside(rect, widget), f"podpis osi poza widgetem: {rect}"
    widget.close()


def test_monochrome_chart_label_density_follows_width(app) -> None:
    narrow = shown(app, chart(), 240, 120)
    narrow_count = len(narrow.axis_label_indices())
    narrow.close()
    wide = shown(app, chart(), 900, 380)
    wide_count = len(wide.axis_label_indices())
    wide.close()
    assert 0 < narrow_count < wide_count, (
        f"gestosc podpisow nie zalezy od szerokosci: waski={narrow_count}, szeroki={wide_count}"
    )


def test_monochrome_chart_labels_do_not_overlap(app) -> None:
    widget = shown(app, chart(), 520, 200)
    rects = widget.axis_label_rects()
    for first, second in zip(rects, rects[1:]):
        assert second.left() >= first.right(), f"podpisy nachodza na siebie: {first} {second}"
    widget.close()


def test_monochrome_chart_line_kind_scales(app) -> None:
    for width, height in SIZES:
        widget = shown(app, chart(kind="line"), width, height)
        assert inside(widget.plot_rect(), widget)
        for rect in widget.axis_label_rects():
            assert inside(rect, widget)
        widget.close()


def test_monochrome_chart_is_readable_when_short(app) -> None:
    """Przy bardzo niskim widgecie (<80 px) nadal rysuje sie bez wyjscia za rect."""
    widget = shown(app, chart(), 320, 72)
    plot = widget.plot_rect()
    assert plot.height() >= 8.0, f"obszar wykresu znikl: {plot}"
    assert inside(plot, widget)
    for rect in widget.axis_label_rects():
        assert inside(rect, widget)
    widget.close()


# -------------------------------------------------------------------- heatmapa
@pytest.mark.parametrize("width,height", SIZES, ids=lambda value: str(value))
def test_heatmap_legend_and_grid_fit(app, width: int, height: int) -> None:
    widget = shown(app, heatmap(), width, height)
    plot = widget.plot_rect()
    legend = widget.legend_rect()
    assert inside(plot, widget), f"plot_rect poza widgetem: {plot}"
    assert not legend.isEmpty(), "legenda wlaczona, ale nie ma dla niej miejsca"
    assert inside(legend, widget), f"legend_rect poza widgetem: {legend}"
    assert legend.height() > 0 and legend.width() > 40

    parts = widget.legend_parts()
    assert len(parts["samples"]) == 4, "legenda musi miec 4 probki"
    for sample in parts["samples"]:
        assert not sample.isEmpty(), f"probka legendy sie nie miesci: {sample}"
        assert inside(sample, widget)
        assert legend.contains(sample), f"probka poza paskiem legendy: {sample}"
    for key in ("mniej", "wiecej"):
        assert not parts[key].isEmpty(), f"podpis legendy '{key}' sie nie miesci"
        assert inside(parts[key], widget)
        assert legend.contains(parts[key])
    widget.close()


def test_heatmap_cell_size_grows_with_space(app) -> None:
    cells = []
    for width, height in SIZES:
        widget = shown(app, heatmap(), width, height)
        cell = widget.cell_size()
        assert 8.0 <= cell <= 40.0, f"rozmiar komorki poza zakresem 8..40 px: {cell}"
        plot = widget.plot_rect()
        assert plot.left() >= widget.row_label_width() - 0.5, "siatka wchodzi na etykiety wierszy"
        assert plot.width() <= widget.width() - widget.row_label_width() + 0.5
        assert inside(plot, widget)
        cells.append(cell)
        widget.close()
    assert cells[0] < cells[-1], f"komorka nie rosnie z miejscem: {cells}"


def test_heatmap_legend_toggle_reserves_space(app) -> None:
    from focuslock.ui.widgets.graphs import Heatmap

    tall = [[((row * 24 + col) % 9) / 8.0 for col in range(24)] for row in range(12)]
    plain = Heatmap(cell=16)
    plain.set_matrix(tall, WEEKDAYS, HOURS)
    plain.set_legend(False)
    with_legend = Heatmap(cell=16)
    with_legend.set_matrix(tall, WEEKDAYS, HOURS)
    with_legend.set_legend(True)

    assert plain.legend_rect().isEmpty(), "legenda wylaczona nie moze zajmowac miejsca"
    assert not with_legend.legend_rect().isEmpty()
    assert with_legend.minimumHeight() > plain.minimumHeight(), (
        "wysokosc minimalna musi rezerwowac miejsce na legende"
    )
    assert with_legend.minimumSizeHint().height() > plain.minimumSizeHint().height()


def test_heatmap_column_labels_densify_on_narrow(app) -> None:
    narrow = shown(app, heatmap(), 240, 120)
    narrow_count = len(narrow.column_label_indices())
    narrow.close()
    wide = shown(app, heatmap(), 900, 380)
    wide_count = len(wide.column_label_indices())
    for first, second in zip(wide.column_label_rects(), wide.column_label_rects()[1:]):
        assert second.left() >= first.right(), f"podpisy kolumn nachodza: {first} {second}"
    for rect in wide.column_label_rects():
        assert inside(rect, wide)
    wide.close()
    assert 0 < narrow_count < wide_count, (
        f"podpisy kolumn nie gescieja: waski={narrow_count}, szeroki={wide_count}"
    )


def test_geometry_is_recomputed_after_resize(app) -> None:
    """Ten sam widget po zmianie rozmiaru liczy geometrie od nowa (bez cache)."""
    widget = chart()
    shown(app, widget, 240, 120)
    narrow_count = len(widget.axis_label_indices())
    widget.resize(900, 380)
    app.processEvents()
    assert len(widget.axis_label_indices()) > narrow_count, (
        "geometria wykresu nie przeliczyla sie po zmianie rozmiaru"
    )
    widget.close()

    heat = heatmap()
    heat.resize(240, 120)
    heat.show()
    app.processEvents()
    small_cell = heat.cell_size()
    heat.resize(900, 380)
    app.processEvents()
    assert heat.cell_size() > small_cell, "rozmiar komorki nie przeliczyl sie po zmianie rozmiaru"
    heat.close()


# -------------------------------------------------------------------- pierscien
@pytest.mark.parametrize(
    "width,height",
    [(110, 110), (124, 124), (240, 120), (320, 320)],
    ids=lambda value: str(value),
)
def test_ring_progress_fits_and_scales_text(app, width: int, height: int) -> None:
    from focuslock.ui.widgets.indicators import RingProgress

    ring = RingProgress(thickness=8)
    ring.set_value(0.62)
    ring.set_text("87")
    ring.set_caption("PUNKTY")
    shown(app, ring, width, height)

    ring_rect = ring.ring_rect()
    assert inside(ring_rect, ring), f"ring_rect poza widgetem: {ring_rect}"
    assert ring_rect.width() > 6.0
    inner = ring.inner_rect()
    value_rect = ring.value_rect()
    caption_rect = ring.caption_rect()
    assert inside(value_rect, ring)
    assert inside(caption_rect, ring)
    assert inner.contains(value_rect), f"wartosc poza dyskiem: {value_rect} vs {inner}"
    assert inner.contains(caption_rect), f"podpis poza dyskiem: {caption_rect} vs {inner}"
    assert caption_rect.top() >= value_rect.bottom(), "podpis nachodzi na wartosc"
    assert ring.value_font_size() >= 9, "wartosc musi miec min. 9 px"
    assert ring.caption_font_size() >= 9, "podpis musi miec min. 9 px"
    ring.close()


def test_ring_progress_value_font_grows_with_diameter(app) -> None:
    from focuslock.ui.widgets.indicators import RingProgress

    sizes = []
    for side in (110, 160, 220, 320):
        ring = RingProgress(thickness=8)
        ring.set_text("87")
        ring.set_caption("PUNKTY")
        shown(app, ring, side, side)
        sizes.append(ring.value_font_size())
        ring.close()
    assert sizes == sorted(sizes), f"stopien pisma wartosci nie rosnie ze srednica: {sizes}"
    assert sizes[-1] > sizes[0]


def test_ring_progress_has_no_texture_under_text(app) -> None:
    """Wewnetrzny dysk jest czysty — zaden dithering nie lezy pod wartoscia."""
    from PyQt6.QtGui import QColor

    from focuslock.ui.theme import COLORS
    from focuslock.ui.widgets.indicators import RingProgress

    ring = RingProgress(thickness=10)
    ring.set_value(0.0)
    ring.set_text("")
    ring.set_caption("")
    shown(app, ring, 220, 220)
    image = ring.grab().toImage()
    surface = QColor(COLORS["surface"])
    inner = ring.inner_rect()
    radius = inner.width() / 2.0 - 4.0
    center_x = inner.center().x()
    center_y = inner.center().y()
    checked = 0
    for y in range(int(inner.top()), int(inner.bottom()), 2):
        for x in range(int(inner.left()), int(inner.right()), 2):
            # Tylko wnetrze dysku (narozniki kwadratu opisujacego sa poza kollem).
            if (x + 0.5 - center_x) ** 2 + (y + 0.5 - center_y) ** 2 > radius**2:
                continue
            pixel = image.pixelColor(x, y)
            assert (pixel.red(), pixel.green(), pixel.blue()) == (
                surface.red(),
                surface.green(),
                surface.blue(),
            ), f"brudna faktura w dysku pierscienia pod tekstem: ({x}, {y}) = {pixel.name()}"
            checked += 1
    assert checked > 100, "test nie sprawdzil zadnej powierzchni"
    ring.close()


def test_ring_progress_draws_value_inside_ring(app) -> None:
    from PyQt6.QtGui import QColor

    from focuslock.ui.theme import COLORS
    from focuslock.ui.widgets.indicators import RingProgress

    ring = RingProgress(thickness=10)
    ring.set_value(0.5)
    ring.set_text("87")
    ring.set_caption("PUNKTY")
    shown(app, ring, 220, 220)
    image = ring.grab().toImage()
    accent = QColor(COLORS["accent"])
    value_rect = ring.value_rect()
    # Probkujemy tylko srodek prostokata wartosci: krawedzie moga dotykac bialego
    # luku postepu, a nas interesuje sam glif.
    box = value_rect.adjusted(
        value_rect.width() * 0.15,
        value_rect.height() * 0.15,
        -value_rect.width() * 0.15,
        -value_rect.height() * 0.15,
    )
    found = 0
    for y in range(int(box.top()), int(box.bottom())):
        for x in range(int(box.left()), int(box.right())):
            pixel = image.pixelColor(x, y)
            if (pixel.red(), pixel.green(), pixel.blue()) == (accent.red(), accent.green(), accent.blue()):
                found += 1
    assert found > 0, "wartosc nie zostala namalowana w pierscieniu"
    ring.close()


def test_ring_progress_has_no_rectangular_backing(app) -> None:
    """Podpis nie moze dostawac prostokatnej podkladki (brzydka ramka wokol 'PUNKTY')."""
    from focuslock.ui.widgets.indicators import RingProgress

    source = inspect.getsource(RingProgress.paintEvent)
    assert "drawRoundedRect" not in source, "podpis nadal ma prostokatna podkladke"
    assert "backing" not in source, "podpis nadal ma prostokatna podkladke"


# ------------------------------------------------------------ pasek i sparkline
@pytest.mark.parametrize("height", [6, 10, 12, 22], ids=lambda value: str(value))
def test_indicators_scale_with_size(app, height: int) -> None:
    from focuslock.ui.widgets.indicators import DitheredBar, Sparkline

    bar = DitheredBar(segments=0, bar_height=height)
    bar.set_value(0.42)
    shown(app, bar, 300, height + 8)
    assert inside(bar.plot_rect(), bar)
    assert bar.maximumHeight() > bar.minimumHeight(), "pasek nie moze miec sztywnej wysokosci"
    bar.close()

    spark = Sparkline(height=height)
    spark.set_values([0.1, 0.5, 0.9, 0.2, 0.0, 1.0])
    shown(app, spark, 260, height + 10)
    assert inside(spark.plot_rect(), spark)
    assert spark.maximumHeight() > spark.minimumHeight(), "sparkline nie moze miec sztywnej wysokosci"
    spark.close()


def test_dithered_bar_segmented_falls_back_when_narrow(app) -> None:
    """Za waski pasek na segmenty nie ucina sie — rysuje sie plynnie."""
    from focuslock.ui.widgets.indicators import DitheredBar

    bar = DitheredBar(segments=12, bar_height=12)
    bar.set_value(0.42)
    shown(app, bar, 48, 12)
    assert inside(bar.plot_rect(), bar)
    bar.close()


# ------------------------------------------------------------------ zle dane
def test_widgets_survive_garbage_data(app) -> None:
    from focuslock.ui.widgets.graphs import Heatmap, MonochromeChart
    from focuslock.ui.widgets.indicators import DitheredBar, RingProgress, Sparkline

    chart_widget = MonochromeChart(height=150)
    for data in (None, [], "smieci", ["a", None, "3", object()], {"values": "x", "labels": 7}):
        if isinstance(data, dict):
            chart_widget.set_data(data)
        else:
            chart_widget.set_series(data)
        chart_widget.resize(300, 120)
        chart_widget.show()
        app.processEvents()
        assert not chart_widget.grab().isNull()
    chart_widget.close()

    heat = Heatmap(cell=16)
    heat.set_legend(True)
    for matrix in (None, [], [[]], [None, "x"], [["a", None, 1], [2]], [{"x": 1}]):
        heat.set_matrix(matrix, None, None)
        heat.resize(260, 140)
        heat.show()
        app.processEvents()
        assert not heat.grab().isNull()
        assert inside(heat.legend_rect(), heat)
    heat.close()

    ring = RingProgress()
    for value in (None, "zle", -5.0, 2.0, float("nan")):
        ring.set_value(value)
        ring.set_text(None)
        ring.set_caption(None)
        ring.show()
        app.processEvents()
        assert 0.0 <= ring.value() <= 1.0
        assert not ring.grab().isNull()
    ring.close()

    bar = DitheredBar()
    bar.set_value("zle")
    bar.set_segments(-3)
    bar.show()
    app.processEvents()
    assert not bar.grab().isNull()
    bar.close()

    spark = Sparkline()
    for values in (None, "abc", ["x", None]):
        spark.set_values(values)
        spark.show()
        app.processEvents()
        assert not spark.grab().isNull()
    spark.close()
