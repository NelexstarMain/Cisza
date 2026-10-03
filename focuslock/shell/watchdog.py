"""Watchdog powloki: pilnuje zycia GUI i cofa lockdown po crashu.

Kontrakt: docs/INTERFACES.md sekcja 13.

Uruchamiany jako osobny proces (`pythonw -m focuslock --watchdog`).
- Dopoki znacznik `paths.heartbeat_path()` jest swiezy - spi.
- Gdy znacznik jest przestarzaly albo `lockstate.active` wskazuje aktywny
  lockdown bez zywego GUI -> `focuslock.recovery.restore_everything(reason="watchdog")`.
- Wszystko w try/except; brak modulu recovery nie wywala procesu.
"""
from __future__ import annotations

import time

from .. import lockstate, paths

HEARTBEAT_TIMEOUT_DEFAULT = 10.0
WATCHDOG_INTERVAL_DEFAULT = 2.0

_last_error = ""


def _log(message: str) -> None:
    text = "[cisza-watchdog] %s" % message
    try:
        print(text, flush=True)
    except Exception:
        pass
    try:
        with open(paths.log_path(), "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


# ------------------------------------------------------------------ heartbeat
def heartbeat_touch() -> None:
    """Zapisuje czas do paths.heartbeat_path() (tmp + replace). Nigdy nie rzuca."""
    global _last_error
    path = paths.heartbeat_path()
    payload = repr(time.time())
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        _last_error = ""
        return
    except Exception as exc:
        _last_error = str(exc)
    try:  # awaryjnie: zapis bezposredni
        path.write_text(payload, encoding="utf-8")
        _last_error = ""
    except Exception as exc:
        _last_error = str(exc)


def heartbeat_last_error() -> str:
    return _last_error


def heartbeat_age(now: float | None = None) -> float | None:
    """Wiek znacznika w sekundach albo None, gdy brak/nieczytelny."""
    path = paths.heartbeat_path()
    try:
        if not path.exists():
            return None
        stamp = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    current = time.time() if now is None else float(now)
    return current - stamp


def is_heartbeat_fresh(timeout: float = HEARTBEAT_TIMEOUT_DEFAULT, now: float | None = None) -> bool:
    """True, gdy znacznik istnieje i jest nie starszy niz `timeout` sekund."""
    try:
        if timeout <= 0:
            return False
        age = heartbeat_age(now)
    except Exception:
        return False
    if age is None:
        return False
    return age <= float(timeout)


def should_restore(
    timeout: float = HEARTBEAT_TIMEOUT_DEFAULT, now: float | None = None
) -> tuple[bool, str]:
    """Czysta decyzja watchdoga: (czy_przywracac, powod)."""
    if is_heartbeat_fresh(timeout, now):
        return False, "heartbeat-fresh"
    state = None
    try:
        state = lockstate.read()
    except Exception:
        state = None
    if state is not None and getattr(state, "active", False):
        return True, "lockstate-active-without-gui"
    if paths.heartbeat_path().exists():
        return True, "stale-heartbeat"
    return False, "no-heartbeat-yet"


# ------------------------------------------------------------------ recovery
def _restore(reason: str) -> int:
    try:
        from .. import recovery  # import leniwy: modul moze nie istniec
    except Exception as exc:
        _log("brak focuslock.recovery (%s) - probuje awaryjnie przywrocic pasek zadan" % exc)
        try:
            from . import taskbar

            fallback = taskbar.show()
            _log("taskbar.show: ok=%s applied=%s" % (fallback.get("ok"), fallback.get("applied")))
        except Exception as inner:
            _log("awaryjne taskbar.show nie powiodlo sie: %s" % inner)
        return 3

    try:
        report = recovery.restore_everything(reason=reason)
    except Exception as exc:
        _log("restore_everything rzucilo wyjatek: %s" % exc)
        return 3

    errors = []
    if isinstance(report, dict):
        errors = list(report.get("errors") or [])
        _log(
            "restore_everything(reason=%s) ok=%s applied=%s warnings=%s errors=%s"
            % (reason, report.get("ok"), len(report.get("applied") or []), len(report.get("warnings") or []), errors)
        )
    else:
        _log("restore_everything zwrocilo %r" % (report,))
    return 2 if not errors else 3


# -------------------------------------------------------------------- petla
def run_watchdog(
    *,
    interval: float = WATCHDOG_INTERVAL_DEFAULT,
    timeout: float = HEARTBEAT_TIMEOUT_DEFAULT,
    dry_run: bool = False,
) -> int:
    """Petla watchdoga. Zwraca kod wyjscia: 0=nic nie robil, 2=przywrocil, 3=blad."""
    interval = max(0.1, float(interval))
    timeout = max(0.5, float(timeout))

    if dry_run:
        due, reason = should_restore(timeout)
        _log("dry-run: restore_due=%s reason=%s (system nietkniety)" % (due, reason))
        return 0

    _log("start: interval=%.1fs timeout=%.1fs" % (interval, timeout))
    while True:
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            _log("przerwano (KeyboardInterrupt)")
            return 0
        try:
            due, reason = should_restore(timeout)
            if not due:
                continue
            _log("wykryto potrzebe przywrocenia: %s" % reason)
            return _restore(reason)
        except KeyboardInterrupt:
            _log("przerwano (KeyboardInterrupt)")
            return 0
        except Exception as exc:
            _log("blad petli: %s" % exc)
            continue


def main(argv: list[str] | None = None) -> int:
    """Prosty punkt wejscia dla `python -m focuslock --watchdog` (opcjonalny)."""
    args = list(argv or [])
    dry_run = "--dry-run" in args
    return run_watchdog(dry_run=dry_run)
