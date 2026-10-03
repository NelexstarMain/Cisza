"""Regresja integracji UI ↔ kontroler/okno.

Powód powstania: ekrany wysyłały ~25 akcji (`request_action.emit(...)`), których
kontroler nigdy nie obsługiwał — przyciski były martwe, choć testy sygnałów
przechodziły. Ten plik pilnuje trzech rzeczy:

1. każda akcja wysyłana z `focuslock/ui/**` ma odbiorcę (nawigacja w `app.py`,
   wyjątek obsługiwany przez okno albo gałąź w `Controller.handle_action`),
2. każdy sygnał `request_*` emitowany z ekranów jest podpinany w `MainWindow._wire`,
3. tryb wolny (`extend_free` / `return_free`) rozlicza bank poprawnie,
   a nawigacja `open_composer` naprawdę podmienia ekran.

Wszystko w `CISZA_DATA_DIR` na `tmp_path` i w `CISZA_DRY_RUN=1`; operacje
systemowe są podstawiane atrapami, więc test nie zmienia systemu.
"""
from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = ROOT / "focuslock" / "ui"

ACTION_RE = re.compile(r"""request_action\.emit\(\s*["']([a-z_]+)["']""")
SIGNAL_RE = re.compile(r"self\.(request_[a-z_]+)\.emit\(")

#: Akcje obsługiwane wyłącznie przez okno (`MainWindow._run_action`), a nie przez
#: kontroler: "open_pin_dialog" → `MainWindow._open_pin_dialog` (modalny PinDialog).
UI_ONLY_ACTIONS: frozenset[str] = frozenset({"open_pin_dialog"})


def _locations(pattern: re.Pattern[str]) -> dict[str, list[str]]:
    """Mapa: nazwa akcji/sygnału -> lista miejsc `plik:linia` w focuslock/ui."""
    found: dict[str, list[str]] = {}
    for path in sorted(UI_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.setdefault(match.group(1), []).append(
                f"{path.relative_to(ROOT).as_posix()}:{line}"
            )
    return found


UI_ACTIONS = _locations(ACTION_RE)
UI_SIGNALS = _locations(SIGNAL_RE)


class _FakeBridge:
    """Atrapa helpera: żadna operacja nie wychodzi poza proces."""

    online = False
    last_error = ""

    def call(self, *args, **kwargs) -> dict:
        return {"ok": True, "applied": [], "warnings": [], "errors": []}

    def drain_events(self) -> list[dict]:
        return []

    def close(self) -> None:
        pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data_dir = tmp_path / "cisza-data"
    monkeypatch.setenv("CISZA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CISZA_DRY_RUN", "1")
    monkeypatch.delenv("CISZA_SAFE", raising=False)

    from focuslock import paths

    assert Path(paths.data_dir()) == data_dir
    return data_dir


def _make_controller(env):
    from focuslock.config import Settings
    from focuslock.controller import Controller
    from focuslock.store import Store

    store = Store(memory=False)
    settings = Settings()
    settings.env_overrides()
    assert settings.system.dry_run is True
    return Controller(store, settings, bridge=_FakeBridge(), emit=lambda *a: None, log=lambda *a: None)


# ------------------------------------------------------------------ 1. akcje UI
def test_scan_found_actions_and_signals():
    """Zabezpieczenie przed cichym zepsuciem regexów skanujących."""
    assert len(UI_ACTIONS) >= 20, sorted(UI_ACTIONS)
    assert "open_composer" in UI_ACTIONS
    assert "refresh_bank" in UI_ACTIONS
    assert len(UI_SIGNALS) >= 8, sorted(UI_SIGNALS)
    assert "request_start" in UI_SIGNALS


@pytest.mark.parametrize("action", sorted(UI_ACTIONS), ids=str)
def test_ui_action_has_handler(action, env, monkeypatch):
    """Każda akcja z UI ma odbiorcę; żadna nie kończy się „nieznana akcja”."""
    from focuslock import appcatalog, recovery

    # skan katalogu aplikacji nie może odpytywać systemu w teście
    monkeypatch.setattr(appcatalog, "get_catalog", lambda **kwargs: [])
    # awaryjne przywracanie powłoki tylko z atrapą (bez realnych operacji)
    monkeypatch.setattr(
        recovery,
        "restore_everything",
        lambda **kwargs: {
            "ok": True,
            "applied": [],
            "warnings": [],
            "errors": [],
            "reason": kwargs.get("reason", ""),
        },
    )
    app_module = pytest.importorskip("focuslock.app")

    if action in UI_ONLY_ACTIONS:
        source = inspect.getsource(app_module.MainWindow)
        assert f'"{action}"' in source, f"{action} nie jest obsługiwana w MainWindow ({UI_ACTIONS[action]})"
        return

    controller = _make_controller(env)
    result = controller.handle_action(action, {})
    errors = " ".join(str(item) for item in (result.get("errors") or []))
    assert "nieznana akcja" not in errors, (
        f"akcja {action} z {UI_ACTIONS[action]} nie ma odbiorcy w Controller.handle_action"
    )

    if action in app_module.NAVIGATION_ACTIONS:
        # kontroler ma fallback nawigacji; okno obsługuje ją samo
        assert result.get("navigate") == app_module.NAVIGATION_ACTIONS[action].removeprefix("open_")


def test_every_ui_action_is_mentioned_by_a_handler():
    """Statyczny odpowiednik testu wyżej: akcja musi być widoczna w źródle odbiorcy.

    To złapałoby pierwotny błąd (25 akcji bez żadnej gałęzi w kontrolerze) nawet
    bez uruchamiania kontrolera.
    """
    pytest.importorskip("PyQt6")
    from focuslock.app import NAVIGATION_ACTIONS
    from focuslock.controller import Controller

    source = inspect.getsource(Controller.handle_action)
    mentioned = set(re.findall(r'"([a-z_]+)"', source))
    missing = sorted(
        action
        for action in UI_ACTIONS
        if action not in NAVIGATION_ACTIONS
        and action not in UI_ONLY_ACTIONS
        and action not in mentioned
    )
    assert missing == [], f"akcje bez odbiorcy w Controller.handle_action: {missing}"
    assert "open_composer" in NAVIGATION_ACTIONS


# ---------------------------------------------------------------- 2. sygnały UI
def test_ui_signals_are_wired_in_main_window():
    """Każdy `request_*.emit(...)` z ekranów jest podpięty w `MainWindow._wire`."""
    from focuslock.app import MainWindow

    source = inspect.getsource(MainWindow._wire)
    wired = set(re.findall(r'"(request_[a-z_]+)"', source))
    assert {"request_action", "request_screen"} <= wired

    missing = {name: places for name, places in UI_SIGNALS.items() if name not in wired}
    assert missing == {}, f"sygnały bez podpięcia w _wire: {missing}"


# ------------------------------------------------------- 3. regresja trybu wolnego
def test_free_mode_extend_and_return_restores_bank(env):
    """`extend_free` dokłada minuty, `return_free` oddaje je do banku (refunded)."""
    controller = _make_controller(env)
    controller.store.add_lot(1800, session_id=None, note="test", ttl_days=1, reason="EARNED")
    assert controller.store.bank_balance() == 1800

    started = controller.start_free(10)
    assert started["ok"] is True
    assert controller.store.bank_balance() == 1200, "start wolnego pobiera 10 min z banku"

    extended = controller.handle_action("extend_free", {"seconds": 300})
    assert extended["ok"] is True
    assert extended.get("spent") == 300
    assert controller.store.bank_balance() == 900

    returned = controller.handle_action("return_free", {})
    assert returned["ok"] is True
    assert returned.get("refunded") == 900, "niewykorzystane 15 min musi wrócić do banku"
    assert controller.store.bank_balance() == 1800

    session = controller.store.get_session(started["session_id"])
    assert session["status"] == "COMPLETED"
    assert session["mode"] == "FREE"


def test_start_free_rejects_missing_minutes(env):
    """Regresja: bez wystarczającej liczby minut tryb wolny nie startuje."""
    controller = _make_controller(env)
    controller.store.add_lot(60, session_id=None, note="test", ttl_days=1, reason="EARNED")
    result = controller.start_free(10)
    assert result["ok"] is False
    assert any("bank" in str(err) for err in result.get("errors", []))


# ------------------------------------------------------------- 4. nawigacja okna
def test_open_composer_switches_screen(env):
    """`home.request_action.emit("open_composer")` ma naprawdę zmienić ekran."""
    qt = pytest.importorskip("PyQt6.QtWidgets")
    app = qt.QApplication.instance() or qt.QApplication([])
    assert app is not None

    from focuslock.app import MainWindow

    controller = _make_controller(env)
    window = MainWindow(controller, controller.settings, controller.store)
    try:
        assert window.stack.currentWidget().objectName() == "screen_home"
        assert "home" in window.screens and "composer" in window.screens

        window.screens["home"].request_action.emit("open_composer", {})
        assert window.stack.currentWidget().objectName() == "screen_composer"

        # powrót na start przez ten sam mechanizm nawigacji
        window.screens["composer"].request_action.emit("open_home", {})
        assert window.stack.currentWidget().objectName() == "screen_home"
    finally:
        window.close()
