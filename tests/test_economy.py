"""Testy ekonomii banku czasu (focuslock/economy.py + focuslock/presets.py).

Wszystkie przypadki uzywaja Store(memory=True) i wstrzykiwanego ``now``.
Uwaga: store.add_lot/spend stempluja wiersze realnym ``time.time()`` (zamrozone
API bazy), dlatego baza odniesienia ``BASE`` to czas rzeczywisty, a przesuniecia
liczymy w dol/gore od niego - testy nie czekaja na zegar.
"""
from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime

# Store(memory=True) i tak wola paths.db_path(), wiec odsuwamy dane od profilu
# (jesli nie zrobil tego juz conftest.py).
if not os.environ.get("CISZA_DATA_DIR"):
    os.environ["CISZA_DATA_DIR"] = tempfile.mkdtemp(prefix="cisza-econ-")

import pytest  # noqa: E402

from focuslock.config import EconomyConfig, Settings  # noqa: E402
from focuslock.economy import Economy  # noqa: E402
from focuslock.presets import (  # noqa: E402
    apply_preset,
    delete_preset,
    ensure_defaults,
    list_presets,
    normalize_payload,
    save_preset,
)
from focuslock.store import Store  # noqa: E402
from focuslock.session import Plan  # noqa: E402

BASE = time.time()
DAY = 86400.0


def make(settings=None, **kwargs) -> tuple[Store, Economy]:
    store = Store(memory=True)
    if settings is None and kwargs:
        settings = Settings()
        for key, value in kwargs.items():
            setattr(settings.economy, key, value)
    return store, Economy(store, settings)


# ------------------------------------------------------------------ zarobek
def test_completed_pomodoro_earns_half_25_minutes() -> None:
    store, econ = make()
    result = econ.credit_pomodoro(1500, session_id=1, completed=True, now=BASE)
    assert result["earned"] == 750
    assert result["capped"] == 0
    assert result["penalty"] == 0
    assert result["reason"] == "ok"
    assert result["balance"] == 750
    assert econ.balance(now=BASE) == 750
    assert econ.earned_today(now=BASE) == 750
    assert econ.daily_cap_left(now=BASE) == 3600 - 750
    store.close()


def test_rounding_down_to_15_seconds() -> None:
    store, econ = make()
    assert econ.credit_pomodoro(20, 1, True, now=BASE)["earned"] == 0
    assert econ.credit_pomodoro(29, 1, True, now=BASE)["reason"] == "zero"
    assert econ.credit_pomodoro(900, 1, True, now=BASE)["earned"] == 450
    assert econ.credit_pomodoro(920, 1, True, now=BASE)["earned"] == 450
    assert econ.credit_pomodoro(0, 1, True, now=BASE)["earned"] == 0
    assert econ.credit_pomodoro(1500, 1, True, now=BASE)["earned"] == 750
    assert econ.balance(now=BASE) == 450 + 450 + 750
    store.close()


def test_daily_cap_full_and_partial() -> None:
    store, econ = make()
    full = econ.credit_pomodoro(7200, 1, True, now=BASE)  # 3600 s = caly limit
    assert full["earned"] == 3600
    assert full["capped"] == 0
    assert full["reason"] == "ok"
    over = econ.credit_pomodoro(1500, 2, True, now=BASE)
    assert over["earned"] == 0
    assert over["capped"] == 750
    assert over["reason"] == "cap"
    assert over["balance"] == 3600
    assert econ.daily_cap_left(now=BASE) == 0

    store2, econ2 = make()
    partial = econ2.credit_pomodoro(7000, 1, True, now=BASE)  # 3495 s
    assert partial["earned"] == 3495
    tail = econ2.credit_pomodoro(1500, 2, True, now=BASE)  # zostaje 105 s
    assert tail["earned"] == 105
    assert tail["capped"] == 645
    assert tail["reason"] == "capped"
    assert tail["balance"] == 3600
    store.close()
    store2.close()


def test_daily_cap_is_per_calendar_day() -> None:
    store, econ = make()
    # Dzisiejszy limit wykorzystany: wiersz ledgera z data odniesienia BASE.
    store.execute(
        "INSERT INTO bank_ledger(ts, delta_seconds, reason, session_id, note, expires_at) "
        "VALUES(?,?,?,?,?,0)",
        (BASE, 3600, "EARNED", None, "seed"),
    )
    assert econ.earned_today(now=BASE) == 3600
    assert econ.daily_cap_left(now=BASE) == 0
    capped = econ.credit_pomodoro(1500, 2, True, now=BASE)
    assert capped["earned"] == 0
    assert capped["capped"] == 750
    assert capped["reason"] == "cap"

    # Nastepny dzien kalendarzowy -> limit znow jest pelny.
    tomorrow = econ.credit_pomodoro(1500, 3, True, now=BASE + 25 * 3600)
    assert tomorrow["earned"] == 750
    assert tomorrow["reason"] == "ok"
    store.close()


def test_ratio_and_config_tolerance() -> None:
    store = Store(memory=True)
    ratio_one = Economy(store, EconomyConfig(earn_ratio=1.0, daily_cap_minutes=600))
    assert ratio_one.credit_pomodoro(1500, 1, True, now=BASE)["earned"] == 1500
    from_dict = Economy(store, {"earn_ratio": 0.25, "daily_cap_minutes": 600})
    assert from_dict.credit_pomodoro(1500, 2, True, now=BASE)["earned"] == 375
    assert from_dict.summary(now=BASE)["ratio"] == 0.25
    store.close()


# --------------------------------------------------------------- przerwanie
def test_aborted_pomodoro_earns_nothing_and_takes_penalty() -> None:
    store, econ = make(abort_penalty_minutes=10)
    store.add_lot(1800, session_id=None, note="seed")
    result = econ.credit_pomodoro(600, session_id=3, completed=False, now=BASE)
    assert result["earned"] == 0
    assert result["capped"] == 0
    assert result["reason"] == "przerwane"
    assert result["penalty"] == 600
    assert result["balance"] == 1200
    store.close()


def test_penalty_never_goes_below_zero() -> None:
    store, econ = make(abort_penalty_minutes=10)
    store.add_lot(300, note="seed")
    result = econ.credit_pomodoro(600, session_id=3, completed=False, now=BASE)
    assert result["penalty"] == 300  # tylko tyle bylo w banku
    assert result["balance"] == 0
    assert econ.balance(now=BASE) >= 0
    store.close()


def test_aborted_without_penalty_keeps_bank() -> None:
    store, econ = make()  # abort_penalty_minutes = 0
    store.add_lot(900, note="seed")
    result = econ.credit_pomodoro(600, 1, completed=False, now=BASE)
    assert result["penalty"] == 0
    assert result["balance"] == 900
    assert result["reason"] == "przerwane"
    store.close()


# ---------------------------------------------------------------------- FIFO
def test_spend_free_is_fifo_and_never_negative() -> None:
    store, econ = make()
    first = store.add_lot(100, note="first")
    second = store.add_lot(200, note="second")
    assert econ.spend_free(150, session_id=1) == 150
    lots = {row["id"]: row for row in store.lots(include_empty=True)}
    assert lots[first]["remaining_seconds"] == 0
    assert lots[second]["remaining_seconds"] == 150
    assert econ.balance() == 150
    # Pusty bank: nic nie da sie wydac.
    assert econ.spend_free(1000, session_id=1) == 150
    assert econ.balance() == 0
    assert econ.spend_free(-5, session_id=1) == 0
    # Zwrot tworzy nowy lot bez wygasania.
    assert econ.refund(60, session_id=1) == 60
    assert econ.balance() == 60
    assert econ.refund(0, session_id=1) == 0
    store.close()


# ------------------------------------------------------------------ TTL / loty
def test_ttl_seven_days_and_expiry() -> None:
    store, econ = make()
    result = econ.credit_pomodoro(1500, session_id=1, completed=True, now=BASE)
    assert result["earned"] == 750
    lot = store.lots()[0]
    assert lot["expires_at"] > 0
    assert lot["expires_at"] == pytest.approx(BASE + 7 * DAY, abs=30)

    assert econ.balance(now=BASE) == 750
    assert econ.balance(now=BASE + 8 * DAY) == 0  # filtr TTL bez sweepu
    assert econ.sweep_expired(now=BASE + 6 * DAY) == 0  # jeszcze nie wygaslo
    assert econ.balance(now=BASE + 6 * DAY) == 750

    expired = econ.sweep_expired(now=BASE + 8 * DAY)
    assert expired == 750
    assert econ.balance(now=BASE + 8 * DAY) == 0
    assert any(row["reason"] == "EXPIRED" for row in store.ledger())
    store.close()


def test_zero_ttl_never_expires() -> None:
    store, econ = make(bank_ttl_days=0)
    econ.credit_pomodoro(1500, 1, True, now=BASE)
    assert store.lots()[0]["expires_at"] == 0
    assert econ.sweep_expired(now=BASE + 365 * DAY) == 0
    assert econ.balance(now=BASE + 365 * DAY) == 750
    store.close()


# --------------------------------------------------------------------- seria
def test_streak_days_continue_and_reset() -> None:
    store, econ = make(streak_min_study_minutes=25)
    first = econ.register_study_day(1500, day="2026-01-01")
    assert first == {"current": 1, "best": 1, "changed": True, "reason": "started"}

    again = econ.register_study_day(1500, day="2026-01-01")
    assert again["current"] == 1
    assert again["changed"] is False

    second = econ.register_study_day(1500, day="2026-01-02")
    assert second["current"] == 2
    assert second["best"] == 2
    assert second["changed"] is True

    # Dzien ponizej progu nie psuje serii, gdy wczoraj byl dzien nauki.
    below = econ.register_study_day(600, day="2026-01-03")
    assert below["current"] == 2
    assert below["changed"] is False
    assert below["reason"] == "below-minimum"

    # Trzydniowa luka: seria startuje od 1, ale best zostaje.
    resumed = econ.register_study_day(1500, day="2026-01-05")
    assert resumed["current"] == 1
    assert resumed["best"] == 2
    assert resumed["changed"] is True
    assert resumed["reason"] == "started"

    # Kolejna luka bez nauki zeruje serie.
    gap = econ.register_study_day(0, day="2026-01-09")
    assert gap["current"] == 0
    assert gap["best"] == 2
    assert gap["changed"] is True
    assert gap["reason"] == "gap-reset"

    # Wsteczne wywolanie nie cofa zapisanej serii.
    stale = econ.register_study_day(1500, day="2026-01-01")
    assert stale["changed"] is False
    assert stale["current"] == 0
    assert stale["reason"] == "stale-day"

    assert store.get_streak("study")["current"] == 0
    # last_day wskazuje ostatni dzien z nauka (data zerujaca nie psuje licznika dni).
    assert store.get_streak("study")["last_day"] == "2026-01-05"
    store.close()


def test_streak_minimum_zero_accepts_any_study() -> None:
    store, econ = make(streak_min_study_minutes=0)
    assert econ.register_study_day(1, day="2026-02-01")["current"] == 1
    assert econ.register_study_day(1, day="2026-02-02")["current"] == 2
    store.close()


# ---------------------------------------------------------------- focus score
def _add_session(store: Store, day: str, seconds: int, done: int, aborted: int, hour: int = 12) -> None:
    start = datetime.fromisoformat(day).timestamp() + hour * 3600
    store.execute(
        "INSERT INTO sessions(mode, status, started_at, ended_at, plan_seconds, actual_seconds, "
        "pomodoros_done, pomodoros_aborted, tag, goal_note, allowlist_json) "
        "VALUES('STUDY','COMPLETED',?,?,?,?,?,?,?,?,?)",
        (start, start + seconds, seconds, seconds, done, aborted, "test", "", "{}"),
    )


def test_focus_score_formula() -> None:
    store, econ = make()
    _add_session(store, "2026-01-05", 3600, done=2, aborted=0)
    _add_session(store, "2026-01-05", 600, done=0, aborted=1)
    now = datetime.fromisoformat("2026-01-05").timestamp() + 23 * 3600

    # 7 dni: avg = 4200/7 = 600 s, cel = 3600 s, skutecznosc = 2/3
    assert econ.focus_score(7, now) == pytest.approx(11.1, abs=0.15)
    # 1 dzien: avg = 4200 s > cel, wiec tylko skutecznosc pomodorow
    assert econ.focus_score(1, now) == pytest.approx(66.7, abs=0.15)
    # Brak danych -> 0.0
    assert econ.focus_score(7, now - 40 * DAY) == 0.0
    store.close()


def test_focus_score_survives_implausible_now() -> None:
    """Metryka nie moze wywrocic ekranu, gdy ktos poda czas monotoniczny zamiast epoch."""
    store, econ = make()
    assert econ.focus_score(30, now=100.0) == 0.0
    assert econ.warnings()
    store.close()


def test_summary_and_totals() -> None:
    store, econ = make()
    store.add_lot(300, note="seed", reason="ADJUST")
    econ.credit_pomodoro(1500, 1, True, now=BASE)
    econ.register_study_day(1500, day="2026-03-01")
    summary = econ.summary(now=BASE)
    assert set(summary) == {
        "balance", "earned_today", "cap_left", "cap_minutes", "streak",
        "best_streak", "focus_score", "ratio", "ttl_days",
    }
    assert summary["balance"] == 1050
    assert summary["earned_today"] == 750
    assert summary["cap_left"] == 2850
    assert summary["cap_minutes"] == 60
    assert summary["streak"] == 1
    assert summary["best_streak"] == 1
    assert summary["ratio"] == pytest.approx(0.5)
    assert summary["ttl_days"] == 7
    assert 0.0 <= summary["focus_score"] <= 100.0
    assert store.totals()["bank_balance"] == 1050
    store.close()


def test_economy_accepts_settings_and_none() -> None:
    store = Store(memory=True)
    default = Economy(store, None)
    assert default.credit_pomodoro(1500, 1, True, now=BASE)["earned"] == 750
    settings = Settings()
    settings.economy.earn_ratio = 2.0
    settings.economy.daily_cap_minutes = 600
    doubled = Economy(store, settings)
    assert doubled.credit_pomodoro(1500, 2, True, now=BASE)["earned"] == 3000
    assert doubled.config.earn_ratio == 2.0
    store.close()


# -------------------------------------------------------------------- presety
def test_ensure_defaults_is_idempotent() -> None:
    store = Store(memory=True)
    first = ensure_defaults(store)
    assert first["ok"] is True
    assert len(first["applied"]) == 3
    second = ensure_defaults(store)
    assert second["applied"] == []
    assert len(list_presets(store)) == 3
    store.close()


def test_save_delete_and_apply_preset() -> None:
    store = Store(memory=True)
    ensure_defaults(store)
    saved = save_preset(store, "  Moj preset  ", {"study_minutes": 50, "break_minutes": 10,
                                                  "long_break_minutes": 20, "long_break_every": 3,
                                                  "study_apps": ["code.exe"], "study_sites": ["docs.python.org"],
                                                  "tag": "informatyka"})
    assert saved["ok"] is True
    assert saved["id"] > 0
    assert save_preset(store, "", {})["ok"] is False
    assert save_preset(store, "x", None)["ok"] is False

    applied = apply_preset(store, "Moj preset")
    assert applied["ok"] is True
    assert applied["name"] == "Moj preset"
    assert applied["study_seconds"] == 3000
    assert applied["break_seconds"] == 600
    assert applied["long_break_seconds"] == 1200
    assert applied["long_break_every"] == 3
    assert applied["tag"] == "informatyka"
    assert applied["allowlist"]["study_apps"] == ["code.exe"]
    assert applied["allowlist"]["study_sites"] == ["docs.python.org"]
    plan = Plan.from_dict(applied)  # wynik da sie podac wprost do silnika sesji
    assert plan.study_seconds == 3000
    assert plan.tag == "informatyka"

    assert apply_preset(store, "matura - matematyka")["ok"] is True  # dopasowanie bez wielkosci liter
    missing = apply_preset(store, "nie ma takiego")
    assert missing["ok"] is False
    assert missing["errors"]

    assert delete_preset(store, saved["id"])["ok"] is True
    assert apply_preset(store, "Moj preset")["ok"] is False
    assert delete_preset(store, "nie-liczba")["ok"] is False
    store.close()


def test_normalize_payload_uses_plan_defaults() -> None:
    plan = normalize_payload({"study_minutes": 25, "unknown_key": "ignored"})
    assert plan["study_seconds"] == 1500
    assert plan["break_seconds"] == 300  # domyslna wartosc Plan
    assert plan["mode"] == "STUDY"
    assert plan["allowlist"] == {}
    free = normalize_payload({"mode": "free", "free_minutes": 15, "block_sites": ["x.com"]})
    assert free["mode"] == "FREE"
    assert free["free_seconds"] == 900
    assert free["allowlist"] == {"block_sites": ["x.com"]}
    assert normalize_payload(None) == Plan().to_dict()
