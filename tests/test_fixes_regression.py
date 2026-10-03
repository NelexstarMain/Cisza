"""Testy regresyjne sprawdzające poprawki krytycznych błędów."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from focuslock.controller import Controller
from focuslock.shell import elevate
from focuslock.store import Store
from focuslock.config import Settings
from focuslock.ui.screens.break_ import BreakScreen
from focuslock.ui.screens.summary import SummaryScreen
from focuslock.ui.screens.home import HomeScreen


def test_elevate_python_launch_command_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = elevate.python_launch_command(["--helper", "--token", "tok123"])
    assert "-m" not in cmd
    assert "focuslock" not in cmd
    assert cmd[0] == str(Path(sys.executable))
    assert cmd[1:] == ["--helper", "--token", "tok123"]


def test_controller_category_normalization_blocked(tmp_path):
    store = Store(tmp_path / "test.db")
    settings = Settings.load(store)
    controller = Controller(store, settings)

    res_app = controller.save_app_profile({"match_value": "game.exe", "category": "BLOCK"})
    assert res_app["ok"] is True
    assert res_app["category"] == "BLOCKED"

    rules = controller.block_rules()
    assert any(r["value"] == "game.exe" for r in rules)

    res_site = controller.save_site_profile({"host": "distraction.com", "category": "BLOCK"})
    assert res_site["ok"] is True
    assert res_site["category"] == "BLOCKED"


def test_controller_build_plan_fallback_to_stored_apps(tmp_path):
    store = Store(tmp_path / "test.db")
    store.upsert_app_profile("Python", "name", "python.exe", "STUDY")
    settings = Settings.load(store)
    controller = Controller(store, settings)

    plan = controller.build_plan({"mode": "STUDY"})
    assert plan is not None
    assert "python.exe" in plan.allowlist.get("apps", [])


def test_summary_screen_navigates_to_home():
    qt = pytest.importorskip("PyQt6.QtWidgets")
    _ = qt.QApplication.instance() or qt.QApplication([])

    screen = SummaryScreen()
    received = []
    screen.request_screen.connect(received.append)
    screen._close.click()
    assert received == ["home"]


def test_break_screen_buttons_emit_skip_break():
    qt = pytest.importorskip("PyQt6.QtWidgets")
    _ = qt.QApplication.instance() or qt.QApplication([])

    screen = BreakScreen()
    received = []
    screen.request_action.connect(lambda action, payload: received.append(action))
    screen._skip.click()
    assert received[-1] == "skip_break"

    screen._resume.click()
    assert received[-1] == "skip_break"


def test_controller_summary_has_complete_data_contract(tmp_path):
    store = Store(tmp_path / "test.db")
    settings = Settings.load(store)
    controller = Controller(store, settings)

    data = controller.summary()
    for key in ("summary", "economy", "daily", "today", "totals", "presets", "lots", "ledger", "series", "events", "blocked", "state"):
        assert key in data, f"Klucz {key} brakuje w controller.summary()"


def test_streak_calculation_uses_cumulative_study_seconds(tmp_path):
    store = Store(tmp_path / "test.db")
    settings = Settings.load(store)
    settings.economy.min_day_study_minutes = 25  # próg: 25 minut (1500 s)
    controller = Controller(store, settings)

    # Sesja 1: 15 minut nauki (900 s)
    s1 = store.start_session("STUDY", 900)
    store.finish_session(s1, "COMPLETED", 900)

    # Sesja 2: uruchomiona przez kontroler
    controller.start_study({"study_minutes": 15})
    controller.engine._study_seconds_done = 900
    summary_s2 = controller._finish(reason="user")
    # Dzisiaj łączny czas (900 + 900 = 1800 s) przekroczył 25 minut, więc dzień powinien zostać zaliczony do serii
    streak_info = summary_s2.get("streak", {})
    assert isinstance(streak_info, dict)
    assert streak_info.get("current", 0) >= 1
