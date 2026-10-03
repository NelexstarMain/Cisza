"""Testy ziarna tla: rodzaje, poziomy, cache i miejsca uzycia.

Pilnuje czterech rzeczy:

1. **rodzaje** - ziarno ma kilka charakterow (piksele, grudki, pylki, ciemny
   pieprz), a kazdy z nich zostaje szary (R == G == B),
2. **poziomy** - od ``sand`` do ``coarse`` rosnie i kafelek, i krycie wzoru,
   a deklarowane krycie zgadza sie z pikselami kafelka,
3. **cache i koszt** - kafelek liczy sie raz (ta sama instancja `QPixmap`),
4. **miejsca** - ziarno jest nakladane nie tylko na tlo okna, ale tez na karty,
   puste stany, plakietki, komunikaty, kafle, pasek nawigacji, wykresy, kontrolki
   i dialogi (statyczny skan zrodel).

Wszystko dziala na ``QT_QPA_PLATFORM=offscreen``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    qt = pytest.importorskip("PyQt6.QtWidgets")
    return qt.QApplication.instance() or qt.QApplication([])


def _tx():
    from focuslock.ui.widgets import texture

    return texture


def _render(width: int, height: int, paint):
    """Maluje na czarnym `QImage` i zwraca obraz."""
    from PyQt6.QtGui import QColor, QImage, QPainter

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#000000"))
    painter = QPainter(image)
    paint(painter)
    painter.end()
    return image


def _is_gray(pixel) -> bool:
    return pixel.red() == pixel.green() == pixel.blue()


# ------------------------------------------------------------------- rodzaje
def test_grain_has_several_kinds_and_levels():
    grains = _tx().GRAINS
    names = [item.name for item in grains]
    assert len(grains) >= 7, f"za malo rodzajow ziarna: {names}"
    assert len(set(names)) == len(names)
    # cztery poziomy zwyklego ziarna tworza drabinke od sand do coarse
    assert _tx().LADDER == ("sand", "fine", "medium", "coarse")
    # rozne charakterystyki: grudki (block > 1), pylki i ciemny pieprz
    assert any(item.block > 1 for item in grains), "brak ziarna w grudkach"
    assert any(item.block > 2 for item in grains), "brak grubszych grudek"
    assert any(item.name == "dust" and item.coverage <= 0.05 for item in grains), "brak pylkow"
    assert any(item.tint == "bg" for item in grains), "brak ciemnego ziarna na jasnych tlach"


def test_grain_levels_get_coarser():
    tx = _tx()
    ladder = tx.ladder()
    assert [item.name for item in ladder] == list(tx.LADDER)
    sizes = [item.size for item in ladder]
    coverage = [item.coverage for item in ladder]
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes), f"kafelki nie rosna: {sizes}"
    assert coverage == sorted(coverage) and len(set(coverage)) == len(coverage), (
        f"krycie nie rosnie razem z poziomem: {coverage}"
    )
    assert tx.grain("sand").weight < tx.grain("coarse").weight
    for item in _tx().GRAINS:
        assert item.size % item.block == 0, f"{item.name}: kafelek musi dzielic sie na grudki"
        assert 8 <= item.size <= 192, f"{item.name}: kafelek poza zakresem ({item.size})"
        assert item.alpha <= 26, f"{item.name}: ziarno nie moze byc szumem (alpha {item.alpha})"
        assert item.weight <= 4.5, f"{item.name}: ziarno nie moze byc szara plama"


def test_grain_tiles_are_gray_and_match_declared_coverage(app):
    for item in _tx().GRAINS:
        image = _tx().grain_pixmap(item.name).toImage()
        assert image.width() == item.size and image.height() == item.size
        opaque = 0
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixelColor(x, y)
                if pixel.alpha() == 0:
                    continue
                opaque += 1
                assert _is_gray(pixel), f"{item.name}: ziarno musi byc szare ({x}, {y})"
        actual = opaque / float(image.width() * image.height())
        assert abs(actual - item.coverage) < 0.03, (
            f"{item.name}: deklarowane krycie {item.coverage:.3f} != piksele {actual:.3f}"
        )


def test_clump_grain_is_made_of_blocks(app):
    """Grudki sa zlepione: piksele wystepuja w kwadratach, nie pojedynczo."""
    image = _tx().grain_pixmap("clump").toImage()

    def lit(x: int, y: int) -> bool:
        return image.pixelColor(x, y).alpha() > 0

    blocks = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            if lit(x, y):
                assert lit(x + 1, y) and lit(x, y + 1) and lit(x + 1, y + 1), (
                    f"grudka nie jest kwadratem 2x2 w ({x}, {y})"
                )
                blocks += 1
    assert blocks >= 4, "zbyt malo grudek w kafelku"


def test_grain_has_no_horizontal_stripes(app):
    """Ziarno nie moze dawac poziomych pasow.

    Macierz Bayera (uporzadkowany dithering) ma przy niskich kryciach cale jasne
    i cale ciemne wiersze - na duzym tle czytalo sie to jak pasy, a nie ziarno.
    Detektor: autokorelacja wierszy w odstepie 2 musi byc bliska zeru.
    """
    from math import sqrt
    from PyQt6.QtCore import QRectF

    tx = _tx()
    image = _render(
        256,
        128,
        lambda p: tx.paint_stack(p, QRectF(0.0, 0.0, 256.0, 128.0), tx.BACKDROP_STACK, opacity=0.7),
    )
    means = [
        sum(image.pixelColor(x, y).red() for x in range(256)) / 256.0 for y in range(128)
    ]
    left, right = means[:-2], means[2:]
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denominator = sqrt(
        sum((a - mean_left) ** 2 for a in left) * sum((b - mean_right) ** 2 for b in right)
    )
    correlation = numerator / denominator if denominator else 1.0
    assert correlation < 0.5, f"ziarno ma poziome pasy (autokorelacja {correlation:.2f})"
    # kazdy wiersz ma ziarno (zaden nie jest systematycznie pusty)
    assert min(means) > 0.0


# -------------------------------------------------------------- cache, koszt
def test_grain_pixmap_is_cached_and_cache_is_clearable(app):
    tx = _tx()
    first = tx.grain_pixmap("fine")
    assert tx.grain_pixmap("fine") is first, "kafelek musi byc cache'owany"
    assert tx.grain_pixmap("fine", alpha=5) is not first, "inne krycie = inny kafelek"
    assert tx.grain_pixmap("fine", tint="bg") is not first, "inny kolor = inny kafelek"
    tx.clear_cache()
    assert tx.grain_pixmap("fine") is not first, "clear_cache musi wyczyscic kafle"


def test_fade_layer_is_soft_at_the_end(app):
    """Ziarno z maska wygasa plynnie (bez progu na koncu warstwy)."""
    from PyQt6.QtCore import QRectF

    tx = _tx()
    band = QRectF(0.0, 0.0, 120.0, 60.0)
    image = _render(120, 60, lambda p: tx.paint_grain_fade(p, band, "fine", fade="bottom", span=1.0))

    def lit(y: int) -> int:
        """Suma jasnosci wiersza - krycie maski musi byc widoczne w liczbach."""
        return sum(image.pixelColor(x, y).red() for x in range(120))

    assert lit(0) > 0, "gorna krawedz warstwy musi miec ziarno"
    assert lit(2) > lit(50) > 0, "ziarno musi gasnac wraz z glebia warstwy"
    assert lit(59) == 0, "dolna krawedz nie moze zostawiac progu"


# ------------------------------------------------------------ stos i szarosc
def test_stack_adds_layers_without_breaking_grayscale(app):
    from PyQt6.QtCore import QRectF

    tx = _tx()
    rect = QRectF(0.0, 0.0, 160.0, 120.0)
    one = _render(160, 120, lambda p: tx.paint_stack(p, rect, ("fine",)))
    many = _render(160, 120, lambda p: tx.paint_stack(p, rect, tx.BACKDROP_STACK))

    def brightness(image) -> int:
        return sum(image.pixelColor(x, y).red() for y in range(120) for x in range(160))

    assert brightness(many) > brightness(one), "wiecej poziomow musi dawac bogatsze tlo"
    for y in range(0, 120, 3):
        for x in range(0, 160, 3):
            assert _is_gray(many.pixelColor(x, y)), f"stos ziarna nie moze zmieniac koloru ({x}, {y})"


def test_texture_levels_are_clipped_to_rounded_surface(app):
    """Ziarno powierzchni nie wychodzi poza zaokraglenie (naroznik zostaje czysty)."""
    from PyQt6.QtCore import QRectF

    tx = _tx()
    rect = QRectF(0.0, 0.0, 120.0, 120.0)
    image = _render(120, 120, lambda p: tx.paint_surface(p, rect, radius=20.0, opacity=1.0))

    # naroznik poza lukiem (r = 20, z marginesem 1 px) nie moze dostac ziarna
    outside = 0
    for y in range(0, 19):
        for x in range(0, 19):
            dx = (x + 0.5) - 20.0
            dy = (y + 0.5) - 20.0
            if dx * dx + dy * dy <= 21.0 ** 2:
                continue
            assert image.pixelColor(x, y).red() == 0, f"ziarno wyszlo poza luk ({x}, {y})"
            outside += 1
    assert outside > 40, "test nie sprawdzil naroznika"

    assert sum(
        1 for x in range(2, 118) for y in range(2, 118) if image.pixelColor(x, y).red() > 0
    ) > 100, "srodek powierzchni musi miec ziarno"
    assert any(
        image.pixelColor(x, y).red() > 0 for x in range(0, 4) for y in range(40, 80)
    ), "krawedz prosta (poza lukiem) musi miec ziarno"


# ------------------------------------------------------------------- miejsca
def test_widgets_paint_grain(app):
    from focuslock.ui import theme
    from focuslock.ui.theme import COLORS
    from focuslock.ui.widgets.primitives import Card, EmptyState
    from focuslock.ui.widgets.tiles import Badge, Toast

    theme.apply_to(app)
    base = COLORS["surface"]

    card = Card("TYTUL KARTY")
    card.resize(220, 120)
    card.show()
    app.processEvents()
    card_image = card.grab().toImage()
    brighter = sum(
        1
        for y in range(8, 112)
        for x in range(8, 212)
        if card_image.pixelColor(x, y).red() > int(base[1:3], 16)
    )
    assert brighter > 200, f"karta nie ma ziarna (rozjasnionych pikseli: {brighter})"
    assert card_image.pixelColor(1, 1).name().upper() == base.upper(), "naroznik karty musi zostac czysty"
    card.hide()

    empty = EmptyState("BRAK WPISOW", "sprobuj innego filtra")
    empty.resize(300, 120)
    empty.show()
    app.processEvents()
    empty_image = empty.grab().toImage()
    assert any(
        empty_image.pixelColor(x, y).red() > int(COLORS["surface2"][1:3], 16)
        for y in range(8, 112)
        for x in range(8, 292)
    ), "pusty stan nie ma ziarna"
    empty.hide()

    badge = Badge("HARDCORE")
    badge.show()
    app.processEvents()
    badge_image = badge.grab().toImage()
    assert any(
        badge_image.pixelColor(x, y).red() > int(COLORS["surface2"][1:3], 16)
        for y in range(2, badge_image.height() - 2)
        for x in range(2, badge_image.width() - 2)
    ), "plakietka nie ma ziarna"
    badge.hide()

    toast = Toast()
    toast.show_message("ZABLOKOWANO: Notatnik")
    toast._fade.setOpacity(1.0)  # animacja wjazdu nie moze zjadac podgladu
    app.processEvents()
    toast_image = toast.grab().toImage()
    lit = [toast_image.pixelColor(x, y).red() for y in range(4, 38) for x in range(14, 150)]
    assert max(lit) >= 200, "tekst komunikatu musi zostac czytelny"
    assert sum(1 for value in lit if 0 < value < 120) > 30, "komunikat nie ma ziarna"
    toast.hide()


def test_nav_rail_and_charts_paint_grain(app):
    from focuslock.ui.widgets.graphs import Heatmap, MonochromeChart
    from focuslock.ui.widgets.nav import NavRail

    rail = NavRail(width=168)
    rail.set_items([("home", "START"), ("stats", "STATYSTYKI")])
    rail.resize(168, 240)
    rail.show()
    app.processEvents()
    rail_image = rail.grab().toImage()
    values = {
        rail_image.pixelColor(x, y).red()
        for y in range(120, 230)
        for x in range(60, 160)
    }
    assert len(values) > 3, "pasek nawigacji nie ma ziarna"
    rail.hide()

    chart = MonochromeChart(kind="line", height=180)
    chart.set_series([1.0, 5.0, 3.0, 8.0, 4.0], ["PON", "WTO", "ŚR", "CZW", "PT"])
    chart.resize(420, 200)
    chart.show()
    app.processEvents()
    chart_image = chart.grab().toImage()
    plot = chart.plot_rect()
    grain_pixels = sum(
        1
        for y in range(int(plot.top()), int(plot.bottom()))
        for x in range(int(plot.left()), int(plot.right()))
        if 0 < chart_image.pixelColor(x, y).red() < 40
    )
    assert grain_pixels > 40, "pole wykresu nie ma ziarna"
    chart.hide()

    heat = Heatmap()
    heat.set_matrix([[0.0, 0.4, 0.8], [0.2, 0.6, 1.0]])
    heat.set_legend(True)
    heat.resize(320, 160)
    heat.show()
    app.processEvents()
    heat_image = heat.grab().toImage()
    assert not heat_image.isNull()
    values = {
        heat_image.pixelColor(x, y).red()
        for y in range(heat_image.height())
        for x in range(heat_image.width())
    }
    assert len(values) > 4, "heatmapa nie ma ziarna w przerwach miedzy komorkami"
    heat.hide()


def test_grain_is_used_in_many_places():
    """Statyczny dowod: ziarno jest nakladane w wielu modulach, nie tylko w tle."""
    files: dict[str, int] = {}
    for path in (ROOT / "focuslock").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits = text.count("texture.paint_")
        if hits:
            files[path.relative_to(ROOT).as_posix()] = hits
    assert len(files) >= 9, f"ziarno jest tylko w {len(files)} plikach: {sorted(files)}"
    assert sum(files.values()) >= 15, f"za malo uzyc ziarna: {files}"
    assert "focuslock/app.py" in files, "tlo okna musi uzywac wspolnego ziarna"


def test_grains_report_lists_every_grain():
    tx = _tx()
    report = tx.grains_report()
    for item in tx.GRAINS:
        assert item.name in report, f"brak {item.name} w opisie poziomow"
