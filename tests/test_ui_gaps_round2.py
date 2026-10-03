"""Domkniecie brakow z audytu przyciskow (runda 2).

Kazdy test odpowiada jednemu z szesciu punktow, ktore audyt zostawil otwarte:
wybor kopii, "POMIN KROK", pojedynczy skan z "ODSWIEZ LISTE", wynik sesji na
ekranie podsumowania oraz podlaczony pasek nawigacji (NavRail).
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCREENS = ROOT / "focuslock" / "ui" / "screens"


def _read(name: str) -> str:
    return SCREENS.joinpath(name).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------- 1. "PRZYWRÓĆ Z KOPII"
def test_restore_backup_button_opens_file_picker():
    source = _read("settings.py")
    assert "QFileDialog.getOpenFileName" in source
    assert '"restore_database", {"path": chosen}' in source


def test_restore_backup_without_choice_does_not_emit(monkeypatch):
    """Anulowanie wyboru pliku nie moze wysylac zadania bez sciezki."""
    pytest.importorskip("PyQt6")
    source = _read("settings.py")
    picker = source[source.index("def _pick_restore_backup") :]
    picker = picker[: picker.index("def ", 10)]
    assert "Nie wybrano pliku kopii" in picker
    assert picker.index("if not chosen") < picker.index('"restore_database"')


# --------------------------------------------------- 2. "POMIŃ KROK"
def test_onboarding_skip_has_its_own_handler():
    source = _read("onboarding.py")
    assert "self._skip.clicked.connect(self._skip_step)" in source
    assert "self._next.clicked.connect(self._go_next)" in source
    assert "def _skip_step" in source


def test_onboarding_skip_marks_the_step():
    source = _read("onboarding.py")
    skip_body = source[source.index("def _skip_step") :]
    skip_body = skip_body[: skip_body.index("def ", 10)]
    assert "self._skipped.add(self._step)" in skip_body
    assert "KROK POMINIĘTY" in source


# --------------------------------------------------- 3. "ODŚWIEŻ LISTĘ"
def test_composer_refresh_emits_one_request():
    source = _read("composer.py")
    body = source[source.index("def _refresh_apps") :]
    body = body[: body.index("def ", 10)]
    emissions = body.count("request_action.emit(") + body.count("request_refresh_apps.emit(")
    assert emissions == 1, f"odswiezanie katalogu emituje {emissions} zadania"


# --------------------------------------------------- 4. wynik sesji w podsumowaniu
def test_summary_has_session_event_hook():
    source = _read("summary.py")
    assert "def on_app_event" in source
    assert "session_finished" in source


def test_app_passes_session_result_to_summary():
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    show = inspect.getsource(app_module.MainWindow.show_screen)
    assert "extra" in show
    event_handler = inspect.getsource(app_module.MainWindow._on_event)
    assert 'self.show_screen("summary", self._summary_payload(data))' in event_handler


def test_summary_payload_uses_database_row(monkeypatch, tmp_path):
    """`_summary_payload` musi brac dane z wiersza sesji, a nie zerowac ekranu."""
    pytest.importorskip("PyQt6")
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "dane"))
    monkeypatch.setenv("CISZA_DRY_RUN", "1")

    from focuslock import app as app_module
    from focuslock.store import Store

    store = Store(memory=True)
    session_id = store.start_session("STUDY", 1500, tag="matematyka", goal_note="ciagi")
    store.finish_session(session_id, "COMPLETED", 900, 1, 0)

    fake = SimpleNamespace(
        controller=SimpleNamespace(store=store, blocked_attempts=3),
    )
    payload = app_module.MainWindow._summary_payload(fake, {"session_id": session_id, "reason": "user", "economy": {"balance": 750}})

    assert payload["result"]["status"] == "COMPLETED"
    assert payload["result"]["study_seconds"] == 900
    assert payload["result"]["tag"] == "matematyka"
    assert payload["result"]["goal_note"] == "ciagi"
    assert payload["result"]["blocked"] == 3
    assert payload["bank"]["balance"] == 750


# --------------------------------------------------- 5. NavRail podlaczony
def test_nav_rail_is_wired_in_main_window():
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    source = inspect.getsource(app_module.MainWindow._build_nav_rail)
    assert "NavRail" in source
    assert "rail.navigate.connect(self.show_screen)" in source
    assert "self._nav = self._build_nav_rail()" in inspect.getsource(app_module.MainWindow.__init__)


def test_show_screen_syncs_nav_selection():
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    source = inspect.getsource(app_module.MainWindow.show_screen)
    assert "nav.set_current(name)" in source
