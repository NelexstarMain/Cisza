"""Wyciszenie powiadomien, dzwieku i blokada usypiania (Windows).

Kontrakt: docs/INTERFACES.md sekcja 12.

- Toasty: rejestr HKCU NOC_GLOBAL_SETTING_TOASTS_ENABLED (DWORD).
- Sleep: SetThreadExecutionState (flagi dzialaja per-watek!).
- Dzwiek: best-effort - IAudioEndpointVolume nie jest dostepne bez zaleznosci
  COM (comtypes), wiec wyciszamy wylacznie dzwieki zdarzen systemowych
  (HKCU\\AppEvents\\Schemes\\Apps\\.Default\\<event>\\.Current) z kopia
  oryginalnych wartosci w paths.backup_dir(). Multimediow to nie wycisza.
"""
from __future__ import annotations

import ctypes
import json
import sys

from .. import paths

IS_WINDOWS = sys.platform == "win32"

TOASTS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Notifications\Settings"
TOASTS_VALUE = "NOC_GLOBAL_SETTING_TOASTS_ENABLED"

APPEVENTS_KEY = r"AppEvents\Schemes\Apps\.Default"
APPEVENTS_CURRENT = ".Current"
SOUND_BACKUP_NAME = "appevents.json"

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_winreg = None
if IS_WINDOWS:
    try:
        import winreg as _winreg  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover
        _winreg = None


def _report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


# ------------------------------------------------------------------- toasty
def mute_toasts(enabled: bool, *, dry_run: bool = False) -> dict:
    """enabled=True wycisza toasty (DWORD 0), enabled=False przywraca (1)."""
    report = _report()
    value = 0 if enabled else 1
    label = "dnd.toasts:%s" % ("muted" if enabled else "enabled")
    if dry_run:
        report["applied"].append(label)
        report["warnings"].append("dry_run: rejestr nietkniety")
        return report
    if not IS_WINDOWS or _winreg is None:
        report["ok"] = False
        report["errors"].append("dnd.mute_toasts: tylko Windows")
        return report
    try:
        with _winreg.CreateKeyEx(
            _winreg.HKEY_CURRENT_USER, TOASTS_KEY, 0, _winreg.KEY_SET_VALUE
        ) as key:
            _winreg.SetValueEx(key, TOASTS_VALUE, 0, _winreg.REG_DWORD, value)
        report["applied"].append(label)
    except OSError as exc:
        report["ok"] = False
        report["errors"].append("rejestr toastow: %s" % exc)
    return report


# ------------------------------------------------------------------- sleep
def prevent_sleep(enabled: bool, *, dry_run: bool = False) -> dict:
    """Blokuje usypianie systemu (flagi SetThreadExecutionState dzialaja per-watek)."""
    report = _report()
    flags = (ES_CONTINUOUS | ES_SYSTEM_REQUIRED) if enabled else ES_CONTINUOUS
    label = "dnd.prevent_sleep:%s" % ("on" if enabled else "off")
    if dry_run:
        report["applied"].append(label)
        report["warnings"].append("dry_run: stan wykonania nietkniety")
        return report
    if not IS_WINDOWS:
        report["ok"] = False
        report["errors"].append("dnd.prevent_sleep: tylko Windows")
        return report
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        result = kernel32.SetThreadExecutionState(ctypes.c_uint32(flags))
        if not result:
            report["ok"] = False
            report["errors"].append("SetThreadExecutionState zwrocilo 0")
            return report
        report["applied"].append(label)
        report["warnings"].append("blokada usypiania dziala tylko w watku wywolujacym")
    except OSError as exc:
        report["ok"] = False
        report["errors"].append("SetThreadExecutionState: %s" % exc)
    return report


# ------------------------------------------------------------------- dzwiek
def sound_backup_path():
    return paths.backup_dir() / SOUND_BACKUP_NAME


def _event_keys() -> list[str]:
    keys: list[str] = []
    with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, APPEVENTS_KEY) as root:
        index = 0
        while True:
            try:
                name = _winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            keys.append(name)
    return keys


def _read_event_sound(event: str):
    path = "%s\\%s\\%s" % (APPEVENTS_KEY, event, APPEVENTS_CURRENT)
    try:
        with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, path) as key:
            value, kind = _winreg.QueryValueEx(key, "")
    except OSError:
        return None
    return (str(value), int(kind))


def _write_event_sound(event: str, value: str, kind: int) -> bool:
    path = "%s\\%s\\%s" % (APPEVENTS_KEY, event, APPEVENTS_CURRENT)
    try:
        with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, path, 0, _winreg.KEY_SET_VALUE) as key:
            _winreg.SetValueEx(key, "", 0, kind, value)
    except OSError:
        return False
    return True


def mute_sound(enabled: bool, *, dry_run: bool = False) -> dict:
    """Best-effort: wycisza dzwieki zdarzen systemowych (AppEvents).

    Nie wycisza multimediow ani aplikacji - do tego potrzebny jest
    IAudioEndpointVolume (COM), niedostepny bez dodatkowej zaleznosci.
    """
    report = _report()
    label = "dnd.sound:%s" % ("muted" if enabled else "restored")
    report["warnings"].append(
        "best-effort: wyciszane sa tylko dzwieki zdarzen systemowych; "
        "multimedia wymagaja IAudioEndpointVolume (brak zaleznosci COM)"
    )
    if dry_run:
        report["applied"].append(label)
        report["warnings"].append("dry_run: rejestr nietkniety")
        return report
    if not IS_WINDOWS or _winreg is None:
        report["ok"] = False
        report["errors"].append("dnd.mute_sound: tylko Windows")
        return report

    backup = sound_backup_path()
    if enabled:
        data: dict[str, list] = {}
        try:
            for event in _event_keys():
                entry = _read_event_sound(event)
                if entry is None:
                    continue
                value, kind = entry
                if value == "":
                    continue
                data[event] = [value, kind]
        except OSError as exc:
            report["ok"] = False
            report["errors"].append("odczyt AppEvents: %s" % exc)
            return report

        try:
            if not backup.exists() and data:
                tmp = backup.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
                tmp.replace(backup)
                report["applied"].append("dnd.sound.backup")
        except OSError as exc:
            report["ok"] = False
            report["errors"].append("kopia dzwiekow: %s" % exc)
            return report

        muted = 0
        for event, (_value, kind) in data.items():
            if _write_event_sound(event, "", kind):
                muted += 1
        report["applied"].append(label)
        report["applied"].append("dnd.sound.events=%d" % muted)
        if muted == 0:
            report["ok"] = False
            report["errors"].append("nie udalo sie wyciszyc zadnego zdarzenia AppEvents")
        return report

    # enabled == False -> przywrocenie z kopii
    if not backup.exists():
        report["warnings"].append("brak kopii dzwiekow - nic do przywrocenia")
        report["applied"].append("dnd.sound:noop")
        return report
    try:
        data = json.loads(backup.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report["ok"] = False
        report["errors"].append("odczyt kopii dzwiekow: %s" % exc)
        return report
    restored = 0
    for event, entry in (data or {}).items():
        try:
            value, kind = entry
        except (TypeError, ValueError):
            continue
        if _write_event_sound(str(event), str(value), int(kind)):
            restored += 1
    report["applied"].append(label)
    report["applied"].append("dnd.sound.events=%d" % restored)
    report["ok"] = restored > 0 or not data
    return report
