"""Proces pomocniczy z uprawnieniami administratora (RPC + operacje systemowe).

Uruchamiany przez GUI: pythonw -m focuslock --helper --token <token> --authkey <hex>
Nasluchuje na paths.rpc_pipe_name(). Wszystkie metody zwracaja dict.
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import threading
import time
import traceback
from typing import Any, Callable, Optional

from . import ipc, lockstate, paths

# --------------------------------------------------------------------- importy leniwe
def _mod(name: str):
    import importlib

    return importlib.import_module(name)


def taskbar_mode_from(settings: dict) -> str:
    """Tryb paska zadan z payloadu ustawien ("filtered" | "hide" | "keep").

    Stare ustawienia mialy tylko `hide_taskbar` (bool) - wtedy True = "hide",
    False = "keep"; brak obu pol to domyslny "filtered".
    """
    raw = str((settings or {}).get("taskbar_mode") or "").strip().lower()
    if raw in ("filtered", "hide", "keep"):
        return raw
    if "hide_taskbar" in (settings or {}):
        return "hide" if settings.get("hide_taskbar") else "keep"
    return "filtered"


def read_toasts_value() -> int:
    """Obecna wartosc NOC_GLOBAL_SETTING_TOASTS_ENABLED (-1 = brak/nieczytelne)."""
    import winreg

    path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Notifications\Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, "NOC_GLOBAL_SETTING_TOASTS_ENABLED")
            return int(value)
    except Exception:
        return -1


def set_toasts_raw(value: int, *, dry_run: bool = False) -> dict:
    """Ustawia powiadomienia na dokladna wartosc z rejestru (0/1)."""
    report = {"ok": True, "applied": [], "warnings": [], "errors": []}
    if value not in (0, 1):
        report["warnings"].append("brak zapisanej wartosci powiadomien - pomijam")
        return report
    if dry_run:
        report["applied"].append(f"NOC_GLOBAL_SETTING_TOASTS_ENABLED={value} (dry-run)")
        return report
    import winreg

    path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Notifications\Settings"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "NOC_GLOBAL_SETTING_TOASTS_ENABLED", 0, winreg.REG_DWORD, int(value))
        report["applied"].append(f"NOC_GLOBAL_SETTING_TOASTS_ENABLED={value}")
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["errors"].append(str(exc))
    return report


class Helper:
    """Stan i logika procesu pomocniczego."""

    def __init__(self, token: str, authkey: bytes, dry_run: bool = False) -> None:
        self.token = token
        self.authkey = authkey
        self.dry_run = dry_run
        self.started_at = time.time()
        self.server: Optional[ipc.RpcServer] = None
        self._lock = threading.Lock()
        self._guard = None
        self._launcher = None
        self._hotkeys = None
        self._network = None
        self._recent: list[dict] = []
        self._handlers: dict[str, Callable[[dict], Any]] = {
            "ping": self.ping,
            "restore_all": self.restore_all,
            "shell_lock": self.shell_lock,
            "shell_unlock": self.shell_unlock,
            "shell_status": self.shell_status,
            "network_enable": self.network_enable,
            "network_disable": self.network_disable,
            "network_status": self.network_status,
            "guard_start": self.guard_start,
            "guard_update": self.guard_update,
            "guard_stop": self.guard_stop,
            "guard_stats": self.guard_stats,
            "process_list": self.process_list,
            "process_action": self.process_action,
            "taskmgr_set": self.taskmgr_set,
            "dnd_set": self.dnd_set,
            "hotkeys_set": self.hotkeys_set,
            "shutdown": self.shutdown,
        }

    # ------------------------------------------------------------------ narzedzia
    def emit(self, event: str, data: dict) -> None:
        entry = {"event": event, "data": data, "ts": time.time()}
        with self._lock:
            self._recent.append(entry)
            self._recent = self._recent[-100:]
        if self.server is not None:
            self.server.broadcast(event, data)

    @staticmethod
    def _report(**kwargs: Any) -> dict:
        base = {"ok": True, "applied": [], "warnings": [], "errors": []}
        base.update(kwargs)
        if base["errors"]:
            base["ok"] = False
        return base

    def _missing(self, module: str, exc: Exception) -> dict:
        return self._report(ok=False, errors=[f"modul {module} niedostepny: {exc}"])

    # --------------------------------------------------------------------- metody
    def ping(self, params: dict) -> dict:
        try:
            admin = bool(_mod("focuslock.shell.elevate").is_admin())
        except Exception:
            admin = False
        return {
            "ok": True,
            "admin": admin,
            "pid": __import__("os").getpid(),
            "started_at": self.started_at,
            "dry_run": self.dry_run,
            "python": sys.version.split()[0],
            "recent": self._recent[-20:],
        }

    # --- powloka
    def shell_lock(self, params: dict) -> dict:
        settings = params.get("settings") or {}
        hardcore = bool(params.get("hardcore", False))
        dry = self.dry_run or bool(params.get("dry_run", False))
        report = self._report(applied=[], warnings=[], errors=[])
        shell_state = lockstate.ShellState()
        try:
            desktop = _mod("focuslock.shell.desktop")
            if settings.get("black_wallpaper", True):
                captured = desktop.capture()
                shell_state.wallpaper = getattr(captured, "wallpaper", "")
                shell_state.wallpaper_style = getattr(captured, "wallpaper_style", 10)
                # Stan SPRZED lockdownu (capture), nie docelowy - inaczej restore()
                # nie przywrocilby ikon pulpitu.
                shell_state.icons_hidden = bool(getattr(captured, "icons_hidden", False))
                res = desktop.apply_black(
                    dry_run=dry, hide_icons=bool(settings.get("hide_desktop_icons", True))
                )
                report["applied"].extend(res.get("applied", []))
                report["warnings"].extend(res.get("warnings", []))
                report["errors"].extend(res.get("errors", []))
        except Exception as exc:
            report["errors"].append(f"tapeta/pulpit: {exc}")
        try:
            taskbar = _mod("focuslock.shell.taskbar")
            taskbar_mode = taskbar_mode_from(settings)
            shell_state.taskbar_mode = taskbar_mode
            if taskbar_mode == "hide":
                res = taskbar.hide(dry_run=dry)
                report["applied"].extend(res.get("applied", []))
                report["warnings"].extend(res.get("warnings", []))
                report["errors"].extend(res.get("errors", []))
                shell_state.taskbar_hidden = True
            elif taskbar_mode == "filtered":
                # Pasek ma zostac widoczny. Gdy poprzednia sesja go ukryla, wracamy
                # do stanu widocznego, a przyciski nieuzywanych aplikacji usuwa GUI
                # (focuslock/shell/taskbarfilter.py - nie wymaga uprawnien admina).
                if taskbar.is_hidden():
                    res = taskbar.show(dry_run=dry)
                    report["applied"].extend(res.get("applied", []))
                    report["warnings"].extend(res.get("warnings", []))
                    report["errors"].extend(res.get("errors", []))
                shell_state.taskbar_hidden = False
                report["applied"].append("taskbar.mode:filtered")
            else:
                shell_state.taskbar_hidden = taskbar.is_hidden()
                report["applied"].append("taskbar.mode:keep")
        except Exception as exc:
            report["errors"].append(f"pasek zadan: {exc}")
        try:
            if settings.get("mute_toasts", True):
                # Zapamietujemy wartosc sprzed sesji, zeby przywrocic DOKLADNIE ja
                # (uzytkownik mogl miec powiadomienia wylaczone na stale).
                shell_state.toasts_previous = read_toasts_value()
                res = _mod("focuslock.shell.dnd").mute_toasts(True, dry_run=dry)
                report["applied"].extend(res.get("applied", []))
                report["warnings"].extend(res.get("warnings", []))
                shell_state.toasts_muted = True
            if settings.get("prevent_sleep", True):
                res = _mod("focuslock.shell.dnd").prevent_sleep(True, dry_run=dry)
                shell_state.sleep_prevented = True
                report["warnings"].extend(res.get("warnings", []))
        except Exception as exc:
            report["errors"].append(f"powiadomienia/usypianie: {exc}")
        try:
            if settings.get("block_hotkeys", True):
                hotkeys = _mod("focuslock.shell.hotkeys")
                blocker_kwargs = {
                    "on_emergency": lambda how: self.emit("exit_request", {"how": how}),
                    "emergency_enabled": bool(settings.get("emergency_hold_key", True)),
                }
                try:
                    import inspect

                    params = inspect.signature(hotkeys.HotkeyBlocker).parameters
                    if "on_exit" in params:
                        blocker_kwargs["on_exit"] = lambda: self.emit("exit_request", {"how": "pin"})
                except Exception:
                    pass
                self._hotkeys = hotkeys.HotkeyBlocker(**blocker_kwargs)
                res = self._hotkeys.install()
                report["applied"].extend(res.get("applied", []))
                report["warnings"].extend(res.get("warnings", []))
                report["errors"].extend(res.get("errors", []))
                shell_state.hotkeys_blocked = True
        except Exception as exc:
            report["errors"].append(f"hooki klawiatury: {exc}")
        if hardcore:
            res = self.taskmgr_set({"disabled": True, "dry_run": dry})
            report["applied"].extend(res.get("applied", []))
            shell_state.taskmgr_disabled = bool(res.get("ok"))

        lockstate.update(
            shell={
                "wallpaper": shell_state.wallpaper,
                "wallpaper_style": shell_state.wallpaper_style,
                "icons_hidden": shell_state.icons_hidden,
                "taskbar_hidden": shell_state.taskbar_hidden,
                "taskbar_mode": shell_state.taskbar_mode,
                "hotkeys_blocked": shell_state.hotkeys_blocked,
                "toasts_muted": shell_state.toasts_muted,
                "sleep_prevented": shell_state.sleep_prevented,
                "taskmgr_disabled": shell_state.taskmgr_disabled,
            },
            hardcore=hardcore,
            active=True,
        )
        report["ok"] = not report["errors"]
        return report

    def shell_unlock(self, params: dict) -> dict:
        dry = self.dry_run or bool(params.get("dry_run", False))
        return self._release_shell(dry=dry)

    def _release_shell(self, *, dry: bool) -> dict:
        report = self._report()
        state = lockstate.read()
        try:
            # Najpierw przywracamy przyciski paska zadan (filtrowanie z sesji),
            # potem sam pasek - kolejnosc jest bezpieczna takze przy braku filtra.
            res = _mod("focuslock.shell.taskbarfilter").restore(dry_run=dry)
            report["applied"].extend(res.get("applied", []))
            report["warnings"].extend(res.get("warnings", []))
            report["errors"].extend(res.get("errors", []))
        except Exception as exc:
            report["warnings"].append(f"filtrowanie paska zadan: {exc}")
        try:
            res = _mod("focuslock.shell.taskbar").show(dry_run=dry)
            report["applied"].extend(res.get("applied", []))
            report["errors"].extend(res.get("errors", []))
        except Exception as exc:
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
                    dry_run=dry,
                )
            else:
                res = desktop.restore(desktop.DesktopState(), dry_run=dry)
            report["applied"].extend(res.get("applied", []))
            report["warnings"].extend(res.get("warnings", []))
            report["errors"].extend(res.get("errors", []))
        except Exception as exc:
            report["errors"].append(f"pulpit: {exc}")
        try:
            dnd = _mod("focuslock.shell.dnd")
            previous = state.shell.toasts_previous if state is not None else -1
            if previous in (0, 1):
                # Dokladne odtworzenie stanu powiadomien sprzed sesji.
                res = set_toasts_raw(previous, dry_run=dry)
                report["applied"].extend(res.get("applied", []))
                report["warnings"].extend(res.get("warnings", []))
                report["errors"].extend(res.get("errors", []))
            else:
                res = dnd.mute_toasts(False, dry_run=dry)
                report["applied"].extend(res.get("applied", []))
            dnd.prevent_sleep(False, dry_run=dry)
            dnd.mute_sound(False, dry_run=dry)
        except Exception as exc:
            report["warnings"].append(f"powiadomienia: {exc}")
        try:
            res = self.taskmgr_set({"disabled": False, "dry_run": dry})
            report["applied"].extend(res.get("applied", []))
            report["warnings"].extend(res.get("warnings", []))
            if not res.get("ok"):
                report["warnings"].append("menedzer zadan: " + "; ".join(res.get("errors", [])))
        except Exception as exc:
            report["warnings"].append(f"menedzer zadan: {exc}")
        try:
            hotkeys = _mod("focuslock.shell.hotkeys")
            if hasattr(hotkeys, "force_uninstall"):
                hotkeys.force_uninstall()
            elif self._hotkeys is not None:
                self._hotkeys.uninstall()
            else:
                hotkeys.HotkeyBlocker(emergency_enabled=False).uninstall()
            self._hotkeys = None
        except Exception as exc:
            report["errors"].append(f"hooki: {exc}")
        report["ok"] = not report["errors"]
        return report

    def shell_status(self, params: dict) -> dict:
        state = lockstate.read()
        try:
            hidden = _mod("focuslock.shell.taskbar").is_hidden()
        except Exception:
            hidden = None
        try:
            taskbar_filter = _mod("focuslock.shell.taskbarfilter").status()
        except Exception as exc:  # noqa: BLE001
            taskbar_filter = {"ok": False, "active": False, "errors": [str(exc)]}
        return {
            "ok": True,
            "taskbar_hidden": hidden,
            "taskbar_filter": taskbar_filter,
            "lockstate": state.to_json() if state else None,
        }

    # --- siec
    def _network_obj(self, port: int = 8765):
        networklock = _mod("focuslock.block.networklock")
        if self._network is None:
            self._network = networklock.NetworkLock(
                port=port, dry_run=self.dry_run, on_event=lambda ev, data: self.emit(ev, data)
            )
        return self._network

    def network_enable(self, params: dict) -> dict:
        try:
            net = self._network_obj(int(params.get("port", 8765)))
            mode = str(params.get("mode", "allowlist"))
            dry = self.dry_run or bool(params.get("dry_run", False))
            if mode == "blocklist":
                result = net.enable_blocklist(
                    params.get("block_hosts") or [],
                    browser_exes=params.get("browser_exes") or [],
                    block_browser_direct=bool(params.get("block_browser_direct", False)),
                )
            else:
                result = net.enable_allowlist(
                    params.get("allow_hosts") or [],
                    safe_hosts=params.get("safe_hosts") or [],
                    browser_exes=params.get("browser_exes") or [],
                    block_browser_direct=bool(params.get("block_browser_direct", True)),
                )
            applied_text = " ".join(str(item) for item in (result.get("applied") or [])).lower()
            lockstate.update(
                network={
                    "mode": mode,
                    "proxy_port": int(params.get("port", 8765)),
                    "firewall_profile": "CiszaBlock" if ("advfirewall" in applied_text or "zapora" in applied_text) else "",
                    "hosts_patched": mode == "blocklist" or "hosts" in applied_text,
                },
                active=True,
            )
            _ = dry
            return result
        except Exception as exc:
            return self._missing("focuslock.block.networklock", exc)

    def network_disable(self, params: dict) -> dict:
        try:
            net = self._network_obj(int(params.get("port", 8765)))
            return net.disable()
        except Exception as exc:
            return self._missing("focuslock.block.networklock", exc)

    def network_status(self, params: dict) -> dict:
        try:
            return self._network_obj(int(params.get("port", 8765))).status()
        except Exception as exc:
            return {"ok": False, "active": False, "mode": "", "blocked_attempts": 0, "errors": [str(exc)]}

    # --- procesy
    def guard_start(self, params: dict) -> dict:
        try:
            processes = _mod("focuslock.block.processes")
            rules_allow = [processes.MatchRule.from_dict(r) for r in params.get("allow") or []]
            rules_block = [processes.MatchRule.from_dict(r) for r in params.get("block") or []]
            with self._lock:
                if self._guard is not None:
                    self._guard.stop()
                guard_kwargs = {
                    "action": str(params.get("action", "suspend")),
                    "interval": float(params.get("interval", 0.75)),
                    "dry_run": self.dry_run or bool(params.get("dry_run", False)),
                    "on_event": lambda ev, data: self.emit(ev, data),
                }
                try:
                    import inspect

                    if "allow_parents" in inspect.signature(processes.ProcessGuard).parameters:
                        guard_kwargs["allow_parents"] = params.get("allow_parents") or []
                except Exception:
                    pass
                self._guard = processes.ProcessGuard(rules_allow, rules_block, **guard_kwargs)
                result = self._guard.start()
            try:
                launchwatch = _mod("focuslock.block.launchwatch")
                self._launcher = launchwatch.LaunchWatcher(
                    self._guard, interval=0.5, on_event=lambda ev, data: self.emit(ev, data)
                )
                launch_result = self._launcher.start()
                result.setdefault("applied", []).append(f"launchwatch:{launch_result.get('backend', launch_result.get('applied'))}")
            except Exception as exc:
                result.setdefault("warnings", []).append(f"launchwatch: {exc}")
            lockstate.update(guard_active=True)
            return result
        except Exception as exc:
            return self._missing("focuslock.block.processes", exc)

    def guard_update(self, params: dict) -> dict:
        if self._guard is None:
            return self.guard_start(params)
        try:
            processes = _mod("focuslock.block.processes")
            allow = [processes.MatchRule.from_dict(r) for r in params["allow"]] if params.get("allow") is not None else None
            block = [processes.MatchRule.from_dict(r) for r in params["block"]] if params.get("block") is not None else None
            update_kwargs: dict = {"allow": allow, "block": block, "action": params.get("action")}
            if params.get("allow_parents") is not None:
                try:
                    import inspect

                    if "allow_parents" in inspect.signature(self._guard.update).parameters:
                        update_kwargs["allow_parents"] = params.get("allow_parents")
                except Exception:
                    pass
            return self._guard.update(**update_kwargs)
        except Exception as exc:
            return self._missing("focuslock.block.processes", exc)

    def guard_stop(self, params: dict) -> dict:
        report = self._report()
        try:
            if self._launcher is not None:
                self._launcher.stop()
                self._launcher = None
        except Exception as exc:
            report["warnings"].append(f"launchwatch: {exc}")
        try:
            if self._guard is not None:
                res = self._guard.stop()
                report["applied"].extend(res.get("applied", []))
                report["errors"].extend(res.get("errors", []))
                self._guard = None
        except Exception as exc:
            report["errors"].append(f"guard: {exc}")
        lockstate.update(guard_active=False)
        report["ok"] = not report["errors"]
        return report

    def guard_stats(self, params: dict) -> dict:
        if self._guard is None:
            return {"ok": True, "blocked": 0, "suspended": 0, "killed": 0, "skipped": 0, "recent": []}
        try:
            return self._guard.stats()
        except Exception as exc:
            return {"ok": False, "errors": [str(exc)], "blocked": 0, "suspended": 0, "killed": 0, "recent": []}

    def process_list(self, params: dict) -> dict:
        try:
            processes = _mod("focuslock.block.processes")
            items = []
            for info in processes.list_processes():
                if processes.is_system_safe(info.name):
                    continue
                items.append({"pid": info.pid, "name": info.name, "exe": info.exe})
            items.sort(key=lambda x: x["name"])
            return {"ok": True, "processes": items}
        except Exception as exc:
            return self._missing("focuslock.block.processes", exc)

    def process_action(self, params: dict) -> dict:
        import psutil

        pid = int(params.get("pid", 0))
        action = str(params.get("action", "kill"))
        if self.dry_run:
            return self._report(applied=[f"{action}:{pid} (dry-run)"])
        try:
            proc = psutil.Process(pid)
            if action == "kill":
                proc.kill()
            elif action == "suspend":
                proc.suspend()
            elif action == "resume":
                proc.resume()
            else:
                return self._report(ok=False, errors=[f"nieznana akcja: {action}"])
            return self._report(applied=[f"{action}:{pid}"])
        except Exception as exc:
            return self._report(ok=False, errors=[str(exc)])

    # --- system
    def taskmgr_set(self, params: dict) -> dict:
        disabled = bool(params.get("disabled", True))
        dry = self.dry_run or bool(params.get("dry_run", False))
        if dry:
            return self._report(applied=[f"DisableTaskMgr={int(disabled)} (dry-run)"])
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1 if disabled else 0)
            return self._report(applied=[f"DisableTaskMgr={int(disabled)}"])
        except Exception as exc:
            return self._report(ok=False, errors=[str(exc)])

    def dnd_set(self, params: dict) -> dict:
        enabled = bool(params.get("enabled", True))
        dry = self.dry_run or bool(params.get("dry_run", False))
        try:
            dnd = _mod("focuslock.shell.dnd")
            report = self._report()
            for name, call in (("toasts", dnd.mute_toasts), ("sound", dnd.mute_sound), ("sleep", dnd.prevent_sleep)):
                try:
                    res = call(enabled, dry_run=dry)
                    report["applied"].extend(res.get("applied", []))
                    report["warnings"].extend(res.get("warnings", []))
                    report["errors"].extend(res.get("errors", []))
                except Exception as exc:
                    report["warnings"].append(f"{name}: {exc}")
            report["ok"] = not report["errors"]
            return report
        except Exception as exc:
            return self._missing("focuslock.shell.dnd", exc)

    def hotkeys_set(self, params: dict) -> dict:
        enabled = bool(params.get("enabled", True))
        try:
            hotkeys = _mod("focuslock.shell.hotkeys")
            if not enabled:
                if hasattr(hotkeys, "force_uninstall"):
                    return hotkeys.force_uninstall()
                if self._hotkeys is not None:
                    res = self._hotkeys.uninstall()
                    self._hotkeys = None
                    return res
                return hotkeys.HotkeyBlocker(emergency_enabled=False).uninstall()
            if self._hotkeys is None:
                self._hotkeys = hotkeys.HotkeyBlocker(
                    on_emergency=lambda how: self.emit("exit_request", {"how": how}),
                    emergency_enabled=bool(params.get("emergency", True)),
                )
            return self._hotkeys.install()
        except Exception as exc:
            return self._missing("focuslock.shell.hotkeys", exc)

    def restore_all(self, params: dict) -> dict:
        dry = self.dry_run or bool(params.get("dry_run", False))
        report = self._report()
        try:
            self.guard_stop({"dry_run": dry})
        except Exception as exc:
            report["warnings"].append(f"guard: {exc}")
        try:
            res = self.network_disable({"dry_run": dry})
            report["errors"].extend(res.get("errors", []))
        except Exception as exc:
            report["warnings"].append(f"siec: {exc}")
        try:
            res = self._release_shell(dry=dry)
            report["applied"].extend(res.get("applied", []))
            report["errors"].extend(res.get("errors", []))
        except Exception as exc:
            report["errors"].append(f"powloka: {exc}")
        try:
            from . import recovery

            res = recovery.restore_everything(dry_run=dry, reason=str(params.get("reason", "helper")))
            report["applied"].extend(res.get("applied", []))
        except Exception as exc:
            report["warnings"].append(f"recovery: {exc}")
        report["ok"] = not report["errors"]
        return report

    def shutdown(self, params: dict) -> dict:
        report = self.restore_all({})
        threading.Timer(0.5, lambda: self.stop()).start()
        return report

    # ---------------------------------------------------------------------- start
    def build_server(self) -> ipc.RpcServer:
        self.server = ipc.RpcServer(
            paths.rpc_pipe_name(),
            self.authkey,
            {name: (lambda params, fn=fn: fn(params)) for name, fn in self._handlers.items()},
            token=self.token,
            logger=lambda msg: self.emit("helper_log", {"message": msg}),
        )
        return self.server

    def stop(self) -> None:
        try:
            self.guard_stop({})
        except Exception:
            pass
        if self.server is not None:
            self.server.stop()


def main(argv: Optional[list[str]] = None) -> int:
    import os

    parser = argparse.ArgumentParser(prog="focuslock --helper")
    parser.add_argument("--token", default=os.environ.get("CISZA_HELPER_TOKEN", ""))
    parser.add_argument("--authkey", default=os.environ.get("CISZA_HELPER_AUTHKEY", ""))
    parser.add_argument("--dry-run", action="store_true")
    args, _ = parser.parse_known_args(argv or [])

    if not args.token or not args.authkey:
        print("Cisza helper: brak tokenu/klucza (CISZA_HELPER_TOKEN, CISZA_HELPER_AUTHKEY)", file=sys.stderr)
        return 3

    helper = Helper(args.token, bytes.fromhex(args.authkey), dry_run=args.dry_run)
    server = helper.build_server()
    try:
        server.start()
    except Exception as exc:  # np. inna instancja juz nasluchuje
        print(f"Cisza helper: nie udalo sie uruchomic serwera RPC: {exc}", file=sys.stderr)
        return 2
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        helper.stop()
    return 0


def set_dpi_awareness() -> None:
    """Helper nie rysuje UI, ale unikamy skalowania okien systemowych."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
