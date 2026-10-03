"""Szybkie wykrywanie nowo uruchomionych procesow.

Kontrakt zamrozony w docs/INTERFACES.md (sekcja 2):
- LaunchWatcher(guard, interval=0.5, on_event=None),
- start()/stop() zwracaja raport {"ok","applied","warnings","errors"},
- property backend == "wmi" | "poll".

Preferowany backend to WMI (Win32_ProcessStartTrace). Gdy pywin32 nie jest
zainstalowany albo WMI odmowi uprawnien (klasa zdarzen wymaga administratora),
watcher automatycznie przechodzi na odpytywanie psutil co `interval` sekund.

Zdarzenia przekazywane do `on_event`: ("process_start", {"pid","name","exe","backend"}).
Zablokowanie procesu zglosi sam ProcessGuard (event "blocked_app").

Kod jest czysto ASCII, bez zaleznosci od PyQt.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .processes import (
    ProcessGuard,
    ProcessInfo,
    is_own_process,
    is_system_safe,
    list_processes,
)

try:  # psutil jest wymagany w runtime, ale import nie moze wywalic modulu
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

__all__ = ["WMI_QUERY", "LaunchWatcher"]

WMI_QUERY = "SELECT * FROM Win32_ProcessStartTrace"
_WMI_PROBE_TIMEOUT_MS = 1  # pierwszy NextEvent sluzy jako test uprawnien
_WMI_LOOP_TIMEOUT_MS = 250  # okno oczekiwania w petli nasluchu
_WMI_START_TIMEOUT = 3.0  # s - ile start() czeka na decyzje o backendzie

# Kody HRESULT traktowane jako "brak zdarzen w oknie czasowym".
_TIMEOUT_HRESULTS = frozenset(
    {
        0x80041064,  # WBEM_E_TIMED_OUT
        0x80041069,  # wbemErrTimedout (warianty bibliotek)
        0x80041033,  # WBEM_E_* (spotykane przy NextEvent(timeout))
    }
)


def _report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


def _err(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _is_timeout(exc: BaseException) -> bool:
    """Rozpoznaje brak zdarzen w oknie czasowym (to nie jest blad)."""
    text = " ".join(str(arg) for arg in getattr(exc, "args", ())).lower()
    if "time" in text and ("out" in text or "timed" in text):
        return True
    hresult = getattr(exc, "hresult", None)
    if hresult is None:
        return False
    try:
        return (int(hresult) & 0xFFFFFFFF) in _TIMEOUT_HRESULTS
    except Exception:
        return False


class LaunchWatcher:
    """Wykrywa nowe procesy i przekazuje je do ProcessGuard."""

    def __init__(
        self,
        guard: ProcessGuard,
        interval: float = 0.5,
        on_event: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._guard = guard
        try:
            self._interval = max(0.05, float(interval))
        except Exception:
            self._interval = 0.5
        self._on_event = on_event if callable(on_event) else None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._decided = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backend = "poll"
        self._seen_count = 0
        self._last_error = ""

    # ------------------------------------------------------------------- publiczne

    @property
    def backend(self) -> str:
        """"wmi" gdy dziala Win32_ProcessStartTrace, inaczej "poll"."""
        with self._lock:
            return self._backend

    def start(self) -> dict:
        """Uruchamia nasluch; nigdy nie rzuca wyjatku."""
        report = _report()
        try:
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    report["warnings"].append("LaunchWatcher juz dziala")
                    return report
                self._stop.clear()
                self._decided.clear()
                self._backend = "poll"
                self._thread = threading.Thread(
                    target=self._run, name="cisza-launchwatch", daemon=True
                )
                self._thread.start()
            # Krotko czekamy, az watek wybierze backend (WMI albo odpytywanie).
            self._decided.wait(timeout=_WMI_START_TIMEOUT)
            if not self._decided.is_set():
                report["warnings"].append("Nie udalo sie ustalic backendu - zostaje odpytywanie")
            backend = self.backend
            report["applied"].append(f"launchwatch:start:{backend}")
            report["backend"] = backend  # dodatkowe pole dla helpera/UI (raport RPC)
            if self._last_error:
                report["warnings"].append(self._last_error)
        except Exception as exc:
            report["ok"] = False
            report["errors"].append(_err(exc))
        return report

    def stop(self) -> dict:
        """Zatrzymuje nasluch; nigdy nie rzuca wyjatku."""
        report = _report()
        try:
            with self._lock:
                thread = self._thread
                self._stop.set()
            if thread is None:
                report["warnings"].append("LaunchWatcher nie byl uruchomiony")
            else:
                if thread.is_alive():
                    thread.join(timeout=max(1.0, self._interval * 4))
                report["applied"].append(f"launchwatch:stop:{self.backend}")
            with self._lock:
                self._thread = None
            report["backend"] = self.backend
            if self._last_error:
                report["warnings"].append(self._last_error)
        except Exception as exc:
            report["ok"] = False
            report["errors"].append(_err(exc))
        return report

    # -------------------------------------------------------------------- wewnetrzne

    def _run(self) -> None:
        """Wybiera backend, a przy awarii WMI w trakcie pracy przechodzi na psutil."""
        try:
            if self._wmi_available():
                with self._lock:
                    self._backend = "wmi"
                self._decided.set()
                if self._wmi_loop():
                    return
                with self._lock:
                    self._backend = "poll"
                    self._last_error = "WMI przerwalo nasluch - uzywam odpytywania"
            else:
                with self._lock:
                    self._backend = "poll"
                self._decided.set()
            self._poll_loop()
        except Exception as exc:
            self._last_error = _err(exc)
            self._decided.set()
            try:
                self._poll_loop()
            except Exception as exc2:
                self._last_error = _err(exc2)
        finally:
            self._stop.set()

    def _wmi_available(self) -> bool:
        try:
            import win32com.client  # noqa: F401
            import pythoncom  # noqa: F401
        except Exception as exc:
            self._last_error = f"WMI niedostepne ({exc.__class__.__name__}) - uzywam odpytywania"
            return False
        return True

    def _wmi_loop(self) -> bool:
        """True = WMI dzialalo i zostalo zatrzymane normalnie; False = trzeba fallbacku."""
        import pythoncom  # lokalnie: tylko gdy backend WMI dziala
        import win32com.client

        inited = False
        try:
            try:
                pythoncom.CoInitialize()
                inited = True
            except Exception:
                pass
            locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            service = locator.ConnectServer(".", "root\\cimv2")
            events = service.ExecNotificationQuery(WMI_QUERY)
            # Pierwsze wywolanie sluzy jako test uprawnien: brak zdarzen w oknie
            # czasowym jest poprawne, blad (np. AccessDenied) - nie.
            try:
                event = events.NextEvent(_WMI_PROBE_TIMEOUT_MS)
                self._handle_wmi_event(event)
            except Exception as exc:
                if not _is_timeout(exc):
                    self._last_error = f"WMI: brak dostepu do Win32_ProcessStartTrace ({_err(exc)})"
                    return False
            while not self._stop.is_set():
                try:
                    event = events.NextEvent(_WMI_LOOP_TIMEOUT_MS)
                except Exception as exc:
                    if _is_timeout(exc):
                        continue
                    self._last_error = f"WMI: {_err(exc)}"
                    return False
                self._handle_wmi_event(event)
            return True
        except Exception as exc:
            self._last_error = f"WMI: {_err(exc)}"
            return False
        finally:
            if inited:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def _handle_wmi_event(self, event: Any) -> None:
        pid = 0
        name = ""
        for attr in ("ProcessID", "ProcessName"):
            try:
                value = getattr(event, attr)
            except Exception:
                try:
                    value = event.Properties_(attr).Value
                except Exception:
                    value = None
            if attr == "ProcessID":
                try:
                    pid = int(value)
                except Exception:
                    pid = 0
            else:
                name = str(value or "")
        if pid <= 0:
            return
        self._dispatch(self._resolve_info(pid, name))

    def _resolve_info(self, pid: int, fallback_name: str = "") -> ProcessInfo:
        """Dociaga szczegoly procesu (nazwa moze nie miec rozszerzenia w WMI)."""
        if psutil is not None:
            try:
                proc = psutil.Process(pid)
                name = proc.name() or fallback_name
                try:
                    exe = proc.exe() or ""
                except Exception:
                    exe = ""
                try:
                    created = float(proc.create_time())
                except Exception:
                    created = time.time()
                return ProcessInfo(pid=pid, name=name, exe=exe, create_time=created)
            except Exception:
                pass
        return ProcessInfo(pid=pid, name=fallback_name, exe="", create_time=time.time())

    def _poll_loop(self) -> None:
        """Fallback: odpytywanie psutil co `interval` sekund."""
        known = {info.pid for info in list_processes()}
        while not self._stop.wait(self._interval):
            try:
                current = list_processes()
                pids = set()
                for info in current:
                    pids.add(info.pid)
                    if info.pid in known:
                        continue
                    self._dispatch(info, backend="poll")
                known = pids
            except Exception as exc:
                self._last_error = _err(exc)

    def _dispatch(self, info: ProcessInfo, backend: Optional[str] = None) -> None:
        try:
            if info is None or info.pid <= 0:
                return
            if is_system_safe(info.name) or is_own_process(info):
                return
            self._seen_count += 1
            used_backend = backend or self.backend
            self._emit(
                "process_start",
                {"pid": info.pid, "name": info.name, "exe": info.exe, "backend": used_backend},
            )
            guard = self._guard
            if guard is not None:
                guard.check_process(info)
        except Exception as exc:
            self._last_error = _err(exc)

    def _emit(self, event: str, payload: dict) -> None:
        callback = self._on_event
        if callback is None:
            return
        try:
            callback(event, payload)
        except Exception:
            pass
