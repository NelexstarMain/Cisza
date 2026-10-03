"""Testy maszyny stanow sesji (focuslock/session.py + focuslock/timer.py).

Czas jest wstrzykiwany (wirtualny zegar liczony od 0.0), wiec testy nie czekaja
na zegar systemowy. Zapis do bazy uzywa Store(memory=True).
"""
from __future__ import annotations

import json
import os
import tempfile

# Store(memory=True) i tak wola paths.db_path(), wiec odsuwamy dane od profilu
# (jesli nie zrobil tego juz conftest.py).
if not os.environ.get("CISZA_DATA_DIR"):
    os.environ["CISZA_DATA_DIR"] = tempfile.mkdtemp(prefix="cisza-fsm-")

import pytest  # noqa: E402

from focuslock.session import SESSION_STATE_KEY, Phase, Plan, SessionEngine, elapsed_gap  # noqa: E402
from focuslock.store import Store  # noqa: E402
from focuslock.timer import Countdown, format_seconds  # noqa: E402


class Recorder:
    """Zbiera wywolania callbackow silnika."""

    def __init__(self) -> None:
        self.ticks: list[dict] = []
        self.phases: list[dict] = []
        self.pomodoros: list[dict] = []
        self.finishes: list[dict] = []

    def engine(self) -> SessionEngine:
        return SessionEngine(
            on_tick=self.ticks.append,
            on_phase=self.phases.append,
            on_pomodoro_end=self.pomodoros.append,
            on_finish=self.finishes.append,
        )


# --------------------------------------------------------------------- timer
def test_format_seconds_mm_ss_and_hh_mm_ss() -> None:
    assert format_seconds(0) == "00:00"
    assert format_seconds(9) == "00:09"
    assert format_seconds(59) == "00:59"
    assert format_seconds(60) == "01:00"
    assert format_seconds(1500) == "25:00"
    assert format_seconds(3600) == "01:00:00"
    assert format_seconds(3661) == "01:01:01"
    assert format_seconds(7325) == "02:02:05"
    assert format_seconds(-5) == "00:00"


def test_countdown_deadline_elapsed_and_reset() -> None:
    timer = Countdown(10, now=lambda: 0.0)
    timer.reset(10, now=100.0)
    assert timer.remaining(103.0) == pytest.approx(7.0)
    assert timer.elapsed(103.0) == pytest.approx(3.0)
    assert timer.deadline(103.0) == pytest.approx(110.0)
    assert timer.expired(109.9) is False
    assert timer.expired(110.0) is True
    timer.reset(5, now=200.0)
    assert timer.remaining(200.0) == pytest.approx(5.0)
    assert timer.total == 5


# ----------------------------------------------------------------- cykl STUDY
def test_full_cycle_study_break_long_break_done() -> None:
    rec = Recorder()
    engine = rec.engine()
    plan = Plan(
        mode="STUDY",
        study_seconds=10,
        break_seconds=5,
        long_break_seconds=7,
        long_break_every=2,
        arm_seconds=2,
        tag="matematyka",
        goal_note="calki",
    )
    state = engine.start(plan, session_id=7, now=0.0)
    assert state["phase"] == Phase.ARMING.value
    assert state["remaining"] == 2
    assert state["session_id"] == 7
    assert state["tag"] == "matematyka"
    assert state["goal_note"] == "calki"
    assert len(rec.phases) == 1

    assert engine.tick(now=1.0)["phase"] == Phase.ARMING.value
    armed = engine.tick(now=2.0)
    assert armed["phase"] == Phase.STUDY.value
    assert armed["remaining"] == 10
    assert armed["pomodoro_index"] == 1

    done_first = engine.tick(now=12.0)
    assert done_first["phase"] == Phase.BREAK.value
    assert done_first["remaining"] == 5
    assert done_first["pomodoros_done"] == 1
    assert done_first["study_seconds_done"] == 10
    assert rec.pomodoros[-1]["completed"] is True
    assert rec.pomodoros[-1]["study_seconds"] == 10
    assert rec.pomodoros[-1]["session_id"] == 7

    second = engine.tick(now=17.0)
    assert second["phase"] == Phase.STUDY.value
    assert second["pomodoro_index"] == 2

    long_break = engine.tick(now=27.0)
    assert long_break["phase"] == Phase.LONG_BREAK.value
    assert long_break["remaining"] == 7
    assert long_break["pomodoros_done"] == 2
    assert rec.phases[-1]["long"] is True

    assert engine.tick(now=34.0)["phase"] == Phase.STUDY.value
    finished = engine.finish("user", now=40.0)
    assert finished["phase"] == Phase.DONE.value
    assert finished["ok"] is True
    assert finished["changed"] is True
    assert finished["finished_reason"] == "user"
    # 10 + 10 + 6 sekund czesciowego pomodoro
    assert finished["study_seconds_done"] == 26
    assert rec.finishes and rec.finishes[-1]["reason"] == "user"
    assert rec.ticks, "tick musi wolac on_tick"

    payload = json.dumps(engine.state())  # stan musi byc JSON-friendly
    assert '"phase": "DONE"' in payload
    # Konczenie drugi raz jest no-op.
    assert engine.finish("user", now=41.0)["changed"] is False


def test_tick_does_not_change_paused_state() -> None:
    engine = SessionEngine()
    engine.start(Plan(study_seconds=10, break_seconds=5, arm_seconds=0), 1, now=0.0)
    assert engine.tick(now=4.0)["remaining"] == 6
    assert engine.pause(now=4.0)["paused"] is True
    frozen = engine.tick(now=104.0)
    assert frozen["paused"] is True
    assert frozen["remaining"] == 6
    assert engine.resume(now=104.0)["paused"] is False
    assert engine.tick(now=110.0)["phase"] == Phase.BREAK.value


def test_pause_does_not_eat_time() -> None:
    engine = SessionEngine()
    engine.start(Plan(study_seconds=10, break_seconds=5, arm_seconds=0), 1, now=0.0)
    engine.tick(now=5.0)
    assert engine.state(5.0)["remaining"] == 5
    engine.pause(5.0)
    assert engine.state(1000.0)["remaining"] == 5  # 995 s pauzy nic nie zjada
    engine.resume(1000.0)
    assert engine.tick(now=1004.9)["phase"] == Phase.STUDY.value
    assert engine.tick(now=1005.0)["phase"] == Phase.BREAK.value


def test_skip_break_starts_next_pomodoro() -> None:
    engine = SessionEngine()
    engine.start(Plan(study_seconds=10, break_seconds=5, long_break_seconds=8,
                      long_break_every=1, arm_seconds=0), 1, now=0.0)
    assert engine.tick(now=10.0)["phase"] == Phase.LONG_BREAK.value
    skipped = engine.skip_break()
    assert skipped["phase"] == Phase.STUDY.value
    assert skipped["changed"] is True
    assert skipped["reason"] == "skipped"
    # Pomijanie poza przerwa nic nie robi.
    assert engine.skip_break()["changed"] is False


def test_abort_pomodoro_reports_incomplete() -> None:
    rec = Recorder()
    engine = rec.engine()
    engine.start(Plan(study_seconds=25, break_seconds=5, arm_seconds=0), 3, now=0.0)
    engine.tick(now=10.0)
    state = engine.abort_pomodoro("user", now=10.0)
    assert state["pomodoros_aborted"] == 1
    assert state["pomodoros_done"] == 0
    assert state["phase"] == Phase.BREAK.value
    assert rec.pomodoros[-1]["completed"] is False
    assert rec.pomodoros[-1]["study_seconds"] == 10
    assert rec.pomodoros[-1]["reason"] == "user"
    # W trybie FREE przerywanie pomodoro nie ma sensu.
    engine.finish("user", now=61.0)
    engine.start(Plan(mode="FREE", free_seconds=60, arm_seconds=0), 4, now=100.0)
    assert engine.abort_pomodoro(now=101.0)["changed"] is False


def test_free_mode_countdown_and_finish() -> None:
    rec = Recorder()
    engine = rec.engine()
    started = engine.start(Plan(mode="FREE", free_seconds=60, arm_seconds=0), 9, now=0.0)
    assert started["phase"] == Phase.STUDY.value
    assert started["mode"] == "FREE"
    assert started["free_seconds_left"] == 60
    assert started["display"] == "01:00"
    assert engine.tick(now=59.0)["free_seconds_left"] == 1
    ended = engine.tick(now=60.0)
    assert ended["phase"] == Phase.DONE.value
    assert ended["free_seconds_left"] == 0
    assert rec.finishes[-1]["reason"] == "free_end"
    assert rec.pomodoros == [], "FREE nie generuje pomodoro"
    assert ended["study_seconds_done"] == 0


def test_free_mode_open_ended_waits_for_finish() -> None:
    engine = SessionEngine()
    state = engine.start(Plan(mode="FREE", free_seconds=0, arm_seconds=0), 5, now=0.0)
    assert state["open_ended"] is True
    assert state["display"] == "--:--"
    assert engine.tick(now=9999.0)["phase"] == Phase.STUDY.value
    assert engine.finish("user", now=9999.0)["phase"] == Phase.DONE.value


# ----------------------------------------------------------- restart po crashu
def test_serialize_deserialize_round_trip() -> None:
    engine = SessionEngine()
    engine.start(Plan(study_seconds=100, break_seconds=20, arm_seconds=0, tag="bio"), 42, now=1000.0)
    engine.tick(now=1030.0)
    data = engine.serialize(now=1030.0)

    assert data["phase"] == Phase.STUDY.value
    assert data["timer"]["total"] == pytest.approx(100)
    assert data["timer"]["elapsed"] == pytest.approx(30)
    assert data["session_id"] == 42
    assert data["plan"]["tag"] == "bio"
    assert data["saved_at"] > 0

    report = SessionEngine.deserialize(data)
    assert report["ok"] is True
    assert report["phase"] == Phase.STUDY.value
    assert report["remaining"] == pytest.approx(70, abs=0.5)
    assert report["plan"]["study_seconds"] == 100

    fresh = SessionEngine()
    restored = fresh.restore(data, now=1030.0)
    assert restored["ok"] is True
    assert restored["phase"] == Phase.STUDY.value
    assert restored["remaining"] == 70
    assert restored["session_id"] == 42
    assert restored["tag"] == "bio"

    # Dodatkowy czas poza aplikacja skraca pozostaly budzet.
    aged = SessionEngine()
    assert aged.restore(data, now=1040.0, gap_seconds=10)["remaining"] == 60
    assert aged.tick(now=1100.0)["phase"] == Phase.BREAK.value


def test_deserialize_is_crash_safe() -> None:
    for broken in (None, {}, "nie-json", {"phase": "NIE-MA"}, {"phase": "STUDY"},
                   {"phase": "STUDY", "plan": None}, 12345, []):
        report = SessionEngine.deserialize(broken)
        assert report["ok"] is False
        assert report["errors"]
        assert report["remaining"] == 0.0
    engine = SessionEngine()
    failed = engine.restore({"phase": "STUDY"}, now=0.0, gap_seconds=0)
    assert failed["ok"] is False
    assert failed["phase"] == Phase.IDLE.value


def test_restore_expired_pomodoro_is_flagged() -> None:
    data = {
        "version": 1,
        "phase": "STUDY",
        "plan": {"mode": "STUDY", "study_seconds": 10, "arm_seconds": 0},
        "session_id": 1,
        "pomodoros_done": 0,
        "pomodoros_aborted": 0,
        "study_seconds_done": 0,
        "paused": False,
        "timer": {"total": 10, "elapsed": 3},
        "saved_at": 0.0,
        "saved_monotonic": 0.0,
    }
    engine = SessionEngine()
    state = engine.restore(data, now=500.0, gap_seconds=30)
    assert state["remaining"] == 0
    assert state["expired_on_restore"] is True
    assert engine.tick(now=500.0)["phase"] == Phase.BREAK.value


def test_restore_keeps_paused_state() -> None:
    engine = SessionEngine()
    engine.start(Plan(study_seconds=100, break_seconds=20, arm_seconds=0), 8, now=1000.0)
    engine.tick(now=1030.0)
    engine.pause(now=1030.0)
    data = engine.serialize(now=1030.0)
    assert data["paused"] is True

    revived = SessionEngine()
    state = revived.restore(data, now=5000.0, gap_seconds=3600)
    assert state["paused"] is True
    assert state["remaining"] == 70  # pauza nie zjada budzetu
    assert revived.resume(now=5000.0)["paused"] is False


def test_elapsed_gap_pure_function() -> None:
    data = {"saved_at": 1000.0, "saved_monotonic": 50.0}
    # Bez restartu systemu oba zegary mowia to samo - liczy sie mniejsza roznica.
    assert elapsed_gap(data, wall_now=1020.0, mono_now=70.0) == pytest.approx(20.0)
    # KOREKTA zegara sciennego w przod: zostaje mniejsza roznica monotoniczna.
    assert elapsed_gap(data, wall_now=5000.0, mono_now=70.0) == pytest.approx(20.0)
    # Po restarcie systemu monotonic jest mniejszy - zostaje czas scienny.
    assert elapsed_gap(data, wall_now=1030.0, mono_now=5.0) == pytest.approx(30.0)
    # Cofniecie zegara nie daje ujemnych wartosci.
    assert elapsed_gap(data, wall_now=900.0, mono_now=5.0) == pytest.approx(0.0)
    assert elapsed_gap({}, wall_now=1.0, mono_now=1.0) == pytest.approx(0.0)


def test_save_and_load_from_store_after_crash() -> None:
    store = Store(memory=True)
    engine = SessionEngine()
    engine.start(Plan(study_seconds=100, break_seconds=20, arm_seconds=0), 7, now=2000.0)
    engine.tick(now=2010.0)
    engine.save_to_store(store, now=2010.0)
    assert store.get_setting(SESSION_STATE_KEY)["phase"] == Phase.STUDY.value

    revived = SessionEngine()
    state = revived.load_from_store(store, now=2010.0)
    assert state["ok"] is True
    assert state["phase"] == Phase.STUDY.value
    assert state["remaining"] == 90
    assert state["session_id"] == 7

    empty = SessionEngine()
    assert empty.load_from_store(Store(memory=True), now=0.0)["ok"] is False
    store.close()


def test_callback_errors_do_not_break_fsm() -> None:
    def boom(_payload: dict) -> None:
        raise RuntimeError("ui padlo")

    engine = SessionEngine(on_tick=boom, on_phase=boom, on_pomodoro_end=boom, on_finish=boom)
    engine.start(Plan(study_seconds=5, break_seconds=5, arm_seconds=0), 1, now=0.0)
    assert engine.tick(now=5.0)["phase"] == Phase.BREAK.value
    assert engine.errors()
    assert "RuntimeError" in engine.errors()[0]


def test_plan_normalization() -> None:
    plan = Plan.from_dict({"mode": "free", "study_minutes": 25, "break_minutes": 5,
                           "arm_seconds": 2, "free_minutes": 30, "allowlist": {"study_apps": ["x.exe"]}})
    assert plan.mode == "FREE"
    assert plan.study_seconds == 1500
    assert plan.break_seconds == 300
    assert plan.free_seconds == 1800
    assert plan.arm_seconds == 2
    assert plan.allowlist == {"study_apps": ["x.exe"]}
    weird = Plan.from_dict({"mode": "cos", "study_minutes": -5, "study_seconds": -10})
    assert weird.mode == "STUDY"
    assert weird.study_seconds == 0
    assert Plan.from_dict(None).study_seconds == 1500
