"""Liczniki odliczajace wstecz (monotoniczne, w pelni testowalne).

Kontrakt: docs/INTERFACES.md sekcja 16. Zero zaleznosci od PyQt i od Windows.

Zasady:
- czas jest czytany przez wstrzykiwany zegar (`now` w konstruktorze Countdown,
  domyslnie ``time.monotonic``) albo przez jawnie podany znacznik czasu
  (`now` w kazdej metodzie);
- brak stanu globalnego;
- ``format_seconds`` zwraca ``mm:ss``, a powyzej godziny ``hh:mm:ss``.

Uwaga terminologiczna: w konstruktorze ``now`` to *zegar* (callable zwracajacy
sekundy), a w metodach ``now`` to konkretny *znacznik czasu* w sekundach.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

__all__ = ["Clock", "monotonic", "format_seconds", "Countdown"]

Clock = Callable[[], float]


def monotonic() -> float:
    """Domyslny zegar modulu (monotoniczny, odporny na zmiany czasu systemowego)."""
    return time.monotonic()


def format_seconds(seconds: float) -> str:
    """Formatuje liczbe sekund jako ``mm:ss`` lub ``hh:mm:ss`` powyzej godziny.

    Wartosci ujemne sa traktowane jak zero; czesc ulamkowa jest obcinana.
    """
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class Countdown:
    """Odliczanie w dol z deadline, pauza i wstrzykiwanym zegarem.

    Licznik startuje w momencie utworzenia (``start()`` jest wolane automatycznie).
    ``remaining()`` nigdy nie schodzi ponizej zera, a ``elapsed()`` nie przekracza
    ``total``. Czas spedzony na pauzie nie zjada budzetu licznika.
    """

    def __init__(self, seconds: float = 0, *, now: Optional[Clock] = None) -> None:
        self._clock: Clock = now or monotonic
        self._total = max(0.0, float(seconds))
        self._started_at = 0.0
        self._running = False
        self._paused_at: Optional[float] = None
        self._paused_total = 0.0
        self.start()

    # ------------------------------------------------------------------ narzedzia
    def _resolve(self, now: Optional[float] = None) -> float:
        return float(self._clock()) if now is None else float(now)

    def _pause_so_far(self, at: float) -> float:
        total = self._paused_total
        if self._paused_at is not None:
            total += max(0.0, at - self._paused_at)
        return total

    # -------------------------------------------------------------------- stan
    @property
    def total(self) -> float:
        """Zaplanowany czas w sekundach."""
        return self._total

    @property
    def running(self) -> bool:
        """Czy licznik jest aktywny (po ``reset``/``start``)."""
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    @property
    def started_at(self) -> float:
        """Znacznik czasu startu (zegar przekazany do ``start``)."""
        return self._started_at

    # ------------------------------------------------------------------ sterowanie
    def start(self, now: Optional[float] = None) -> None:
        """(Re)startuje licznik od pelnego budzetu w chwili ``now``."""
        self._started_at = self._resolve(now)
        self._running = True
        self._paused_at = None
        self._paused_total = 0.0

    def reset(self, seconds: Optional[float] = None, now: Optional[float] = None) -> None:
        """Ustawia nowy budzet (opcjonalnie) i startuje od zera."""
        if seconds is not None:
            self._total = max(0.0, float(seconds))
        self.start(now)

    def pause(self, now: Optional[float] = None) -> bool:
        """Wstrzymuje licznik. Zwraca True, gdy stan sie zmienil."""
        if not self._running or self._paused_at is not None:
            return False
        self._paused_at = self._resolve(now)
        return True

    def resume(self, now: Optional[float] = None) -> bool:
        """Wznawia licznik. Zwraca True, gdy stan sie zmienil."""
        if self._paused_at is None:
            return False
        self._paused_total += max(0.0, self._resolve(now) - self._paused_at)
        self._paused_at = None
        return True

    # -------------------------------------------------------------------- odczyt
    def elapsed(self, now: Optional[float] = None) -> float:
        """Sekundy aktywnego odliczania (bez pauz), ograniczone do ``total``."""
        if not self._running:
            return 0.0
        at = self._resolve(now)
        raw = at - self._started_at - self._pause_so_far(at)
        return min(self._total, max(0.0, raw))

    def remaining(self, now: Optional[float] = None) -> float:
        """Sekundy do konca (>= 0)."""
        if not self._running:
            return self._total
        return max(0.0, self._total - self.elapsed(now))

    def expired(self, now: Optional[float] = None) -> bool:
        """True, gdy licznik wystartowal i budzet zostal wykorzystany."""
        if not self._running:
            return False
        return self.remaining(now) <= 0.0

    def progress(self, now: Optional[float] = None) -> float:
        """Postep 0.0..1.0 (1.0 dla budzetu zerowego)."""
        if self._total <= 0:
            return 1.0
        return min(1.0, max(0.0, self.elapsed(now) / self._total))

    def deadline(self, now: Optional[float] = None) -> float:
        """Znacznik czasu planowanego konca (przy pauzie: bez dalszych pauz)."""
        if not self._running:
            return 0.0
        return self._started_at + self._paused_total + self._total

    def display(self, now: Optional[float] = None) -> str:
        """``mm:ss`` albo ``hh:mm:ss`` z pozostalego czasu."""
        return format_seconds(self.remaining(now))

    def snapshot(self, now: Optional[float] = None) -> dict:
        """Migawka stanu licznika (JSON-friendly)."""
        at = self._resolve(now) if now is not None else None
        return {
            "total": self._total,
            "remaining": self.remaining(at),
            "elapsed": self.elapsed(at),
            "paused": self.paused,
            "expired": self.expired(at),
            "display": self.display(at),
        }

    def to_dict(self, now: Optional[float] = None) -> dict:
        """Dane do zapisu po crashu (pozycje odtwarzalne przez ``session.restore``)."""
        at = self._resolve(now) if now is not None else None
        return {"total": self._total, "elapsed": self.elapsed(at), "paused": self.paused}

    def __repr__(self) -> str:  # pragma: no cover - diagnostyka
        return (
            f"Countdown(total={self._total:.3f}, remaining={self.remaining():.3f}, "
            f"paused={self.paused})"
        )

    @staticmethod
    def format(seconds: float) -> str:
        """Skrot do ``format_seconds`` (wygodne dla UI)."""
        return format_seconds(seconds)
