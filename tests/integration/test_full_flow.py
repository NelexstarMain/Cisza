"""Test integracyjny pelnego obiegu sesji - bez dotykania systemu.

Uruchamiany w ``CISZA_DRY_RUN=1`` i z ``CISZA_DATA_DIR`` wskazujacym na katalog
tymczasowy (``tmp_path``), wiec *nic* nie trafia do prawdziwego
``%LOCALAPPDATA%\\Cisza``. Test sklada caly tor:

    Store -> Economy + SessionEngine -> start sesji -> ticki -> przerwa
          -> koniec -> wpisy w ``sessions``/``session_events``/``bank_ledger``/``bank_lots``

oraz przypadek przerwanego pomodoro, w ktorym bank nie rosnie.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


class _FakeBridge:
    """Atrapa helpera: zadne operacje systemowe nie sa wykonywane."""

    online = False

    def call(self, *args, **kwargs) -> dict:  # pragma: no cover - tylko kontrakt
        return {"ok": True, "applied": [], "warnings": [], "errors": []}

    def drain_events(self) -> list[dict]:  # pragma: no cover
        return []

    def close(self) -> None:  # pragma: no cover
        pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Izolowane srodowisko: dry-run + wlasny katalog danych."""
    data_dir = tmp_path / "cisza-data"
    monkeypatch.setenv("CISZA_DRY_RUN", "1")
    monkeypatch.setenv("CISZA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("CISZA_SAFE", raising=False)

    from focuslock import paths

    assert Path(paths.data_dir()) == data_dir
    assert Path(paths.db_path()) == data_dir / "cisza.db"
    return data_dir


def test_full_study_flow_records_everything(env):
    """Caly obieg: start -> pomodoro -> przerwa (skip) -> pomodoro -> koniec."""
    from focuslock.config import Settings
    from focuslock.economy import Economy
    from focuslock.session import Phase, Plan, SessionEngine
    from focuslock.store import Store

    store = Store(memory=False)  # sciezka z CISZA_DATA_DIR, nie z LOCALAPPDATA
    assert Path(store.path) == env / "cisza.db"
    settings = Settings()
    settings.env_overrides()
    assert settings.system.dry_run is True, "CISZA_DRY_RUN musi wlaczac dry_run"
    settings.save(store)

    economy = Economy(store, settings)
    engine = SessionEngine()
    pomodoros: list[dict] = []
    phases: list[str] = []

    plan = Plan(
        mode="STUDY",
        study_seconds=60,
        break_seconds=30,
        long_break_seconds=0,
        long_break_every=4,
        arm_seconds=1,
        tag="integracja",
    )
    session_id = store.start_session(
        "STUDY", plan_seconds=plan.study_seconds, tag=plan.tag, goal_note="test integracyjny"
    )
    store.add_event(session_id, "SESSION_START", {"plan": plan.to_dict()})

    def on_pomodoro_end(payload: dict) -> None:
        pomodoros.append(dict(payload))
        result = economy.credit_pomodoro(
            study_seconds=payload["study_seconds"],
            session_id=session_id,
            completed=payload["completed"],
        )
        store.add_event(session_id, "POMODORO_END", {**payload, "earned": result["earned"]})

    def on_phase(payload: dict) -> None:
        phases.append(str(payload["phase"]))
        store.add_event(session_id, f"PHASE_{payload['phase']}", {"previous": payload["previous"]})

    engine.set_callbacks(phase=on_phase, pomodoro_end=on_pomodoro_end)
    base = time.monotonic()
    engine.start(plan, session_id, now=base)
    assert engine.state(base)["phase"] == Phase.ARMING.value

    # ARMING (1 s) -> STUDY
    engine.tick(now=base + 1.0)
    assert engine.phase == Phase.STUDY
    engine.state(base + 2.0)  # ticki co 1 s w trakcie nauki
    engine.tick(now=base + 10.0)

    # STUDY (60 s) -> automatycznie BREAK + rozliczenie pomodoro
    engine.tick(now=base + 62.0)
    assert engine.phase == Phase.BREAK
    assert pomodoros[-1]["completed"] is True
    assert pomodoros[-1]["study_seconds"] == 60
    assert economy.balance() == 30, "60 s nauki * 0.5 = 30 s banku"

    # przerwa -> pomijamy ja i zaczynamy kolejne pomodoro
    engine.skip_break(now=base + 65.0)
    assert engine.phase == Phase.STUDY

    engine.tick(now=base + 130.0)  # drugie pomodoro ukonczone
    assert pomodoros[-1]["completed"] is True
    assert economy.balance() == 60

    # koniec sesji
    engine.finish("user", now=base + 131.0)
    assert engine.phase == Phase.DONE
    state = engine.state(base + 131.0)
    store.finish_session(
        session_id,
        "COMPLETED",
        actual_seconds=state["study_seconds_done"],
        pomodoros_done=state["pomodoros_done"],
        pomodoros_aborted=state["pomodoros_aborted"],
    )
    store.add_event(session_id, "SESSION_END", {"reason": "user"})

    # ---------------------------------------------------------------- baza
    session = store.get_session(session_id)
    assert session is not None
    assert session["status"] == "COMPLETED"
    assert session["mode"] == "STUDY"
    assert session["actual_seconds"] == 120
    assert session["pomodoros_done"] == 2
    assert session["pomodoros_aborted"] == 0
    assert session["tag"] == "integracja"
    assert store.active_session() is None

    events = store.events(session_id=session_id, limit=100)
    kinds = [event["kind"] for event in events]
    assert "SESSION_START" in kinds
    assert "SESSION_END" in kinds
    assert kinds.count("POMODORO_END") == 2
    assert "PHASE_BREAK" in kinds

    ledger = [row for row in store.ledger(limit=50) if row["session_id"] == session_id]
    earned = [row for row in ledger if row["reason"] == "EARNED"]
    assert len(earned) == 2
    assert sum(row["delta_seconds"] for row in earned) == 60

    lots = [lot for lot in store.lots() if lot["session_id"] == session_id]
    assert len(lots) == 2
    assert sum(lot["remaining_seconds"] for lot in lots) == 60
    assert all(lot["expires_at"] > 0 for lot in lots), "loty pomodoro musza miec TTL"

    assert economy.balance() == 60
    assert economy.earned_today() == 60
    assert economy.daily_cap_left() == settings.economy.daily_cap_minutes * 60 - 60

    stats = store.daily_stats(_today())
    assert stats["study_seconds"] == 120
    assert stats["pomodoros_done"] == 2
    assert stats["pomodoros_aborted"] == 0
    assert stats["sessions"] == 1

    # brak wyjatkow z callbackow FSM
    assert engine.errors() == []


def test_aborted_pomodoro_does_not_grow_bank(env):
    """Przerwane pomodoro: earned == 0, brak lotu, bank nadal pusty."""
    from focuslock.config import Settings
    from focuslock.economy import Economy
    from focuslock.session import Phase, Plan, SessionEngine
    from focuslock.store import Store

    store = Store(memory=False)
    settings = Settings()
    settings.env_overrides()
    economy = Economy(store, settings)
    engine = SessionEngine()
    pomodoros: list[dict] = []

    def on_pomodoro_end(payload: dict) -> None:
        pomodoros.append(dict(payload))
        economy.credit_pomodoro(
            study_seconds=payload["study_seconds"],
            session_id=session_id,
            completed=payload["completed"],
        )

    session_id = store.start_session("STUDY", plan_seconds=600, tag="przerwane")
    engine.set_callbacks(pomodoro_end=on_pomodoro_end)
    base = time.monotonic()
    engine.start(Plan(study_seconds=600, break_seconds=60, arm_seconds=0, tag="przerwane"), session_id, now=base)
    assert engine.phase == Phase.STUDY

    engine.tick(now=base + 30.0)  # 30 s nauki
    engine.abort_pomodoro("abort", now=base + 30.0)

    assert pomodoros and pomodoros[-1]["completed"] is False
    assert pomodoros[-1]["study_seconds"] == 30
    assert engine.state(base + 30.0)["pomodoros_aborted"] == 1

    assert economy.balance() == 0, "przerwane pomodoro nie moze zarabiac"
    assert economy.earned_today() == 0
    assert store.lots() == []
    ledger = store.ledger(limit=50)
    assert all(row["reason"] != "EARNED" for row in ledger)

    engine.finish("abort", now=base + 31.0)
    state = engine.state(base + 31.0)
    store.finish_session(
        session_id, "ABORTED", actual_seconds=state["study_seconds_done"],
        pomodoros_done=0, pomodoros_aborted=1,
    )
    session = store.get_session(session_id)
    assert session["status"] == "ABORTED"
    assert session["pomodoros_aborted"] == 1
    assert store.daily_stats(_today())["pomodoros_aborted"] == 1


def test_free_mode_spends_only_from_bank(env):
    """Tryb FREE zdejmuje minuty z banku i oddaje niewykorzystane (refund)."""
    from focuslock.config import Settings
    from focuslock.economy import Economy
    from focuslock.session import Phase, Plan, SessionEngine
    from focuslock.store import Store

    store = Store(memory=False)
    settings = Settings()
    settings.env_overrides()
    economy = Economy(store, settings)

    store.add_lot(120, session_id=None, note="start", ttl_days=1, reason="EARNED")
    assert economy.balance() == 120

    engine = SessionEngine()
    session_id = store.start_session("FREE", plan_seconds=60, tag="wolne")
    spent = economy.spend_free(60, session_id)
    assert spent == 60
    assert economy.balance() == 60

    base = time.monotonic()
    engine.start(Plan(mode="FREE", free_seconds=60, arm_seconds=0, tag="wolne"), session_id, now=base)
    assert engine.phase == Phase.STUDY
    engine.tick(now=base + 30.0)
    left = engine.state(base + 30.0)["free_seconds_left"]
    assert left == 30

    engine.finish("user", now=base + 30.0)
    assert engine.phase == Phase.DONE
    assert economy.refund(left, session_id) == 30
    assert economy.balance() == 90, "niewykorzystane minuty wracaja do banku"
    assert store.get_session(session_id)["mode"] == "FREE"


def test_panic_exit_respects_panic_requires_pin(env):
    """`request_end("panic")`: z wlaczonym PIN-em wymaga PIN-u, z wylaczonym konczy sesje."""
    from focuslock.config import Settings
    from focuslock.controller import Controller
    from focuslock.session import Plan
    from focuslock.store import Store

    store = Store(memory=False)
    settings = Settings()
    settings.env_overrides()
    settings.set_pin("1234")
    assert settings.has_pin is True

    controller = Controller(
        store, settings, bridge=_FakeBridge(), emit=lambda *a: None, log=lambda *a: None
    )
    session_id = store.start_session("STUDY", plan_seconds=600, tag="panic")
    base = time.monotonic()
    controller.engine.start(
        Plan(study_seconds=600, break_seconds=60, arm_seconds=0, tag="panic"), session_id, now=base
    )
    controller.engine.tick(now=base + 10.0)

    # domyslnie "wyjscie awaryjne wymaga PIN-u" jest wlaczone
    settings.lock.panic_requires_pin = True
    result = controller.request_end("panic")
    assert result.get("requires_pin") is True
    assert controller.engine.state()["phase"] == "STUDY"
    assert store.get_session(session_id)["status"] == "RUNNING"

    # wylaczone: przytrzymanie kombinacji konczy sesje natychmiast, bez PIN-u
    settings.lock.panic_requires_pin = False
    result = controller.request_end("panic")
    assert result["ok"] is True
    assert "requires_pin" not in result
    assert controller.engine.state()["phase"] == "DONE"

    session = store.get_session(session_id)
    assert session["status"] == "ABORTED"
    assert session["ended_at"] is not None
    kinds = [event["kind"] for event in store.events(session_id=session_id)]
    assert "PANIC_EXIT" in kinds
