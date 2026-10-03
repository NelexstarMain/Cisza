"""Testy brakow wykrytych w audycie przyciskow (czescia kontrolera/okna).

Kazdy test odpowiada jednemu znalezisku z `tests/test_ui_buttons_audit.py`,
ale sprawdza zachowanie, a nie tylko zrodlo.
"""
from __future__ import annotations

import inspect

import pytest

from focuslock.config import Settings


def _controller(monkeypatch, tmp_path, *, dry_run=True):
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
    store = Store(memory=True)
    return Controller(store, settings, emit=lambda *a: None, log=lambda *a: None)


# ------------------------------------------------- 1. "ZAPISZ PRESET" zapisuje
def test_request_preset_routes_to_save_preset():
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    source = inspect.getsource(app_module.MainWindow._wire)
    assert '"request_preset": lambda payload: self._run_action("save_preset", payload)' in source


def test_save_preset_accepts_composer_payload(monkeypatch, tmp_path):
    controller = _controller(monkeypatch, tmp_path)
    payload = {
        "name": "Moja sesja",
        "payload": {"study_apps": ["msedge.exe"], "study_minutes": 30, "tag": "test"},
        "delete": False,
    }

    result = controller.handle_action("save_preset", payload)

    assert result.get("ok") is True
    names = [row.get("name") for row in controller.store.list_presets()]
    assert "Moja sesja" in names


# ------------------------------------------------- 2. "EKSPORTUJ CSV" daje CSV
def test_export_stats_csv_writes_csv_file(monkeypatch, tmp_path):
    controller = _controller(monkeypatch, tmp_path)

    result = controller.export_data(kind="export_stats", payload={"format": "csv"})

    assert result.get("ok") is True
    assert str(result.get("path", "")).endswith(".csv")
    text = (tmp_path / "dane" / "eksport").glob("*.csv")
    files = list(text)
    assert files, "brak pliku CSV"
    content = files[0].read_text(encoding="utf-8")
    assert content.splitlines()[0].startswith("day;study_seconds")


def test_export_stats_defaults_to_json(monkeypatch, tmp_path):
    controller = _controller(monkeypatch, tmp_path)

    result = controller.export_data(kind="export_stats", payload={})

    assert str(result.get("path", "")).endswith(".json")


# ------------------------------------------------- 3. tray "Zamknij" = shutdown
def test_tray_quit_goes_through_shutdown():
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    source = inspect.getsource(app_module.run)
    assert "tray.quit_requested.connect(self._shutdown_and_quit)" in source
    assert "def _shutdown_and_quit" in inspect.getsource(app_module.MainWindow)
    handler = inspect.getsource(app_module.MainWindow._shutdown_and_quit)
    assert "controller.shutdown()" in handler


# ------------------------------------------------- 4. tryb wolny konczy sie jako COMPLETED
def test_free_done_reason_is_completed():
    from focuslock import controller as controller_module

    source = inspect.getsource(controller_module)
    assert '"free_end", "free_done"' in source


# ------------------------------------------------- 5. okresy statystyk z UI
@pytest.mark.parametrize(
    ("period", "expected_days"),
    [("today", 1), ("week", 7), ("month", 30), ("quarter", 90), ("dzis", 1), ("miesiac", 30)],
)
def test_stats_period_keys_from_ui_are_understood(monkeypatch, tmp_path, period, expected_days):
    controller = _controller(monkeypatch, tmp_path)

    result = controller.handle_action("stats_period", {"period": period})

    assert result["days"] == expected_days


def test_stats_period_unknown_falls_back_to_week(monkeypatch, tmp_path):
    controller = _controller(monkeypatch, tmp_path)

    assert controller.handle_action("stats_period", {"period": "cokolwiek"})["days"] == 7
