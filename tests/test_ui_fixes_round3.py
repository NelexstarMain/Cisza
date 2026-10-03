"""Regresje trzeciej rundy poprawek: podsumowanie, katalog w tle, wyglad.

Pilnuje czterech rzeczy, ktore uzytkownik zglosil jako bledy:
1. ekran podsumowania nie moze wracac po zamknieciu (sesja rozliczana raz),
2. `catalog_apps` nie moze skanowac dysku w watku GUI (lag w KREATORZE),
3. typografia ma spokojniejsze rozstrzelenie i wiekszy tekst podstawowy,
4. tlo okna ma prawie przezroczysta siatke, a pasek boczny rysuje pigulke.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from focuslock import appcatalog  # noqa: E402
from focuslock.config import Settings  # noqa: E402
from focuslock.controller import Controller  # noqa: E402
from focuslock.store import Store  # noqa: E402
from focuslock.ui import theme  # noqa: E402


class _StubBridge:
    """Helper offline: `_lockdown` i `_release` nie moga czekac na nazwane pipe."""

    def __init__(self) -> None:
        self.online = False
        self.calls: list[str] = []

    def ensure(self, **_kwargs) -> bool:
        return False

    def call(self, method: str, **_params):
        self.calls.append(str(method))
        return {"ok": True}

    def drain_events(self) -> list:
        return []


def _controller(tmp_path, monkeypatch, emit=None) -> Controller:
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "cisza.db")
    settings = Settings.load(store)
    settings.system.dry_run = True
    return Controller(store, settings, bridge=_StubBridge(), emit=emit or (lambda event, data: None))


# ------------------------------------------------------- 1. koniec sesji raz
def test_finish_is_idempotent(tmp_path, monkeypatch):
    """Drugie rozliczenie tej samej sesji nie wysyla kolejnego `session_finished`."""
    events: list[tuple[str, dict]] = []
    controller = _controller(tmp_path, monkeypatch, emit=lambda e, d: events.append((e, d)))
    controller.start_study({"study_minutes": 1, "break_minutes": 0})
    controller._finish("user")
    first = [e for e in events if e[0] == "session_finished"]
    assert len(first) == 1
    controller._finish("user")
    assert len([e for e in events if e[0] == "session_finished"]) == 1


def test_tick_after_done_does_not_reopen_summary(tmp_path, monkeypatch):
    """Po fazie DONE kazdy tick wolal `_finish` - ekran podsumowania wracal co sekunde."""
    events: list[tuple[str, dict]] = []
    controller = _controller(tmp_path, monkeypatch, emit=lambda e, d: events.append((e, d)))
    controller.start_study({"study_minutes": 1, "break_minutes": 0})
    controller.engine.finish("auto")  # silnik konczy faze bez kontrolera
    controller.tick()
    assert len([e for e in events if e[0] == "session_finished"]) == 1
    for _ in range(3):
        controller.tick()
    assert len([e for e in events if e[0] == "session_finished"]) == 1, "podsumowanie wraca po ticku"
    assert controller.engine.state().get("phase") == "DONE"


def test_next_session_still_reports_finished(tmp_path, monkeypatch):
    """Flaga nie moze zablokowac rozliczenia KOLEJNEJ sesji."""
    events: list[tuple[str, dict]] = []
    controller = _controller(tmp_path, monkeypatch, emit=lambda e, d: events.append((e, d)))
    controller.start_study({"study_minutes": 1, "break_minutes": 0})
    controller._finish("user")
    controller.start_study({"study_minutes": 1, "break_minutes": 0})
    controller._finish("user")
    assert len([e for e in events if e[0] == "session_finished"]) == 2


# ------------------------------------------------- 2. katalog bez skanu w GUI
def test_catalog_apps_uses_cache_only(tmp_path, monkeypatch):
    """`catalog_apps` z UI nie moze uruchamiac pelnego skanu (PowerShell)."""
    controller = _controller(tmp_path, monkeypatch)
    scanned: list[dict] = []

    def fake_get_catalog(**kwargs):
        scanned.append(dict(kwargs))
        return []

    monkeypatch.setattr(appcatalog, "get_catalog", fake_get_catalog)
    result = controller.handle_action("catalog_apps", {"query": ""})
    assert result["ok"] is True
    assert result["apps"] == []
    assert result["warming"] is True, "UI musi wiedziec, ze katalog dopiero sie buduje"
    assert scanned == [], "catalog_apps nie moze skanowac dysku"


def test_catalog_apps_reads_ready_cache(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    appcatalog.save_cache([appcatalog.CatalogApp("Anki", "anki.exe", source="startmenu")])
    scanned: list[dict] = []
    monkeypatch.setattr(appcatalog, "get_catalog", lambda **kw: scanned.append(kw) or [])
    result = controller.handle_action("catalog_apps", {"query": "ank"})
    assert [app["name"] for app in result["apps"]] == ["Anki"]
    assert result["warming"] is False
    assert scanned == []


def test_refresh_apps_scans_once(tmp_path, monkeypatch):
    """Jedyna sciezka skanu to `refresh_apps` (leci w watek puli w app.py)."""
    controller = _controller(tmp_path, monkeypatch)
    scanned: list[dict] = []

    def fake_get_catalog(**kwargs):
        scanned.append(dict(kwargs))
        return [appcatalog.CatalogApp("Anki", "anki.exe", source="startmenu")]

    monkeypatch.setattr(appcatalog, "get_catalog", fake_get_catalog)
    result = controller.handle_action("refresh_apps", {"force": True})
    assert scanned and scanned[0].get("force") is True
    assert [app["name"] for app in result["apps"]] == ["Anki"]
    assert result["cached"] is False


def test_load_cache_is_memoised(tmp_path, monkeypatch):
    """Odczyt cache z dysku nie moze powtarzac sie przy kazdym znaku w wyszukiwarce."""
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path))
    appcatalog.save_cache([appcatalog.CatalogApp("Anki", "anki.exe", source="startmenu")])
    path = appcatalog.cache_path()
    reads = {"n": 0}
    original = type(path).read_text

    def counting_read(self, *args, **kwargs):
        reads["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", counting_read, raising=False)
    appcatalog._MEM_CACHE.clear()  # jak po restarcie procesu
    first = appcatalog.load_cache()
    second = appcatalog.load_cache()
    assert first is not None and second is not None
    assert reads["n"] == 1, f"cache czytany z dysku {reads['n']} razy"


# ----------------------------------------------------------------- 3. typografia
def test_typography_is_calmer():
    assert theme.ROLE_FONTS["title"][1] <= 2.0, "tytul nie moze miec rozstrzelenia 6 px"
    assert theme.ROLE_FONTS["primary"][1] <= 2.0
    assert theme.FONT_SIZES["body"] >= 14, "tekst podstawowy musi byc czytelny"
    assert theme.TYPO.display_family != theme.TYPO.ui_family


def test_qss_stays_monochrome():
    assert theme.non_gray_colors(theme.qss()) == []


# --------------------------------------------------------------------- 4. wyglad
def test_backdrop_draws_faint_grid():
    qt = pytest.importorskip("PyQt6.QtWidgets")
    app_module = pytest.importorskip("focuslock.app")

    app = qt.QApplication.instance() or qt.QApplication([])
    backdrop = app_module._Backdrop()
    backdrop.resize(200, 120)
    image = backdrop.grab().toImage()
    assert not image.isNull()
    on_line = image.pixelColor(32, 40)
    off_line = image.pixelColor(33, 40)
    assert on_line.red() >= off_line.red(), "siatka nie jest rysowana"
    assert on_line.red() <= 48, f"siatka jest za mocna: {on_line.red()}"
    _ = app


def test_nav_rail_gooey_pill_follows_active_item():
    qt = pytest.importorskip("PyQt6.QtWidgets")
    from focuslock.ui.widgets.nav import NavRail

    app = qt.QApplication.instance() or qt.QApplication([])
    rail = NavRail(width=160)
    rail.set_items([("home", "START"), ("bank", "BANK"), ("stats", "STATYSTYKI")])
    rail.resize(160, 240)
    rail.show()
    app.processEvents()
    assert rail.current() == "home"
    bank = rail._buttons["bank"]
    rail.set_current("bank")
    assert rail._target == pytest.approx(float(bank.geometry().center().y()))
    # Ksztalt gooey: podczas lotu powstaje polaczona kropla (wiecej niz kapsula).
    rail._blob = rail._target - 40.0
    path = rail._blob_path(float(bank.geometry().left()), float(bank.geometry().width()), 40.0)
    assert path.boundingRect().height() > 40.0, "brak polaczonej kropli miedzy pozycjami"
    assert not rail.grab().isNull()
    rail.close()
    _ = app


def test_home_hides_empty_goal_and_status():
    qt = pytest.importorskip("PyQt6.QtWidgets")
    from focuslock.ui.screens.home import HomeScreen

    app = qt.QApplication.instance() or qt.QApplication([])
    home = HomeScreen()
    home.set_data({
        "state": {"phase": "IDLE"},
        "summary": {},
        "plan": {"study_minutes": 25, "break_minutes": 5},
    })
    assert "GOTOWY DO NAUKI" not in home._status.text()
    assert "KONTROLA" in home._status.text()
    assert home._goal.isHidden(), "puste pole celu nie moze zajmowac miejsca"
    assert home._tag.isHidden()
    home.set_data({"plan": {"goal_note": "matura", "tag": "matma"}})
    assert not home._goal.isHidden() and not home._tag.isHidden()
    home.close()
    _ = app
