"""Animacje paska nawigacji: lepka substancja, oddech i kropelka hover.

Wszystko dziala offscreen (`QT_QPA_PLATFORM=offscreen`), bez okna i bez
kontrolera. Test pilnuje czterech rzeczy zgloszonych przez uzytkownika:

1. wlasciwosci `blob` i `stretch` naprawde zmieniaja sie w trakcie przejscia,
2. sciezka substancji ma wklęsłe luki przy scianie, rozciaga przod i zweza talie,
3. rozmyta maska goo obsluguje tylko kropelke hover,
4. pod kursorem rysowana jest przygaszona plama,
5. po ukryciu/zamknieciu nie zostaje zaden zywy timer ani animacja
   (brak przeciekow po kilku zmianach pozycji).

Animacje przewijamy przez `setCurrentTime` (deterministycznie), bo globalny
zegar animacji Qt potrafi przestac tykac po wczesniejszych testach w sesji.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

qt = pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import (  # noqa: E402
    QAbstractAnimation,
    QPoint,
    QPointF,
    QPropertyAnimation,
)
from PyQt6.QtGui import QPainterPath  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

from focuslock.ui.widgets.nav import NavRail  # noqa: E402

ITEMS = [("home", "START"), ("bank", "BANK"), ("stats", "STATYSTYKI")]


def _app():
    return qt.QApplication.instance() or qt.QApplication([])


@pytest.fixture
def rail():
    app = _app()
    widget = NavRail(width=160)
    widget.set_items(list(ITEMS))
    widget.resize(160, 240)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    app.processEvents()


def _drive(rail, ms: int) -> None:
    """Przewija animacje bez zegara Qt.

    `setCurrentTime` jest deterministyczne: w tym srodowisku globalny zegar
    animacji Qt potrafi przestac tykac po wczesniejszych testach, a wtedy
    `QTest.qWait` nie posuwa zadnej animacji (nie jest to wina widgetu).
    """
    rail._animation.setCurrentTime(int(ms))
    rail._stretch_animation.setCurrentTime(int(ms))


def _finish_transition(rail) -> None:
    _drive(rail, max(rail._animation.duration(), rail._stretch_animation.duration()))


def test_blob_and_stretch_animate_during_transition(rail):
    """Obie wlasciwosci animacji musza realnie drgnac w trakcie lotu."""
    start_blob = rail._blob
    rail.set_current("stats")
    assert rail._animation.state() == QAbstractAnimation.State.Running
    assert rail._stretch_animation.state() == QAbstractAnimation.State.Running

    _drive(rail, 140)  # polowa lotu (280 ms) i polowa substancji (320 ms)

    assert abs(rail._blob - start_blob) > 2.0, "pigulka nie rusza sie z animacja"
    assert rail._stretch > 0.05, "substancja nie rozciaga sie w trakcie ruchu"
    _finish_transition(rail)
    assert rail._animation.state() != QAbstractAnimation.State.Running


def test_liquid_path_has_concave_fillets_at_the_wall(rail):
    """Pigulka zlewa sie ze sciana wklęsłym lukiem (nie prostym narożnikiem)."""
    top, bottom, right = 60.0, 120.0, 150.0
    path = rail._liquid_path(top, bottom, right)
    wall = NavRail.WALL_X
    fillet = NavRail.FILLET_R

    assert path.boundingRect().left() == pytest.approx(wall), "sciezka nie startuje na scianie"
    # Wysokość ściany nad guzikiem to promień wklęsłego łuku.
    assert path.boundingRect().top() == pytest.approx(top - fillet, abs=1.0)

    # Na scianie i w środku guzika: wewnątrz.
    assert path.contains(QPointF(wall + 0.5, (top + bottom) / 2.0))
    assert path.contains(QPointF(right - 20.0, (top + bottom) / 2.0))
    # Przekatna narożnika jest WYCIETA wklęsłym łukiem (przy wypukłym narożniku
    # ten punkt bylby w środku kształtu).
    assert not path.contains(QPointF(wall + fillet * 0.35, top - fillet * 0.35)), (
        "brak wklęsłego łuku przy scianie"
    )
    # Poniżej łuku (wewnątrz guzika) znowu jest substancja.
    assert path.contains(QPointF(wall + fillet * 0.35, top + 2.0))


def test_substance_stretches_front_edge_and_narrows_waist(rail):
    """Przod wyprzedza srodek, tyl zostaje z tylu, a talia sie zweza."""
    right = 150.0
    fillet = NavRail.FILLET_R
    rail._start = 60.0
    rail._blob = 110.0
    rail._target = 160.0
    rail._stretch = 0.0

    resting = rail._substance_path(right, 40.0).boundingRect()
    # Wysokosc guzika (40) plus dwa wklęsłe łuki po bokach sciany.
    assert resting.height() == pytest.approx(40.0 + 2.0 * fillet, abs=1.5)
    assert resting.right() == pytest.approx(right, abs=1.0)

    rail._stretch = 0.5  # profil sin(pi * 0.5) = 1.0 - pełne rozciągnięcie
    stretched = rail._substance_path(right, 40.0).boundingRect()

    assert stretched.height() > resting.height() + 30.0, "brak rozciągnięcia cieczy"
    assert stretched.right() < right - 10.0, "talia nie zwezila sie"
    # Smuga na scianie cofa się az do pozycji startowej (60 px).
    assert stretched.top() < resting.top() - 10.0, "brak smugi od pozycji startowej"
    assert stretched.left() == pytest.approx(NavRail.WALL_X, abs=1.0)


def test_hover_drop_is_painted(rail):
    """Kropelka pod kursorem zmienia obraz (jest rysowana)."""
    app = _app()
    rail._reset_hover()
    plain = rail.grab().toImage()

    rail._pointer_to(QPointF(30.0, 60.0))
    rail._set_hover(1.0)
    app.processEvents()
    hover = rail.grab().toImage()

    assert not hover.isNull()
    assert hover != plain, "hover nie rysuje zadnej plamy"
    assert hover.pixelColor(30, 60) != plain.pixelColor(30, 60)


def test_goo_mask_is_soft_and_covers_the_shape(rail):
    """Maska goo (rozmycie) ma miekkie krawedzie i trzyma rozmiar widgetu."""
    shape = QPainterPath()
    shape.addEllipse(QPointF(80.0, 120.0), 14.0, 14.0)

    mask = rail._goo_mask(shape)
    assert mask.size() == rail.size()
    alphas = {
        mask.pixelColor(x, y).alpha()
        for x in range(60, 100, 2)
        for y in range(100, 140, 2)
    }
    assert any(0 < value < 255 for value in alphas), "brak miekkiej krawedzi"
    assert alphas != {0}, "maska jest pusta"


def test_goo_mask_runs_only_for_hover(rail, monkeypatch):
    """Rozmycie goo liczy sie tylko dla kropelki hover - nie w bezruchu, nie w locie."""
    calls: list[int] = []
    original = rail._goo_mask

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(rail, "_goo_mask", counting)

    rail._reset_hover()
    rail._set_stretch(0.0)
    rail.grab()
    assert calls == [], "bezruch nie moze uruchamiac rozmycia"

    rail._set_stretch(0.5)
    rail.grab()
    assert calls == [], "wektorowa substancja nie potrzebuje rozmycia"

    rail._pointer_to(QPointF(40.0, 80.0))
    rail._set_hover(1.0)
    rail.grab()
    assert calls, "kropelka hover musi uzyc miekkiej maski"


def test_substance_changes_the_painting_while_moving(rail):
    """Sciezka substancji faktycznie zmienia malowanie paska."""
    rail._set_stretch(0.0)
    resting = rail.grab().toImage()

    rail._set_stretch(0.5)
    moving = rail.grab().toImage()
    assert moving != resting

    rail._set_stretch(0.0)
    assert rail.grab().toImage() == resting


def test_hover_follows_real_mouse_move(rail):
    """Prawdziwy ruch myszy nad paskiem ustawia cel kropelki."""
    app = _app()
    QTest.mouseMove(rail, QPoint(40, 80))
    app.processEvents()
    assert rail._hover_to.y() == pytest.approx(80.0, abs=2.0)
    assert rail._hover_to.x() == pytest.approx(40.0, abs=2.0)


def test_hover_over_button_reaches_event_filter(rail):
    """Pozycje to dzieci, wiec ruch myszy musi docierac filtrem zdarzen."""
    app = _app()
    button = rail._buttons["bank"]
    button.setMouseTracking(True)
    QTest.mouseMove(button, QPoint(20, 10))
    app.processEvents()
    assert rail._hover_to.y() > 0.0, "kropelka nie sledzi kursora nad przyciskiem"


def test_breath_is_subtle_and_pauses_during_transition(rail):
    """Oddech ma 1-2 px, rusza sie w bezruchu i milknie na czas lotu."""
    assert 1.0 <= NavRail.BREATH_AMP <= 2.0
    assert NavRail.BREATH_MS <= 60
    assert NavRail.BREATH_PERIOD_S >= 2.5
    assert rail._breath_timer.isActive(), "brak oddechu w bezruchu"

    rail._breath_phase = 0.25 * NavRail.BREATH_PERIOD_S  # szczyt sinusa
    rail._tick_breath()
    assert rail._breath > 0.5

    rail.set_current("stats")
    assert not rail._breath_timer.isActive(), "oddech musi zamilknac na czas przejscia"
    assert rail._animation.state() == QAbstractAnimation.State.Running

    _finish_transition(rail)
    assert rail._breath_timer.isActive(), "oddech nie wraca po zakonczeniu ruchu"


def test_hide_stops_breath_timer(rail):
    app = _app()
    assert rail._breath_timer.isActive()
    rail.hide()
    app.processEvents()
    assert not rail._breath_timer.isActive()
    assert rail._animation.state() != QAbstractAnimation.State.Running


def test_close_stops_animations_and_timers(rail):
    app = _app()
    rail.set_current("stats")
    rail.close()
    app.processEvents()
    assert rail._animation.state() != QAbstractAnimation.State.Running
    assert rail._stretch_animation.state() != QAbstractAnimation.State.Running
    assert not rail._breath_timer.isActive()
    assert not rail._hover_timer.isActive()


def test_set_items_during_animation_does_not_raise(rail):
    """Przebudowa listy w trakcie lotu: bez wyjatku i bez wiszacej animacji."""
    app = _app()
    rail.set_current("stats")
    _drive(rail, 60)  # jestesmy w polowie lotu
    assert rail._animation.state() == QAbstractAnimation.State.Running
    rail.set_items([("a", "A"), ("b", "B")])
    app.processEvents()
    assert rail.keys() == ["a", "b"]
    assert rail.current() in rail.keys()
    assert rail._animation.state() != QAbstractAnimation.State.Running
    assert rail._stretch_animation.state() != QAbstractAnimation.State.Running


def _own_animations(widget) -> list[QPropertyAnimation]:
    """Animacje nalezace do samego paska (pozycje maja wlasne, np. `bloom`)."""
    return [anim for anim in widget.findChildren(QPropertyAnimation) if anim.parent() is widget]


def test_no_animation_leak_after_three_moves(rail):
    """Trzy zmiany pozycji nie moga tworzyc nowych QPropertyAnimation."""
    app = _app()
    before = len(_own_animations(rail))
    assert before == 3, f"nieoczekiwana liczba animacji paska: {before}"

    for key in ("bank", "stats", "home"):
        rail.set_current(key)
        assert rail._animation.state() == QAbstractAnimation.State.Running
        _finish_transition(rail)
    app.processEvents()

    after = len(_own_animations(rail))
    assert after == before, f"wyciek animacji: {before} -> {after}"
    assert rail._animation.state() != QAbstractAnimation.State.Running
    assert rail._stretch_animation.state() != QAbstractAnimation.State.Running
