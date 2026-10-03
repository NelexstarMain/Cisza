"""Test twardej zasady: caly interfejs jest monochromatyczny (R == G == B).

Sprawdza trzy rzeczy:
1. `theme.COLORS` zawiera wylacznie szarosci,
2. `theme.qss()` nie zawiera ani jednego koloru z nasyceniem,
3. zaden plik `.py` w `focuslock/ui/` nie zawiera nie-szarego hexa
   (skan calego drzewa, nie tylko modulow z sekcji 17).
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import pytest

from focuslock.ui import theme

UI_DIR = Path(__file__).resolve().parents[1] / "focuslock" / "ui"

EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000026ff\U00002700-\U000027bf\U0000fe0f]"
)


def ui_python_files() -> list[Path]:
    return sorted(UI_DIR.rglob("*.py"))


def _image_hash(pixmap) -> str:
    """Hash zawartosci QPixmap (do porownan ikona vs monogram)."""
    image = pixmap.toImage()
    return hashlib.sha1(image.bits().asstring(image.sizeInBytes())).hexdigest()


def test_colors_tokens_are_gray() -> None:
    assert theme.COLORS, "brak tokenow kolorow"
    for name, value in theme.COLORS.items():
        assert theme.HEX_RE.fullmatch(value), f"token {name}={value!r} nie jest kolorem hex"
        assert theme.is_gray(value), f"token {name}={value!r} nie jest szary"


def test_qss_uses_only_gray_colors() -> None:
    offenders = theme.non_gray_colors(theme.qss())
    assert offenders == [], f"nie-szare kolory w theme.qss(): {offenders}"


def test_ui_source_files_have_only_gray_hex() -> None:
    files = ui_python_files()
    assert files, "brak plikow .py w focuslock/ui"
    offenders: dict[str, list[str]] = {}
    for path in files:
        found = theme.non_gray_colors(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(UI_DIR.parent.parent))] = found
    assert offenders == {}, f"nie-szare kolory w zrodlach UI: {offenders}"


def test_ui_sources_have_no_emoji() -> None:
    offenders: dict[str, list[str]] = {}
    for path in ui_python_files():
        text = path.read_text(encoding="utf-8")
        found = EMOJI_RE.findall(text)
        if found:
            offenders[str(path.relative_to(UI_DIR.parent.parent))] = found
    assert offenders == {}, f"emoji w zrodlach UI: {offenders}"


def test_no_network_images_in_ui() -> None:
    """Zadnych obrazkow z sieci: zaden widget nie laduje URL-a ani nie ciagnie HTTP."""
    offenders: dict[str, list[str]] = {}
    loaders = re.compile(r"(?:QPixmap|QIcon|QImage|load|loadFromData|src)\s*\(?\s*[\"']https?://")
    clients = re.compile(r"^\s*(?:import|from)\s+(?:requests|urllib|httpx|aiohttp)\b", re.MULTILINE)
    for path in ui_python_files():
        text = path.read_text(encoding="utf-8")
        found = loaders.findall(text)
        if clients.search(text):
            found = [*found, "http-client"]
        if found:
            offenders[str(path.relative_to(UI_DIR.parent.parent))] = found
    assert offenders == {}, f"ladowanie zasobow sieciowych w UI: {offenders}"


def test_widget_api_matches_contract() -> None:
    """Widgety z sekcji 17 kontraktu istnieja i sa eksportowane."""
    pytest.importorskip("PyQt6.QtWidgets")
    from focuslock.ui import widgets

    for name in (
        "RingProgress",
        "DitheredBar",
        "Hairline",
        "Card",
        "GhostButton",
        "PrimaryButton",
        "StatTile",
        "MonochromeChart",
        "Heatmap",
        "Toast",
        "NavRail",
        "Toggle",
    ):
        assert hasattr(widgets, name), f"brak widgetu {name} w focuslock.ui.widgets"


def test_screens_expose_contract_signals() -> None:
    """Kazdy ekran ma piec sygnalow z sekcji 17 kontraktu (+ `request_screen`)."""
    pytest.importorskip("PyQt6.QtWidgets")
    from focuslock.ui import screens

    expected = (
        "request_start",
        "request_free",
        "request_end",
        "request_settings",
        "request_pin_check",
        "request_screen",
    )
    for name in SCREEN_NAMES:
        screen_cls = getattr(screens, name, None)
        assert screen_cls is not None, f"brak ekranu {name}"
        for signal_name in expected:
            assert hasattr(screen_cls, signal_name), f"{name} nie ma sygnalu {signal_name}"
        assert hasattr(screen_cls, "set_data"), f"{name} nie ma slotu set_data"
        assert hasattr(screen_cls, "update_state"), f"{name} nie ma slotu update_state"
        assert hasattr(screen_cls, "on_app_event"), f"{name} nie ma slotu on_app_event"

    assert hasattr(screens.PinDialog, "request_pin_check")
    assert hasattr(screens.PinDialog, "pin_result")


def test_charts_are_gui_free() -> None:
    """`charts.py` musi byc czysta warstwa danych (bez importu PyQt)."""
    source = (UI_DIR / "charts.py").read_text(encoding="utf-8")
    assert "PyQt" not in source, "charts.py nie moze zalezec od PyQt"

    import focuslock.ui.charts as charts

    assert charts.fmt_hms(3661) == "1:01:01"
    series = charts.bars_from_daily([{"day": "2026-09-30", "study_seconds": 600}])
    assert series["values"] == [10.0]
    assert series["labels"] == ["30.09"]
    heat = charts.heatmap_from_events([{"ts": 1_700_000_000, "kind": "BLOCKED_APP"}])
    assert len(heat["matrix"]) == 7 and all(len(row) == 24 for row in heat["matrix"])


SCREEN_NAMES = (
    "HomeScreen",
    "ComposerScreen",
    "RunningScreen",
    "BreakScreen",
    "FreeScreen",
    "BankScreen",
    "StatsScreen",
    "SettingsScreen",
    "OnboardingScreen",
    "SummaryScreen",
    "JournalScreen",
)


def test_screens_build_offscreen_without_session() -> None:
    """Kazdy ekran buduje sie bez sesji i nie wywraca na dziwnych danych."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PyQt6.QtWidgets")
    from focuslock.ui import screens

    app = qt.QApplication.instance() or qt.QApplication([])
    odd = {"state": [1, 2, 3], "summary": 5, "series": "x", "presets": 7, "plan": 3,
           "result": [], "blocked": None, "settings": 9, "apps": 1, "sites": "y", "events": 42}
    state = {"phase": "STUDY", "mode": "STUDY", "remaining": 120, "total": 1500}

    for name in SCREEN_NAMES:
        window = getattr(screens, name)()
        window.set_data(None)
        window.set_data({"state": state, "summary": {"balance": 60}, "plan": {"study_minutes": 25}})
        window.update_state(state)
        window.on_app_event("blocked_app", {"name": "steam.exe"})
        window.set_data(dict(odd))
        window.set_data("smieci")
        window.update_state(None)
        window.resize(900, 640)
        window.show()
        app.processEvents()
        assert not window.grab().isNull(), f"{name} nie namalowal sie"
        window.close()

    dialog = screens.PinDialog()
    dialog.set_prompt("PODAJ PIN")
    dialog.notify_result(False)
    dialog.notify_result(True)
    dialog.close()


def test_composer_catalog_flow() -> None:
    """Kreator: katalog aplikacji, zaznaczanie kafli, wyszukiwarka i katalog stron."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PyQt6.QtWidgets")
    from focuslock import sitecatalog
    from focuslock.ui import screens

    app = qt.QApplication.instance() or qt.QApplication([])
    composer = screens.ComposerScreen()
    composer.resize(1100, 720)

    assert hasattr(composer, "request_refresh_apps")
    assert not composer._app_empty.isHidden(), "stan pusty listy aplikacji musi byc widoczny"
    assert composer._app_empty.text()

    apps = [
        {"name": "Google Chrome", "exe": "chrome.exe", "source": "startmenu", "icon_path": "", "label": "Google Chrome"},
        {"name": "Visual Studio Code", "exe": "code.exe", "source": "startmenu", "icon_path": "", "label": "VS Code"},
        {"name": "Steam", "exe": "steam.exe", "source": "running", "icon_path": "", "label": "Steam"},
    ]
    composer.on_action_result(
        "catalog_apps", {"ok": True, "apps": apps, "count": 3, "query": "", "cached": True, "summary": {}}
    )
    assert len(composer._app_tiles) == 3
    assert composer._app_empty.isHidden(), "stan pusty znika po otrzymaniu katalogu"
    composer.show()
    app.processEvents()
    assert not composer.grab().isNull()

    # zaznaczanie kafla + chipy + licznik
    composer._toggle_app("chrome.exe")
    assert "chrome.exe" in composer._selected_apps
    assert composer._app_tiles["chrome.exe"].is_selected()
    assert "WYBRANO: 1" in composer._app_selected_caption.text()
    assert composer._app_chips.count() == 1
    composer._toggle_app("chrome.exe")
    assert not composer._selected_apps and composer._app_chips.count() == 0

    # filtr OTWARTE TERAZ zawęża siatkę do pozycji source == running
    composer._app_filter_group.set_value("running", emit=True)
    assert list(composer._app_tiles) == ["steam.exe"]
    composer._app_filter_group.set_value("all", emit=True)

    # wyszukiwarka filtruje lokalnie i pyta kontrolera
    requests: list[tuple] = []
    composer.request_action.connect(lambda name, payload: requests.append((name, dict(payload or {}))))
    composer._app_search.setText("code")
    composer._apply_app_query()
    assert ("catalog_apps", {"query": "code"}) in requests
    assert list(composer._app_tiles) == ["code.exe"]

    # odświeżanie katalogu: sygnał + akcja force + stan SKANUJĘ
    composer._refresh_apps()
    assert ("refresh_apps", {"force": True}) in requests
    assert composer._app_refresh.text() == "SKANUJĘ…"
    composer.on_action_busy("refresh_apps", False)
    assert composer._app_refresh.text() == "ODŚWIEŻ LISTĘ"

    # katalog stron: kategorie + wiersze + wybór i zapis
    payload = sitecatalog.catalog_payload("STUDY")
    composer.on_action_result("catalog_sites", payload)
    assert composer._site_rows["STUDY"], "katalog stron musi zbudowac wiersze"
    host = next(iter(composer._site_rows["STUDY"]))
    composer._toggle_site(host, True)
    assert host in composer._selected_sites
    assert composer._site_chips.count() == 1
    composer._save_sites()
    study_save = [payload for name, payload in requests if name == "save_site_selection"][-1]
    assert study_save["category"] == "STUDY" and study_save["sites"] == [host]

    # zakładka BLOKOWANE: zapis z kategoria BLOCKED + potwierdzenie
    composer._site_tabs.setCurrentIndex(1)
    assert ("catalog_sites", {"kind": "BLOCKED", "query": ""}) in requests, "zmiana zakladki pobiera katalog"
    blocked_payload = {**sitecatalog.catalog_payload("BLOCKED"), "kind": "BLOCKED", "query": ""}
    composer.on_action_result("catalog_sites", blocked_payload)
    blocked_host = next(iter(composer._site_rows["BLOCKED"]))
    composer._toggle_site(blocked_host, True)
    assert composer._site_save.isEnabled(), "przycisk zapisu dziala tez dla blokowanych"
    composer._save_sites()
    blocked_save = [payload for name, payload in requests if name == "save_site_selection"][-1]
    assert blocked_save["category"] == "BLOCKED" and blocked_save["sites"] == [blocked_host]
    composer.on_action_result(
        "save_site_selection",
        {"ok": True, "saved": 1, "category": "BLOCKED", "blocklist_size": 31, "hosts": [blocked_host]},
    )
    assert composer._toast is not None
    assert "DO BLOKOWANYCH" in composer._toast.message()

    # przywracanie domyślnej listy blokad
    composer._restore_blocklist()
    assert ("clear_blocklist", {}) in requests
    assert composer._blocklist_defaults.isEnabled() is False, "przycisk blokuje sie na czas akcji"
    composer.on_action_result("clear_blocklist", {"ok": True, "blocklist_size": 31})
    assert "PRZYWRÓCONO DOMYŚLNĄ LISTĘ (31 STRON)" in composer._toast.message()
    assert composer._blocklist_defaults.isEnabled() is True
    composer._site_tabs.setCurrentIndex(0)

    # pusty katalog stron -> komunikat
    composer.on_action_result("catalog_sites", {"ok": True, "kind": "STUDY", "query": "", "categories": []})
    assert not composer._site_empty_STUDY.isHidden()

    composer.close()
    _ = app


def test_icon_cache_gray_and_monogram(tmp_path) -> None:
    """IconCache: ikona z pliku .exe, monogram dla brakujacej sciezki, szarosc pikseli."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PyQt6.QtWidgets")
    from focuslock.ui.icons import IconCache, desaturate

    app = qt.QApplication.instance() or qt.QApplication([])
    cache = IconCache()

    existing = Path(sys.executable)
    pixmap = cache.pixmap(str(existing), 32, gray=True)
    assert not pixmap.isNull() and pixmap.width() > 0
    assert cache.pixmap(str(existing), 32, True) is pixmap, "cache musi zwracac ten sam obiekt"

    image = pixmap.toImage()
    opaque = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() == 0:
                continue
            opaque += 1
            assert pixel.red() == pixel.green() == pixel.blue(), "odbarwiona ikona musi miec R == G == B"
    assert opaque > 0, "ikona powinna miec nieprzezroczyste piksele"

    monogram = cache.pixmap(str(tmp_path / "nie-ma-takiego-pliku.exe"), 40, gray=True)
    assert not monogram.isNull()
    assert monogram.width() == 40 and monogram.height() == 40
    assert cache.monogram("", 24).isNull() is False

    colored = cache.pixmap(str(existing), 48, gray=False)
    assert not colored.isNull()
    cache.clear_cache()
    assert cache.stats()["entries"] == 0

    # ikona ze Sklepu: plik .png czytany wprost (nie generyczny glif powloki)
    from PyQt6.QtGui import QColor, QPixmap

    asset = tmp_path / "Square44x44Logo.png"
    picture = QPixmap(64, 64)
    picture.fill(QColor(10, 200, 40, 255))
    assert picture.save(str(asset))
    from_asset = cache.pixmap(str(asset), 40, gray=True)
    assert not from_asset.isNull()
    assert from_asset.width() <= 40 and from_asset.height() <= 40
    middle = from_asset.toImage().pixelColor(from_asset.width() // 2, from_asset.height() // 2)
    assert middle.red() == middle.green() == middle.blue(), "ikona z pliku tez musi byc szara"
    assert _image_hash(from_asset) != _image_hash(cache.monogram(asset.stem, 40)), (
        "plik graficzny nie moze konczyc sie na monogramie"
    )
    assert cache.pixmap(str(tmp_path / "brak.png"), 40, True).isNull() is False

    # statystyki zrodel: plik graficzny vs monogram (po clear_cache liczniki startuja od zera)
    counters = cache.stats()
    assert counters["image"] == 1, f"ikona z pliku graficznego musi byc policzona: {counters}"
    assert counters["monogram"] >= 1, f"brakujaca sciezka musi dac monogram: {counters}"
    assert counters["entries"] == counters["image"] + counters["shell"] + counters["monogram"]

    # bezposrednio: desaturacja zachowuje przezroczystosc
    from PyQt6.QtGui import QColor, QPixmap

    sample = QPixmap(2, 2)
    sample.fill(QColor(200, 30, 40, 128))
    grayed = desaturate(sample)
    assert grayed.toImage().pixelColor(0, 0).alpha() == 128
    _ = app

