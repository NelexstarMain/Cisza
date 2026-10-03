"""Helper (UAC) musi wystartowac, zanim zacznie sie sesja.

Guard procesow, siec i powloka dzialaja w procesie helpera; bez niego tryb `hard`
nie blokuje niczego poza paskiem zadan (ktory filtruje GUI). Wczesniej ustawienie
`launch_helper_on_start` bylo tylko zapisywane i nic z nim nie robiono.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


class _StubBridge:
    def __init__(self, ensure_ok: bool = True, online: bool = False) -> None:
        self.online = online
        self.last_error = "UAC odrzucone"
        self.calls: list[str] = []
        self._ensure_ok = ensure_ok

    def ensure(self, **_kwargs) -> bool:
        self.calls.append("ensure")
        self.online = self._ensure_ok
        return self._ensure_ok

    def call(self, method: str, **_params) -> dict:
        self.calls.append(method)
        return {"ok": True, "applied": [], "warnings": [], "errors": []}

    def drain_events(self) -> list:
        return []

    def close(self) -> None:
        pass


def _plan() -> SimpleNamespace:
    return SimpleNamespace(
        mode="STUDY",
        study_seconds=600,
        break_seconds=300,
        tag="",
        goal_note="",
        allowlist={"apps": ["msedge.exe"], "sites": [], "block_sites": []},
    )


def _controller(monkeypatch, tmp_path, bridge, *, dry_run=False, launch_helper=True):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "dane"))
    monkeypatch.delenv("CISZA_SAFE", raising=False)
    if dry_run:
        monkeypatch.setenv("CISZA_DRY_RUN", "1")
    else:
        monkeypatch.delenv("CISZA_DRY_RUN", raising=False)

    from focuslock.config import Settings
    from focuslock.controller import Controller
    from focuslock.store import Store

    settings = Settings()
    settings.env_overrides()
    settings.system.launch_helper_on_start = launch_helper
    store = Store(memory=True)
    return Controller(store, settings, bridge=bridge, emit=lambda *a: None, log=lambda *a: None)


def test_lockdown_starts_helper_when_configured(monkeypatch, tmp_path):
    bridge = _StubBridge(ensure_ok=True, online=False)
    controller = _controller(monkeypatch, tmp_path, bridge)

    report = controller._lockdown(_plan(), 1)

    assert "ensure" in bridge.calls
    helper_step = [step for step in report["steps"] if step["step"] == "helper"]
    assert helper_step and helper_step[0]["result"]["ok"] is True


def test_lockdown_warns_when_helper_refused(monkeypatch, tmp_path):
    bridge = _StubBridge(ensure_ok=False, online=False)
    controller = _controller(monkeypatch, tmp_path, bridge)

    report = controller._lockdown(_plan(), 1)

    assert "ensure" in bridge.calls
    warnings = " ".join(report.get("warnings") or [])
    assert "helper" in warnings
    assert "UAC" in warnings


def test_lockdown_skips_helper_when_setting_off(monkeypatch, tmp_path):
    bridge = _StubBridge(ensure_ok=True, online=False)
    controller = _controller(monkeypatch, tmp_path, bridge, launch_helper=False)

    report = controller._lockdown(_plan(), 1)

    assert "ensure" not in bridge.calls
    assert [step for step in report["steps"] if step["step"] == "helper"] == []


def test_lockdown_skips_helper_when_already_online(monkeypatch, tmp_path):
    bridge = _StubBridge(ensure_ok=True, online=True)
    controller = _controller(monkeypatch, tmp_path, bridge)

    controller._lockdown(_plan(), 1)

    assert "ensure" not in bridge.calls


def test_lockdown_dry_run_does_not_ask_for_uac(monkeypatch, tmp_path):
    bridge = _StubBridge(ensure_ok=True, online=False)
    controller = _controller(monkeypatch, tmp_path, bridge, dry_run=True)

    controller._lockdown(_plan(), 1)

    assert "ensure" not in bridge.calls


def test_helper_start_action_is_wired():
    """Akcja `helper_start` z Ustawień ma odbiorcę w kontrolerze (i przycisk w UI)."""
    import inspect

    from focuslock.controller import Controller

    source = inspect.getsource(Controller.handle_action)
    assert '"helper_start"' in source
    pytest.importorskip("PyQt6")
    from focuslock.ui.screens.settings import SettingsScreen

    assert "URUCHOM HELPER (UAC)" in inspect.getsource(SettingsScreen._tab_system)
