"""Test integracyjny odtwarzania po crashie (heartbeat + recovery + startup).

Wszystko dzieje sie w ``CISZA_DATA_DIR`` wskazujacym na ``tmp_path``, a operacje
na powloce sa wolane wylacznie z ``dry_run=True``, wiec test nie zmienia systemu
(i nie pisze do prawdziwego ``%LOCALAPPDATA%\\Cisza``).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data_dir = tmp_path / "cisza-data"
    monkeypatch.setenv("CISZA_DRY_RUN", "1")
    monkeypatch.setenv("CISZA_DATA_DIR", str(data_dir))

    from focuslock import paths

    assert Path(paths.data_dir()) == data_dir
    return data_dir


# ----------------------------------------------------------------- heartbeat
def test_missing_heartbeat_is_not_fresh(env):
    """Brak znacznika zycia = brak swiezosci (symulacja kill -9 / zaniku pradu)."""
    from focuslock import paths
    from focuslock.shell import watchdog

    assert not paths.heartbeat_path().exists()
    assert watchdog.heartbeat_age() is None
    assert watchdog.is_heartbeat_fresh(10.0) is False
    assert watchdog.is_heartbeat_fresh(10.0, now=time.time()) is False
    due, reason = watchdog.should_restore(10.0)
    assert (due, reason) == (False, "no-heartbeat-yet")


def test_stale_heartbeat_means_gui_is_dead(env):
    """Znacznik starszy niz timeout -> decyzja o przywroceniu powloki."""
    from focuslock import paths
    from focuslock.shell import watchdog

    paths.heartbeat_path().write_text(repr(time.time() - 120.0), encoding="utf-8")
    assert watchdog.is_heartbeat_fresh(10.0) is False
    age = watchdog.heartbeat_age()
    assert age is not None and age >= 100.0
    due, reason = watchdog.should_restore(10.0)
    assert due is True
    assert reason == "stale-heartbeat"


def test_fresh_heartbeat_keeps_watchdog_quiet(env):
    from focuslock import paths
    from focuslock.shell import watchdog

    watchdog.heartbeat_touch()
    assert watchdog.heartbeat_last_error() == ""
    assert paths.heartbeat_path().exists()
    assert watchdog.is_heartbeat_fresh(10.0) is True
    due, reason = watchdog.should_restore(10.0)
    assert (due, reason) == (False, "heartbeat-fresh")
    assert watchdog.run_watchdog(dry_run=True) == 0


# ----------------------------------------------------------------- recovery
def test_restore_everything_dry_run_is_idempotent(env):
    """Dwa wywolania w dry_run: ten sam, bezkrytyczny raport i nietkniety stan."""
    from focuslock import lockstate, paths, recovery

    lockstate.update(
        active=True,
        hardcore=False,
        session_id=42,
        reason="test",
        shell={
            "wallpaper": "",
            "wallpaper_style": 10,
            "icons_hidden": True,
            "taskbar_hidden": True,
            "hotkeys_blocked": True,
            "toasts_muted": True,
            "sound_muted": False,
            "taskmgr_disabled": True,
            "sleep_prevented": True,
        },
        network={"mode": "allowlist", "proxy_port": 8765, "proxy_enabled_by_us": True},
    )
    paths.heartbeat_path().write_text(repr(time.time()), encoding="utf-8")

    first = recovery.restore_everything(dry_run=True, reason="test")
    second = recovery.restore_everything(dry_run=True, reason="test")

    for report in (first, second):
        assert isinstance(report, dict)
        assert report.get("ok") is True, f"blędy krytyczne: {report.get('errors')}"
        assert report.get("errors") == []
        assert isinstance(report.get("applied"), list)
        assert isinstance(report.get("warnings"), list)
        assert report.get("reason") == "test"

    # idempotencja: dry_run nic nie zmienia, wiec raporty sa powtarzalne
    assert sorted(first["applied"]) == sorted(second["applied"])
    assert first["errors"] == second["errors"] == []

    state = lockstate.read()
    assert state is not None and state.active is True
    assert paths.heartbeat_path().exists()
    assert paths.crash_flag_path().exists() is False


def test_restore_everything_dry_run_without_state(env):
    """Bez zapisanego lockdownu przywracanie tez jest bezpieczne (nie rzuca)."""
    from focuslock import lockstate, recovery

    assert lockstate.read() is None
    report = recovery.restore_everything(dry_run=True, reason="clean")
    assert report["ok"] is True
    assert report["errors"] == []


def test_startup_state_closes_open_sessions(env):
    """Po crashu otwarte sesje sa zamykane jako CRASHED, flaga crashu znika."""
    from focuslock import paths, recovery
    from focuslock.store import Store

    paths.crash_flag_path().write_text(
        '{"reason": "kill", "ts": 1.0, "session_id": null}', encoding="utf-8"
    )
    # Brak aktywnego lockstate -> zadna operacja na powloce nie jest wykonywana.
    assert recovery.read_crash_flag() is not None

    store = Store(memory=False)
    session_id = store.start_session("STUDY", plan_seconds=1500, tag="crash")
    result = recovery.handle_startup_state(store)

    assert result["recovered"] is False  # nic nie bylo aktywne na powloce
    assert session_id in result["open_sessions"]
    session = store.get_session(session_id)
    assert session["status"] == "CRASHED"
    events = store.events(session_id=session_id)
    assert any(event["kind"] == "CRASH_RECOVERED" for event in events)
    assert recovery.read_crash_flag() is None
    assert paths.crash_flag_path().exists() is False


def test_crash_flag_roundtrip(env):
    from focuslock import recovery

    assert recovery.read_crash_flag() is None
    recovery.mark_crash("zanik pradu", session_id=7)
    flag = recovery.read_crash_flag()
    assert flag is not None
    assert flag["reason"] == "zanik pradu"
    assert flag["session_id"] == 7
    recovery.clear_crash_flag()
    assert recovery.read_crash_flag() is None


def test_startup_without_shell_restore_keeps_lockstate(env):
    """`restore_shell=False` (restore_on_boot wylaczony): sesje domkniete, powloka nietknieta."""
    from focuslock import lockstate, paths, recovery
    from focuslock.store import Store

    lockstate.update(
        active=True,
        hardcore=False,
        session_id=None,
        reason="crash",
        shell={"taskbar_hidden": True, "wallpaper_style": 10, "icons_hidden": True},
        network={"mode": "allowlist", "proxy_port": 8765},
    )
    assert lockstate.read().active is True

    store = Store(memory=False)
    session_id = store.start_session("STUDY", plan_seconds=1500, tag="bez-przywracania")
    result = recovery.handle_startup_state(store, restore_shell=False)

    assert result["recovered"] is False
    assert result["skipped"] == "restore_on_boot=False"
    assert result["report"] is None
    assert session_id in result["open_sessions"]

    session = store.get_session(session_id)
    assert session["status"] == "CRASHED"
    assert any(event["kind"] == "CRASH_RECOVERED" for event in store.events(session_id=session_id))

    # lockstate zostaje na dysku (z active=False), zeby restore.py wiedzial, co cofnac
    state = lockstate.read()
    assert state is not None
    assert state.active is False
    assert state.shell.taskbar_hidden is True
    assert state.network.proxy_port == 8765

    # dane nadal w katalogu tymczasowym - nic nie poszlo do %LOCALAPPDATA%
    assert Path(paths.lockstate_path()) == env / "lockstate.json"


def test_startup_with_shell_restore_calls_recovery(env, monkeypatch):
    """`restore_shell=True` (domyslnie) wola restore_everything - weryfikacja przez atrape."""
    from focuslock import lockstate, recovery
    from focuslock.store import Store

    calls: list[dict] = []

    def fake_restore(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "applied": [], "warnings": [], "errors": [], "reason": kwargs.get("reason", "")}

    monkeypatch.setattr(recovery, "restore_everything", fake_restore)
    lockstate.update(active=True, reason="crash")
    store = Store(memory=False)
    session_id = store.start_session("STUDY", plan_seconds=60, tag="przywracanie")

    result = recovery.handle_startup_state(store, restore_shell=True)

    assert result["recovered"] is True
    assert result["report"]["ok"] is True
    assert calls and calls[0]["reason"] == "startup"
    assert store.get_session(session_id)["status"] == "CRASHED"


def test_controller_honours_restore_on_boot(env, monkeypatch):
    """`Controller.startup_recovery` przekazuje flage `restore_on_boot` do recovery."""
    from focuslock import recovery
    from focuslock.config import Settings
    from focuslock.controller import Controller
    from focuslock.store import Store

    calls: list[dict] = []

    def fake_startup(store, **kwargs):
        calls.append(kwargs)
        return {"recovered": False, "open_sessions": [], "report": None, "crash_flag": None}

    monkeypatch.setattr(recovery, "handle_startup_state", fake_startup)
    store = Store(memory=False)
    settings = Settings()
    settings.env_overrides()
    controller = Controller(store, settings, bridge=object(), emit=lambda *a: None, log=lambda *a: None)

    settings.system.restore_on_boot = False
    controller.startup_recovery()
    assert calls[-1] == {"restore_shell": False}

    settings.system.restore_on_boot = True
    controller.startup_recovery()
    assert calls[-1] == {"restore_shell": True}
