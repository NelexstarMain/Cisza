"""Wykrywanie, dopasowywanie i blokowanie procesow.

Kontrakt zamrozony w docs/INTERFACES.md (sekcja 1):
- MatchRule, SYSTEM_SAFE, ProcessInfo,
- list_processes(), match_process(), is_system_safe(), compute_signature(),
- ProcessGuard (start/stop/update/stats, zdarzenia "blocked_app").

Zasady bezpieczenstwa:
- zadna operacja systemowa nie rzuca wyjatku na zewnatrz (wszystko trafia do
  raportu {"ok","applied","warnings","errors"} albo do licznikow stats()),
- w trybie dry_run nic nie jest zmieniane w systemie (zadne suspend/kill),
- procesy krytyczne (SYSTEM_SAFE) i wlasne procesy Ciszy nigdy nie sa ruszane.

Kod jest czysto ASCII (konsola bywa cp1250), bez zaleznosci od PyQt.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Deque, Iterable, Optional, Sequence

try:  # psutil jest wymagany w runtime, ale import nie moze wywalic modulu
    import psutil
except Exception:  # pragma: no cover - brak psutil = tryb pusty, bez wyjatkow
    psutil = None  # type: ignore[assignment]

from .. import config

__all__ = [
    "RULE_KINDS",
    "ACTIONS",
    "PARENT_DEPTH",
    "EXTRA_SAFE_PROCESSES",
    "SELF_PROCESS_NAMES",
    "SYSTEM_SAFE",
    "MatchRule",
    "ProcessInfo",
    "list_processes",
    "match_process",
    "is_system_safe",
    "is_own_process",
    "compute_signature",
    "coerce_rules",
    "coerce_parents",
    "ProcessGuard",
]

RULE_KINDS: tuple[str, ...] = ("name", "path", "signature")
ACTIONS: tuple[str, ...] = ("suspend", "kill")

# Ile poziomow w gore idziemy szukajac dozwolonego przodka (1 = rodzic).
PARENT_DEPTH = 5

# Budzet rozwiazywania przodkow w jednej rundzie (gdy brak zbiorczego ppid_map).
_PARENT_LOOKUP_BUDGET = 50
_PARENT_LOOKUP_BUDGET_SECONDS = 2.0
# Twardy limit czasu jednej iteracji guardu - wolny psutil nie moze zatrzymac petli.
_TICK_HARD_LIMIT = 5.0

_RECENT_LIMIT = 50
_SIGNATURE_CHUNK = 1024 * 1024

# Procesy dokladane do listy z focuslock/config.py (system + wlasne procesy Ciszy).
EXTRA_SAFE_PROCESSES: tuple[str, ...] = (
    # pulpit logowania, powloka startowa
    "logonui.exe",
    "userinit.exe",
    "sihost.exe",
    "shellexperiencehost.exe",
    # Windows Update / instalator / licencje
    "trustedinstaller.exe",
    "tiworker.exe",
    "usoclient.exe",
    "mousocoreworker.exe",
    "sppsvc.exe",
    "wuauclt.exe",
    # bezpieczenstwo i sterowniki
    "smartscreen.exe",
    "lsaiso.exe",
    "wudfhost.exe",
    "dashost.exe",
    "wsmprovhost.exe",
    "wmiregistrationservice.exe",
    # wlasne procesy Ciszy (poza lista z config.py)
    "cisza-gui.exe",
    "cisza-helper.exe",
    "cisza-watchdog.exe",
    "py.exe",
    "python3.exe",
)

# Dodatkowe nazwy wlasnych procesow (rozpoznawane po nazwie, bez psutil).
SELF_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "cisza.exe",
        "cisza-gui.exe",
        "cisza-helper.exe",
        "cisza-watchdog.exe",
        "python.exe",
        "pythonw.exe",
        "python3.exe",
        "py.exe",
    }
)


def _norm_name(name: Any) -> str:
    """Nazwa procesu: bez bialych znakow, malymi literami."""
    return str(name or "").strip().lower()


def _norm_path(path: Any) -> str:
    """Sciezka do porownan: ukosniki jak w Windows (wzorzec i wartosc tak samo)."""
    return str(path or "").replace("/", "\\")


def _report() -> dict:
    """Pusty raport operacji wg kontraktu."""
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


def _err(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


SYSTEM_SAFE: frozenset[str] = frozenset(
    _norm_name(name)
    for name in tuple(config.SYSTEM_SAFE_PROCESSES) + EXTRA_SAFE_PROCESSES
) - {""}


# --------------------------------------------------------------------------- reguly


@lru_cache(maxsize=1024)
def _pattern_regex(pattern: str) -> "re.Pattern[str]":
    """Wzorzec z gwiazdkami ('*' i '?') zamieniony na wyrazenie regularne.

    Tylko '*' oraz '?' sa znakami specjalnymi - nawiasy i inne znaki sa
    traktowane doslownie (sciezki Windows moga zawierac '[').
    """
    parts: list[str] = []
    for char in pattern:
        if char == "*":
            parts.append(".*")
        elif char == "?":
            parts.append(".")
        else:
            parts.append(re.escape(char))
    return re.compile("".join(parts) + r"\Z")


def _wildcard_match(value: str, pattern: str) -> bool:
    """Dopasowanie bez rozrozniania wielkosci liter, obsluga '*' i '?'."""
    if not pattern:
        return False
    try:
        return bool(_pattern_regex(pattern.lower()).match(str(value).lower()))
    except Exception:
        return False


@dataclass(frozen=True)
class MatchRule:
    """Pojedyncza regula dopasowania procesu (name | path | signature)."""

    kind: str
    value: str
    label: str = ""

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip().lower()
        if kind not in RULE_KINDS:
            raise ValueError(f"Nieznany rodzaj reguly: {self.kind!r} (dozwolone: name, path, signature)")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", str(self.value or "").strip())
        object.__setattr__(self, "label", str(self.label or ""))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict) -> "MatchRule":
        if not isinstance(data, dict):
            data = {}
        return cls(
            kind=data.get("kind", "name"),
            value=data.get("value", ""),
            label=data.get("label", ""),
        )

    def describe(self) -> str:
        """Krotki opis reguly do eventow i logow, np. 'name:chrome.exe'."""
        return f"{self.kind}:{self.value}" if self.value else self.kind


def coerce_rules(rules: Any) -> list[MatchRule]:
    """Zamienia dowolne wejscie (MatchRule/dict/str) na liste regul.

    Pomaga, gdy ustawienia przyszly z JSON-a albo z listy nazw procesow.
    Bledne elementy sa po cichu pomijane - to nie jest operacja krytyczna.
    """
    if rules is None:
        return []
    if isinstance(rules, (MatchRule, dict, str)):
        rules = [rules]
    result: list[MatchRule] = []
    try:
        iterator: Iterable[Any] = rules
    except TypeError:
        return result
    for item in iterator:
        try:
            if isinstance(item, MatchRule):
                result.append(item)
            elif isinstance(item, dict):
                result.append(MatchRule.from_dict(item))
            elif isinstance(item, str) and item.strip():
                result.append(MatchRule("name", item))
        except Exception:
            continue
    return result


def coerce_parents(patterns: Any) -> list[str]:
    """Wzorce nazw przodkow (te same wildcardy co MatchRule).

    Akceptuje pojedynczy str/dict/MatchRule albo liste takich elementow.
    Bledne i puste elementy sa pomijane - to nie jest operacja krytyczna.
    """
    if patterns is None:
        return []
    if isinstance(patterns, (MatchRule, dict, str)):
        patterns = [patterns]
    result: list[str] = []
    try:
        iterator: Iterable[Any] = iter(patterns)
    except TypeError:
        return result
    for item in iterator:
        try:
            if isinstance(item, MatchRule):
                value = item.value
            elif isinstance(item, dict):
                value = item.get("value") or item.get("name") or ""
            elif isinstance(item, str):
                value = item
            else:
                continue
            value = str(value or "").strip()
            if value:
                result.append(value)
        except Exception:
            continue
    return result


def _match_any_name(name: str, patterns: Sequence[str]) -> bool:
    """Czy nazwa pasuje do ktoregokolwiek wzorca (wildcardy, bez wielkosci liter)."""
    for pattern in patterns:
        if _wildcard_match(name, pattern):
            return True
    return False


# ---------------------------------------------------------------------------- dane


@dataclass
class ProcessInfo:
    """Migawka procesu (kontrakt: pid, name, exe, create_time)."""

    pid: int
    name: str  # male litery, np. "chrome.exe"
    exe: str  # pelna sciezka lub ""
    create_time: float

    def __post_init__(self) -> None:
        try:
            self.pid = int(self.pid)
        except Exception:
            self.pid = 0
        self.name = _norm_name(self.name)
        self.exe = str(self.exe or "")
        try:
            self.create_time = float(self.create_time or 0.0)
        except Exception:
            self.create_time = 0.0


def _scan_processes() -> list[ProcessInfo]:
    """Jedno przejscie psutil po procesach (bez `ppid`!).

    UWAGA: nie wolno prosic o "ppid" w `process_iter(attrs)`. psutil liczy
    `ppid` przez `ppid_map()`, ktore enumeruje wszystkie procesy, a `as_dict`
    wola to dla KAZDEGO procesu - to O(n^2) i realne zawieszenie watku guardu.
    Przodkow rozwiazujemy osobno: patrz `_batch_ppid_map()`.
    """
    infos: list[ProcessInfo] = []
    if psutil is None:
        return infos
    try:
        iterator = psutil.process_iter(["pid", "name", "exe", "create_time"])
    except Exception:
        return infos
    try:
        for proc in iterator:
            try:
                data = proc.info or {}
                pid = int(data.get("pid") or 0)
                if pid <= 0:
                    continue
                infos.append(
                    ProcessInfo(
                        pid=pid,
                        name=data.get("name") or "",
                        exe=data.get("exe") or "",
                        create_time=data.get("create_time") or 0.0,
                    )
                )
            except Exception:
                continue
    except Exception:
        pass
    return infos


def list_processes() -> list[ProcessInfo]:
    """Lista procesow systemu; przy jakimkolwiek bledzie zwraca to, co udalo sie zebrac."""
    return _scan_processes()


def _batch_ppid_map() -> dict[int, int]:
    """Zbiorcza mapa pid -> ppid (jedno wywolanie psutil na runde).

    Uzywamy wewnetrznego `ppid_map()` platformy, bo `Process.ppid()` buduje
    cala mape przy kazdym wywolaniu, a `process_iter(["ppid"])` wola je per
    proces (patrz `_scan_processes`). Gdy psutil nie ma takiej funkcji,
    zwracamy pusty slownik - guard przejdzie na tryb leniwy z budzetem.
    """
    if psutil is None:
        return {}
    modules = [getattr(psutil, "_psplatform", None)]
    modules += [getattr(psutil, name, None) for name in ("_pswindows", "_pslinux", "_psosx", "_psbsd")]
    for module in modules:
        func = getattr(module, "ppid_map", None)
        if not callable(func):
            continue
        try:
            data = func()
            return {int(pid): int(ppid or 0) for pid, ppid in dict(data).items()}
        except Exception:
            continue
    return {}


# ---------------------------------------------------------------------- dopasowanie


def match_process(info: ProcessInfo, rules: Sequence[MatchRule]) -> Optional[MatchRule]:
    """Pierwsza regula pasujaca do procesu (kolejnosc ma znaczenie) albo None."""
    if info is None or not rules:
        return None
    name = _norm_name(info.name)
    exe = str(info.exe or "")
    signature: Optional[str] = None
    for raw_rule in rules:
        try:
            if isinstance(raw_rule, dict):
                rule = MatchRule.from_dict(raw_rule)
            elif isinstance(raw_rule, str):
                rule = MatchRule("name", raw_rule)
            else:
                rule = raw_rule
            if rule.kind == "name":
                if _wildcard_match(name, rule.value):
                    return rule
            elif rule.kind == "path":
                if exe and _wildcard_match(_norm_path(exe), _norm_path(rule.value)):
                    return rule
            elif rule.kind == "signature":
                if not rule.value:
                    continue
                if signature is None:
                    signature = compute_signature(exe) if exe else ""
                if signature and signature == rule.value:
                    return rule
        except Exception:
            continue
    return None


def is_system_safe(name: str) -> bool:
    """Czy proces jest na liscie nienaruszalnych (SYSTEM_SAFE)?"""
    value = _norm_name(name)
    if not value:
        return False
    if value in SYSTEM_SAFE:
        return True
    base = value.replace("/", "\\").rsplit("\\", 1)[-1]
    return bool(base) and base in SYSTEM_SAFE


def is_own_process(info: ProcessInfo) -> bool:
    """Czy to proces samej Ciszy (albo proces biezacy/rodzic)?

    Rozpoznajemy po PID-zie oraz po nazwie: "cisza*" (GUI, helper, watchdog)
    i "python*" (Cisza dziala jako pythonw -m focuslock). Nie zgadujemy po
    sciezce, bo katalog uzytkownika moze przypadkiem nazywac sie "cisza".
    """
    try:
        if int(info.pid) in (os.getpid(), os.getppid()):
            return True
    except Exception:
        pass
    name = _norm_name(getattr(info, "name", ""))
    if not name:
        return False
    if name in SELF_PROCESS_NAMES:
        return True
    return name.startswith("cisza") or name.startswith("python")


@lru_cache(maxsize=256)
def _signature_cached(path: str, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_SIGNATURE_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return f"sha1:{digest.hexdigest()}:{size}"


def compute_signature(path: str) -> str:
    """Sygnatura pliku w formacie 'sha1:<hex>:<rozmiar>'; '' przy bledzie."""
    try:
        target = os.path.abspath(os.fspath(path))
    except Exception:
        return ""
    try:
        if not target or not os.path.isfile(target):
            return ""
        stat = os.stat(target)
        return _signature_cached(target.lower(), int(stat.st_size), int(getattr(stat, "st_mtime_ns", 0)))
    except Exception:
        return ""


# -------------------------------------------------------------------------- guard


class ProcessGuard:
    """Watcher procesow: usypia/ubija wszystko poza lista dozwolonych.

    - `allow`  : reguly procesow, ktorych nie ruszamy,
    - `allow_parents`: wzorce nazw przodkow - proces potomny takiego przodka
                 (do PARENT_DEPTH poziomow w gore) tez jest przepuszczany,
    - `block`  : lista ZAWSZE blokowanych - pasujacy proces jest ruszany
                 natychmiast (bez karencji) i wygrywa z `allow`; proces spoza
                 `allow`/`allow_parents`/`block` tez jest blokowany, ale po `grace`,
    - `action` : "suspend" (psutil.suspend) albo "kill",
    - `dry_run`: nic nie jest wykonywane, liczniki pokazuja wykrycia.
    """

    def __init__(
        self,
        allow: Sequence[MatchRule],
        block: Sequence[MatchRule] = (),
        action: str = "suspend",
        interval: float = 0.75,
        dry_run: bool = False,
        on_event: Optional[Callable[[str, dict], None]] = None,
        grace: float = 1.0,
        allow_parents: Sequence[str] = (),
    ) -> None:
        self._lock = threading.RLock()
        self._allow: list[MatchRule] = coerce_rules(allow)
        self._block: list[MatchRule] = coerce_rules(block)
        self._allow_parents: list[str] = coerce_parents(allow_parents)
        self._action = str(action).lower() if str(action).lower() in ACTIONS else "suspend"
        try:
            self.interval = max(0.05, float(interval))
        except Exception:
            self.interval = 0.75
        try:
            self.grace = max(0.0, float(grace))
        except Exception:
            self.grace = 1.0
        self.dry_run = bool(dry_run)
        self._on_event = on_event if callable(on_event) else None

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: dict[int, float] = {}
        self._acted: set[int] = set()
        self._suspended: set[int] = set()
        # Cache przodkow: ppid procesu nie zmienia sie w czasie jego zycia,
        # wiec miedzy iteracjami rozwiazujemy tylko NOWE pid-y.
        self._ppid_cache: dict[int, int] = {}
        self._name_cache: dict[int, str] = {}
        self._ppid_batch: Optional[dict[int, int]] = None
        self._ppid_batch_failed = False
        self._ppid_budget = _PARENT_LOOKUP_BUDGET
        self._counts: dict[str, int] = {
            "blocked": 0,
            "suspended": 0,
            "killed": 0,
            "skipped": 0,
            "allowed": 0,
            "allowed_by_parent": 0,
            "deferred": 0,
            "overruns": 0,
            "errors": 0,
        }
        self._recent: Deque[dict] = deque(maxlen=_RECENT_LIMIT)
        self._last_error = ""

    # ------------------------------------------------------------------- publiczne

    def start(self) -> dict:
        """Uruchamia watek-daemon; nigdy nie rzuca wyjatku."""
        report = _report()
        try:
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    report["warnings"].append("Guard juz dziala")
                    return report
                # Kazdy bieg ma wlasny Event: stary watek nie moze "ozyc" po stop().
                stop_event = threading.Event()
                self._stop = stop_event
                self._thread = threading.Thread(
                    target=self._run, args=(stop_event,), name="cisza-process-guard", daemon=True
                )
                self._thread.start()
                report["applied"].append("processes:start")
                if self.dry_run:
                    report["applied"].append("dry-run")
        except Exception as exc:
            report["ok"] = False
            report["errors"].append(_err(exc))
        return report

    def stop(self) -> dict:
        """Zatrzymuje watek i wznawia procesy uspione przez ten guard."""
        report = _report()
        thread: Optional[threading.Thread] = None
        try:
            with self._lock:
                thread = self._thread
                self._stop.set()
            if thread is None:
                report["warnings"].append("Guard nie byl uruchomiony")
            else:
                if thread.is_alive():
                    thread.join(timeout=max(1.0, self.interval * 4))
                if thread.is_alive():
                    report["warnings"].append(
                        "Watek guardu konczy jeszcze biezna iteracje (dane psutil wolne)"
                    )
                report["applied"].append("processes:stop")
            with self._lock:
                self._thread = None
            resumed = self._resume_suspended()
            if resumed:
                report["applied"].append(f"processes:resume:{resumed}")
            if self._last_error:
                report["warnings"].append(self._last_error)
        except Exception as exc:
            report["ok"] = False
            report["errors"].append(_err(exc))
        return report

    def update(
        self,
        allow: Optional[Sequence[MatchRule]] = None,
        block: Optional[Sequence[MatchRule]] = None,
        action: Optional[str] = None,
        allow_parents: Optional[Sequence[str]] = None,
    ) -> dict:
        """Zmienia reguly/akcje w locie; nigdy nie rzuca wyjatku."""
        report = _report()
        try:
            with self._lock:
                if allow is not None:
                    self._allow = coerce_rules(allow)
                    report["applied"].append(f"allow:{len(self._allow)}")
                if block is not None:
                    self._block = coerce_rules(block)
                    report["applied"].append(f"block:{len(self._block)}")
                if allow_parents is not None:
                    self._allow_parents = coerce_parents(allow_parents)
                    report["applied"].append(f"allow_parents:{len(self._allow_parents)}")
                if action is not None:
                    value = str(action).lower()
                    if value in ACTIONS:
                        self._action = value
                        report["applied"].append(f"action:{value}")
                    else:
                        report["ok"] = False
                        report["errors"].append(
                            f"Nieznana akcja: {action!r} (dozwolone: suspend, kill)"
                        )
        except Exception as exc:
            report["ok"] = False
            report["errors"].append(_err(exc))
        return report

    def stats(self) -> dict:
        """Liczniki + ostatnie blokady; nigdy nie rzuca wyjatku."""
        try:
            with self._lock:
                now = time.time()
                pending = sum(
                    1
                    for pid, first in self._seen.items()
                    if pid not in self._acted and (now - first) < self.grace
                )
                return {
                    "ok": True,
                    "blocked": int(self._counts["blocked"]),
                    "suspended": int(self._counts["suspended"]),
                    "killed": int(self._counts["killed"]),
                    "skipped": int(self._counts["skipped"]),
                    "recent": [dict(item) for item in self._recent],
                    # pola dodatkowe (nie koliduja z kontraktem):
                    "running": bool(self._thread is not None and self._thread.is_alive()),
                    "dry_run": bool(self.dry_run),
                    "action": self._action,
                    "allowed": int(self._counts["allowed"]),
                    "allowed_by_parent": int(self._counts["allowed_by_parent"]),
                    "deferred": int(self._counts["deferred"]),
                    "overruns": int(self._counts["overruns"]),
                    "errors": int(self._counts["errors"]),
                    "last_error": self._last_error,
                    "pending": pending,
                }
        except Exception:
            return {
                "ok": False,
                "blocked": 0,
                "suspended": 0,
                "killed": 0,
                "skipped": 0,
                "recent": [],
                "running": False,
                "dry_run": bool(self.dry_run),
                "action": self._action,
                "allowed": 0,
                "allowed_by_parent": 0,
                "deferred": 0,
                "overruns": 0,
                "errors": 0,
                "last_error": self._last_error,
                "pending": 0,
            }

    def check_process(self, info: ProcessInfo, now: Optional[float] = None) -> dict:
        """Sprawdza jeden proces poza petla (uzywane przez LaunchWatcher).

        Zwraca raport {"ok","applied","warnings","errors"}; nie rzuca wyjatku.
        """
        report = _report()
        try:
            if info is None:
                report["warnings"].append("Brak danych procesu")
                return report
            moment = time.time() if now is None else float(now)
            with self._lock:
                allow = tuple(self._allow)
                block = tuple(self._block)
                action = self._action
                dry = self.dry_run
                allow_parents = tuple(self._allow_parents)
            own_pids = self._own_tree_pids()
            deadline: Optional[float] = None
            if allow_parents:
                self._begin_parent_round([info])
                deadline = time.monotonic() + _PARENT_LOOKUP_BUDGET_SECONDS
            status = self._consider(
                info, moment, own_pids, allow, block, action, dry, allow_parents, deadline
            )
            if status:
                report["applied"].append(status)
        except Exception as exc:
            report["ok"] = False
            report["errors"].append(_err(exc))
        return report

    # -------------------------------------------------------------------- wewnetrzne

    def _run(self, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                try:
                    self._tick(time.time(), stop_event)
                except Exception as exc:
                    self._note_error(exc)
                stop_event.wait(self.interval)
        except Exception as exc:  # pragma: no cover - zabezpieczenie watku
            self._note_error(exc)
        finally:
            stop_event.set()

    def _tick(self, now: float, stop_event: Optional[threading.Event] = None) -> None:
        stop_event = stop_event if stop_event is not None else self._stop
        started = time.monotonic()
        with self._lock:
            allow = tuple(self._allow)
            block = tuple(self._block)
            action = self._action
            dry = self.dry_run
            allow_parents = tuple(self._allow_parents)
        processes = _scan_processes()
        alive = {info.pid for info in processes}
        deadline: Optional[float] = None
        if allow_parents:
            self._begin_parent_round(processes)
            # Budzet liczymy od poczatku fazy przodkow, zeby wolne skanowanie
            # psutil (rozgrzewka) nie zjadalo limitu i nie odkladalo wszystkiego.
            deadline = time.monotonic() + _PARENT_LOOKUP_BUDGET_SECONDS
        with self._lock:
            for pid in list(self._seen):
                if pid not in alive:
                    self._seen.pop(pid, None)
                    self._acted.discard(pid)
                    self._suspended.discard(pid)
            if allow_parents:
                for pid in list(self._ppid_cache):
                    if pid not in alive:
                        self._ppid_cache.pop(pid, None)
                for pid in list(self._name_cache):
                    if pid not in alive:
                        self._name_cache.pop(pid, None)
        own_pids = self._own_tree_pids()
        for info in processes:
            if stop_event.is_set():
                break  # stop() nie czeka na cala liste procesow
            if time.monotonic() - started > _TICK_HARD_LIMIT:
                self._note_overrun(
                    f"Iteracja przerwana po {_TICK_HARD_LIMIT:.0f} s - wolny psutil, "
                    "reszta procesow w nastepnym cyklu"
                )
                break
            self._consider(
                info, now, own_pids, allow, block, action, dry, allow_parents, deadline
            )

    def _consider(
        self,
        info: ProcessInfo,
        now: float,
        own_pids: set,
        allow: Sequence[MatchRule],
        block: Sequence[MatchRule],
        action: str,
        dry: bool,
        allow_parents: Sequence[str] = (),
        deadline: Optional[float] = None,
    ) -> str:
        """Decyzja dla jednego procesu; zwraca krotki status (do raportu)."""
        name = _norm_name(info.name)
        if not name or info.pid in own_pids or is_own_process(info) or is_system_safe(name):
            self._bump("skipped")
            return "skipped:system-safe" if is_system_safe(name) else "skipped:own"
        immediate = False
        try:
            # 1) "block" to lista ZAWSZE blokowanych: dziala natychmiast i wygrywa
            #    zarowno z allow, jak i z dziedziczeniem po przodku.
            block_rule = match_process(info, block) if block else None
            if block_rule is not None:
                immediate = True
                rule_text = block_rule.describe()
            else:
                # 2) jawna lista dozwolonych i dziedziczenie po przodku.
                if match_process(info, allow) is not None:
                    self._bump("allowed")
                    return "skipped:allowed"
                if allow_parents:
                    parent = self._parent_match(info.pid, allow_parents, deadline)
                    if parent is None:
                        # Budzet/wolny psutil - nie dzialamy w ciemno, sprobujemy pozniej.
                        self._bump("deferred")
                        return "deferred:parent-budget"
                    if parent:
                        # Dziedziczenie po rodzicu to nie blokada - brak eventu blocked_app.
                        self._bump("allowed_by_parent")
                        self._emit(
                            "allowed_child",
                            {"pid": info.pid, "name": info.name, "parent": parent},
                        )
                        return "skipped:allowed-parent"
                # 3) proces spoza wszystkich list - akcja po karencji.
                rule_text = "*"
        except Exception as exc:
            self._note_error(exc)
            return "error"

        with self._lock:
            if info.pid in self._acted:
                return ""
            first = self._seen.setdefault(info.pid, now)
            if not immediate and (now - first) < self.grace:
                return "pending"
            self._acted.add(info.pid)

        result = self._apply(info, action, dry)
        if result == "skip":
            return "skip"
        with self._lock:
            if result != "error":
                self._counts["blocked"] += 1
            if result == "suspended":
                self._counts["suspended"] += 1
                self._suspended.add(info.pid)
            elif result == "killed":
                self._counts["killed"] += 1
            elif result == "error":
                self._counts["errors"] += 1
            self._recent.append(
                {
                    "name": info.name,
                    "action": action,
                    "ts": now,
                    "pid": info.pid,
                    "rule": rule_text,
                    "dry_run": bool(dry),
                }
            )
        if result != "error":
            self._emit(
                "blocked_app",
                {
                    "pid": info.pid,
                    "name": info.name,
                    "action": action,
                    "rule": rule_text,
                    "dry_run": bool(dry),
                },
            )
        return f"blocked:{info.name}:{action}"

    def _apply(self, info: ProcessInfo, action: str, dry: bool) -> str:
        """Wykonuje akcje na procesie; zwraca suspended|killed|dry-run|skip|error."""
        if dry:
            return "dry-run"
        if psutil is None:
            self._last_error = "Brak psutil - akcje na procesach niedostepne"
            return "error"
        try:
            proc = psutil.Process(info.pid)
            if _norm_name(proc.name()) != _norm_name(info.name):
                # PID zostal ponownie uzyty przez inny proces - nie ruszamy.
                return "skip"
            if action == "kill":
                proc.kill()
                return "killed"
            proc.suspend()
            return "suspended"
        except psutil.NoSuchProcess:
            return "skip"
        except psutil.AccessDenied as exc:
            self._last_error = f"Brak uprawnien dla pid={info.pid}: {exc}"
            return "error"
        except Exception as exc:
            self._last_error = _err(exc)
            return "error"

    def _resume_suspended(self) -> int:
        """Wznawia procesy uspione przez ten guard (best-effort)."""
        if self.dry_run or psutil is None:
            with self._lock:
                self._suspended.clear()
            return 0
        with self._lock:
            pids = sorted(self._suspended)
            self._suspended.clear()
        resumed = 0
        for pid in pids:
            try:
                psutil.Process(pid).resume()
                resumed += 1
            except Exception:
                continue
        return resumed

    def _begin_parent_round(self, processes: Sequence[ProcessInfo]) -> None:
        """Przygotowuje runde rozwiazywania przodkow (jedna na iteracje/zdarzenie)."""
        with self._lock:
            self._ppid_batch = None
            self._ppid_batch_failed = False
            self._ppid_budget = _PARENT_LOOKUP_BUDGET
            for info in processes:
                name = _norm_name(getattr(info, "name", ""))
                if name:
                    self._name_cache[info.pid] = name

    def _ppid_for(self, pid: int) -> Optional[int]:
        """ppid z cache; przy braku - zbiorcza mapa (raz na runde) albo tryb leniwy.

        None = "nie ustalono" (wyczerpany budzet) - wtedy guard nie dziala w ciemno.
        """
        cached = self._ppid_cache.get(pid)
        if cached is not None:
            return cached
        if self._ppid_batch is None and not self._ppid_batch_failed:
            batch = _batch_ppid_map()
            if batch:
                self._ppid_batch = batch
            else:
                self._ppid_batch_failed = True
                self._note_overrun(
                    "psutil bez zbiorczego ppid_map - przodkowie rozwiazywani leniwie z budzetem"
                )
        if self._ppid_batch is not None:
            ppid = int(self._ppid_batch.get(pid, 0))
            self._ppid_cache[pid] = ppid
            return ppid
        with self._lock:
            if self._ppid_budget <= 0:
                return None
            self._ppid_budget -= 1
        ppid = self._ppid_of(pid)
        self._ppid_cache[pid] = ppid
        return ppid

    def _name_for(self, pid: int) -> str:
        """Nazwa procesu z cache (male litery); '' gdy niedostepna."""
        cached = self._name_cache.get(pid)
        if cached is not None:
            return cached
        name = self._name_of(pid)
        self._name_cache[pid] = name
        return name

    def _parent_match(
        self, pid: int, patterns: Sequence[str], deadline: Optional[float] = None
    ) -> Optional[str]:
        """Nazwa przodka pasujacego do `patterns` (max PARENT_DEPTH poziomow).

        Zwraca '' gdy zaden przodek nie pasuje, a None gdy nie udalo sie ustalic
        (wyczerpany budzet czasu/liczby zapytan) - wtedy NIE wolno dzialac.
        """
        if not patterns:
            return ""
        try:
            current = int(pid)
        except Exception:
            return ""
        seen = {current}
        for _ in range(PARENT_DEPTH):
            if deadline is not None and time.monotonic() > deadline:
                return None
            ppid = self._ppid_for(current)
            if ppid is None:
                return None
            if ppid <= 0 or ppid == current or ppid in seen:
                return ""
            seen.add(ppid)
            parent_name = self._name_for(ppid)
            if parent_name and _match_any_name(parent_name, patterns):
                return parent_name
            current = ppid
        return ""

    @staticmethod
    def _ppid_of(pid: int) -> int:
        """ppid pojedynczego procesu; 0 gdy niedostepny (martwy, brak uprawnien).

        UWAGA: kazde wywolanie buduje wewnetrznie cala mape ppid, dlatego
        uzywamy tego tylko w trybie awaryjnym i pod twardym budzetem.
        """
        if psutil is None:
            return 0
        try:
            return int(psutil.Process(int(pid)).ppid())
        except Exception:
            return 0

    @staticmethod
    def _name_of(pid: int) -> str:
        """Nazwa pojedynczego procesu (male litery); '' gdy niedostepna."""
        if psutil is None:
            return ""
        try:
            return _norm_name(psutil.Process(int(pid)).name())
        except Exception:
            return ""

    def _own_tree_pids(self) -> set:
        """PID-y wlasne + caly poddrzewo procesu biezacego (zeby go nie tknac)."""
        pids = {os.getpid(), os.getppid()}
        if psutil is not None:
            try:
                me = psutil.Process(os.getpid())
                pids.add(me.ppid())
                pids.update(child.pid for child in me.children(recursive=True))
            except Exception:
                pass
        return pids

    def _bump(self, key: str, amount: int = 1) -> None:
        try:
            with self._lock:
                self._counts[key] = self._counts.get(key, 0) + amount
        except Exception:
            pass

    def _note_error(self, exc: BaseException) -> None:
        try:
            with self._lock:
                self._counts["errors"] = self._counts.get("errors", 0) + 1
                self._last_error = _err(exc)
        except Exception:
            pass

    def _note_overrun(self, message: str) -> None:
        """Ostrzezenie o przekroczonym budzecie (widoczne w stats()['last_error'])."""
        try:
            with self._lock:
                self._counts["overruns"] = self._counts.get("overruns", 0) + 1
                self._last_error = message
        except Exception:
            pass

    def _emit(self, event: str, payload: dict) -> None:
        callback = self._on_event
        if callback is None:
            return
        try:
            callback(event, payload)
        except Exception:
            # Blad odbiorcy nie moze zabic watku guardu.
            pass
