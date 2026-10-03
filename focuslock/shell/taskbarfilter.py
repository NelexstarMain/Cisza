"""Pasek zadan widoczny w sesji, ale tylko z przyciskami dozwolonych aplikacji.

Kontrakt: docs/INTERFACES.md sekcja 9a.

Po co to jest: tryby `hard`/`hardcore` ukrywaly pasek zadan, a skroty (Alt+Tab,
Win) sa zablokowane - dozwolona aplikacja (np. Edge) byla nie do klikniecia.
Tutaj pasek zadan zostaje na ekranie, ale przyciski okien procesow spoza
allowlisty znikaja (`ITaskbarList::DeleteTab`), a po sesji wracaja (`AddTab`).

Zasady bezpieczenstwa:
- zadna operacja nie rzuca wyjatku na zewnatrz - raport {"ok","applied",
  "warnings","errors"},
- `dry_run=True` nic nie zmienia w systemie (zadnego DeleteTab/AddTab/ShowWindow),
- procesy SYSTEM_SAFE, wlasne procesy Ciszy i same okna paska zadan sa nietkniete,
- sposob ukrycia kazdego okna (DeleteTab albo awaryjne ShowWindow) i jego HWND
  trafiaja do pliku stanu, wiec `restore()` dziala takze z innego procesu
  (watchdog, tools/restore.py) i po restarcie,
- pusta allowlista nie jest filtrowana (inaczej zniknely by wszystkie przyciski).

Kod jest czysto ASCII i nie zalezy od PyQt ani od psutil (psutil tylko przyspiesza
odczyt nazw procesow - jest fallback na WinAPI).
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Iterable, Optional, Sequence

from .. import paths

__all__ = [
    "IS_WINDOWS",
    "MIN_INTERVAL",
    "STATE_NAME",
    "TaskbarList",
    "allowed",
    "apply",
    "filtered_windows",
    "is_active",
    "list_windows",
    "plan",
    "reset_throttle",
    "restore",
    "state_path",
    "status",
]

IS_WINDOWS = sys.platform == "win32"

STATE_NAME = "taskbar_filter.json"

# Minimalny odstep miedzy kolejnymi filtrowaniami (kontroler wola to co tick).
MIN_INTERVAL = 1.0

# Okna, ktorych nie wolno ruszac: pasek zadan, pulpit, powloka WinRT, przelacznik zadan.
SKIP_CLASSES = frozenset(
    {
        "shell_traywnd",
        "shell_secondarytraywnd",
        "progman",
        "workerw",
        "shell_inputswitchtoplevelwindow",
        "tasklistthumbnailwnd",
        "multitaskingviewframe",
        "foregroundstaging",
        "windows.ui.core.corewindow",
        "applicationmanager_immersiveshellwindow",
        "xamlexplorerhostislandwindow",
        "toplevelwindowforoverflowsubmenu",
    }
)

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080

SW_HIDE = 0
SW_SHOW = 5
SW_SHOWMINIMIZED = 2
SW_SHOWNORMAL = 1

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

S_OK = 0
S_FALSE = 1
CTX_INPROC_SERVER = 1

CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
IID_ITASKBAR_LIST = "{56FDF342-FD6D-11d0-958A-006097C9A090}"

_LOCK = threading.Lock()
_last_apply_at = 0.0

try:  # WINFUNCTYPE istnieje tylko na Windows
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
except AttributeError:  # pragma: no cover - poza Windows
    WNDENUMPROC = None  # type: ignore[assignment]


def _report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


def _err(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


# ------------------------------------------------------------------------ stan
def state_path():
    return paths.backup_dir() / STATE_NAME


def _load_state() -> dict:
    path = state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(payload: dict) -> None:
    path = state_path()
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _clear_state() -> None:
    try:
        path = state_path()
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _state_hwnds() -> dict[int, dict]:
    raw = _load_state().get("hwnds") or {}
    result: dict[int, dict] = {}
    for key, value in dict(raw).items():
        try:
            result[int(key)] = dict(value or {})
        except (TypeError, ValueError):
            continue
    return result


def filtered_windows() -> list[int]:
    """HWND-y okien, ktorym zabralismy przycisk paska zadan (z pliku stanu)."""
    return sorted(_state_hwnds())


def is_active() -> bool:
    return bool(_state_hwnds())


def status() -> dict:
    state = _load_state()
    hwnds = _state_hwnds()
    return {
        "ok": True,
        "active": bool(hwnds),
        "filtered": len(hwnds),
        "apps": list(state.get("apps") or []),
        "updated_at": float(state.get("updated_at") or 0.0),
        "windows": hwnds,
    }


def reset_throttle() -> None:
    global _last_apply_at
    with _LOCK:
        _last_apply_at = 0.0


# ------------------------------------------------------------------- WinAPI
_user32_lib = None
_kernel32_lib = None


def _user32():
    global _user32_lib
    if _user32_lib is not None:
        return _user32_lib
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.EnumWindows.restype = wintypes.BOOL
    lib.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    lib.EnumChildWindows.restype = wintypes.BOOL
    lib.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
    lib.IsWindowVisible.restype = wintypes.BOOL
    lib.IsWindowVisible.argtypes = [wintypes.HWND]
    lib.IsWindow.restype = wintypes.BOOL
    lib.IsWindow.argtypes = [wintypes.HWND]
    lib.IsIconic.restype = wintypes.BOOL
    lib.IsIconic.argtypes = [wintypes.HWND]
    lib.GetWindow.restype = wintypes.HWND
    lib.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    lib.GetWindowThreadProcessId.restype = wintypes.DWORD
    lib.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    lib.GetWindowTextLengthW.restype = ctypes.c_int
    lib.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    lib.GetWindowTextW.restype = ctypes.c_int
    lib.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    lib.GetClassNameW.restype = ctypes.c_int
    lib.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    lib.ShowWindow.restype = wintypes.BOOL
    lib.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    # Na 32-bitowym Windows istnieje tylko GetWindowLongW (GetWindowLongPtrW to makro).
    getter = getattr(lib, "GetWindowLongPtrW", None) or getattr(lib, "GetWindowLongW")
    getter.restype = ctypes.c_ssize_t
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    lib._cisza_get_long = getter  # type: ignore[attr-defined]
    _user32_lib = lib
    return lib


def _ex_style(lib, hwnd: int) -> int:
    getter = getattr(lib, "_cisza_get_long")
    try:
        return int(getter(hwnd, GWL_EXSTYLE))
    except OSError:
        return 0


def _kernel32():
    global _kernel32_lib
    if _kernel32_lib is not None:
        return _kernel32_lib
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.OpenProcess.restype = wintypes.HANDLE
    lib.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    lib.CloseHandle.restype = wintypes.BOOL
    lib.CloseHandle.argtypes = [wintypes.HANDLE]
    lib.QueryFullProcessImageNameW.restype = wintypes.BOOL
    lib.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32_lib = lib
    return lib


def _window_text(hwnd: int) -> str:
    lib = _user32()
    length = int(lib.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 2)
    lib.GetWindowTextW(hwnd, buffer, length + 2)
    return buffer.value


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    try:
        if _user32().GetClassNameW(hwnd, buffer, 256):
            return buffer.value
    except OSError:
        pass
    return ""


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    try:
        _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    except OSError:
        return 0
    return int(pid.value)


def _pid_name(pid: int) -> str:
    """Nazwa procesu (male litery) - psutil, awaryjnie WinAPI."""
    try:
        import psutil  # lokalny import: modul dziala takze bez psutil

        return _norm(psutil.Process(int(pid)).name())
    except Exception:
        pass
    try:
        kernel = _kernel32()
        handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return _norm(os.path.basename(buffer.value))
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        pass
    return ""


def _child_pid(hwnd: int) -> int:
    """PID pierwszego okna potomnego roznego od wlasciciela (aplikacje ze Sklepu)."""
    lib = _user32()
    owner = _window_pid(hwnd)
    found = {"pid": 0}

    @WNDENUMPROC
    def _callback(child, _lparam):
        try:
            pid = _window_pid(child)
        except Exception:
            return True
        if pid and pid != owner:
            found["pid"] = pid
            return False
        return True

    try:
        lib.EnumChildWindows(hwnd, _callback, 0)
    except OSError:
        pass
    return int(found["pid"])


def _resolve_process(hwnd: int) -> tuple[int, str]:
    """PID i nazwa procesu okna; dla ApplicationFrameHost bierze proces aplikacji."""
    pid = _window_pid(hwnd)
    name = _pid_name(pid) if pid else ""
    if name == "applicationframehost.exe":
        child = _child_pid(hwnd)
        if child and child != pid:
            child_name = _pid_name(child)
            if child_name:
                return child, child_name
    return pid, name


def list_windows(*, exclude_pids: Sequence[int] = (), titles_required: bool = True) -> list[dict]:
    """Widoczne okna najwyzszego poziomu z nazwa procesu (do filtrowania paska)."""
    if not IS_WINDOWS:
        return []
    lib = _user32()
    excluded = {int(pid) for pid in exclude_pids if int(pid or 0) > 0}
    excluded.add(os.getpid())
    try:
        excluded.add(os.getppid())
    except Exception:
        pass

    windows: list[dict] = []

    @WNDENUMPROC
    def _callback(hwnd, _lparam):
        try:
            handle = int(hwnd)
            if not lib.IsWindowVisible(hwnd):
                return True
            if lib.GetWindow(hwnd, GW_OWNER):
                return True
            if _ex_style(lib, handle) & WS_EX_TOOLWINDOW:
                return True
            title = _window_text(hwnd)
            if titles_required and not title:
                return True
            class_name = _class_name(hwnd)
            if class_name.lower() in SKIP_CLASSES:
                return True
            pid, name = _resolve_process(hwnd)
            if not pid or not name or pid in excluded:
                return True
            windows.append(
                {
                    "hwnd": handle,
                    "pid": pid,
                    "name": name,
                    "title": title,
                    "class": class_name,
                    "minimized": bool(lib.IsIconic(hwnd)),
                }
            )
        except Exception:
            return True
        return True

    try:
        lib.EnumWindows(_callback, 0)
    except OSError:
        pass
    return windows


def _is_window(hwnd: int) -> bool:
    try:
        return bool(_user32().IsWindow(hwnd))
    except OSError:
        return False


def _visible(hwnd: int) -> bool:
    try:
        return bool(_user32().IsWindowVisible(hwnd))
    except OSError:
        return False


def _hide_window(hwnd: int) -> bool:
    try:
        _user32().ShowWindow(hwnd, SW_HIDE)
        return not bool(_user32().IsWindowVisible(hwnd))
    except OSError:
        return False


def _show_window(hwnd: int, minimized: bool = False) -> bool:
    try:
        _user32().ShowWindow(hwnd, SW_SHOWMINIMIZED if minimized else SW_SHOWNORMAL)
        return bool(_user32().IsWindowVisible(hwnd))
    except OSError:
        return False


# ---------------------------------------------------------------------- reguly
def _rule_values(rules: Iterable[Any]) -> list[str]:
    values: list[str] = []
    for item in rules or ():
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            values.append(str(item.get("value") or item.get("name") or ""))
        else:
            value = getattr(item, "value", None)
            if value is None:
                value = getattr(item, "name", "")
            values.append(str(value or ""))
    return [value for value in (v.strip() for v in values) if value]


def _wildcard_match(value: str, pattern: str) -> bool:
    if not pattern:
        return False
    import re

    parts: list[str] = []
    for char in pattern:
        if char == "*":
            parts.append(".*")
        elif char == "?":
            parts.append(".")
        else:
            parts.append(re.escape(char))
    try:
        return bool(re.match("".join(parts) + r"\Z", value, re.IGNORECASE))
    except Exception:
        return False


def _match_any_name(name: str, patterns: Iterable[str]) -> bool:
    value = _norm(name)
    for pattern in patterns or ():
        if _wildcard_match(value, str(pattern)):
            return True
    return False


def _is_system_safe(name: str) -> bool:
    if not name:
        return False
    try:
        from ..block import processes  # lokalny import - brak cyklu przy imporcie

        return bool(processes.is_system_safe(name))
    except Exception:
        return False


def _match_rules(window: dict, rules: Sequence[Any]) -> bool:
    """Dopasowanie okna do regul allowlisty (te same co guard procesow)."""
    if not rules:
        return False
    try:
        from ..block import processes

        coerced = processes.coerce_rules(rules)
        if not coerced:
            return False
        info = processes.ProcessInfo(
            pid=int(window.get("pid") or 0),
            name=window.get("name") or "",
            exe=window.get("exe") or "",
            create_time=0.0,
        )
        return processes.match_process(info, coerced) is not None
    except Exception:
        return _match_any_name(window.get("name") or "", _rule_values(rules))


def allowed(
    window: dict,
    *,
    rules: Sequence[Any] = (),
    apps: Sequence[str] = (),
    parents: Sequence[str] = (),
) -> bool:
    """Czy okno nalezy do dozwolonej aplikacji (reguly, lista procesow, przodkowie)."""
    name = window.get("name") or ""
    if apps and _match_any_name(name, apps):
        return True
    if parents and _match_any_name(name, parents):
        return True
    return _match_rules(window, rules)


def plan(
    windows: Sequence[dict],
    *,
    rules: Sequence[Any] = (),
    apps: Sequence[str] = (),
    parents: Sequence[str] = (),
    safe: Sequence[str] = (),
    own_pids: Iterable[int] = (),
) -> dict:
    """Czysta decyzja: ktore okna zostawic, a ktorym zabrac przycisk paska.

    Zwraca {"hide": [hwnd], "keep": [hwnd], "skipped": [{"hwnd","name","reason"}]}.
    """
    safe_names = {_norm(name) for name in safe if _norm(name)}
    own = {int(pid) for pid in own_pids or () if int(pid or 0) > 0}
    hide: list[int] = []
    keep: list[int] = []
    skipped: list[dict] = []
    for window in windows or ():
        try:
            hwnd = int(window.get("hwnd") or 0)
        except (TypeError, ValueError):
            continue
        if hwnd <= 0:
            continue
        pid = int(window.get("pid") or 0)
        name = _norm(window.get("name"))
        if pid and pid in own:
            keep.append(hwnd)
            continue
        if not name:
            skipped.append({"hwnd": hwnd, "name": "", "reason": "brak-nazwy-procesu"})
            continue
        if name in safe_names or _is_system_safe(name):
            keep.append(hwnd)
            continue
        if allowed(window, rules=rules, apps=apps, parents=parents):
            keep.append(hwnd)
            continue
        hide.append(hwnd)
    return {"hide": hide, "keep": keep, "skipped": skipped}


# ------------------------------------------------------------------ COM/ITaskbarList
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> "_GUID":
    parts = text.strip("{}").split("-")
    data4 = (ctypes.c_ubyte * 8)()
    tail = (parts[3] + parts[4]).lower()
    for index in range(8):
        data4[index] = int(tail[index * 2 : index * 2 + 2], 16)
    return _GUID(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16), data4)


class TaskbarList:
    """Cienki wrapper na COM `ITaskbarList` (AddTab/DeleteTab)."""

    # indeksy w vtable: 0..2 IUnknown, 3 HrInit, 4 AddTab, 5 DeleteTab
    _RELEASE = 2
    _HR_INIT = 3
    _ADD_TAB = 4
    _DELETE_TAB = 5

    def __init__(self) -> None:
        self._ptr = ctypes.c_void_p()
        self._ole32 = None
        self.error = ""

    def open(self) -> bool:
        if not IS_WINDOWS:
            self.error = "tylko Windows"
            return False
        try:
            self._ole32 = ctypes.WinDLL("ole32", use_last_error=True)
            self._ole32.CoInitialize.restype = ctypes.c_long
            self._ole32.CoInitialize.argtypes = [ctypes.c_void_p]
            self._ole32.CoCreateInstance.restype = ctypes.c_long
            self._ole32.CoCreateInstance.argtypes = [
                ctypes.POINTER(_GUID),
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            try:
                self._ole32.CoInitialize(None)
            except OSError:
                pass
            clsid = _guid(CLSID_TASKBAR_LIST)
            iid = _guid(IID_ITASKBAR_LIST)
            pointer = ctypes.c_void_p()
            result = self._ole32.CoCreateInstance(
                ctypes.byref(clsid), None, CTX_INPROC_SERVER, ctypes.byref(iid), ctypes.byref(pointer)
            )
            if result not in (S_OK, S_FALSE) or not pointer.value:
                self.error = "CoCreateInstance(ITaskbarList) hr=0x%08X" % (int(result) & 0xFFFFFFFF)
                return False
            self._ptr = pointer
            init = int(self._method(self._HR_INIT, [])(self._ptr))
            if init not in (S_OK, S_FALSE):
                self.error = "HrInit hr=0x%08X" % (init & 0xFFFFFFFF)
                self.close()
                return False
            return True
        except Exception as exc:  # noqa: BLE001 - COM moze rzucic wszystko
            self.error = _err(exc)
            self._ptr = ctypes.c_void_p()
            return False

    def _method(self, index: int, argtypes: list):
        vtable = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        address = vtable[index]
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        return proto(address)

    def add_tab(self, hwnd: int) -> int:
        try:
            return int(self._method(self._ADD_TAB, [wintypes.HWND])(self._ptr, wintypes.HWND(hwnd)))
        except Exception as exc:  # noqa: BLE001
            self.error = _err(exc)
            return -1

    def delete_tab(self, hwnd: int) -> int:
        try:
            return int(self._method(self._DELETE_TAB, [wintypes.HWND])(self._ptr, wintypes.HWND(hwnd)))
        except Exception as exc:  # noqa: BLE001
            self.error = _err(exc)
            return -1

    def close(self) -> None:
        if not self._ptr:
            return
        try:
            self._method(self._RELEASE, [])(self._ptr)
        except Exception:  # noqa: BLE001
            pass
        self._ptr = ctypes.c_void_p()


# --------------------------------------------------------------------- operacje
def _still_forbidden(hwnd: int, *, rules: Sequence[Any], apps: Sequence[str], parents: Sequence[str]) -> bool:
    """Czy proces okna nadal jest poza allowlista (dla okien ukrytych awaryjnie).

    Gdy nie da sie ustalic nazwy procesu, zwracamy True - lepiej zostawic okno
    w spokoju niz pokazac przycisk aplikacji, ktora ma byc zablokowana.
    """
    pid = _window_pid(hwnd)
    name = _pid_name(pid) if pid else ""
    if not name:
        return True
    window = {"hwnd": hwnd, "pid": pid, "name": name, "title": ""}
    return not allowed(window, rules=rules, apps=apps, parents=parents)


def _restore_one(api: Optional[TaskbarList], hwnd: int, meta: dict, report: dict) -> bool:
    if not _is_window(hwnd):
        return False
    method = str(meta.get("method") or "delete_tab")
    if method == "hide_window":
        return _show_window(hwnd, bool(meta.get("minimized")))
    if api is not None and api.add_tab(hwnd) in (S_OK, S_FALSE):
        return True
    report["warnings"].append("nie udalo sie przywrocic przycisku 0x%X" % hwnd)
    return False


def apply(
    rules: Sequence[Any] = (),
    *,
    apps: Sequence[str] = (),
    parents: Sequence[str] = (),
    dry_run: bool = False,
    exclude_pids: Sequence[int] = (),
    hide_fallback: bool = True,
    force: bool = True,
    interval: float = MIN_INTERVAL,
    now: Optional[float] = None,
    windows: Optional[Sequence[dict]] = None,
) -> dict:
    """Usuwa przyciski paska zadan oknom spoza allowlisty (i przywraca zbedne).

    `windows` sluzy testom (wstrzykniecie listy okien); domyslnie czyta system.
    """
    global _last_apply_at

    report = _report()
    report.update({"hidden": 0, "restored": 0, "kept": 0, "skipped": []})

    if not rules and not apps:
        report["skipped"].append("pusta-allowlista")
        report["warnings"].append("pusta allowlista - filtrowanie paska zadan pominiete")
        return report

    if not force and interval > 0:
        moment = time.monotonic() if now is None else float(now)
        with _LOCK:
            if _last_apply_at and moment - _last_apply_at < interval:
                report["skipped"].append("throttle")
                return report
            _last_apply_at = moment
    elif now is not None:
        with _LOCK:
            _last_apply_at = float(now)
    else:
        with _LOCK:
            _last_apply_at = time.monotonic()

    if not IS_WINDOWS:
        report["warnings"].append("filtrowanie paska zadan dziala tylko na Windows")
        return report

    previous = _state_hwnds()
    listed = list(windows) if windows is not None else list_windows(exclude_pids=exclude_pids)
    planned = plan(
        listed,
        rules=rules,
        apps=apps,
        parents=parents,
        own_pids={int(pid) for pid in exclude_pids if int(pid or 0) > 0},
    )
    wanted = [int(hwnd) for hwnd in planned["hide"]]
    wanted_set = set(wanted)
    names = {int(window.get("hwnd") or 0): str(window.get("name") or "") for window in listed}

    if dry_run:
        for hwnd in wanted:
            report["applied"].append("taskbarfilter:DeleteTab:0x%X" % hwnd)
        to_restore = [hwnd for hwnd in previous if hwnd not in wanted_set]
        for hwnd in to_restore:
            report["applied"].append("taskbarfilter:AddTab:0x%X" % hwnd)
        report["warnings"].append("dry_run: pasek zadan nietkniety")
        report["hidden"] = len(wanted)
        report["restored"] = len(to_restore)
        report["kept"] = len(planned["keep"])
        return report

    api: Optional[TaskbarList] = TaskbarList()
    if not api.open():
        report["warnings"].append("ITaskbarList niedostepny (%s) - uzywam ukrywania okien" % api.error)
        api = None

    state_hwnds: dict[int, dict] = {}
    restored = 0
    leftover: dict[int, dict] = {}

    # 1) okna, ktore maja juz nie byc filtrowane (albo juz nie istnieja)
    for hwnd, meta in previous.items():
        if hwnd in wanted_set:
            state_hwnds[hwnd] = meta
            continue
        if not _is_window(hwnd):
            continue
        if not _visible(hwnd) and _still_forbidden(hwnd, rules=rules, apps=apps, parents=parents):
            # Okno ukryte awaryjnie (hide_window) nie jest juz widoczne dla
            # EnumWindows - zostaje w stanie, ale sprawdzamy jego proces.
            state_hwnds[hwnd] = meta
            continue
        if _restore_one(api, hwnd, meta, report):
            restored += 1
            report["applied"].append("taskbarfilter:AddTab:0x%X" % hwnd)
        else:
            leftover[hwnd] = meta

    # 2) nowe okna do odfiltrowania
    for hwnd in wanted:
        if hwnd in state_hwnds:
            continue
        meta = names.get(hwnd, "")
        method = ""
        if api is not None and api.delete_tab(hwnd) in (S_OK, S_FALSE):
            method = "delete_tab"
        elif hide_fallback and _hide_window(hwnd):
            method = "hide_window"
        if not method:
            report["errors"].append("nie udalo sie ukryc przycisku 0x%X (%s)" % (hwnd, meta or "?"))
            continue
        state_hwnds[hwnd] = {
            "name": meta,
            "method": method,
            "minimized": bool(next((w.get("minimized") for w in listed if int(w.get("hwnd") or 0) == hwnd), False)),
        }
        report["applied"].append("taskbarfilter:%s:0x%X" % (method, hwnd))

    state_hwnds.update(leftover)
    if state_hwnds:
        _save_state({"active": True, "updated_at": time.time(), "apps": list(apps), "hwnds": state_hwnds})
    else:
        _clear_state()

    report["hidden"] = len(state_hwnds)
    report["restored"] = restored
    report["kept"] = len(planned["keep"])
    report["ok"] = not report["errors"]
    return report


def restore(*, dry_run: bool = False) -> dict:
    """Przywraca przyciski paska zadan wszystkim odfiltrowanym oknom."""
    report = _report()
    report.update({"restored": 0, "left": 0})
    hwnds = _state_hwnds()
    if not hwnds:
        report["applied"].append("taskbarfilter:brak-stanu")
        return report

    if dry_run:
        report["applied"].append("taskbarfilter:restore:%d" % len(hwnds))
        report["warnings"].append("dry_run: pasek zadan nietkniety")
        report["restored"] = len(hwnds)
        return report

    if not IS_WINDOWS:
        report["ok"] = False
        report["errors"].append("filtrowanie paska zadan dziala tylko na Windows")
        return report

    api: Optional[TaskbarList] = TaskbarList()
    if not api.open():
        report["warnings"].append("ITaskbarList niedostepny (%s)" % api.error)
        api = None

    leftover: dict[int, dict] = {}
    for hwnd, meta in hwnds.items():
        if not _is_window(hwnd):
            continue
        if _restore_one(api, hwnd, meta, report):
            report["restored"] += 1
            report["applied"].append("taskbarfilter:AddTab:0x%X" % hwnd)
        else:
            leftover[hwnd] = meta

    if leftover:
        _save_state({"active": True, "updated_at": time.time(), "apps": [], "hwnds": leftover})
    else:
        _clear_state()
    reset_throttle()
    report["left"] = len(leftover)
    report["ok"] = not report["errors"]
    return report
