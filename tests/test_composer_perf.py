"""Kreator: brak blokujacego skanu katalogu + porcjowanie kafli (testy offscreen).

Pilnuje czterech rzeczy:
1. `ComposerScreen._build()` nie zleca zadnego skanu aplikacji (katalog startuje
   jako "układa się w tle", a nie jako pustka po synchronizacji),
2. `showEvent` pobiera wylacznie cache (`catalog_apps` bez `force`) i tylko raz
   zleca uzupelnienie katalogu w tle, gdy kontroler zglosi `warming`,
3. siatka kafli jest porcjowana (pierwsza porcja + doładowanie), a powtorna
   przebudowa nie tworzy nowych widgetow,
4. `AppTile` rysuje sie w kazdym stanie (zwykly/zaznaczony/hover), ma stala
   wielkosc i dociaga ikone leniwie (tylko dla zbudowanych kafli).

Wszystko dziala bez kontrolera (`QT_QPA_PLATFORM=offscreen`), wiec zaden skan
systemowy nie wychodzi z procesu.
"""
from __future__ import annotations

import hashlib
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

qt = pytest.importorskip("PyQt6.QtWidgets")

from focuslock.ui.screens.composer import APP_BATCH, ComposerScreen
from focuslock.ui.widgets.catalog import AppTile


# --------------------------------------------------------------------- pomoc
class _SignalProbe:
    """Zastepnik sygnalu `request_action` na czas budowy ekranu (zapis emisji)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._slots: list = []

    def __get__(self, instance, owner=None):  # noqa: N805 (deskryptor)
        return self

    def emit(self, *args) -> None:
        name = str(args[0]) if args else ""
        payload = dict(args[1]) if len(args) > 1 and isinstance(args[1], dict) else {}
        self.calls.append((name, payload))
        for slot in list(self._slots):
            slot(*args)

    def connect(self, slot) -> None:
        self._slots.append(slot)


def _patch_signal(probe: _SignalProbe):
    """Podmienia sygnal klasy i zwraca funkcje przywracajaca oryginal."""
    missing = object()
    original = ComposerScreen.__dict__.get("request_action", missing)

    def restore() -> None:
        if original is missing:
            del ComposerScreen.request_action
        else:
            ComposerScreen.request_action = original

    ComposerScreen.request_action = probe
    return restore


@pytest.fixture()
def app():
    instance = qt.QApplication.instance() or qt.QApplication([])
    yield instance


def _apps(count: int, *, with_icons: bool = False) -> list[dict]:
    return [
        {
            "name": f"Aplikacja {index:03d}",
            "exe": f"app{index:03d}.exe",
            "source": "startmenu",
            "icon_path": f"C:/brak/ikony/app{index:03d}.exe" if with_icons else "",
        }
        for index in range(count)
    ]


def _image_hash(pixmap) -> str:
    image = pixmap.toImage()
    return hashlib.sha1(image.bits().asstring(image.sizeInBytes())).hexdigest()


# ------------------------------------------------- 1. brak skanu w _build()
def test_build_does_not_request_any_app_scan(app) -> None:
    """Konstrukcja ekranu nie emituje ani `catalog_apps`, ani `refresh_apps`."""
    probe = _SignalProbe()
    restore = _patch_signal(probe)
    composer = None
    try:
        composer = ComposerScreen()
    finally:
        restore()
    try:
        names = [name for name, _payload in probe.calls]
        assert "catalog_apps" not in names, "kreator skanuje katalog juz przy budowie"
        assert "refresh_apps" not in names, "kreator zleca skan w tle przy budowie"
        assert "installed_apps" not in names
        # Katalog stron jest lokalny (bez skanu systemowego) - to jedyne zadanie startu.
        assert "catalog_sites" in names
        assert not composer._app_empty.isHidden(), "stan pusty musi byc widoczny na starcie"
        assert "W TLE" in composer._app_empty.text(), "zamiast pustki pokazujemy stan w tle"
    finally:
        composer.close()


def test_page_title_is_constant_across_steps(app) -> None:
    """Naglowek nie dubluje kroku: tytul strony to zawsze „KREATOR SESJI"."""
    composer = ComposerScreen()
    try:
        assert composer.page_title() == ComposerScreen.TITLE == "KREATOR SESJI"
        composer._go_next()
        composer._go_next()
        assert composer.page_title() == "KREATOR SESJI"
        assert "KROK 3/3" in composer._markers.text()
    finally:
        composer.close()


# ------------------------------------------- 2. showEvent: cache i jeden skan
def test_show_event_reads_cache_only_and_warms_once(app) -> None:
    composer = ComposerScreen()
    try:
        requests: list[tuple[str, dict]] = []
        composer.request_action.connect(
            lambda name, payload: requests.append((name, dict(payload or {})))
        )
        started = time.perf_counter()
        composer.show()
        app.processEvents()
        elapsed = time.perf_counter() - started
        assert elapsed < 1.5, f"showEvent blokuje watek GUI ({elapsed:.2f}s)"

        assert ("catalog_apps", {"query": ""}) in requests
        assert all(
            "force" not in payload for name, payload in requests if name == "catalog_apps"
        ), "showEvent nie moze zlecac skanu (force)"
        assert not any(name == "refresh_apps" for name, _ in requests), (
            "showEvent nie moze zlecac pelnego skanu - dopiero odpowiedz warming"
        )
        assert not composer._app_empty.isHidden()
        assert "W TLE" in composer._app_empty.text()

        # Kontroler nie ma cache: mowi "warming" -> dokladnie jeden skan w tle.
        composer.on_action_result("catalog_apps", {"ok": True, "apps": [], "query": "", "warming": True})
        warm = [item for item in requests if item[0] == "refresh_apps"]
        assert warm == [("refresh_apps", {"force": True})]
        assert not composer._app_empty.isHidden(), "brak danych to nie pusty katalog"
        assert "W TLE" in composer._app_empty.text()
        assert composer._app_count.text() == "", "w tle nie pokazujemy falszywego '0 aplikacji'"

        # Kolejny wynik warming nie moze zlecac drugiego skanu.
        composer.on_action_result("catalog_apps", {"ok": True, "apps": [], "query": "", "warming": True})
        assert len([1 for name, _ in requests if name == "refresh_apps"]) == 1

        # Wynik skanu z danymi konczy stan "w tle".
        composer.on_action_result(
            "refresh_apps", {"ok": True, "apps": _apps(4), "query": "", "cached": False}
        )
        assert len(composer._app_tiles) == 4
        assert composer._app_empty.isHidden()
    finally:
        composer.close()


def test_pusty_katalog_bez_warming_pokazuje_prawdziwy_stan(app) -> None:
    """Gotowy (nie-warming) katalog z zerem pozycji ma czytelny pusty stan."""
    composer = ComposerScreen()
    try:
        composer.on_action_result(
            "catalog_apps", {"ok": True, "apps": [], "query": "", "cached": True}
        )
        assert not composer._app_empty.isHidden()
        assert "W TLE" not in composer._app_empty.text()
        assert composer._app_count.text() == "ZNALEZIONO: 0"
    finally:
        composer.close()


# -------------------------------------------------------- 3. porcjowanie siatki
def test_grid_is_chunked_for_300_apps(app) -> None:
    composer = ComposerScreen()
    try:
        composer.resize(1100, 720)
        composer.on_action_result(
            "catalog_apps", {"ok": True, "apps": _apps(300), "query": "", "cached": True}
        )
        assert len(composer._app_tiles) == APP_BATCH, "pierwsza porcja ma stala dlugosc"
        assert not composer._app_more.isHidden(), "jest co doładować"

        sizes = {(tile.width(), tile.height()) for tile in composer._app_tiles.values()}
        assert len(sizes) == 1, f"kafle musza miec jedna wielkosc: {sizes}"

        while not composer._app_more.isHidden():
            before = len(composer._app_tiles)
            composer._show_more_apps()
            assert len(composer._app_tiles) > before
        assert len(composer._app_tiles) == 300
        assert composer._app_count.text() == "ZNALEZIONO: 300"
    finally:
        composer.close()


def test_rebuild_rechecks_state_instead_of_recreating_widgets(app) -> None:
    composer = ComposerScreen()
    try:
        composer.resize(1100, 720)
        composer.on_action_result(
            "catalog_apps", {"ok": True, "apps": _apps(300), "query": "", "cached": True}
        )
        for _ in range(4):
            composer._rebuild_app_grid()
        widgets = composer._app_grid_host.findChildren(AppTile)
        assert len(widgets) == len(composer._app_tiles) <= APP_BATCH

        # Zmiana filtra przebudowuje siatke, ale nadal trzyma sie porcji.
        composer._app_search.setText("aplikacja 00")
        composer._apply_app_query()
        assert composer._app_tiles
        assert len(composer._app_grid_host.findChildren(AppTile)) == len(composer._app_tiles)
        assert len(composer._app_tiles) <= APP_BATCH

        # Zaznaczenie po przebudowie tylko aktualizuje istniejacy kafel.
        key = next(iter(composer._app_tiles))
        tile = composer._app_tiles[key]
        composer._on_app_filter_changed("all")
        composer.on_action_result(
            "catalog_apps", {"ok": True, "apps": _apps(300), "query": "", "cached": True}
        )
        assert key in composer._app_tiles
        composer._toggle_app(key)
        assert composer._app_tiles[key].is_selected()
        _ = tile
    finally:
        composer.close()


# --------------------------------------------------------- 4. leniwe ikony
def test_icons_load_only_for_built_tiles(app, monkeypatch) -> None:
    from focuslock.ui import icons as icons_module

    loaded: list[str] = []
    original = icons_module.ICONS.pixmap

    def spy(path, size=40, gray=True):
        loaded.append(str(path))
        return original(path, size, gray)

    monkeypatch.setattr(icons_module.ICONS, "pixmap", spy)
    composer = ComposerScreen()
    try:
        composer.on_action_result(
            "catalog_apps",
            {"ok": True, "apps": _apps(300, with_icons=True), "query": "", "cached": True},
        )
        assert len(composer._app_tiles) == APP_BATCH
        assert len(loaded) == APP_BATCH, f"ikony policzone dla {len(loaded)} kafli, nie {APP_BATCH}"
    finally:
        composer.close()


# ------------------------------------------------------------- 5. wyglad kafla
def test_app_tile_paints_all_states(app) -> None:
    tile = AppTile(
        "code.exe",
        "Visual Studio Code — edytor kodu źródłowego",
        "code.exe",
        icon_size=40,
        width=172,
    )
    try:
        assert tile.width() == 172
        assert tile.height() == AppTile._height_for(40)
        assert tile.toolTip()

        normal = tile.grab()
        assert not normal.isNull()

        tile.set_selected(True)
        selected = tile.grab()
        assert not selected.isNull()
        assert _image_hash(selected) != _image_hash(normal)

        tile.set_hover(True)
        hovered = tile.grab()
        assert not hovered.isNull()
        assert _image_hash(hovered) != _image_hash(selected)

        tile.set_selected(False)
        tile.set_hover(False)
        assert not tile.grab().isNull()
    finally:
        tile.close()
