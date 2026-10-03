"""Tapeta i ikony pulpitu (Windows).

Kontrakt: docs/INTERFACES.md sekcja 8.

Zasady:
- Wszystkie operacje sa best-effort: blad systemu trafia do raportu (errors),
  nigdy nie wycieka jako wyjatek.
- dry_run=True niczego nie zmienia w systemie, tylko zwraca liste `applied`.
- Przed zmiana tapety zapisujemy poprzedni stan do lockstate (przez kontroler)
  oraz awaryjnie na dysk (paths.backup_dir()/desktop_state.json), zeby
  tools/restore.py i watchdog mogly cofnac zmiane nawet po crashu.
"""
from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import paths

IS_WINDOWS = sys.platform == "win32"

SPI_SETDESKWALLPAPER = 0x0014
SPI_GETDESKWALLPAPER = 0x0073
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

SW_HIDE = 0
SW_SHOW = 5

DESKTOP_KEY = r"Control Panel\Desktop"
WALLPAPER_VALUE = "Wallpaper"
WALLPAPER_STYLE_VALUE = "WallpaperStyle"
TILE_WALLPAPER_VALUE = "TileWallpaper"

STATE_BACKUP_NAME = "desktop_state.json"

try:  # WINFUNCTYPE istnieje tylko na Windows
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
except AttributeError:  # pragma: no cover - poza Windows
    WNDENUMPROC = None


@dataclass
class DesktopState:
    """Stan powloki, ktory musimy cofnac.

    Pola z kontraktu: wallpaper, wallpaper_style, icons_hidden.
    Pola dodatkowe (z wartosciami domyslnymi, zgodne wstecz): tile_wallpaper,
    capture_error - potrzebne do wiernego odtworzenia stylu i do diagnostyki.
    """

    wallpaper: str = ""
    wallpaper_style: int = 10
    icons_hidden: bool = False
    tile_wallpaper: int = 0
    capture_error: str = ""


# --------------------------------------------------------------------- raport
def _report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


# ------------------------------------------------------------------ winapi
_user32_lib = None
_winreg = None

if IS_WINDOWS:
    try:
        import winreg as _winreg  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover - tylko Windows
        _winreg = None


def _user32():
    """Zwraca user32 z ustawionymi typami (bezpiecznie dla 64 bitow)."""
    global _user32_lib
    if _user32_lib is not None:
        return _user32_lib
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.SystemParametersInfoW.restype = wintypes.BOOL
    lib.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    lib.FindWindowW.restype = wintypes.HWND
    lib.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    lib.FindWindowExW.restype = wintypes.HWND
    lib.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    lib.EnumWindows.restype = wintypes.BOOL
    lib.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    lib.IsWindowVisible.restype = wintypes.BOOL
    lib.IsWindowVisible.argtypes = [wintypes.HWND]
    lib.ShowWindow.restype = wintypes.BOOL
    lib.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32_lib = lib
    return lib


def _set_wallpaper(path_text: str | None) -> bool:
    """SPI_SETDESKWALLPAPER.

    `path_text`:
    - sciezka pliku -> ustawia tapete,
    - "" -> czarny ekran (pusty lancuch usuwa tapete),
    - None -> przeladowanie tapety z rejestru (uzywane w awaryjnym fallbacku).
    """
    if not IS_WINDOWS:
        return False
    lib = _user32()
    flags = SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    pointer = None if path_text is None else ctypes.c_wchar_p(path_text)
    try:
        result = lib.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, pointer, flags)
    except OSError:
        return False
    if result:
        return True
    if path_text:
        return False
    # Windows potrafi odrzucic pusty lancuch: wpis w rejestrze + NULL
    # (NULL przeladowuje tapete z rejestru, gdzie jest teraz pusty wpis).
    try:
        if _winreg is not None:
            with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, DESKTOP_KEY, 0, _winreg.KEY_SET_VALUE) as key:
                _winreg.SetValueEx(key, WALLPAPER_VALUE, 0, _winreg.REG_SZ, "")
        result = lib.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, None, flags)
    except OSError:
        return False
    return bool(result)


def current_wallpaper() -> str:
    """Tapeta z punktu widzenia systemu ("" = brak tapety / czarne tlo)."""
    if not IS_WINDOWS:
        return ""
    buffer = ctypes.create_unicode_buffer(1024)
    try:
        if _user32().SystemParametersInfoW(SPI_GETDESKWALLPAPER, 1024, buffer, 0):
            return buffer.value
    except OSError:
        pass
    return ""


def _write_registry(state: DesktopState) -> None:
    if not IS_WINDOWS or _winreg is None:
        raise OSError("registry unavailable")
    with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, DESKTOP_KEY, 0, _winreg.KEY_SET_VALUE) as key:
        _winreg.SetValueEx(key, WALLPAPER_VALUE, 0, _winreg.REG_SZ, state.wallpaper or "")
        _winreg.SetValueEx(key, WALLPAPER_STYLE_VALUE, 0, _winreg.REG_SZ, str(int(state.wallpaper_style)))
        _winreg.SetValueEx(key, TILE_WALLPAPER_VALUE, 0, _winreg.REG_SZ, str(int(state.tile_wallpaper)))


def _read_str(key, name: str, default: str) -> str:
    try:
        value, _kind = _winreg.QueryValueEx(key, name)
        return str(value)
    except OSError:
        return default


def _read_int(key, name: str, default: int) -> int:
    try:
        value, _kind = _winreg.QueryValueEx(key, name)
        return int(str(value))
    except (OSError, ValueError):
        return default


# --------------------------------------------------------------- ikony pulpitu
def _find_desktop_listview() -> int:
    """HWND listy ikon pulpitu (SHELLDLL_DefView -> SysListView32) lub 0."""
    if not IS_WINDOWS:
        return 0
    lib = _user32()
    found: list[int] = []

    @WNDENUMPROC
    def _callback(hwnd, _lparam):  # pragma: no cover - wymaga zywego explorera
        view = lib.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if view:
            listview = lib.FindWindowExW(view, None, "SysListView32", None)
            if listview:
                found.append(int(listview))
                return False
        return True

    try:
        lib.EnumWindows(_callback, 0)
    except OSError:
        pass
    if found:
        return found[0]
    try:
        progman = lib.FindWindowW("Progman", None)
        if progman:
            view = lib.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
            if view:
                listview = lib.FindWindowExW(view, None, "SysListView32", None)
                if listview:
                    return int(listview)
    except OSError:
        pass
    return 0


def is_icons_hidden() -> bool:
    """True, gdy ikony pulpitu sa ukryte; False takze gdy okna nie znaleziono."""
    hwnd = _find_desktop_listview()
    if not hwnd:
        return False
    try:
        return not bool(_user32().IsWindowVisible(hwnd))
    except OSError:
        return False


def _set_icons_hidden(hidden: bool) -> bool:
    hwnd = _find_desktop_listview()
    if not hwnd:
        return False
    try:
        _user32().ShowWindow(hwnd, SW_HIDE if hidden else SW_SHOW)
    except OSError:
        return False
    return True


# ------------------------------------------------------------------- stan
def capture() -> DesktopState:
    """Czyta tapete i styl z HKCU oraz stan ikon. Nigdy nie rzuca."""
    state = DesktopState()
    if not IS_WINDOWS or _winreg is None:
        state.capture_error = "not-windows"
        return state
    try:
        with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, DESKTOP_KEY) as key:
            state.wallpaper = _read_str(key, WALLPAPER_VALUE, "")
            state.wallpaper_style = _read_int(key, WALLPAPER_STYLE_VALUE, 10)
            state.tile_wallpaper = _read_int(key, TILE_WALLPAPER_VALUE, 0)
    except OSError as exc:
        state.capture_error = "registry: %s" % exc
    state.icons_hidden = is_icons_hidden()
    return state


def backup_path() -> Path:
    return paths.backup_dir() / STATE_BACKUP_NAME


def save_state(state: DesktopState) -> Path:
    """Awaryjna kopia stanu sprzed lockdownu (odtwarzana przez restore)."""
    path = backup_path()
    payload = asdict(state)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_state() -> DesktopState | None:
    path = backup_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    known = {key: data[key] for key in DesktopState.__annotations__ if key in data}
    try:
        return DesktopState(**known)
    except TypeError:
        return None


# ------------------------------------------------------------------ operacje
def apply_black(*, dry_run: bool = False, hide_icons: bool = True) -> dict:
    """Ustawia czarna tapete (pusty SPI_SETDESKWALLPAPER) i ukrywa ikony."""
    report = _report()
    if dry_run:
        report["applied"].append("desktop.wallpaper=black")
        if hide_icons:
            report["applied"].append("desktop.icons.hide")
        report["warnings"].append("dry_run: system nietkniety")
        return report
    if not IS_WINDOWS:
        report["ok"] = False
        report["errors"].append("desktop: tylko Windows")
        return report

    previous = capture()
    if previous.capture_error:
        report["warnings"].append("capture: " + previous.capture_error)
    try:
        save_state(previous)
        report["applied"].append("desktop.state.backup")
    except OSError as exc:
        report["warnings"].append("backup stanu: %s" % exc)

    if not _set_wallpaper(""):
        report["errors"].append("SPI_SETDESKWALLPAPER nie potwierdzil czarnej tapety")
    else:
        report["applied"].append("desktop.wallpaper=black")
        active = current_wallpaper()
        if active:
            report["warnings"].append("system nadal zglasza tapete: %s" % active)
        else:
            report["applied"].append("desktop.wallpaper=black:verified")

    if hide_icons:
        if _set_icons_hidden(True):
            report["applied"].append("desktop.icons.hide")
        else:
            report["warnings"].append("nie znaleziono okna ikon pulpitu (SysListView32)")

    report["ok"] = not report["errors"]
    return report


def restore(state, *, dry_run: bool = False) -> dict:
    """Przywraca tapete i ikony. `state` to DesktopState (albo ShellState)."""
    report = _report()
    wallpaper = str(getattr(state, "wallpaper", "") or "")
    style = int(getattr(state, "wallpaper_style", 10) or 10)
    tile = int(getattr(state, "tile_wallpaper", 0) or 0)
    icons_hidden = bool(getattr(state, "icons_hidden", False))
    backup_used = False

    # Recovery (watchdog/tools/restore.py) dziala w osobnym procesie i czesto
    # nie ma stanu z lockstate. Zamiast malowac ekran na czarno ratujemy tapete
    # z awaryjnej kopii zapisanej przy apply_black().
    if not wallpaper:
        stored = load_state()
        if stored is not None and stored.wallpaper:
            wallpaper = stored.wallpaper
            style = stored.wallpaper_style
            tile = stored.tile_wallpaper
            backup_used = True

    if dry_run:
        report["applied"].append("desktop.wallpaper.restore:%s" % (wallpaper or "<brak>"))
        if backup_used:
            report["applied"].append("desktop.state.backup-used")
        if not icons_hidden:
            report["applied"].append("desktop.icons.show")
        report["warnings"].append("dry_run: system nietkniety")
        return report
    if not IS_WINDOWS:
        report["ok"] = False
        report["errors"].append("desktop: tylko Windows")
        return report

    restored = DesktopState(
        wallpaper=wallpaper, wallpaper_style=style, icons_hidden=icons_hidden, tile_wallpaper=tile
    )
    try:
        _write_registry(restored)
        report["applied"].append("desktop.registry.restore")
    except OSError as exc:
        report["errors"].append("rejestr tapety: %s" % exc)

    if backup_used:
        report["applied"].append("desktop.state.backup-used")
        report["warnings"].append("stan z lockstate byl pusty - uzyto awaryjnej kopii tapety")

    if not _set_wallpaper(wallpaper):
        if wallpaper:
            report["errors"].append("nie udalo sie przywrocic tapety: %s" % wallpaper)
        else:
            report["warnings"].append("tapeta byla pusta (czarne tlo) - przywrocono brak tapety")
    else:
        report["applied"].append("desktop.wallpaper.restore")

    if not icons_hidden:
        if _set_icons_hidden(False):
            report["applied"].append("desktop.icons.show")
        else:
            report["warnings"].append("nie znaleziono okna ikon pulpitu do przywrocenia")

    report["ok"] = not report["errors"]
    return report
