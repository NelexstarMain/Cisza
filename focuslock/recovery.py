"""Awaryjne przywracanie powloki i sieci.

Uzywane przez: watchdog, tools/restore.py, helper.restore_all oraz start aplikacji
po nieczystym zamknieciu. Kazdy krok jest niezalezny i idempotentny.
"""
from __future__ import annotations

import importlib
import json
import re
import time
from typing import Any, Optional

from . import lockstate, paths

#: Wartosci proxy w HKCU, ktore ustawia blokada sieci (block/networklock.py).
PROXY_VALUE_NAMES = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL")

#: Wzorzec lokalnego serwera Ciszy w ProxyServer (127.0.0.1 / localhost).
_LOCAL_PROXY_RE = re.compile(r"(127\.0\.0\.1|localhost)", re.IGNORECASE)
#: Domyslny port proxy Ciszy (NetworkConfig.proxy_port) - dowod, ze wpis jest nasz.
_CISZA_PROXY_PORT_RE = re.compile(r":8765\b")

#: Hints wskazujace, ze operacja nie powiodla sie z powodu braku admina.
_ADMIN_HINTS = ("uprawnie", "administrator", "denied", "elevat")

_LEFTOVERS_INSTRUCTIONS = (
    "Nie wszystko udalo sie cofnac po sesji - czesc zmian wymaga uprawnien administratora.\n\n"
    "1. Zapisz swoja prace i zamknij programy.\n"
    "2. Kliknij prawym przyciskiem myszy plik napraw-internet.cmd (w katalogu projektu)\n"
    "   i wybierz „Uruchom jako administrator”.\n"
    "3. Podaj haslo konta administracyjnego (konto admin), jesli system o nie poprosi.\n"
    "4. Po zakonczeniu skryptu zrestartuj przegladarke (albo caly komputer).\n\n"
    "Cisza ponowi sprzatanie przy nastepnym starcie."
)


def _mod(name: str):
    return importlib.import_module(name)


def _empty_report(reason: str) -> dict:
    return {"ok": True, "reason": reason, "applied": [], "warnings": [], "errors": []}


def mark_crash(reason: str, session_id: Optional[int] = None) -> None:
    payload = {"reason": reason, "ts": time.time(), "session_id": session_id}
    try:
        paths.crash_flag_path().write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def read_crash_flag() -> Optional[dict]:
    file = paths.crash_flag_path()
    if not file.exists():
        return None
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_crash_flag() -> None:
    try:
        paths.crash_flag_path().unlink(missing_ok=True)
    except OSError:
        pass


def restore_everything(*, dry_run: bool = False, reason: str = "") -> dict:
    """Przywraca powloke i siec do stanu sprzed sesji. Bezpieczne do wielokrotnego wywolania."""
    report = _empty_report(reason)
    state = lockstate.read()

    try:
        # Przyciski paska zadan zabrane przez sesje (tryb "filtered") musza wrocic
        # takze wtedy, gdy GUI juz nie zyje - stan filtra jest na dysku.
        res = _mod("focuslock.shell.taskbarfilter").restore(dry_run=dry_run)
        report["applied"].extend(res.get("applied", []))
        report["warnings"].extend(res.get("warnings", []))
        report["errors"].extend(res.get("errors", []))
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append(f"filtrowanie paska zadan: {exc}")

    try:
        res = _mod("focuslock.shell.taskbar").show(dry_run=dry_run)
        report["applied"].extend(res.get("applied", []))
        report["warnings"].extend(res.get("warnings", []))
        report["errors"].extend(res.get("errors", []))
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"pasek zadan: {exc}")

    try:
        desktop = _mod("focuslock.shell.desktop")
        if state is not None:
            res = desktop.restore(
                desktop.DesktopState(
                    wallpaper=state.shell.wallpaper,
                    wallpaper_style=state.shell.wallpaper_style,
                    icons_hidden=state.shell.icons_hidden,
                ),
                dry_run=dry_run,
            )
            label = "pulpit:przywrocony"
        else:
            res = desktop.restore(desktop.DesktopState(), dry_run=dry_run)
            label = "pulpit:domyslny"
        report["applied"].extend(res.get("applied", []))
        report["applied"].append(label)
        report["warnings"].extend(res.get("warnings", []))
        report["errors"].extend(res.get("errors", []))
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"pulpit: {exc}")

    try:
        dnd = _mod("focuslock.shell.dnd")
        previous = state.shell.toasts_previous if state is not None else -1
        if previous in (0, 1):
            from .helper import set_toasts_raw

            res = set_toasts_raw(previous, dry_run=dry_run)
            report["applied"].extend(res.get("applied", []))
            report["warnings"].extend(res.get("warnings", []))
            report["errors"].extend(res.get("errors", []))
        else:
            res = dnd.mute_toasts(False, dry_run=dry_run)
            report["applied"].extend(res.get("applied", []))
        dnd.prevent_sleep(False, dry_run=dry_run)
        dnd.mute_sound(False, dry_run=dry_run)
        report["applied"].append("powiadomienia:przywrocone")
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append(f"powiadomienia: {exc}")

    try:
        hotkeys = _mod("focuslock.shell.hotkeys")
        if hasattr(hotkeys, "force_uninstall"):
            hotkeys.force_uninstall()
        else:
            hotkeys.HotkeyBlocker(emergency_enabled=False).uninstall()
        report["applied"].append("hooki:usuniete")
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append(f"hooki: {exc}")

    try:
        from .helper import Helper  # lokalny import, zeby uniknac cyklu

        res = Helper("", b"", dry_run=dry_run).taskmgr_set({"disabled": False, "dry_run": dry_run})
        report["applied"].extend(res.get("applied", []))
        if not res.get("ok"):
            # WinError 5 przy braku praw / braku klucza nie moze udawac sukcesu.
            report["warnings"].append("menedzer zadan: " + "; ".join(res.get("errors", []) or ["nieudane"]))
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append(f"menedzer zadan: {exc}")

    try:
        res = _mod("focuslock.block.firewall").remove_blocks(dry_run=dry_run)
        report["applied"].extend(res.get("applied", []))
        report["warnings"].extend(res.get("warnings", []))
        report["errors"].extend(res.get("errors", []))
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append(f"zapora: {exc}")

    try:
        networklock = _mod("focuslock.block.networklock")
        res = networklock.NetworkLock(dry_run=dry_run).disable()
        report["applied"].extend(res.get("applied", []))
        report["warnings"].extend(res.get("warnings", []))
        report["errors"].extend(res.get("errors", []))
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"siec: {exc}")

    if not dry_run:
        lockstate.clear()
        try:
            paths.heartbeat_path().unlink(missing_ok=True)
        except OSError:
            pass
    report["ok"] = not report["errors"]
    return report


# ------------------------------------------------------- sprzatanie po sesji (leftovers)
def leftovers_instructions() -> str:
    """Instrukcja dla uzytkownika: jak usunac pozostalosci wymagajace admina."""
    return _LEFTOVERS_INSTRUCTIONS


def read_leftovers() -> Optional[dict]:
    """Zwraca zapisany znacznik pozostalosci (albo None, gdy go nie ma)."""
    file = paths.leftovers_path()
    if not file.exists():
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    leftovers = data.get("leftovers")
    if not isinstance(leftovers, list) or not leftovers:
        return None
    return data


def save_leftovers(leftovers: list, *, applied: Optional[list] = None, warnings: Optional[list] = None) -> Optional[str]:
    """Zapisuje liste pozostalosci razem z timestampem. Zwraca sciezke albo None."""
    items = [str(item) for item in (leftovers or []) if str(item).strip()]
    if not items:
        return None
    payload = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "leftovers": items,
        "instructions": _LEFTOVERS_INSTRUCTIONS,
        "applied": [str(item) for item in (applied or [])],
        "warnings": [str(item) for item in (warnings or [])],
    }
    target = paths.leftovers_path()
    try:
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        return None
    return str(target)


def clear_leftovers() -> None:
    """Usuwa znacznik pozostalosci (system jest czysty). Nigdy nie rzuca."""
    try:
        paths.leftovers_path().unlink(missing_ok=True)
    except OSError:
        pass


def _new_cleanup_report(dry_run: bool) -> dict:
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "checked": {
            "firewall": False,
            "firewall_rules": 0,
            "proxy": False,
            "hosts": False,
            "taskbar": False,
            "desktop": False,
            "hotkeys": False,
        },
        "leftovers": [],
        "applied": [],
        "warnings": [],
        "errors": [],
    }


def _merge(target: dict, source: Optional[dict]) -> None:
    """Dokleja raport czastkowy (applied/warnings/errors) do raportu glownego."""
    if not source:
        return
    for key in ("applied", "warnings", "errors"):
        target[key].extend(str(item) for item in (source.get(key) or []))


def _admin_reason(source: Optional[dict]) -> str:
    """Czy niepowodzenie wynika z braku uprawnien administratora?"""
    text = " ".join(
        str(item) for item in ((source or {}).get("errors") or []) + ((source or {}).get("warnings") or [])
    ).lower()
    if any(hint in text for hint in _ADMIN_HINTS):
        return "brak uprawnien administratora"
    return "nie udalo sie usunac"


def _firewall_rule_count(module) -> int:
    """Liczba regul profilu (0, gdy modul nie umie ich policzyc)."""
    getter = getattr(module, "active_rule_names", None)
    if not callable(getter):
        return 0
    try:
        return len(getter())
    except Exception:  # noqa: BLE001
        return 0


def _firewall_state(module) -> tuple[bool, int]:
    """(sa reguly, ile) - najpierw szybki odczyt rejestru, potem wolne netsh."""
    quick = getattr(module, "quick_rule_names", None)
    if callable(quick):
        names = quick()
        if names is not None:
            return bool(names), len(names)
    return bool(module.is_active()), _firewall_rule_count(module)


def _is_admin() -> bool:
    """True, gdy proces ma uprawnienia administratora (bez wyjatkow)."""
    try:
        elevate = _mod("focuslock.shell.elevate")
        return bool(elevate.is_admin())
    except Exception:  # noqa: BLE001 - brak modulu/ctypes => traktujemy jak brak admina
        return False


def _proxy_settings() -> dict:
    """Biezace wartosci proxy z HKCU (pusty slownik, gdy rejestr niedostepny).

    Korzystamy z odczytu w `block.networklock` - to jedno zrodlo prawdy dla
    klucza Internet Settings, wiec weryfikacja nie rozjedzie sie z zapisem.
    """
    try:
        networklock = _mod("focuslock.block.networklock")
        values = networklock._read_proxy_settings()  # noqa: SLF001 - wspolny odczyt rejestru
    except Exception:  # noqa: BLE001 - brak winreg/modulu to nie blad krytyczny
        return {}
    return dict(values or {})


def _write_proxy_settings(values: dict) -> list[str]:
    """Przywraca wartosci proxy w HKCU; rzuca OSError przy bledzie."""
    networklock = _mod("focuslock.block.networklock")
    applied = networklock._write_proxy_settings(values)  # noqa: SLF001 - wspolny zapis rejestru
    networklock._notify_settings_change()  # noqa: SLF001
    return [str(item) for item in (applied or [])]


def _proxy_is_cisza(values: Optional[dict], state=None) -> bool:
    """True, gdy proxy systemowe wskazuje na lokalny serwer Ciszy.

    Nie ruszamy cudzego lokalnego proxy (np. 127.0.0.1:8080): wpis uznajemy za
    nasz tylko wtedy, gdy potwierdza to lockstate (`proxy_enabled_by_us` albo
    zapisane `proxy_previous`) lub gdy port to domyslne 8765.
    """
    if not values:
        return False
    try:
        enabled = int(values.get("ProxyEnable") or 0)
    except (TypeError, ValueError):
        enabled = 0
    if enabled != 1:
        return False
    server = str(values.get("ProxyServer") or "")
    if not _LOCAL_PROXY_RE.search(server):
        return False
    network = getattr(state, "network", None) if state is not None else None
    if network is not None:
        if bool(getattr(network, "proxy_enabled_by_us", False)):
            return True
        if dict(getattr(network, "proxy_previous", {}) or {}):
            return True
    return bool(_CISZA_PROXY_PORT_RE.search(server))


def _proxy_restore_values(state) -> dict:
    """Wartosci proxy do przywrocenia: zapisane w lockstate albo wylaczenie proxy."""
    previous = {}
    if state is not None and getattr(state, "network", None) is not None:
        previous = dict(getattr(state.network, "proxy_previous", {}) or {})
    if previous:
        return {name: previous.get(name) for name in PROXY_VALUE_NAMES}
    values = {name: None for name in PROXY_VALUE_NAMES}
    values["ProxyEnable"] = 0
    return values


def cleanup_leftovers(*, dry_run: bool = False) -> dict:
    """Sprawdza stan systemu po sesji i cofa to, co zostalo po helperze.

    Sprawdza po kolei: reguly zapory `CiszaBlock-*`, proxy systemowe w HKCU,
    sekcje CISZA-BEGIN/END w pliku hosts, ukryty pasek zadan, ukryte ikony
    pulpitu/czarna tapete oraz hooki klawiatury. Kazdy krok jest niezalezny,
    w try/except - zaden wyjatek nie wycieka na zewnatrz.

    Zwraca raport: {"ok", "dry_run", "checked", "leftovers", "applied",
    "warnings", "errors"}. `leftovers` to rzeczy, ktorych NIE udalo sie cofnac
    (np. "brak uprawnien administratora: reguly zapory CiszaBlock (14)").

    `dry_run=True` nic nie zmienia (zadnego polecenia, zapisu rejestru ani
    znacznika) - sluzy tylko do diagnostyki.
    """
    report = _new_cleanup_report(dry_run)
    state = lockstate.read()

    # ------------------------------------------------------------------ zapora
    try:
        firewall = _mod("focuslock.block.firewall")
    except Exception as exc:  # noqa: BLE001
        firewall = None
        report["warnings"].append(f"zapora: modul niedostepny: {exc}")
    if firewall is not None:
        try:
            active, count = _firewall_state(firewall)
            report["checked"]["firewall"] = active
            report["checked"]["firewall_rules"] = count
            if active:
                if dry_run:
                    report["applied"].append(
                        f"zapora: do usuniecia {count or 'nieznana liczba'} regul CiszaBlock (dry-run)"
                    )
                elif not _is_admin():
                    # Bez podniesionych uprawnien netsh i tak odmowi - nie ma sensu
                    # wysylac kilkunastu nieudanych polecen na koncu kazdej sesji.
                    suffix = f" ({count})" if count else ""
                    report["leftovers"].append(f"brak uprawnien administratora: reguly zapory CiszaBlock{suffix}")
                    report["warnings"].append("zapora: reguly CiszaBlock wymagaja uruchomienia jako administrator")
                else:
                    removal = firewall.remove_blocks(dry_run=False)
                    _merge(report, removal)
                    try:
                        still_active = bool(firewall.is_active())
                    except Exception:  # noqa: BLE001
                        still_active = not bool((removal or {}).get("ok", True))
                    if still_active:
                        reason = _admin_reason(removal)
                        suffix = f" ({count})" if count else ""
                        report["leftovers"].append(f"{reason}: reguly zapory CiszaBlock{suffix}")
                        report["warnings"].append("zapora: reguly CiszaBlock nie zostaly usuniete")
                    else:
                        report["applied"].append("zapora: reguly CiszaBlock usuniete")
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"zapora: {exc}")

    # --------------------------------------------------------------- proxy HKCU
    try:
        values = _proxy_settings()
        cisza_proxy = _proxy_is_cisza(values, state)
        report["checked"]["proxy"] = cisza_proxy
        if cisza_proxy:
            target_values = _proxy_restore_values(state)
            if dry_run:
                report["applied"].append("proxy: przywrocenie ustawien systemowych (dry-run)")
            else:
                try:
                    report["applied"].extend(_write_proxy_settings(target_values))
                    report["applied"].append("proxy: przywrocono ustawienia systemowe")
                except OSError as exc:
                    report["leftovers"].append(f"nie udalo sie przywrocic proxy systemowego: {exc}")
                if _proxy_is_cisza(_proxy_settings(), state):
                    report["leftovers"].append(
                        "proxy systemowe nadal wskazuje na lokalny serwer Ciszy (127.0.0.1)"
                    )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"proxy: {exc}")

    # ------------------------------------------------------------------- hosts
    try:
        hosts = _mod("focuslock.block.hosts")
        blocked = bool(hosts.is_blocked())
        report["checked"]["hosts"] = blocked
        if blocked:
            if dry_run:
                report["applied"].append("hosts: usuniecie sekcji CISZA-BEGIN/END (dry-run)")
            else:
                removal = hosts.unblock(dry_run=False)
                _merge(report, removal)
                if bool(hosts.is_blocked()):
                    report["leftovers"].append(
                        "brak uprawnien administratora: sekcja CISZA-BEGIN/END w pliku hosts"
                    )
                    report["warnings"].append("hosts: nie udalo sie usunac sekcji Cisza")
                else:
                    report["applied"].append("hosts: usunieto sekcje CISZA-BEGIN/END")
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"hosts: {exc}")

    # -------------------------------------------------------------- pasek zadan
    try:
        taskbar = _mod("focuslock.shell.taskbar")
        hidden = bool(taskbar.is_hidden())
        report["checked"]["taskbar"] = hidden
        if hidden:
            if dry_run:
                report["applied"].append("pasek zadan: przywrocenie na ekran (dry-run)")
            else:
                _merge(report, taskbar.show(dry_run=False))
                if bool(taskbar.is_hidden()):
                    report["leftovers"].append("pasek zadan pozostal ukryty")
                else:
                    report["applied"].append("pasek zadan: przywrocony")
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"pasek zadan: {exc}")

    # ------------------------------------------------------------------ pulpit
    try:
        desktop = _mod("focuslock.shell.desktop")
        current = desktop.capture()
        icons_hidden = bool(getattr(current, "icons_hidden", False))
        wallpaper = str(getattr(current, "wallpaper", "") or "").strip()
        stored = None
        loader = getattr(desktop, "load_state", None)
        if callable(loader):
            try:
                stored = loader()
            except Exception:  # noqa: BLE001
                stored = None
        lockstate_wallpaper = ""
        if state is not None:
            lockstate_wallpaper = str(getattr(state.shell, "wallpaper", "") or "").strip()
        # Czarna (pusta) tapeta to pozostalosc tylko wtedy, gdy mamy skad ja
        # przywrocic: stan z lockstate albo awaryjna kopie z apply_black.
        # Inaczej uzytkownik po prostu nie ma tapety i nie ma czego cofac.
        backup_wallpaper = lockstate_wallpaper or str(getattr(stored, "wallpaper", "") or "").strip()
        needs_desktop = bool(icons_hidden or (not wallpaper and backup_wallpaper))
        report["checked"]["desktop"] = needs_desktop
        if needs_desktop:
            if lockstate_wallpaper:
                target_state = desktop.DesktopState(
                    wallpaper=lockstate_wallpaper,
                    wallpaper_style=state.shell.wallpaper_style,
                    icons_hidden=False,
                )
            elif stored is not None:
                target_state = stored
                target_state.icons_hidden = False
            else:
                target_state = desktop.DesktopState(icons_hidden=False)
            if dry_run:
                report["applied"].append("pulpit: przywrocenie tapety i ikon (dry-run)")
            else:
                _merge(report, desktop.restore(target_state, dry_run=False))
                after = desktop.capture()
                desktop_leftovers = []
                if bool(getattr(after, "icons_hidden", False)):
                    desktop_leftovers.append("ikony pulpitu pozostaly ukryte")
                if backup_wallpaper and not str(getattr(after, "wallpaper", "") or "").strip():
                    desktop_leftovers.append("tapeta pozostala czarna")
                if desktop_leftovers:
                    report["leftovers"].extend(desktop_leftovers)
                else:
                    report["applied"].append("pulpit: przywrocony")
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"pulpit: {exc}")

    # ---------------------------------------------------------- hooki klawiatury
    try:
        hotkeys = _mod("focuslock.shell.hotkeys")
        available = bool(getattr(hotkeys, "KEYBOARD_OK", True))
        if not available:
            report["checked"]["hotkeys"] = True
            report["warnings"].append("hooki klawiatury: biblioteka keyboard niedostepna - nic do zdjecia")
        elif dry_run:
            report["applied"].append("hooki klawiatury: zdjecie (dry-run)")
        else:
            result = hotkeys.force_uninstall()
            _merge(report, result)
            ok = bool((result or {}).get("ok", True)) and not (result or {}).get("errors")
            report["checked"]["hotkeys"] = ok
            if ok:
                report["applied"].append("hooki klawiatury: zdjete")
            else:
                detail = "; ".join(str(item) for item in ((result or {}).get("errors") or [])) or "nieznany blad"
                report["leftovers"].append(f"hooki klawiatury pozostaly: {detail}")
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append(f"hooki: {exc}")

    report["leftovers"] = list(dict.fromkeys(str(item) for item in report["leftovers"] if str(item).strip()))
    report["ok"] = not report["leftovers"] and not report["errors"]

    if not dry_run:
        try:
            if report["leftovers"]:
                save_leftovers(report["leftovers"], applied=report["applied"], warnings=report["warnings"])
            else:
                clear_leftovers()
        except Exception as exc:  # noqa: BLE001 - znacznik nie moze przewrocic sprzatania
            report["warnings"].append(f"znacznik pozostalosci: {exc}")
    return report


def handle_startup_state(store, *, restore_shell: bool = True) -> dict:
    """Wykrywa sesje sprzatane po crashie i domyka je w bazie.

    `restore_shell=False` (ustawienie "przywracaj system po starcie" wylaczone)
    domyka tylko sesje w bazie i nie dotyka powloki ani sieci.
    """
    result: dict[str, Any] = {"recovered": False, "open_sessions": [], "report": None, "crash_flag": read_crash_flag()}
    state = lockstate.read()
    open_sessions = store.open_sessions() if store is not None else []
    result["open_sessions"] = [s["id"] for s in open_sessions]

    needs_restore = bool((state and state.active) or open_sessions or result["crash_flag"])
    if not needs_restore:
        return result

    if state and state.active and restore_shell:
        report = restore_everything(reason="startup")
        result["report"] = report
        result["recovered"] = True
    elif state and state.active:
        result["skipped"] = "restore_on_boot=False"
        # Stan lockdownu zostaje na dysku - aplikacja nadal wie, co trzeba cofnac
        # przy najblizszym recznym przywroceniu (tools/restore.py --panic).
        lockstate.update(active=False, reason="startup-bez-przywracania")

    for session in open_sessions:
        store.finish_session(
            session["id"],
            "CRASHED",
            int(session.get("actual_seconds") or 0),
            int(session.get("pomodoros_done") or 0),
            int(session.get("pomodoros_aborted") or 0),
        )
        store.add_event(session["id"], "CRASH_RECOVERED", {"reason": "start aplikacji po nieczystym zamknieciu"})
    clear_crash_flag()
    return result
