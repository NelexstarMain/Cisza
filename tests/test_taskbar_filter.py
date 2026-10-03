"""Testy filtra paska zadan: tylko dozwolone aplikacje zostaja na pasku.

Czesc czysta (`plan`, `allowed`, migracja ustawien) dziala na kazdym systemie,
a sciezki dotykajace COM/WinAPI sa uruchamiane tylko na Windows i zawsze
z podstawiona atrapa `ITaskbarList` - testy nie zmieniaja prawdziwego paska.
"""
from __future__ import annotations

import json

import pytest

from focuslock import config
from focuslock.shell import taskbarfilter


WINDOWS_ONLY = pytest.mark.skipif(not taskbarfilter.IS_WINDOWS, reason="filtrowanie paska zadan: tylko Windows")


def _window(hwnd: int, name: str, pid: int = 1000, title: str = "okno", minimized: bool = False) -> dict:
    return {"hwnd": hwnd, "pid": pid, "name": name, "title": title, "class": "Chrome_WidgetWin_1", "minimized": minimized}


# --------------------------------------------------------------------- plan()
def test_plan_hides_windows_outside_allowlist():
    windows = [_window(1, "msedge.exe"), _window(2, "notepad.exe"), _window(3, "steam.exe")]
    result = taskbarfilter.plan(windows, apps=["msedge.exe"])
    assert result["hide"] == [2, 3]
    assert result["keep"] == [1]


def test_plan_keeps_system_safe_and_own_processes():
    windows = [
        _window(1, "explorer.exe"),
        _window(2, "svchost.exe"),
        _window(3, "pythonw.exe", pid=4242),
        _window(4, "notepad.exe", pid=777),
    ]
    result = taskbarfilter.plan(windows, apps=["msedge.exe"], own_pids=[777])
    assert result["keep"] == [1, 2, 3, 4]
    assert result["hide"] == []


def test_plan_keeps_wildcards_and_parents():
    windows = [_window(1, "msedge.exe"), _window(2, "msedge_proxy.exe"), _window(3, "unknown.exe")]
    result = taskbarfilter.plan(windows, apps=["ms*"], parents=["*_proxy.exe"])
    assert result["keep"] == [1, 2]
    assert result["hide"] == [3]


def test_plan_reports_windows_without_process_name():
    windows = [{"hwnd": 9, "pid": 0, "name": "", "title": "?"}]
    result = taskbarfilter.plan(windows, apps=["msedge.exe"])
    assert result["hide"] == []
    assert result["skipped"][0]["hwnd"] == 9
    assert result["skipped"][0]["reason"] == "brak-nazwy-procesu"


def test_plan_ignores_broken_entries():
    windows = [{"hwnd": 0}, {"hwnd": "x"}, _window(5, "notepad.exe")]
    result = taskbarfilter.plan(windows, apps=["msedge.exe"])
    assert result["hide"] == [5]


# ------------------------------------------------------------------ allowed()
def test_allowed_matches_rules_dicts_and_names():
    window = _window(1, "MSEDGE.EXE")
    assert taskbarfilter.allowed(window, rules=[{"kind": "name", "value": "msedge.exe"}]) is True
    assert taskbarfilter.allowed(window, apps=["msedge.exe"]) is True
    assert taskbarfilter.allowed(window, apps=["chrome.exe"]) is False


def test_allowed_uses_real_match_rules():
    window = _window(1, "code.exe")
    rules = [{"kind": "name", "value": "code.exe", "label": "VS Code"}]
    assert taskbarfilter.allowed(window, rules=rules) is True


# --------------------------------------------------------------------- apply()
def test_apply_without_allowlist_does_nothing():
    report = taskbarfilter.apply([], force=True)
    assert report["ok"] is True
    assert report["skipped"] == ["pusta-allowlista"]
    assert report["applied"] == []
    assert taskbarfilter.is_active() is False


def test_apply_dry_run_does_not_touch_state():
    windows = [_window(1, "notepad.exe"), _window(2, "msedge.exe")]
    report = taskbarfilter.apply(["msedge.exe"], apps=["msedge.exe"], dry_run=True, windows=windows, force=True)
    assert any("DeleteTab" in item for item in report["applied"]) or report["warnings"]
    assert taskbarfilter.is_active() is False


class _FakeTaskbarList:
    """Atrapa COM: zapisuje wylwolania i udaje sukces."""

    instances: list["_FakeTaskbarList"] = []

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.error = ""
        _FakeTaskbarList.instances.append(self)

    def open(self) -> bool:
        return True

    def add_tab(self, hwnd: int) -> int:
        self.calls.append(("add", hwnd))
        return 0

    def delete_tab(self, hwnd: int) -> int:
        self.calls.append(("delete", hwnd))
        return 0

    def close(self) -> None:
        pass

    @classmethod
    def all_calls(cls) -> list[tuple[str, int]]:
        calls: list[tuple[str, int]] = []
        for instance in cls.instances:
            calls.extend(instance.calls)
        return calls


@pytest.fixture()
def fake_taskbar(monkeypatch):
    _FakeTaskbarList.instances = []
    taskbarfilter.reset_throttle()
    monkeypatch.setattr(taskbarfilter, "TaskbarList", _FakeTaskbarList)
    # Atrapa okien: HWND-y w testach nie istnieja w systemie.
    monkeypatch.setattr(taskbarfilter, "_is_window", lambda hwnd: int(hwnd) > 0)
    monkeypatch.setattr(taskbarfilter, "_visible", lambda hwnd: int(hwnd) > 0)
    return _FakeTaskbarList


@WINDOWS_ONLY
def test_apply_and_restore_round_trip(fake_taskbar):
    windows = [_window(11, "notepad.exe"), _window(12, "msedge.exe"), _window(13, "explorer.exe")]
    report = taskbarfilter.apply(["msedge.exe"], apps=["msedge.exe"], windows=windows, force=True)
    assert report["ok"] is True
    assert report["hidden"] == 1
    assert fake_taskbar.instances[0].calls == [("delete", 11)]
    assert taskbarfilter.filtered_windows() == [11]

    state = json.loads(taskbarfilter.state_path().read_text(encoding="utf-8"))
    assert state["active"] is True
    assert "11" in state["hwnds"]

    taskbarfilter.restore()
    assert taskbarfilter.is_active() is False
    assert ("add", 11) in fake_taskbar.all_calls()


@WINDOWS_ONLY
def test_apply_restores_window_that_became_allowed(fake_taskbar):
    windows = [_window(21, "notepad.exe")]
    taskbarfilter.apply(["msedge.exe"], apps=["msedge.exe"], windows=windows, force=True)
    assert taskbarfilter.filtered_windows() == [21]

    # Allowlista zmieniona na sam Notatnik - jego przycisk ma wrocic na pasek.
    report = taskbarfilter.apply(["notepad.exe"], apps=["notepad.exe"], windows=windows, force=True)
    assert report["restored"] == 1
    assert taskbarfilter.is_active() is False
    assert ("add", 21) in fake_taskbar.all_calls()


@WINDOWS_ONLY
def test_apply_throttles_repeated_calls(fake_taskbar):
    windows = [_window(31, "notepad.exe")]
    first = taskbarfilter.apply(["msedge.exe"], apps=["msedge.exe"], windows=windows, force=False, interval=60, now=100.0)
    assert first["skipped"] == []
    second = taskbarfilter.apply(["msedge.exe"], apps=["msedge.exe"], windows=windows, force=False, interval=60, now=100.5)
    assert second["skipped"] == ["throttle"]
    third = taskbarfilter.apply(["msedge.exe"], apps=["msedge.exe"], windows=windows, force=False, interval=60, now=200.0)
    assert third["skipped"] == []
    taskbarfilter.restore()


def test_restore_without_state_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "dane"))
    report = taskbarfilter.restore()
    assert report["ok"] is True
    assert report["applied"] == ["taskbarfilter:brak-stanu"]


def test_restore_dry_run_does_not_clear_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "dane"))
    taskbarfilter._save_state({"active": True, "updated_at": 1.0, "apps": ["msedge.exe"], "hwnds": {"77": {"name": "steam.exe", "method": "delete_tab"}}})
    report = taskbarfilter.restore(dry_run=True)
    assert report["restored"] == 1
    assert taskbarfilter.filtered_windows() == [77]


def test_status_reports_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "dane"))
    taskbarfilter._save_state({"active": True, "updated_at": 5.0, "apps": ["msedge.exe"], "hwnds": {"5": {"name": "steam.exe"}}})
    state = taskbarfilter.status()
    assert state["active"] is True
    assert state["filtered"] == 1
    assert state["apps"] == ["msedge.exe"]


# ------------------------------------------------------- migracja ustawien
def test_legacy_hide_taskbar_true_now_filters_taskbar():
    settings = config.Settings.from_dict({"lock": {"hide_taskbar": True}})
    assert settings.lock.taskbar_mode == "filtered"
    assert settings.lock.hide_taskbar is False


def test_legacy_hide_taskbar_false_keeps_taskbar_untouched():
    settings = config.Settings.from_dict({"lock": {"hide_taskbar": False}})
    assert settings.lock.taskbar_mode == "keep"


def test_taskbar_mode_hide_mirrors_legacy_flag():
    settings = config.Settings.from_dict({"lock": {"taskbar_mode": "hide"}})
    assert settings.lock.hide_taskbar is True
    assert settings.lock.taskbar_mode == "hide"


def test_unknown_taskbar_mode_falls_back_to_filtered():
    lock = config.LockConfig(taskbar_mode="cokolwiek")
    assert lock.taskbar_mode == "filtered"
    assert lock.hide_taskbar is False


def test_default_settings_keep_taskbar_visible():
    assert config.Settings().lock.taskbar_mode == "filtered"
