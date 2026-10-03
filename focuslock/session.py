"""Maszyna stanow sesji nauki (pomidoro + tryb wolny).

Kontrakt: docs/INTERFACES.md sekcja 16. Zero zaleznosci od PyQt i od Windows,
zero stanu globalnego - caly stan zyje w instancji ``SessionEngine``.

Przebieg w trybie STUDY::

    IDLE -> ARMING -> STUDY -> BREAK -> STUDY -> ... -> LONG_BREAK -> ... -> DONE

Przejscia nastepuja automatycznie w ``tick()`` (wolane co 1 s):
po kazdym ukonczonym pomodoro silnik sam wchodzi w przerwe, a dluga przerwa
wchodzi co ``Plan.long_break_every`` ukonczonych pomodorow. Po przerwie
automatycznie startuje kolejne pomodoro. Faza DONE jest osiagana wylacznie
przez ``finish()`` (albo przez koniec licznika w trybie FREE).

Tryb FREE (``Plan.mode == "FREE"``) korzysta z tego samego szkieletu faz, ale:
- nie ma przerw ani pomodorow (``start_break``/``abort_pomodoro`` sa no-op),
- licznikiem jest ``Plan.free_seconds``; gdy jest <= 0, sesja trwa do ``finish()``
  i ``state()["open_ended"]`` wynosi True,
- koniec licznika wywoluje ``finish("free_end")``.

Wszystkie metody przyjmuja opcjonalny ``now`` (monotoniczny znacznik czasu);
brak wartosci oznacza ``time.monotonic()``. Dzieki temu logika jest w pelni
testowalna bez czekania na zegar.

Callbacki (wywolywane bezpiecznie - wyjatek w callbacku nie psuje FSM):
- ``on_tick(state: dict)`` - po kazdym ``tick()``,
- ``on_phase({"phase", "previous", "pomodoro_index", "long", "state"})``,
- ``on_pomodoro_end({"completed", "study_seconds", "reason", "pomodoro_index",
  "session_id", "tag"})``,
- ``on_finish({"reason", "state"})``.

Zwracane dict-y akcji sa nadzbiorem ``state()`` plus ``ok``, ``changed`` i ``reason``.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .timer import Countdown, format_seconds

__all__ = ["Phase", "Plan", "SessionEngine", "SESSION_STATE_KEY", "elapsed_gap"]

SESSION_STATE_KEY = "session_state"

Callback = Optional[Callable[[dict], None]]

_MINUTE_ALIASES = {
    "study_minutes": "study_seconds",
    "break_minutes": "break_seconds",
    "long_break_minutes": "long_break_seconds",
    "free_minutes": "free_seconds",
}

_SECOND_ALIASES = {"arming_seconds": "arm_seconds"}


def _minutes_to_seconds(value: Any) -> int:
    try:
        return int(round(float(value) * 60))
    except (TypeError, ValueError):
        return 0


class Phase(str, Enum):
    """Fazy sesji (dziedziczy po str, wiec porownania z ``"STUDY"`` dzialaja)."""

    IDLE = "IDLE"
    ARMING = "ARMING"
    STUDY = "STUDY"
    BREAK = "BREAK"
    LONG_BREAK = "LONG_BREAK"
    DONE = "DONE"


def _as_seconds(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


@dataclass
class Plan:
    """Plan sesji - pola i domyslne wartosci zgodne z kontraktem."""

    mode: str = "STUDY"                 # "STUDY" | "FREE"
    study_seconds: int = 1500
    break_seconds: int = 300
    long_break_seconds: int = 900
    long_break_every: int = 4
    arm_seconds: int = 3
    free_seconds: int = 0
    tag: str = ""
    goal_note: str = ""
    allowlist: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "study_seconds": int(self.study_seconds),
            "break_seconds": int(self.break_seconds),
            "long_break_seconds": int(self.long_break_seconds),
            "long_break_every": int(self.long_break_every),
            "arm_seconds": int(self.arm_seconds),
            "free_seconds": int(self.free_seconds),
            "tag": str(self.tag),
            "goal_note": str(self.goal_note),
            "allowlist": dict(self.allowlist) if isinstance(self.allowlist, dict) else {},
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Plan":
        """Buduje plan z dict-a, tolerujac aliasy w minutach (``study_minutes`` itd.).

        Jawne wartosci w sekundach maja priorytet nad aliasami w minutach.
        """
        if isinstance(data, Plan):
            return data.normalized()
        if not isinstance(data, dict):
            return cls()
        raw: dict[str, Any] = {}
        for key, value in data.items():
            name = str(key)
            if name in _MINUTE_ALIASES:
                target = _MINUTE_ALIASES[name]
                if target not in data:
                    raw[target] = _minutes_to_seconds(value)
            elif name in _SECOND_ALIASES:
                target = _SECOND_ALIASES[name]
                if target not in data:
                    raw[target] = value
            else:
                raw[name] = value
        plan = cls(
            mode=raw.get("mode", cls.mode),
            study_seconds=raw.get("study_seconds", cls.study_seconds),
            break_seconds=raw.get("break_seconds", cls.break_seconds),
            long_break_seconds=raw.get("long_break_seconds", cls.long_break_seconds),
            long_break_every=raw.get("long_break_every", cls.long_break_every),
            arm_seconds=raw.get("arm_seconds", cls.arm_seconds),
            free_seconds=raw.get("free_seconds", cls.free_seconds),
            tag=raw.get("tag", cls.tag),
            goal_note=raw.get("goal_note", cls.goal_note),
            allowlist=raw.get("allowlist", {}),
        )
        return plan.normalized()

    def normalized(self) -> "Plan":
        """Zwraca plan z przycietymi wartosciami (ujemne czasy -> 0, mode -> STUDY/FREE)."""
        mode = str(self.mode or "STUDY").strip().upper()
        if mode not in ("STUDY", "FREE"):
            mode = "STUDY"
        allowlist = dict(self.allowlist) if isinstance(self.allowlist, dict) else {}
        return Plan(
            mode=mode,
            study_seconds=_as_seconds(self.study_seconds),
            break_seconds=_as_seconds(self.break_seconds),
            long_break_seconds=_as_seconds(self.long_break_seconds),
            long_break_every=_as_seconds(self.long_break_every),
            arm_seconds=_as_seconds(self.arm_seconds),
            free_seconds=_as_seconds(self.free_seconds),
            tag=str(self.tag or ""),
            goal_note=str(self.goal_note or ""),
            allowlist=allowlist,
        )


def elapsed_gap(data: dict, *, wall_now: Optional[float] = None,
                mono_now: Optional[float] = None) -> float:
    """Ile sekund minelo od ``serialize()`` do teraz.

    Bierze mniejsza z dwoch wartosci: roznicy czasu sciennego i roznicy zegara
    monotonicznego (gdy ten nie cofnal sie, np. bez restartu systemu). Dla
    swiezych danych bez znacznikow zwraca 0.0. Funkcja jest czysta - mozna ja
    testowac bez zegara systemowego.
    """
    wall = time.time() if wall_now is None else float(wall_now)
    mono = time.monotonic() if mono_now is None else float(mono_now)
    try:
        saved_at = float(data.get("saved_at") or 0.0)
    except (TypeError, ValueError):
        saved_at = 0.0
    try:
        saved_mono = float(data.get("saved_monotonic") or 0.0)
    except (TypeError, ValueError):
        saved_mono = 0.0
    gaps: list[float] = []
    if saved_at > 0:
        gaps.append(max(0.0, wall - saved_at))
    if saved_mono > 0 and mono >= saved_mono:
        gaps.append(max(0.0, mono - saved_mono))
    return min(gaps) if gaps else 0.0


class SessionEngine:
    """Maszyna stanow sesji (pomidoro + tryb wolny)."""

    def __init__(
        self,
        *,
        on_tick: Callback = None,
        on_phase: Callback = None,
        on_pomodoro_end: Callback = None,
        on_finish: Callback = None,
    ) -> None:
        self._callbacks: dict[str, Callback] = {
            "tick": on_tick,
            "phase": on_phase,
            "pomodoro_end": on_pomodoro_end,
            "finish": on_finish,
        }
        self._errors: list[str] = []
        self._plan = Plan()
        self._phase: Phase = Phase.IDLE
        self._timer: Optional[Countdown] = None
        self._session_id: Optional[int] = None
        self._pomodoros_done = 0
        self._pomodoros_aborted = 0
        self._study_seconds_done = 0.0
        self._paused = False
        self._finished_reason = ""
        self._expired_on_restore = False

    # ------------------------------------------------------------------ narzedzia
    @staticmethod
    def _now(now: Optional[float] = None) -> float:
        return float(now) if now is not None else time.monotonic()

    def set_callbacks(self, **callbacks: Callback) -> dict:
        """Podmienia wybrane callbacki (klucze: tick, phase, pomodoro_end, finish)."""
        for name, callback in callbacks.items():
            if name in self._callbacks:
                self._callbacks[name] = callback
        return self.state()

    def _emit(self, name: str, payload: dict) -> None:
        callback = self._callbacks.get(name)
        if callback is None:
            return
        try:
            callback(payload)
        except Exception as exc:  # noqa: BLE001 - FSM nie moze sie wywrocic przez UI
            self._errors.append(f"{name}: {type(exc).__name__}: {exc}")
            del self._errors[:-20]

    def errors(self) -> list[str]:
        """Bledy callbackow (ostatnie 20) - do logu/diagnostyki."""
        return list(self._errors)

    @property
    def plan(self) -> Plan:
        return self._plan

    @property
    def phase(self) -> Phase:
        return self._phase

    # -------------------------------------------------------------- plan i fazy
    def _phase_seconds(self, phase: Phase) -> int:
        if phase == Phase.ARMING:
            return self._plan.arm_seconds
        if phase == Phase.BREAK:
            return self._plan.break_seconds
        if phase == Phase.LONG_BREAK:
            return self._plan.long_break_seconds
        if phase == Phase.STUDY:
            return self._plan.free_seconds if self._plan.mode == "FREE" else self._plan.study_seconds
        return 0

    def _make_timer(self, phase: Phase, seconds: int, now: float) -> Optional[Countdown]:
        if phase in (Phase.IDLE, Phase.DONE):
            return None
        if phase == Phase.STUDY and self._plan.mode == "FREE" and int(seconds) <= 0:
            return None  # tryb wolny bez limitu - trwa do finish()
        timer = Countdown(0.0)
        timer.reset(max(0, int(seconds)), now=now)
        return timer

    def _pomodoro_index(self) -> int:
        index = self._pomodoros_done + self._pomodoros_aborted
        if self._plan.mode == "STUDY" and self._phase in (Phase.ARMING, Phase.STUDY):
            index += 1
        return index

    def _should_long_break(self, done: Optional[int] = None) -> bool:
        every = int(self._plan.long_break_every)
        count = self._pomodoros_done if done is None else int(done)
        return bool(every > 0 and count > 0 and count % every == 0)

    def _enter(self, phase: Phase, seconds: int, now: float, *, emit: bool = True) -> None:
        previous = self._phase
        self._phase = phase
        self._timer = self._make_timer(phase, seconds, now)
        self._paused = False
        if emit and previous != phase:
            self._emit(
                "phase",
                {
                    "phase": phase.value,
                    "previous": previous.value,
                    "pomodoro_index": self._pomodoro_index(),
                    "long": phase == Phase.LONG_BREAK,
                    "state": self.state(now),
                },
            )

    # ------------------------------------------------------------------- start
    def start(self, plan: "Plan | dict", session_id: int, now: Optional[float] = None) -> dict:
        """Startuje sesje: IDLE/ARMING -> ARMING (gdy ``arm_seconds`` > 0) lub STUDY."""
        at = self._now(now)
        self._plan = Plan.from_dict(plan)
        self._session_id = int(session_id) if session_id is not None else None
        self._pomodoros_done = 0
        self._pomodoros_aborted = 0
        self._study_seconds_done = 0.0
        self._finished_reason = ""
        self._paused = False
        self._expired_on_restore = False
        self._errors.clear()
        if self._plan.arm_seconds > 0:
            self._enter(Phase.ARMING, self._plan.arm_seconds, at)
        else:
            self._enter(Phase.STUDY, self._phase_seconds(Phase.STUDY), at)
        return self._result(True, "start", at)

    # -------------------------------------------------------------------- tick
    def tick(self, now: Optional[float] = None) -> dict:
        """Sekundowy puls: domyka fazy i wywoluje callbacki. Zwraca ``state()``."""
        at = self._now(now)
        changed = False
        if self._phase not in (Phase.IDLE, Phase.DONE) and not self._paused:
            if self._phase == Phase.ARMING and (self._timer is None or self._timer.expired(at)):
                self._enter(Phase.STUDY, self._phase_seconds(Phase.STUDY), at)
                changed = True
            elif self._phase == Phase.STUDY and self._timer is not None and self._timer.expired(at):
                if self._plan.mode == "FREE":
                    self.finish("free_end", at)
                else:
                    self._end_pomodoro(completed=True, reason="complete", now=at)
                changed = True
            elif self._phase in (Phase.BREAK, Phase.LONG_BREAK) and self._timer is not None:
                if self._timer.expired(at):
                    self._enter(Phase.STUDY, self._phase_seconds(Phase.STUDY), at)
                    changed = True
        self._emit("tick", self.state(at))
        return self._result(changed, "", at)

    def _end_pomodoro(self, completed: bool, reason: str, now: float) -> None:
        """Rozlicza pomodoro i automatycznie przechodzi do przerwy lub nauki."""
        timer = self._timer
        elapsed = int(timer.elapsed(now)) if timer is not None else 0
        study_seconds = int(timer.total) if (completed and timer is not None) else elapsed
        index = self._pomodoros_done + self._pomodoros_aborted + 1
        if completed:
            self._pomodoros_done += 1
        else:
            self._pomodoros_aborted += 1
        self._study_seconds_done += max(0, study_seconds)
        self._emit(
            "pomodoro_end",
            {
                "completed": bool(completed),
                "study_seconds": int(max(0, study_seconds)),
                "reason": str(reason),
                "pomodoro_index": index,
                "session_id": self._session_id,
                "tag": self._plan.tag,
            },
        )
        if self._plan.break_seconds > 0 or self._plan.long_break_seconds > 0:
            self.start_break(long=self._should_long_break(), now=now)
        else:
            self._enter(Phase.STUDY, self._plan.study_seconds, now)

    # ------------------------------------------------------------------ przerwy
    def start_break(self, long: Optional[bool] = None, now: Optional[float] = None) -> dict:
        """Rozpoczyna przerwe (dluga wg ``long_break_every``, gdy ``long`` is None)."""
        at = self._now(now)
        if self._plan.mode != "STUDY":
            return self._result(False, "not-study-mode", at)
        if self._phase not in (Phase.ARMING, Phase.STUDY, Phase.BREAK, Phase.LONG_BREAK):
            return self._result(False, "not-running", at)
        use_long = self._should_long_break() if long is None else bool(long)
        seconds = self._plan.long_break_seconds if use_long else self._plan.break_seconds
        if seconds <= 0:
            self._enter(Phase.STUDY, self._plan.study_seconds, at)
            return self._result(True, "no-break-in-plan", at)
        self._enter(Phase.LONG_BREAK if use_long else Phase.BREAK, seconds, at)
        return self._result(True, "long-break" if use_long else "break", at)

    def skip_break(self, now: Optional[float] = None) -> dict:
        """Pomija przerwe i od razu startuje kolejne pomodoro."""
        at = self._now(now)
        if self._phase not in (Phase.BREAK, Phase.LONG_BREAK):
            return self._result(False, "not-on-break", at)
        self._enter(Phase.STUDY, self._phase_seconds(Phase.STUDY), at)
        return self._result(True, "skipped", at)

    # ------------------------------------------------------------------- pauza
    def pause(self, now: Optional[float] = None) -> dict:
        """Wstrzymuje licznik - czas pauzy nie zjada budzetu fazy."""
        at = self._now(now)
        if self._timer is None or self._phase in (Phase.IDLE, Phase.DONE):
            return self._result(False, "nothing-to-pause", at)
        if self._timer.pause(at):
            self._paused = True
            return self._result(True, "paused", at)
        return self._result(False, "already-paused", at)

    def resume(self, now: Optional[float] = None) -> dict:
        """Wznawia po ``pause()``."""
        at = self._now(now)
        if self._timer is None or not self._paused:
            return self._result(False, "not-paused", at)
        if self._timer.resume(at):
            self._paused = False
            return self._result(True, "resumed", at)
        return self._result(False, "not-paused", at)

    # ------------------------------------------------------------------- koniec
    def abort_pomodoro(self, reason: str = "abort", now: Optional[float] = None) -> dict:
        """Przerywa pomodoro (``on_pomodoro_end`` z ``completed=False``) i wchodzi w przerwe."""
        at = self._now(now)
        if self._plan.mode != "STUDY" or self._phase not in (Phase.ARMING, Phase.STUDY):
            return self._result(False, "not-in-pomodoro", at)
        self._end_pomodoro(completed=False, reason=reason, now=at)
        return self._result(True, str(reason), at)

    def finish(self, reason: str = "user", now: Optional[float] = None) -> dict:
        """Konczy sesje: faza DONE, ``on_finish``. Czesciowe pomodoro nie jest zaliczane."""
        at = self._now(now)
        if self._phase in (Phase.IDLE, Phase.DONE):
            return self._result(False, "not-running", at)
        if self._phase == Phase.STUDY and self._plan.mode == "STUDY" and self._timer is not None:
            self._study_seconds_done += max(0.0, self._timer.elapsed(at))
        previous = self._phase
        self._phase = Phase.DONE
        self._timer = None
        self._paused = False
        self._finished_reason = str(reason or "user")
        self._emit(
            "phase",
            {
                "phase": Phase.DONE.value,
                "previous": previous.value,
                "pomodoro_index": self._pomodoro_index(),
                "long": False,
                "state": self.state(at),
            },
        )
        payload = {"reason": self._finished_reason, "state": self.state(at)}
        self._emit("finish", payload)
        return self._result(True, self._finished_reason, at)

    # -------------------------------------------------------------------- stan
    def state(self, now: Optional[float] = None) -> dict:
        """Biezacy stan dla UI/kontrolera (JSON-friendly)."""
        at = self._now(now)
        timer = self._timer
        if timer is not None:
            remaining = timer.remaining(at)
            total = timer.total
            elapsed = timer.elapsed(at)
        else:
            remaining = total = elapsed = 0.0
        live_study = self._study_seconds_done
        if timer is not None and self._phase == Phase.STUDY and self._plan.mode == "STUDY":
            live_study += elapsed
        open_ended = (
            self._plan.mode == "FREE"
            and self._phase == Phase.STUDY
            and timer is None
        )
        if open_ended:
            display = "--:--"
        else:
            display = format_seconds(remaining)
        return {
            "phase": self._phase.value,
            "mode": self._plan.mode,
            "remaining": int(math.ceil(max(0.0, remaining) - 1e-9)),
            "total": int(total),
            "elapsed": int(elapsed),
            "pomodoro_index": self._pomodoro_index(),
            "pomodoros_done": int(self._pomodoros_done),
            "pomodoros_aborted": int(self._pomodoros_aborted),
            "study_seconds_done": int(live_study),
            "free_seconds_left": int(math.ceil(max(0.0, remaining) - 1e-9))
            if (self._plan.mode == "FREE" and self._phase == Phase.STUDY)
            else 0,
            "paused": bool(self._paused),
            "session_id": self._session_id,
            "tag": self._plan.tag,
            # pola dodatkowe (nie lamia kontraktu, ulatwiaja UI):
            "goal_note": self._plan.goal_note,
            "long_break": self._phase == Phase.LONG_BREAK,
            "open_ended": bool(open_ended),
            "display": display,
            "finished_reason": self._finished_reason,
            "expired_on_restore": bool(self._expired_on_restore),
        }

    def _result(self, changed: bool, reason: str = "", now: Optional[float] = None,
                *, ok: bool = True) -> dict:
        payload = self.state(now)
        payload["ok"] = bool(ok)
        payload["changed"] = bool(changed)
        payload["reason"] = str(reason or "")
        return payload

    # --------------------------------------------------------------- restart
    def serialize(self, now: Optional[float] = None) -> dict:
        """Stan do zapisu w bazie (odporny na crash/restart procesu).

        ``now`` musi byc tym samym zegarem, ktory dostawalo ``start()``/``tick()``
        (domyslnie ``time.monotonic()``). Zapisywane sa oba znaczniki: czasu
        sciennego i monotoniczny - ``deserialize`` bierze mniejsza roznice, wiec
        restart procesu i restart systemu sa obslugiwane poprawnie.
        """
        at = self._now(now)
        timer = None
        if self._timer is not None:
            timer = {
                "total": float(self._timer.total),
                "elapsed": float(self._timer.elapsed(at)),
                "paused": bool(self._timer.paused),
            }
        return {
            "version": 1,
            "phase": self._phase.value,
            "plan": self._plan.to_dict(),
            "session_id": self._session_id,
            "pomodoros_done": int(self._pomodoros_done),
            "pomodoros_aborted": int(self._pomodoros_aborted),
            "study_seconds_done": round(float(self._study_seconds_done), 3),
            "paused": bool(self._paused),
            "finished_reason": self._finished_reason,
            "timer": timer,
            "saved_at": time.time(),
            "saved_monotonic": at,
        }

    @classmethod
    def deserialize(cls, data: Any) -> dict:
        """Waliduje zapis po crashu i zwraca raport odtwarzalny przez ``restore()``.

        Nigdy nie rzuca wyjatku: przy uszkodzonych danych zwraca ``{"ok": False,
        "errors": [...]}``. Pole ``remaining`` jest juz pomniejszone o czas, ktory
        minal od ``serialize()`` (patrz ``elapsed_gap``).
        """
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                data = None
        if not isinstance(data, dict) or not data:
            return cls._empty_report(["brak zapisu stanu sesji"])
        try:
            phase = Phase(str(data.get("phase", "IDLE")).strip().upper())
        except ValueError:
            return cls._empty_report([f"nieznana faza: {data.get('phase')!r}"])
        if phase not in (Phase.IDLE, Phase.DONE) and not isinstance(data.get("plan"), dict):
            return cls._empty_report(["brak planu w zapisie sesji"])
        plan = Plan.from_dict(data.get("plan") or {}).to_dict()
        timer_raw = data.get("timer") if isinstance(data.get("timer"), dict) else None
        total = float(timer_raw.get("total") or 0.0) if timer_raw else 0.0
        elapsed = float(timer_raw.get("elapsed") or 0.0) if timer_raw else 0.0
        paused = bool(data.get("paused")) or bool(timer_raw and timer_raw.get("paused"))
        gap = elapsed_gap(data)
        gap = 0.0 if paused else gap
        elapsed = max(0.0, min(total, elapsed + gap))
        remaining = max(0.0, total - elapsed)
        session_id = data.get("session_id")
        report = {
            "ok": True,
            "errors": [],
            "warnings": [],
            "version": int(data.get("version") or 1),
            "phase": phase.value,
            "plan": plan,
            "session_id": int(session_id) if session_id is not None else None,
            "pomodoros_done": _as_seconds(data.get("pomodoros_done")),
            "pomodoros_aborted": _as_seconds(data.get("pomodoros_aborted")),
            "study_seconds_done": _as_seconds(data.get("study_seconds_done")),
            "paused": paused,
            "timer": {"total": total, "elapsed": elapsed} if timer_raw else None,
            "remaining": remaining,
            "gap_seconds": gap,
            "expired": bool(total > 0 and remaining <= 0),
            "saved_at": float(data.get("saved_at") or 0.0),
        }
        return report

    @staticmethod
    def _empty_report(errors: list[str]) -> dict:
        return {
            "ok": False,
            "errors": list(errors),
            "warnings": [],
            "version": 0,
            "phase": Phase.IDLE.value,
            "plan": {},
            "session_id": None,
            "pomodoros_done": 0,
            "pomodoros_aborted": 0,
            "study_seconds_done": 0,
            "paused": False,
            "timer": None,
            "remaining": 0.0,
            "gap_seconds": 0.0,
            "expired": False,
            "saved_at": 0.0,
        }

    def restore(self, data: Any, now: Optional[float] = None, *,
                gap_seconds: Optional[float] = None) -> dict:
        """Odtwarza stan po crashu. ``data`` to wynik ``serialize()`` lub ``deserialize()``.

        ``gap_seconds`` dodaje sekundy, ktore minely dodatkowo po odczycie zapisu
        (deserialize sam odejmuje czas od chwili zapisu). Gdy licznik zdazyl
        wygasnac poza aplikacja, ``state()["expired_on_restore"]`` jest True a
        ``remaining`` wynosi 0 - kontrola decyduje, czy ``tick()`` ma domknac
        pomodoro, czy wywolac ``finish("crash")``.
        """
        at = self._now(now)
        if isinstance(data, dict) and data.get("ok") is not None and "plan" in data and "phase" in data:
            report = data
        else:
            report = self.deserialize(data)
        if not report.get("ok"):
            return self._result(False, "restore-failed", at, ok=False)
        self._plan = Plan.from_dict(report.get("plan") or {})
        self._session_id = report.get("session_id")
        self._pomodoros_done = int(report.get("pomodoros_done") or 0)
        self._pomodoros_aborted = int(report.get("pomodoros_aborted") or 0)
        self._study_seconds_done = float(report.get("study_seconds_done") or 0)
        self._errors.clear()
        self._finished_reason = ""
        try:
            phase = Phase(str(report.get("phase")).upper())
        except ValueError:
            return self._result(False, "restore-failed", at, ok=False)
        self._phase = phase
        timer_info = report.get("timer") or {}
        total = float(timer_info.get("total") or 0.0)
        elapsed = float(timer_info.get("elapsed") or 0.0)
        paused = bool(report.get("paused"))
        if not paused and gap_seconds:
            elapsed += max(0.0, float(gap_seconds))
        elapsed = max(0.0, min(total, elapsed))
        self._paused = False
        self._expired_on_restore = False
        if phase in (Phase.IDLE, Phase.DONE) or total <= 0:
            self._timer = None
        else:
            timer = Countdown(0.0)
            timer.reset(total, now=at - elapsed)
            self._timer = timer
            if paused:
                timer.pause(at)
                self._paused = True
            elif timer.remaining(at) <= 0:
                self._expired_on_restore = True
        return self._result(True, "restore", at)

    # --------------------------------------------------------- most do bazy
    def save_to_store(self, store, now: Optional[float] = None) -> dict:
        """Zapisuje ``serialize()`` w ustawieniach (klucz ``session_state``)."""
        payload = self.serialize(now)
        store.set_setting(SESSION_STATE_KEY, payload)
        return payload

    def load_from_store(self, store, now: Optional[float] = None) -> dict:
        """Wczytuje i odtwarza zapis po crashu (bez zadnych wyjatkow)."""
        try:
            raw = store.get_setting(SESSION_STATE_KEY)
        except Exception as exc:  # noqa: BLE001
            return self._result(False, f"store-error: {type(exc).__name__}", now, ok=False)
        if not raw:
            return self._result(False, "empty", now, ok=False)
        return self.restore(raw, now)
