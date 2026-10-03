"""Pasek zadan systemu Windows (Shell_TrayWnd + Shell_SecondaryTrayWnd).

Kontrakt: docs/INTERFACES.md sekcja 9.

UWAGA BEZPIECZENSTWA: ukrycie paska zadan utrudnia korzystanie z komputera.
Kazde wywolanie hide() jest odwracalne przez show() (takze po restarcie procesu:
show() szuka okien po klasie, a pierwotny prostokat czyta z pliku stanu).
Metoda uzyta dla kazdego okna jest zwracana w raporcie w `applied`.

Ustalenia z testow na Windows 11 build 26200:
- `ShowWindow(SW_HIDE)` dziala i jest metoda podstawowa.
- Przesuniecie `SetWindowPos` na -32000 jest natychmiast cofane przez Explora
  (pasek wraca na swoje miejsce), dlatego galaz awaryjna jest weryfikowana
  i przy niepowodzeniu wycofywana, a powod trafia do `errors`.
"""
from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes

from .. import paths

IS_WINDOWS = sys.platform == "win32"

SW_HIDE = 0
SW_SHOW = 5
SW_SHOWNORMAL = 1
SW_SHOWNA = 8

HWND_TOPMOST = -1
WS_EX_TOOLWINDOW = 0x00000080

GWL_EXSTYLE = -20

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040

SPI_GETWORKAREA = 0x0030
SM_CXSCREEN = 0
SM_CYSCREEN = 1

OFFSCREEN_X = -32000
OFFSCREEN_Y = -32000
MAIN_CLASS = "Shell_TrayWnd"
SECONDARY_CLASS = "Shell_SecondaryTrayWnd"

STATE_NAME = "taskbar_state.json"
DEFAULT_TASKBAR_THICKNESS = 48

try:  # WINFUNCTYPE istnieje tylko na Windows
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
except AttributeError:  # pragma: no cover - poza Windows
    WNDENUMPROC = None


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


_user32_lib = None


def _user32():
    global _user32_lib
    if _user32_lib is not None:
        return _user32_lib
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.FindWindowW.restype = wintypes.HWND
    lib.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    lib.EnumWindows.restype = wintypes.BOOL
    lib.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    lib.GetClassNameW.restype = ctypes.c_int
    lib.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    lib.IsWindowVisible.restype = wintypes.BOOL
    lib.IsWindowVisible.argtypes = [wintypes.HWND]
    lib.ShowWindow.restype = wintypes.BOOL
    lib.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    lib.ShowWindowAsync.restype = wintypes.BOOL
    lib.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    lib.GetWindowRect.restype = wintypes.BOOL
    lib.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    lib.SetWindowPos.restype = wintypes.BOOL
    lib.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    lib.GetSystemMetrics.restype = ctypes.c_int
    lib.GetSystemMetrics.argtypes = [ctypes.c_int]
    lib.SystemParametersInfoW.restype = wintypes.BOOL
    lib.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    lib.IsWindow.restype = wintypes.BOOL
    lib.IsWindow.argtypes = [wintypes.HWND]
    _user32_lib = lib
    return lib


def _get_long(lib, hwnd: int, index: int) -> int:
    pointer_fn = getattr(lib, "GetWindowLongPtrW", None)
    if pointer_fn is not None:
        fn = pointer_fn
        fn.restype = ctypes.c_ssize_t
        fn.argtypes = [wintypes.HWND, ctypes.c_int]
    else:
        fn = lib.GetWindowLongW
        fn.restype = ctypes.c_long
        fn.argtypes = [wintypes.HWND, ctypes.c_int]
    return int(fn(hwnd, index))


def _set_long(lib, hwnd: int, index: int, value: int) -> None:
    pointer_fn = getattr(lib, "SetWindowLongPtrW", None)
    if pointer_fn is not None:
        fn = pointer_fn
        fn.restype = ctypes.c_ssize_t
        fn.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    else:
        fn = lib.SetWindowLongW
        fn.restype = ctypes.c_long
        fn.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    fn(hwnd, index, value)


def _rect(hwnd: int):
    lib = _user32()
    rect = RECT()
    try:
        if not lib.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    except OSError:
        return None
    return rect


def _visible(hwnd: int) -> bool:
    try:
        return bool(_user32().IsWindowVisible(hwnd))
    except OSError:
        return False


def _on_screen(hwnd: int) -> bool:
    rect = _rect(hwnd)
    if rect is None:
        return False
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return False
    return rect.left > OFFSCREEN_X + 1000 and rect.top > OFFSCREEN_Y + 1000


def _bar_hidden(hwnd: int) -> bool:
    return (not _visible(hwnd)) or (not _on_screen(hwnd))


# --------------------------------------------------------------- stan prostokatow
def state_path():
    return paths.backup_dir() / STATE_NAME


def _persist_rects(rects: dict) -> None:
    """Zapis pierwotnych prostokatow (przezywa restart procesu)."""
    payload = {
        str(hwnd): [rect.left, rect.top, rect.right, rect.bottom]
        for hwnd, rect in rects.items()
    }
    path = state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_rects() -> dict:
    path = state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rects = {}
    for key, value in (data or {}).items():
        try:
            hwnd = int(key)
            left, top, right, bottom = (int(part) for part in value)
        except (TypeError, ValueError):
            continue
        rect = RECT(left, top, right, bottom)
        rects[hwnd] = rect
    return rects


def _clear_state() -> None:
    try:
        path = state_path()
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _default_rect(hwnd: int):
    """Prostokat paska wyliczony z obszaru roboczego (gdy brak zapisu)."""
    lib = _user32()
    width = int(lib.GetSystemMetrics(SM_CXSCREEN)) or 1920
    height = int(lib.GetSystemMetrics(SM_CYSCREEN)) or 1080
    work = RECT()
    has_work = bool(lib.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0))
    if not has_work:
        work = RECT(0, 0, width, height)
    if work.bottom < height:
        return RECT(0, work.bottom, width, height)
    if work.top > 0:
        return RECT(0, 0, width, work.top)
    if work.left > 0:
        return RECT(0, 0, work.left, height)
    if work.right < width:
        return RECT(work.right, 0, width, height)
    return RECT(0, max(0, height - DEFAULT_TASKBAR_THICKNESS), width, height)


# ------------------------------------------------------------------ API
def list_taskbars() -> list[int]:
    """HWND paska glownego i wszystkich dodatkowych (per monitor)."""
    if not IS_WINDOWS:
        return []
    lib = _user32()
    handles: list[int] = []
    try:
        main = lib.FindWindowW(MAIN_CLASS, None)
        if main:
            handles.append(int(main))
    except OSError:
        pass

    @WNDENUMPROC
    def _callback(hwnd, _lparam):
        buffer = ctypes.create_unicode_buffer(256)
        try:
            if lib.GetClassNameW(hwnd, buffer, 256):
                if buffer.value == SECONDARY_CLASS:
                    handles.append(int(hwnd))
        except OSError:
            pass
        return True

    try:
        lib.EnumWindows(_callback, 0)
    except OSError:
        pass
    return sorted(set(handles))


def hide(*, dry_run: bool = False) -> dict:
    """Ukrywa paski zadan: ShowWindow(SW_HIDE), awaryjnie SetWindowPos off-screen."""
    report = _report()
    if dry_run:
        bars = list_taskbars()
        for hwnd in bars:
            report["applied"].append("taskbar.hide:ShowWindow:0x%X" % hwnd)
        if not bars:
            report["applied"].append("taskbar.hide:brak-okien")
        report["warnings"].append("dry_run: pasek zadan nietkniety")
        return report
    if not IS_WINDOWS:
        report["ok"] = False
        report["errors"].append("taskbar: tylko Windows")
        return report

    lib = _user32()
    bars = list_taskbars()
    if not bars:
        report["ok"] = False
        report["errors"].append("nie znaleziono paska zadan (Shell_TrayWnd)")
        return report

    saved_rects: dict[int, RECT] = {}
    for hwnd in bars:
        rect = _rect(hwnd)
        if rect is not None:
            saved_rects[hwnd] = rect

    try:
        _persist_rects(saved_rects)
        report["applied"].append("taskbar.state.backup")
    except OSError as exc:
        report["warnings"].append("zapis stanu paska: %s" % exc)

    for hwnd in bars:
        rect = saved_rects.get(hwnd)
        width = max(1, rect.right - rect.left) if rect else 0
        height = max(1, rect.bottom - rect.top) if rect else 0
        try:
            lib.ShowWindow(hwnd, SW_HIDE)
        except OSError as exc:
            report["errors"].append("ShowWindow(0x%X): %s" % (hwnd, exc))
            continue
        if _bar_hidden(hwnd):
            report["applied"].append("taskbar.hide:ShowWindow:0x%X" % hwnd)
            continue

        # Galaz awaryjna: przesuniecie poza ekran (Explorer czesto je cofa,
        # dlatego weryfikujemy efekt i wycofujemy zmiany przy niepowodzeniu).
        moved = False
        try:
            moved = bool(
                lib.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    OFFSCREEN_X,
                    OFFSCREEN_Y,
                    width,
                    height,
                    SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
            )
        except OSError as exc:
            report["errors"].append("SetWindowPos(0x%X): %s" % (hwnd, exc))
        if moved and _bar_hidden(hwnd):
            try:
                style = _get_long(lib, hwnd, GWL_EXSTYLE)
                if not style & WS_EX_TOOLWINDOW:
                    _set_long(lib, hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)
            except OSError:
                pass
            report["applied"].append("taskbar.hide:SetWindowPos-offscreen:0x%X" % hwnd)
            continue

        # wycofanie nieudanej proby
        if rect is not None:
            try:
                lib.SetWindowPos(
                    hwnd,
                    None,
                    rect.left,
                    rect.top,
                    width,
                    height,
                    SWP_NOACTIVATE | SWP_NOZORDER | SWP_SHOWWINDOW,
                )
            except OSError:
                pass
        report["errors"].append(
            "nie udalo sie ukryc paska 0x%X (Explorer przywraca pozycje okna)" % hwnd
        )

    report["ok"] = bool(report["applied"]) and not report["errors"]
    if not report["ok"]:
        report["warnings"].append("pasek zadan moze pozostac widoczny - sprawdz raport `applied`")
    return report


def show(*, dry_run: bool = False) -> dict:
    """Przywraca paski zadan: ShowWindow(SW_SHOW) + pierwotny prostokat."""
    report = _report()
    if dry_run:
        for hwnd in list_taskbars():
            report["applied"].append("taskbar.show:ShowWindow:0x%X" % hwnd)
        report["warnings"].append("dry_run: pasek zadan nietkniety")
        return report
    if not IS_WINDOWS:
        report["ok"] = False
        report["errors"].append("taskbar: tylko Windows")
        return report

    lib = _user32()
    bars = list_taskbars()
    if not bars:
        report["ok"] = False
        report["errors"].append("nie znaleziono paska zadan do przywrocenia")
        return report

    saved_rects = _load_rects()
    failed = False
    for hwnd in bars:
        try:
            style = _get_long(lib, hwnd, GWL_EXSTYLE)
            if style & WS_EX_TOOLWINDOW:
                _set_long(lib, hwnd, GWL_EXSTYLE, style & ~WS_EX_TOOLWINDOW)
                report["applied"].append("taskbar.show:exstyle-restore:0x%X" % hwnd)
            if not _on_screen(hwnd):
                rect = saved_rects.get(hwnd) or _default_rect(hwnd)
                width = max(1, rect.right - rect.left)
                height = max(1, rect.bottom - rect.top)
                lib.SetWindowPos(
                    hwnd,
                    None,
                    rect.left,
                    rect.top,
                    width,
                    height,
                    SWP_NOACTIVATE | SWP_NOZORDER | SWP_SHOWWINDOW,
                )
            lib.ShowWindow(hwnd, SW_SHOW)
        except OSError as exc:
            report["errors"].append("show(0x%X): %s" % (hwnd, exc))
            failed = True
            continue
        if not (_visible(hwnd) and _on_screen(hwnd)):
            try:
                lib.ShowWindowAsync(hwnd, SW_SHOWNORMAL)
            except OSError:
                pass
        if _visible(hwnd) and _on_screen(hwnd):
            report["applied"].append("taskbar.show:0x%X" % hwnd)
        else:
            report["errors"].append("pasek 0x%X nie wrocil na ekran" % hwnd)
            failed = True

    if not failed:
        _clear_state()
    report["ok"] = not report["errors"]
    return report


def is_hidden() -> bool:
    """True, gdy istnieje pasek zadan i zaden nie jest widoczny na ekranie."""
    bars = list_taskbars()
    if not bars:
        return False
    return all(_bar_hidden(hwnd) for hwnd in bars)


def offscreen_position() -> tuple[int, int]:
    """Pozycja uzywana w fallbacku (do testow i diagnostyki)."""
    return (OFFSCREEN_X, OFFSCREEN_Y)
