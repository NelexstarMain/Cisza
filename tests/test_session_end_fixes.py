"""Regresje: start z widocznym oknem, koniec sesji bez zawieszania, dzialajacy PIN.

Trzy bledy zgloszone przez uzytkownika:
1. aplikacja startowala "w tle" (zminimalizowana),
2. "ZAKONCZ SESJE" nie robilo nic / zawieszalo okno,
3. okno PIN-u bylo tworzone z `Settings` jako rodzicem Qt, wiec kazda sciezka
   wymagajaca PIN-u konczyla sie cicho.
"""
from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

import pytest

from focuslock.config import Settings


# --------------------------------------------------------------------- 1. start
def test_start_minimized_is_off_by_default():
    assert Settings().ui.start_minimized is False


def test_run_shows_window_unless_user_asked_for_background(monkeypatch):
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    source = inspect.getsource(app_module.run)
    assert "settings.ui.start_minimized and settings.ui.tray_icon" in source
    assert "window.show()" in source


def test_closing_window_quits_when_no_session():
    pytest.importorskip("PyQt6")
    from focuslock.app import MainWindow

    source = inspect.getsource(MainWindow.closeEvent)
    assert "QApplication.quit()" in source
    assert "state.get(\"phase\") not in (\"IDLE\", \"DONE\", \"\")" in source


# --------------------------------------------------- 2. koniec sesji (bez helpera)
class _OfflineBridge:
    """Atrapa mostu bez helpera: kazde wywolanie RPC byloby nieudane i wolne."""

    online = False
    last_error = "brak polaczenia"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, method: str, **_kwargs) -> dict:
        self.calls.append(method)
        return {"ok": False, "errors": ["helper niedostepny: brak polaczenia"]}

    def drain_events(self) -> list:
        return []

    def close(self) -> None:
        pass


def _controller(monkeypatch, tmp_path, bridge, *, dry_run=False, hardcore=False, pin=""):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "dane"))
    monkeypatch.delenv("CISZA_SAFE", raising=False)
    if dry_run:
        monkeypatch.setenv("CISZA_DRY_RUN", "1")
    else:
        monkeypatch.delenv("CISZA_DRY_RUN", raising=False)

    from focuslock.controller import Controller
    from focuslock.store import Store

    settings = Settings()
    settings.env_overrides()
    settings.lock.mode = "hardcore" if hardcore else "hard"
    if pin:
        settings.set_pin(pin)
    store = Store(memory=True)
    return Controller(store, settings, bridge=bridge, emit=lambda *a: None, log=lambda *a: None)


def _plan():
    return SimpleNamespace(
        mode="STUDY",
        study_seconds=600,
        break_seconds=300,
        tag="",
        goal_note="",
        allowlist={"apps": ["msedge.exe"], "sites": [], "block_sites": []},
    )


def test_release_without_helper_does_not_call_rpc(monkeypatch, tmp_path):
    bridge = _OfflineBridge()
    controller = _controller(monkeypatch, tmp_path, bridge)
    controller._lockdown(_plan(), 1)
    bridge.calls.clear()  # lockdown prosi helpera (i tak go nie ma) - to nie temat testu

    start = time.time()
    report = controller._release(1, reason="test")
    duration = time.time() - start

    assert bridge.calls == [], f"koniec sesji wolal RPC bez helpera: {bridge.calls}"
    assert duration < 2.0
    step = next(item for item in report["steps"] if item["step"] == "helper")
    assert step["result"]["reason"] == "helper-offline"


def test_request_end_finishes_quickly_without_helper(monkeypatch, tmp_path):
    bridge = _OfflineBridge()
    controller = _controller(monkeypatch, tmp_path, bridge)
    controller.start_study({"apps": ["msedge.exe"]})

    start = time.time()
    result = controller.request_end("user")
    duration = time.time() - start

    assert result["ok"] is True
    assert duration < 5.0, f"koniec sesji trwal {duration:.1f}s"


def test_hardcore_end_reports_error_without_pin_request(monkeypatch, tmp_path):
    bridge = _OfflineBridge()
    controller = _controller(monkeypatch, tmp_path, bridge, hardcore=True)
    controller.start_study({"apps": ["msedge.exe"]})

    result = controller.request_end("user")

    assert result["ok"] is False
    assert result.get("requires_pin") is not True
    assert "hardcore" in " ".join(result["errors"]).lower()


# ------------------------------------------------------------------ 3. okno PIN
def test_pin_dialog_gets_widget_parent_not_settings():
    pytest.importorskip("PyQt6")
    from focuslock.app import MainWindow

    source = inspect.getsource(MainWindow._pin_dialog)
    assert "PinDialog(self, prompt, title)" in source
    assert "PinDialog(self.settings" not in source


def test_request_end_never_returns_silently():
    pytest.importorskip("PyQt6")
    from focuslock.app import MainWindow

    source = inspect.getsource(MainWindow._request_end)
    assert "Nie ma aktywnej sesji." in source
    assert "_ask_end_pin" in source
    assert "self._toast(errors[0][:200])" in source


def test_pin_dialog_is_stays_on_top():
    pytest.importorskip("PyQt6")
    from focuslock.ui.screens.pin import PinDialog

    source = inspect.getsource(PinDialog.__init__)
    assert "WindowStaysOnTopHint" in source
