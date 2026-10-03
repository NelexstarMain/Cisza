"""Ekonomia banku czasu: zarabianie za pomodoro, wydawanie i kary.

Kontrakt: docs/INTERFACES.md sekcja 15. Zero zaleznosci od PyQt i Windows.

Reguly twarde (wszystkie sekundy, wszystkie teksty ASCII):
- ``earned = floor(study_seconds * earn_ratio / round_seconds) * round_seconds``,
  ale nie wiecej niz ``daily_cap_minutes * 60 - earned_today``; nadwyzka trafia
  do pola ``capped``;
- przerwane pomodoro (``completed=False``) nie zarabia nic, a gdy
  ``abort_penalty_minutes > 0`` zabiera kare z banku FIFO (nigdy ponizej zera);
- zarobione sekundy trafiaja do banku jako lot z TTL ``bank_ttl_days`` dni;
- ``sweep_expired()`` usuwa wygasle loty (raportuje ile sekund przepadlo);
- seria: dzien z nauka >= ``streak_min_study_minutes`` kontynuuje serie
  (dzien po dniu) albo startuje od 1; luka zeruje serie.

Formula ``focus_score(days)`` (0..100)::

    completed_ratio = pomodoros_done / (pomodoros_done + pomodoros_aborted)
    avg_study       = suma study_seconds z ostatnich `days` dni / days
    goal            = daily_cap_minutes * 60
    score           = 100 * completed_ratio * min(1, avg_study / goal)

Dzien bez zadnego pomodoro nie wchodzi do ``completed_ratio`` (0/0 = 0).

UWAGA O CZASIE: ``now`` w tym module to epoch czasu sciennego (``time.time()``),
bo takie samo ``now`` przyjmuje zamrozone API ``Store`` (``bank_balance``,
``earned_today``, ``expire_lots``, ``daily_series``). Nie nalezy tu przekazywac
znacznika z ``time.monotonic()`` uzywanego przez ``SessionEngine``. Brak ``now``
oznacza ``time.time()``.
"""
from __future__ import annotations

import math
import time
from dataclasses import fields as dataclass_fields
from datetime import date
from typing import Any, Optional

from .config import EconomyConfig

__all__ = ["Economy"]

_DAY_FORMAT = "%Y-%m-%d"

REASON_OK = "ok"
REASON_CAPPED = "capped"
REASON_CAP = "cap"
REASON_ABORTED = "przerwane"
REASON_ZERO = "zero"


def _today(now: Optional[float] = None) -> str:
    return time.strftime(_DAY_FORMAT, time.localtime(now if now is not None else time.time()))


def _safe_int(value: Any, default: int = 0) -> int:
    """Konwersja do int bez wyjatkow (None, tekst, float)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _days_between(start: str, end: str) -> Optional[int]:
    """Roznica dni miedzy dwiema datami ``YYYY-MM-DD`` (None gdy daty sa zle)."""
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except (TypeError, ValueError):
        return None


def _resolve_config(settings: Any) -> EconomyConfig:
    """Przyjmuje ``Settings``, ``EconomyConfig`` albo dict - zawsze zwraca EconomyConfig."""
    if settings is None:
        return EconomyConfig()
    if isinstance(settings, EconomyConfig):
        return settings
    nested = getattr(settings, "economy", None)
    if isinstance(nested, EconomyConfig):
        return nested
    if isinstance(nested, dict):
        settings = nested
    if isinstance(settings, dict):
        known = {f.name for f in dataclass_fields(EconomyConfig)}
        return EconomyConfig(**{k: v for k, v in settings.items() if k in known})
    return EconomyConfig()


class Economy:
    """Logika banku czasu oparta o ``focuslock.store.Store``."""

    def __init__(self, store, settings: Any = None) -> None:
        self._store = store
        self._settings = settings
        self._config = _resolve_config(settings)
        self._warnings: list[str] = []

    # ------------------------------------------------------------------ pomoc
    @property
    def store(self):
        return self._store

    def warnings(self) -> list[str]:
        """Ostrzezenia z ostatnich operacji (np. nieudane liczenie focus_score)."""
        return list(self._warnings)

    def _warn(self, message: str) -> None:
        self._warnings.append(message)
        del self._warnings[:-10]

    @property
    def config(self) -> EconomyConfig:
        return self._config

    @property
    def settings(self) -> Any:
        return self._settings

    @property
    def daily_cap_seconds(self) -> int:
        return max(0, int(self._config.daily_cap_minutes)) * 60

    def _resolve_now(self, now: Optional[float] = None) -> float:
        return float(now) if now is not None else time.time()

    # -------------------------------------------------------------- zarobek
    def credit_pomodoro(self, study_seconds: int, session_id: int, completed: bool,
                        now: Optional[float] = None) -> dict:
        """Rozlicza pomodoro i zwraca ``{"earned", "capped", "penalty", "balance", "reason"}``.

        ``reason``: ``ok`` | ``capped`` (czesc nadwyzki obcieta limitem) | ``cap``
        (limit wyczerpany) | ``zero`` (za malo nauki na zaokraglona jednostke) |
        ``przerwane`` (pomodoro przerwane).
        """
        at = self._resolve_now(now)
        study = max(0, int(study_seconds))
        earned = 0
        capped = 0
        penalty = 0
        if not completed:
            reason = REASON_ABORTED
            penalty_minutes = max(0, int(self._config.abort_penalty_minutes))
            if penalty_minutes > 0:
                penalty = int(self._store.spend(penalty_minutes * 60, session_id, note="kara"))
        else:
            raw = self._raw_earned(study)
            left = max(0, self.daily_cap_seconds - int(self._store.earned_today(at)))
            earned = min(raw, left)
            capped = max(0, raw - earned)
            if earned > 0:
                self._store.add_lot(
                    earned,
                    session_id=session_id,
                    note="pomodoro",
                    ttl_days=max(0, int(self._config.bank_ttl_days)),
                    reason="EARNED",
                    created_at=at,
                )
            if raw <= 0:
                reason = REASON_ZERO
            elif earned <= 0:
                reason = REASON_CAP
            elif capped > 0:
                reason = REASON_CAPPED
            else:
                reason = REASON_OK
        balance = self.balance(at)
        return {
            "earned": int(earned),
            "capped": int(capped),
            "penalty": int(penalty),
            "balance": int(balance),
            "reason": reason,
            "cap_left": int(self.daily_cap_left(at)),
        }

    def _raw_earned(self, study_seconds: int) -> int:
        """``floor(study * ratio / round) * round`` - bez limitu dziennego."""
        ratio = float(self._config.earn_ratio)
        step = max(1, int(self._config.round_seconds))
        if study_seconds <= 0 or ratio <= 0:
            return 0
        units = math.floor((study_seconds * ratio) / step + 1e-9)
        return max(0, int(units) * step)

    # ----------------------------------------------------------------- bank
    def spend_free(self, seconds: int, session_id: int) -> int:
        """Wydaje sekundy z banku FIFO; zwraca faktycznie pobrana ilosc (moze byc mniej)."""
        return int(self._store.spend(max(0, int(seconds)), session_id, note="FREE"))

    def refund(self, seconds: int, session_id: int) -> int:
        """Zwraca niewykorzystane sekundy jako nowy lot bez wygasania. Zwraca ile oddano."""
        amount = max(0, int(seconds))
        if amount <= 0:
            return 0
        self._store.add_lot(amount, session_id=session_id, note="REFUND", ttl_days=0, reason="ADJUST")
        return int(amount)

    def balance(self, now: Optional[float] = None) -> int:
        return int(self._store.bank_balance(self._resolve_now(now)))

    def earned_today(self, now: Optional[float] = None) -> int:
        return int(self._store.earned_today(self._resolve_now(now)))

    def daily_cap_left(self, now: Optional[float] = None) -> int:
        return max(0, self.daily_cap_seconds - self.earned_today(now))

    def sweep_expired(self, now: Optional[float] = None) -> int:
        """Usuwa wygasle loty przez ``store.expire_lots(now)``.

        Zwraca liczbe sekund, ktore przepadly. ``now`` jest opcjonalne (zamrozone
        API bazy i kontrakt nie maja go w sygnaturze), ale pozwala testowac TTL
        bez czekania 7 dni.
        """
        return max(0, int(self._store.expire_lots(self._resolve_now(now))))

    # ----------------------------------------------------------------- seria
    def register_study_day(self, studied_seconds: int, day: Optional[str] = None) -> dict:
        """Aktualizuje serie dni nauki. Zwraca ``{"current", "best", "changed", "reason"}``.

        Dzien kwalifikuje sie, gdy ``studied_seconds >= streak_min_study_minutes * 60``.
        Kolejny dzien kalendarzowy kontynuuje serie, kazdy inny odstep startuje od 1.
        Wywolanie dla dnia bez nauki (lub dnia starszego niz zapisany) nie psuje serii,
        ale wykryta luka zeruje ``current``. ``reason`` opisuje, co zrobiono:
        ``started`` | ``extended`` | ``already-counted`` | ``below-minimum`` |
        ``no-study`` | ``gap-reset`` | ``stale-day``.
        """
        reference = _today()
        if day:
            text = str(day).strip()
            if _days_between(text, text) is not None:  # walidacja formatu YYYY-MM-DD
                reference = text
        studied = _safe_int(studied_seconds)
        minimum = max(0, int(self._config.streak_min_study_minutes)) * 60
        row = self._store.get_streak("study") or {}
        current = int(row.get("current") or 0)
        best = int(row.get("best") or 0)
        last_day = str(row.get("last_day") or "")
        changed = False
        reason = "no-change"

        if last_day and reference < last_day:
            return {"current": current, "best": best, "changed": False, "reason": "stale-day"}

        if studied > 0 and studied >= minimum:
            if last_day == reference:
                reason = "already-counted"
            else:
                gap = _days_between(last_day, reference) if last_day else None
                current = current + 1 if gap == 1 else 1
                if current > best:
                    best = current
                self._store.set_streak("study", current, best, reference)
                changed = True
                reason = "extended" if gap == 1 else "started"
        else:
            gap = _days_between(last_day, reference) if last_day else None
            if gap is not None and gap > 1 and current != 0:
                current = 0
                self._store.set_streak("study", 0, best, last_day)
                changed = True
                reason = "gap-reset"
            elif not studied:
                reason = "no-study"
            else:
                reason = "below-minimum"
        return {"current": int(current), "best": int(best), "changed": bool(changed), "reason": reason}

    # ------------------------------------------------------------ statystyki
    def focus_score(self, days: int = 30, now: Optional[float] = None) -> float:
        """Wskaznik 0..100: skutecznosc pomodorow * sredni dzienny czas nauki / cel.

        ``completed_ratio = done / (done + aborted)``; ``avg`` to suma
        ``study_seconds`` z ostatnich ``days`` dni podzielona przez ``days``;
        ``goal = daily_cap_minutes * 60`` (EconomyConfig nie ma osobnego pola celu).
        """
        window = max(1, int(days))
        at = self._resolve_now(now)
        totals = self._store.totals() or {}
        done = max(0, int(totals.get("pomodoros_done") or 0))
        aborted = max(0, int(totals.get("pomodoros_aborted") or 0))
        attempts = done + aborted
        completed_ratio = (done / attempts) if attempts else 0.0
        try:
            series = self._store.daily_series(window, at) or []
        except Exception as exc:  # noqa: BLE001 - metryka nie moze wywrocic ekranu
            self._warn(f"focus_score: {type(exc).__name__}: {exc}")
            series = []
        studied = sum(max(0, int(row.get("study_seconds") or 0)) for row in series)
        goal = max(1, self.daily_cap_seconds)
        average = studied / window
        score = 100.0 * completed_ratio * min(1.0, average / goal)
        return round(max(0.0, min(100.0, score)), 1)

    def summary(self, now: Optional[float] = None) -> dict:
        """Zbiorczy obrazek dla ekranu banku/statystyk."""
        at = self._resolve_now(now)
        streak = self._store.get_streak("study") or {}
        return {
            "balance": self.balance(at),
            "earned_today": self.earned_today(at),
            "cap_left": self.daily_cap_left(at),
            "cap_minutes": int(self._config.daily_cap_minutes),
            "streak": int(streak.get("current") or 0),
            "best_streak": int(streak.get("best") or 0),
            "focus_score": self.focus_score(30, at),
            "ratio": float(self._config.earn_ratio),
            "ttl_days": int(self._config.bank_ttl_days),
        }
