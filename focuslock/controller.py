"""Kontroler aplikacji: spina sesje, ekonomie, blokady i UI.

To jedyne miejsce, ktore decyduje o zakladaniu i zdejmowaniu blokad.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import appcatalog, config, lockstate, paths, recovery, sitecatalog
from .config import SAFE_HOSTS, SYSTEM_SAFE_PROCESSES, Settings
from .helperclient import HelperBridge
from .store import Store

BROWSER_EXES = ("chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe", "iexplore.exe")


def _try_import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001
        return None


class Controller:
    """Logika aplikacji niezalezna od konkretnych widgetow.

    Sygnaly wywolywane sa przez app.py (ktory posiada QTimer); kontroler nie
    wymaga dzialajacego QApplication.
    """

    def __init__(
        self,
        store: Store,
        settings: Settings,
        *,
        bridge: Optional[HelperBridge] = None,
        emit: Optional[Callable[[str, dict], None]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.emit_hook = emit or (lambda ev, data: None)
        self.log = log or (lambda msg: None)
        self.bridge = bridge or HelperBridge(dry_run=settings.system.dry_run, logger=self.log)
        self.window = None
        self._overlay = None
        self._locked = False
        # Reguly dozwolonych aplikacji z biezacej sesji - z nich korzysta filtr
        # paska zadan (focuslock/shell/taskbarfilter.py).
        self._taskbar_rules: list[dict] = []
        self._taskbar_filter_state = False
        self._taskbar_filter_report: dict = {}
        self.blocked_attempts = 0
        self.last_events: list[dict] = []
        self.last_guard_stats: dict = {}
        self._free_budget = 0
        self._watchdog_proc: Optional[subprocess.Popen] = None

        economy_mod = _try_import("focuslock.economy")
        self.economy = economy_mod.Economy(store, settings) if economy_mod else None

        session_mod = _try_import("focuslock.session")
        self.session_mod = session_mod
        if session_mod is not None:
            self.engine = session_mod.SessionEngine(
                on_tick=self._on_tick,
                on_phase=self._on_phase,
                on_pomodoro_end=self._on_pomodoro_end,
                on_finish=self._on_finish,
            )
            self.Plan = session_mod.Plan
        else:
            self.engine = None
            self.Plan = None

    # ------------------------------------------------------------------ konfiguracja
    @property
    def dry_run(self) -> bool:
        return bool(self.settings.system.dry_run)

    @property
    def safe_mode(self) -> bool:
        return bool(self.settings.system.safe_mode)

    def attach_window(self, widget) -> None:
        self.window = widget

    # --------------------------------------------------------------------- allowlisty
    def study_rules(self, allow_names: Optional[list[str]] = None) -> list[dict]:
        """Reguly dopasowania procesow dozwolonych w nauce (z bazy + wyboru w kreatorze).

        Wybor z kreatora rozszerzamy o procesy towarzyszace (np. wybor Edge
        dozwala takze jego proxy/identity helper) - inaczej guard wstrzyma
        czesc aplikacji i uzytkownik zobaczy, ze "wybrana aplikacja nie dziala".
        """
        rules: list[dict] = []
        for profile in self.store.list_app_profiles("STUDY"):
            rules.append({"kind": profile["match_kind"], "value": profile["match_value"], "label": profile["label"]})
        labels = self._app_label_map()
        expanded = appcatalog.expand_with_companions(allow_names or [])
        for name in expanded:
            key = str(name).strip().lower()
            if not key:
                continue
            candidate = {"kind": "name", "value": key, "label": labels.get(key, key)}
            if candidate not in rules:
                rules.append(candidate)
        return rules

    def _app_label_map(self) -> dict[str, str]:
        """Mapa proces -> czytelna nazwa z katalogu (tylko cache, bez skanu).

        Pelny skan katalogu trwa kilkanascie sekund - nie moze blokowac startu
        sesji. Gdy cache nie istnieje, etykieta to po prostu nazwa procesu.
        """
        mapping: dict[str, str] = {}
        try:
            apps = appcatalog.load_cache() or []
        except Exception:
            apps = []
        for app in apps:
            if app.exe:
                mapping.setdefault(app.exe.lower(), app.name)
        return mapping

    def block_rules(self) -> list[dict]:
        seen = set()
        rules = []
        for p in self.store.list_app_profiles("BLOCKED") + self.store.list_app_profiles("BLOCK"):
            key = (p["match_kind"], p["match_value"].lower())
            if key not in seen:
                seen.add(key)
                rules.append({"kind": p["match_kind"], "value": p["match_value"], "label": p["label"]})
        return rules

    def study_hosts(self, extra: Optional[list[str]] = None) -> list[str]:
        hosts = [p["host"] for p in self.store.list_site_profiles("STUDY")]
        for host in sitecatalog.hosts_from_names(list(extra or [])):
            if host and host not in hosts:
                hosts.append(host)
        for safe in self.settings.network.safe_hosts or list(SAFE_HOSTS):
            if safe not in hosts:
                hosts.append(safe)
        return hosts

    def normalize_apps(self, items: Optional[list] = None) -> list[str]:
        """Zamienia wybor z UI na nazwy procesow.

        Obsluguje trzy postacie wpisu: "Nazwa (proces.exe)", "proces.exe",
        "Nazwa z katalogu" (rozwiązywaną przez katalog aplikacji).
        """
        resolved: list[str] = []
        for item in items or []:
            raw = str(item).strip()
            if not raw:
                continue
            match = re.search(r"\(([^()]+\.exe)\)\s*$", raw, re.IGNORECASE)
            candidate = match.group(1) if match else raw
            candidate = candidate.strip().strip('"')
            if "\\" in candidate or "/" in candidate:
                candidate = os.path.basename(candidate)
            if not candidate.lower().endswith(".exe"):
                found = self._lookup_app(candidate)
                candidate = found or candidate
            candidate = candidate.lower()
            if candidate and candidate not in resolved:
                resolved.append(candidate)
        return resolved

    def _lookup_app(self, label: str) -> str:
        try:
            matches = appcatalog.search(label)
        except Exception:
            return ""
        for app in matches:
            if app.exe and app.name.strip().lower() == label.strip().lower():
                return app.exe
        for app in matches:
            if app.exe:
                return app.exe
        return ""

    def _label_for_app(self, exe: str) -> str:
        """Czytelna nazwa dla pliku .exe (z katalogu, a jak brak - z nazwy pliku)."""
        try:
            for app in appcatalog.get_catalog():
                if app.exe.lower() == exe.lower():
                    return app.name
        except Exception:
            pass
        return appcatalog.prettify_process_name(exe)

    def break_hosts(self) -> list[str]:
        return list(self.settings.network.blocklist)

    # ----------------------------------------------------------------------- sesje
    @staticmethod
    def _payload_list(payload: dict, names: tuple[str, ...]) -> list:
        """Zbiera liste wyboru z payloadu - zarowno z wierzchu, jak i z `allowlist`."""
        collected: list = []
        nested = payload.get("allowlist") if isinstance(payload.get("allowlist"), dict) else {}
        for source in (payload, nested):
            for name in names:
                for item in source.get(name) or []:
                    collected.append(item)
        return collected

    def build_plan(self, payload: dict):
        if self.Plan is None:
            return None
        mode = str(payload.get("mode", "STUDY"))
        study_minutes = int(payload.get("study_minutes", self.settings.session.study_minutes))
        break_minutes = int(payload.get("break_minutes", self.settings.session.break_minutes))
        long_break = int(payload.get("long_break_minutes", self.settings.session.long_break_minutes))
        pomodoros = int(payload.get("pomodoros", 0))
        free_minutes = int(payload.get("free_minutes", 0))
        apps_selection = self._payload_list(payload, ("apps", "study_apps"))
        if not apps_selection and self.store is not None and mode == "STUDY":
            profiles = self.store.list_app_profiles("STUDY")
            apps_selection = [p["match_value"] for p in profiles if p.get("match_value")]
            if not apps_selection:
                presets = self.store.list_presets()
                if presets:
                    apps_selection = presets[0].get("payload", {}).get("apps") or presets[0].get("payload", {}).get("study_apps") or []

        sites_selection = self._payload_list(payload, ("sites", "study_sites"))
        if not sites_selection and self.store is not None and mode == "STUDY":
            profiles = self.store.list_site_profiles("STUDY")
            sites_selection = [p["host"] for p in profiles if p.get("host")]

        break_selection = self._payload_list(payload, ("break_sites",))
        block_selection = self._payload_list(payload, ("block_sites", "blocked_sites"))
        return self.Plan(
            mode=mode,
            study_seconds=max(1, study_minutes) * 60,
            break_seconds=max(0, break_minutes) * 60,
            long_break_seconds=max(0, long_break) * 60,
            long_break_every=max(1, int(self.settings.session.long_break_every)),
            arm_seconds=int(self.settings.session.arming_seconds),
            free_seconds=max(0, free_minutes) * 60,
            tag=str(payload.get("tag", "")),
            goal_note=str(payload.get("goal_note", "")),
            allowlist={
                "apps": self.normalize_apps(apps_selection),
                "sites": sitecatalog.hosts_from_names(sites_selection),
                "break_sites": sitecatalog.hosts_from_names(break_selection),
                "block_sites": sitecatalog.hosts_from_names(block_selection),
                "pomodoros": pomodoros,
            },
        )

    def start_study(self, payload: dict) -> dict:
        if self.engine is None:
            return {"ok": False, "errors": ["silnik sesji niedostepny (focuslock/session.py)"]}
        plan = self.build_plan({**payload, "mode": "STUDY"})
        hardcore = self.settings.hardcore
        session_id = self.store.start_session(
            "STUDY",
            plan.study_seconds * max(1, int(payload.get("pomodoros", 1) or 1)),
            tag=plan.tag,
            goal_note=plan.goal_note,
            allowlist=plan.allowlist,
            hardcore=hardcore,
        )
        state = self.engine.start(plan, session_id)
        report = self._lockdown(plan, session_id)
        self.store.add_event(session_id, "SESSION_START", {"plan": self._plan_dict(plan), "hardcore": hardcore})
        self._emit("session_started", {"session_id": session_id, "state": state})
        return {
            "ok": True,
            "session_id": session_id,
            "state": state,
            "allowlist": plan.allowlist,
            "warnings": report.get("warnings", []),
        }

    def start_free(self, minutes: int, tag: str = "wolne", goal: str = "") -> dict:
        if self.engine is None:
            return {"ok": False, "errors": ["silnik sesji niedostepny"]}
        balance = self.economy.balance() if self.economy else 0
        seconds = max(0, int(minutes)) * 60
        minimum = int(self.settings.economy.min_free_block_minutes) * 60
        if seconds < minimum:
            return {"ok": False, "errors": [f"minimalny blok wolnego to {minimum // 60} min"]}
        if balance < seconds:
            return {"ok": False, "errors": [f"za malo w banku: {balance // 60} min < {seconds // 60} min"]}
        plan = self.build_plan({"mode": "FREE", "free_minutes": minutes, "tag": tag, "goal_note": goal})
        session_id = self.store.start_session("FREE", seconds, tag=tag, goal_note=goal, allowlist={}, hardcore=False)
        spent = self.economy.spend_free(seconds, session_id) if self.economy else 0
        self._free_budget = seconds
        state = self.engine.start(plan, session_id)
        self._release(session_id, reason="free_start")  # tryb wolny = bez lockdownu
        self.store.add_event(session_id, "FREE_START", {"seconds": seconds, "spent": spent})
        self._emit("free_started", {"session_id": session_id, "spent": spent, "state": state})
        return {"ok": True, "session_id": session_id, "spent": spent, "state": state}

    def request_end(self, reason: str = "user") -> dict:
        """Uzytkownik chce zakonczyc sesje.

        reason:
          "user"    - przycisk/akcja w GUI (PIN, jesli ustawiony),
          "panic"   - awaryjne przytrzymanie kombinacji; PIN tylko gdy
                      ustawienie lock.panic_requires_pin jest wlaczone,
          "auto"    - koniec zaplanowany przez licznik (bez PIN-u).
        """
        if self.engine is None:
            return {"ok": False, "errors": ["brak sesji"]}
        state = self.engine.state()
        if state.get("phase") in ("IDLE", "DONE"):
            return {"ok": False, "errors": ["brak aktywnej sesji"]}

        panic_without_pin = reason == "panic" and not self.settings.lock.panic_requires_pin
        if panic_without_pin:
            session_id = state.get("session_id")
            self.store.add_event(session_id, "PANIC_EXIT", {"how": "hold", "pin": False})
            return self._finish("panic")

        if self.settings.hardcore:
            # Bez "requires_pin": w hardcore zwykle zakonczenie jest niedostepne,
            # a okno PIN-u tylko dezorientowalo ("nic sie nie dzieje").
            return {
                "ok": False,
                "hardcore": True,
                "errors": [
                    "tryb hardcore: sesja konczy sie dopiero po zaplanowanym czasie "
                    "(awaryjnie: Ctrl+Alt+Shift+X albo przytrzymanie Ctrl+Alt+Shift+Q)"
                ],
            }
        if self.settings.has_pin and reason != "auto":
            return {"ok": True, "requires_pin": True}
        return self._finish(reason)

    def confirm_end(self, pin: str, reason: str = "user") -> dict:
        if not self.settings.check_pin(pin):
            session_id = self.engine.state().get("session_id") if self.engine else None
            self.store.add_event(session_id, "ATTEMPT_EXIT", {"how": "pin", "ok": False})
            self._emit("pin_rejected", {})
            return {"ok": False, "errors": ["nieprawidlowy PIN"]}
        return self._finish(reason)

    # ------------------------------------------------------------------------ tick
    def tick(self) -> dict:
        if self.engine is None:
            return {}
        state = self.engine.tick()
        self._touch_heartbeat()
        self._refresh_overlay()
        if self._locked:
            result = self.refresh_taskbar_filter()
            if result.get("errors"):
                self.log("filtr paska zadan: " + "; ".join(str(e) for e in result["errors"])[:200])
        self._collect_guard_events()
        self._emit("tick", state)
        if state.get("phase") == "DONE":
            self._finish(state.get("finish_reason") or "auto")
        return state

    def _touch_heartbeat(self) -> None:
        try:
            watchdog = _try_import("focuslock.shell.watchdog")
            if watchdog is not None:
                watchdog.heartbeat_touch()
            else:
                path = paths.heartbeat_path()
                tmp = path.with_suffix(".tmp")
                tmp.write_text(str(time.time()), encoding="utf-8")
                tmp.replace(path)
        except Exception:
            pass

    def _ensure_helper(self) -> dict:
        """Uruchamia helper z UAC (guard, siec i powloka dzialaja tylko tam)."""
        try:
            ok = bool(self.bridge.ensure())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "applied": [], "warnings": [], "errors": [f"helper: {type(exc).__name__}: {exc}"]}
        if ok:
            return {"ok": True, "applied": ["helper:online"], "warnings": [], "errors": []}
        detail = getattr(self.bridge, "last_error", "") or "brak polaczenia"
        return {
            "ok": False,
            "applied": [],
            "warnings": [],
            "errors": [
                "helper (UAC) nie wystartowal: "
                + str(detail)
                + " - blokada procesow, sieci i skrotow nie zadziala"
            ],
        }

    def _refresh_overlay(self) -> None:
        if self._overlay is None:
            return
        try:
            self._overlay.refresh()
        except Exception:
            pass

    # ---------------------------------------------------- pasek zadan (filtrowanie)
    def refresh_taskbar_filter(self, *, force: bool = False) -> dict:
        """Zostawia na pasku zadan tylko przyciski dozwolonych aplikacji.

        Wolane po lockdownie i co tick sesji (modul sam sie dlawi do 1 s).
        Bez allowlisty nic nie robimy - inaczej zniknelyby wszystkie przyciski.
        """
        mode = str(getattr(self.settings.lock, "taskbar_mode", "filtered") or "filtered")
        if self.safe_mode or mode != "filtered":
            return {"ok": True, "skipped": True, "reason": f"tryb:{mode}"}
        if not self._taskbar_rules:
            return {"ok": True, "skipped": True, "reason": "pusta-allowlista"}
        module = _try_import("focuslock.shell.taskbarfilter")
        if module is None:
            return {"ok": False, "errors": ["brak modulu focuslock.shell.taskbarfilter"]}
        parents: list[str] = []
        if self.settings.lock.allow_child_processes:
            parents = [
                str(rule.get("value") or "")
                for rule in self._taskbar_rules
                if str(rule.get("kind") or "") == "name" and rule.get("value")
            ]
        try:
            result = module.apply(
                self._taskbar_rules,
                parents=parents,
                dry_run=self.dry_run,
                exclude_pids=(os.getpid(),),
                force=force,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
        self._remember_taskbar_filter(mode, module)
        return result

    def _remember_taskbar_filter(self, mode: str, module) -> None:
        """Zapisuje tryb filtra w lockstate (diagnostyka, restore.py --panic)."""
        try:
            active = bool(module.is_active())
        except Exception:  # noqa: BLE001
            return
        if active == self._taskbar_filter_state:
            return
        self._taskbar_filter_state = active
        try:
            lockstate.update(shell={"taskbar_mode": mode, "taskbar_filtered": active})
        except Exception:  # noqa: BLE001
            pass

    def _stop_taskbar_filter(self) -> dict:
        """Przywraca przyciski paska zadan (idempotentne, takze bez aktywnej sesji)."""
        self._taskbar_rules = []
        module = _try_import("focuslock.shell.taskbarfilter")
        if module is None:
            return {"ok": False, "errors": ["brak modulu focuslock.shell.taskbarfilter"]}
        try:
            result = module.restore(dry_run=self.dry_run)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
        self._remember_taskbar_filter("", module)
        return result

    def _collect_guard_events(self) -> None:
        events = self.bridge.drain_events()
        for item in events:
            self.last_events.append(item)
            self.last_events = self.last_events[-100:]
            if item["event"] == "blocked_app":
                self.blocked_attempts += 1
                session_id = self._session_id()
                self.store.add_event(session_id, "BLOCKED_APP", item["data"])
            elif item["event"] == "blocked_site":
                self.blocked_attempts += 1
                session_id = self._session_id()
                self.store.add_event(session_id, "BLOCKED_SITE", item["data"])
            elif item["event"] == "exit_request":
                self._emit("exit_request", item["data"])
            self._emit(item["event"], item["data"])

    def _session_id(self) -> Optional[int]:
        if self.engine is None:
            return None
        return self.engine.state().get("session_id")

    # ------------------------------------------------------------------- lockdown
    def _plan_dict(self, plan) -> dict:
        return {
            "mode": getattr(plan, "mode", ""),
            "study_seconds": getattr(plan, "study_seconds", 0),
            "break_seconds": getattr(plan, "break_seconds", 0),
            "tag": getattr(plan, "tag", ""),
        }

    def _lockdown(self, plan, session_id: int) -> dict:
        if self.safe_mode:
            self.log("tryb awaryjny (--safe): pomijam lockdown powloki i sieci")
            self._emit("lockdown_skipped", {"reason": "safe_mode"})
            return {"ok": True, "skipped": True}
        report = {"ok": True, "steps": []}
        if not self.dry_run and self.settings.system.launch_helper_on_start and not self.bridge.online:
            # Guard procesow, siec i powloka dzialaja w helperze z UAC. Bez niego
            # "hard" nie blokuje niczego poza paskiem zadan, ktory pilnuje GUI.
            helper_report = self._ensure_helper()
            report["steps"].append({"step": "helper", "result": helper_report})
            if not helper_report.get("ok"):
                message = str(helper_report["errors"][0])
                report.setdefault("warnings", []).append(message)
                self._emit("lockdown_warning", {"message": message})
        self._locked = True
        lock_cfg = self.settings.lock
        settings_payload = {
            "black_wallpaper": lock_cfg.black_wallpaper,
            "hide_desktop_icons": lock_cfg.hide_desktop_icons,
            "hide_taskbar": lock_cfg.hide_taskbar,
            "taskbar_mode": str(getattr(lock_cfg, "taskbar_mode", "filtered") or "filtered"),
            "block_hotkeys": lock_cfg.block_hotkeys,
            "mute_toasts": lock_cfg.mute_toasts,
            "prevent_sleep": lock_cfg.prevent_sleep,
            "emergency_hold_key": lock_cfg.emergency_hold_key,
        }
        shell_res = self.bridge.call(
            "shell_lock",
            settings=settings_payload,
            hardcore=self.settings.hardcore,
            dry_run=self.dry_run,
        )
        report["steps"].append({"step": "shell", "result": shell_res})

        allow = self.study_rules(plan.allowlist.get("apps"))
        report["allowlist_apps"] = list(plan.allowlist.get("apps") or [])
        self._taskbar_rules = list(allow)
        if not allow:
            # Bezpiecznik: pusta lista dozwolonych = zablokowanie WSZYSTKIEGO.
            # Nie uruchamiamy wtedy guardu procesow i wyraznie to sygnalizujemy.
            warning = "Nie wybrano zadnej aplikacji - guard procesow nie zostal uruchomiony."
            report["warnings"] = [warning]
            report["steps"].append({"step": "guard", "result": {"ok": True, "skipped": True, "reason": "empty-allowlist"}})
            self.store.add_event(session_id, "GUARD_SKIPPED", {"reason": "empty-allowlist"})
            self._emit("lockdown_warning", {"message": warning})
        else:
            guard_exe = [rule["value"] for rule in allow if rule.get("kind") == "name"]
            guard_params = {
                "allow": allow,
                "block": self.block_rules(),
                "action": "kill" if (self.settings.hardcore and not lock_cfg.suspend_instead_of_kill) else "suspend",
                "interval": 0.75,
                "dry_run": self.dry_run,
            }
            if lock_cfg.allow_child_processes:
                # Skan przodkow w psutil jest kosztowny i potrafi sie zakleszczyc,
                # dlatego wlaczamy go tylko swiadomie (domyslnie wylaczony).
                guard_params["allow_parents"] = guard_exe
            guard_res = self.bridge.call("guard_start", **guard_params)
            report["steps"].append({"step": "guard", "result": guard_res})

        if str(getattr(lock_cfg, "taskbar_mode", "filtered") or "filtered") == "filtered":
            # Pasek zadan zostaje widoczny, ale tylko z przyciskami dozwolonych
            # aplikacji. Bez allowlisty nic nie filtrujemy (inaczej znikneloby
            # wszystko, tak samo jak guard nie startuje przy pustej liscie).
            filter_report = self.refresh_taskbar_filter(force=True)
            self._taskbar_filter_report = filter_report
            report["steps"].append({"step": "taskbar_filter", "result": filter_report})

        network_mode = self.settings.network.mode
        if network_mode in ("allowlist", "blocklist"):
            session_blocked = list(plan.allowlist.get("block_sites") or [])
            net_res = self.bridge.call(
                "network_enable",
                mode=network_mode,
                allow_hosts=self.study_hosts(plan.allowlist.get("sites")),
                block_hosts=list(dict.fromkeys(self.break_hosts() + session_blocked)),
                safe_hosts=self.settings.network.safe_hosts,
                browser_exes=list(BROWSER_EXES),
                block_browser_direct=self.settings.network.block_browser_direct,
                port=self.settings.network.proxy_port,
                dry_run=self.dry_run,
            )
            report["steps"].append({"step": "network", "result": net_res})

        lockstate.update(session_id=session_id, active=True, hardcore=self.settings.hardcore, reason="study")
        self._overlay = self._make_overlay()
        if self._overlay is not None:
            try:
                report["steps"].append({"step": "overlay", "result": self._overlay.lock(self.window)})
            except Exception as exc:  # noqa: BLE001
                report["steps"].append({"step": "overlay", "result": {"ok": False, "errors": [str(exc)]}})
        self._ensure_watchdog()
        self._emit("lockdown_done", report)
        return report

    def _ensure_watchdog(self) -> None:
        if self.safe_mode or self.dry_run:
            return
        if getattr(self, "_watchdog_proc", None) is not None:
            if self._watchdog_proc.poll() is None:
                return
        try:
            from .shell import elevate
            cmd = elevate.python_launch_command(["--watchdog"])
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._watchdog_proc = subprocess.Popen(cmd, creationflags=flags)
        except Exception as exc:  # noqa: BLE001
            self.log(f"nie udalo sie uruchomic watchdoga: {exc}")

    def _make_overlay(self):
        if self.safe_mode:
            return None
        overlay_mod = _try_import("focuslock.shell.overlay")
        if overlay_mod is None:
            return None
        try:
            return overlay_mod.OverlayManager(dry_run=self.dry_run, on_event=lambda ev, data: self._emit(ev, data))
        except Exception as exc:  # noqa: BLE001
            self.log(f"overlay niedostepny: {exc}")
            return None

    def _release(self, session_id: Optional[int], reason: str = "end") -> dict:
        report = {"ok": True, "steps": []}
        if self._overlay is not None:
            try:
                report["steps"].append({"step": "overlay", "result": self._overlay.unlock()})
            except Exception:
                pass
            self._overlay = None
        if not self.safe_mode and self._locked:
            if getattr(self.bridge, "online", False):
                report["steps"].append({"step": "guard", "result": self.bridge.call("guard_stop")})
                report["steps"].append({"step": "network", "result": self.bridge.call("network_disable")})
                report["steps"].append({"step": "shell", "result": self.bridge.call("shell_unlock")})
            else:
                # Helper nie zyje: blokady powloki, procesow i sieci naklada wylacznie
                # helper, wiec nie ma czego cofac. Bez tego zabezpieczenia koniec sesji
                # czekal na kolejne nieudane proby RPC i okno wygladalo na zawieszone.
                report["steps"].append(
                    {"step": "helper", "result": {"ok": True, "skipped": True, "reason": "helper-offline"}}
                )
        report["steps"].append({"step": "taskbar_filter", "result": self._stop_taskbar_filter()})
        report["steps"].append({"step": "cleanup", "result": self._cleanup_leftovers(session_id)})
        self._locked = False
        if not self.dry_run:
            lockstate.clear()
        if getattr(self, "_watchdog_proc", None) is not None:
            try:
                self._watchdog_proc.terminate()
            except Exception:
                pass
            self._watchdog_proc = None
        self._emit("released", {"reason": reason, "session_id": session_id, "report": report})
        return report

    def _cleanup_leftovers(self, session_id: Optional[int]) -> dict:
        """Sprawdza stan systemu po sesji i zglasza to, czego nie udalo sie cofnac.

        Helper z UAC sprzata "od siebie", ale gdy padnie (albo brak admina),
        reguly zapory, proxy, hosts, pasek zadan, tapeta i hooki zostaja. Ten
        krok weryfikuje je i cofa to, co da sie bez uprawnien administratora;
        reszta trafia do `leftovers`, zdarzenia `cleanup_incomplete` (okno
        ostrzezenia) oraz znacznika czytanego przy nastepnym starcie.
        """
        try:
            result = recovery.cleanup_leftovers(dry_run=self.dry_run)
        except Exception as exc:  # noqa: BLE001 - koniec sesji nie moze sie wywalic
            self.log(f"sprzatanie po sesji nieudane: {exc!r}")
            return {
                "ok": False,
                "leftovers": [],
                "applied": [],
                "warnings": [],
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
        leftovers = [str(item) for item in (result.get("leftovers") or [])]
        if leftovers and not self.dry_run:
            payload = {"leftovers": leftovers, "instructions": recovery.leftovers_instructions()}
            self._emit("cleanup_incomplete", payload)
            try:
                self.store.add_event(session_id, "CLEANUP_INCOMPLETE", payload)
            except Exception as exc:  # noqa: BLE001 - brak wpisu w bazie nie blokuje konca sesji
                self.log(f"nie udalo sie zapisac zdarzenia CLEANUP_INCOMPLETE: {exc!r}")
        return result

    # --------------------------------------------------------------------- finanse
    def _on_pomodoro_end(self, info: dict) -> None:
        session_id = self._session_id()
        if session_id is None or self.economy is None:
            return
        result = self.economy.credit_pomodoro(
            int(info.get("study_seconds", 0)), session_id, bool(info.get("completed", False))
        )
        self.store.add_event(session_id, "POMODORO_END", {**info, **result})
        self._emit("pomodoro_end", {**info, **result})

    def _on_phase(self, info: dict) -> None:
        session_id = self._session_id()
        if session_id is not None:
            self.store.add_event(session_id, "PHASE", info)
        phase = str(info.get("phase") or "").upper()
        if self._locked and self.engine and self.engine.plan:
            plan = self.engine.plan
            if phase in ("BREAK", "LONG_BREAK"):
                break_sites = list(plan.allowlist.get("break_sites") or [])
                if break_sites:
                    try:
                        self.bridge.call(
                            "network_enable",
                            mode=self.settings.network.mode,
                            allow_hosts=self.study_hosts(list(plan.allowlist.get("sites") or []) + break_sites),
                            block_hosts=self.break_hosts(),
                            safe_hosts=self.settings.network.safe_hosts,
                            browser_exes=list(BROWSER_EXES),
                            block_browser_direct=self.settings.network.block_browser_direct,
                            port=self.settings.network.proxy_port,
                            dry_run=self.dry_run,
                        )
                    except Exception:
                        pass
            elif phase == "STUDY":
                try:
                    self.bridge.call(
                        "network_enable",
                        mode=self.settings.network.mode,
                        allow_hosts=self.study_hosts(plan.allowlist.get("sites")),
                        block_hosts=list(dict.fromkeys(self.break_hosts() + list(plan.allowlist.get("block_sites") or []))),
                        safe_hosts=self.settings.network.safe_hosts,
                        browser_exes=list(BROWSER_EXES),
                        block_browser_direct=self.settings.network.block_browser_direct,
                        port=self.settings.network.proxy_port,
                        dry_run=self.dry_run,
                    )
                except Exception:
                    pass
        self._emit("phase", info)

    def _on_tick(self, state: dict) -> None:
        pass

    def _on_finish(self, info: dict) -> None:
        self._emit("engine_finish", info)

    def _finish(self, reason: str = "user") -> dict:
        if self.engine is None:
            return {"ok": False, "errors": ["brak sesji"]}
        session_id = self._session_id()
        # UWAGA: engine.finish() zeruje licznik i budzet wolnego czasu,
        # dlatego metryki sesji czytamy PRZED zamknieciem silnika.
        before = dict(self.engine.state())
        state = self.engine.finish(reason)
        summary: dict[str, Any] = {"reason": reason, "state": state}

        if session_id is not None:
            session = self.store.get_session(session_id)
            mode = (session or {}).get("mode", "STUDY")
            study_seconds = int(before.get("study_seconds_done", 0) or 0)
            pomodoros_done = int(before.get("pomodoros_done", 0) or 0)
            pomodoros_aborted = int(before.get("pomodoros_aborted", 0) or 0)
            # `elapsed` w stanie silnika dotyczy BIEZACEJ fazy (np. swiezo rozpoczętej
            # przerwy), wiec dla nauki liczymy faktycznie przepracowany czas.
            if mode == "FREE":
                total = int(before.get("total", 0) or 0)
                unspent = int(before.get("free_seconds_left", 0) or 0)
                if before.get("phase") == "ARMING" or not total:
                    # W fazie odliczania silnik nie zna jeszcze budzetu - bierzemy go z kontrolera.
                    total, unspent = self._free_budget, self._free_budget
                actual_seconds = max(0, (total - unspent) if total else int(before.get("elapsed", 0) or 0))
                if unspent > 0 and self.economy is not None:
                    self.economy.refund(unspent, session_id)
                summary["refunded"] = unspent
                self._free_budget = 0
            else:
                actual_seconds = study_seconds
            self.store.finish_session(
                session_id,
                "COMPLETED"
                if reason in ("auto", "user", "pomodoros_done", "plan_finished", "free_end", "free_done")
                else "ABORTED",
                actual_seconds,
                pomodoros_done,
                pomodoros_aborted,
            )
            summary["actual_seconds"] = actual_seconds
            if mode == "STUDY" and self.economy is not None:
                day_study_seconds = int((self.store.daily_stats() or {}).get("study_seconds", 0) or 0)
                summary["streak"] = self.economy.register_study_day(day_study_seconds)
            summary["economy"] = self.economy.summary() if self.economy else {}
            summary["stats"] = self.store.daily_stats()
        self._release(session_id, reason=reason)
        summary["session_id"] = session_id
        self._emit("session_finished", summary)
        return {"ok": True, **summary}

    # ------------------------------------------------------------------ ustawienia
    def handle_action(self, action: str, payload: Optional[dict] = None) -> dict:
        """Akcje zlecane przez ekrany (sygnał request_action).

        Jedno miejsce, w ktorym UI moze poprosic o cos wiecej niz start/koniec sesji.
        """
        payload = payload or {}
        try:
            if action == "toggle_pause":
                if self.engine is None:
                    return {"ok": False, "errors": ["brak sesji"]}
                state = self.engine.state()
                if state.get("phase") in ("IDLE", "DONE"):
                    return {"ok": False, "errors": ["brak aktywnej sesji"]}
                result = self.engine.resume() if state.get("paused") else self.engine.pause()
                self._emit("paused" if not state.get("paused") else "resumed", {})
                return {"ok": True, "state": result}

            if action in ("start_break", "skip_break"):
                if self.engine is None:
                    return {"ok": False, "errors": ["brak sesji"]}
                if action == "start_break":
                    result = self.engine.start_break()
                else:
                    result = self.engine.skip_break()
                return {"ok": True, "state": result}

            if action == "preset_apply":
                presets = _try_import("focuslock.presets")
                name = str(payload.get("name") or payload.get("preset") or "")
                if presets is None or not name:
                    return {"ok": False, "errors": ["presety niedostepne albo brak nazwy"]}
                result = presets.apply_preset(self.store, name)
                self._emit("preset_applied", result)
                return result

            if action == "save_preset":
                presets = _try_import("focuslock.presets")
                name = str(payload.get("name") or "")
                if presets is None or not name:
                    return {"ok": False, "errors": ["presety niedostepne albo brak nazwy"]}
                if payload.get("delete"):
                    for item in presets.list_presets(self.store):
                        if str(item.get("name") or "") == name:
                            return presets.delete_preset(self.store, item.get("id"))
                    return {"ok": False, "errors": [f"nie ma presetu o nazwie {name!r}"]}
                plan = payload.get("plan") or payload.get("payload") or payload
                return presets.save_preset(self.store, name, plan)

            if action == "rating":
                value = int(payload.get("value", 0) or 0)
                session_id = payload.get("session_id") or self._session_id()
                if not session_id:
                    last = self.store.recent_sessions(1)
                    session_id = last[0]["id"] if last else None
                if not session_id:
                    return {"ok": False, "errors": ["brak sesji do oceny"]}
                self.store.update_session(int(session_id), self_rating=value)
                self.store.add_event(int(session_id), "RATING", {"value": value})
                return {"ok": True, "session_id": int(session_id), "rating": value}

            if action == "set_pin":
                pin = str(payload.get("pin") or "")
                if len(pin) < 4:
                    return {"ok": False, "errors": ["PIN musi miec co najmniej 4 znaki"]}
                return self.set_pin(pin)

            if action in ("refresh_apps", "catalog_apps", "installed_apps"):
                force = bool(payload.get("force")) or action in ("refresh_apps", "installed_apps")
                query = str(payload.get("query", "") or "").strip()
                limit = int(payload.get("limit", 0) or 0)
                apps = appcatalog.search(query, appcatalog.get_catalog(force=force))
                if limit > 0:
                    apps = apps[:limit]
                return {
                    "ok": True,
                    "apps": [app.to_dict() for app in apps],
                    "count": len(apps),
                    "query": query,
                    "cached": not force,
                    "summary": appcatalog.describe(apps),
                }

            if action in ("catalog_sites", "refresh_sites"):
                kind = str(payload.get("kind") or sitecatalog.STUDY).upper()
                if kind not in (sitecatalog.STUDY, sitecatalog.BLOCKED):
                    kind = sitecatalog.STUDY
                query = str(payload.get("query", "") or "").strip()
                try:
                    custom = self.store.list_site_profiles()
                except Exception:
                    custom = []
                payload_out = sitecatalog.catalog_payload_with_custom(custom, kind)
                if query:
                    needle = query.lower()
                    filtered = []
                    for category in payload_out["categories"]:
                        sites = [
                            site
                            for site in category["sites"]
                            if needle in site["name"].lower() or needle in site["host"].lower()
                        ]
                        if sites:
                            filtered.append({**category, "sites": sites})
                    payload_out["categories"] = filtered
                payload_out["kind"] = kind
                payload_out["query"] = query
                return payload_out

            if action == "save_app_selection":
                items = self.normalize_apps(list(payload.get("apps") or []))
                saved = 0
                for exe in items:
                    if not exe:
                        continue
                    label = self._label_for_app(exe)
                    self.store.upsert_app_profile(label, "name", exe, "STUDY")
                    saved += 1
                return {"ok": True, "saved": saved, "apps": items}

            if action == "save_site_selection":
                hosts = sitecatalog.hosts_from_names(list(payload.get("sites") or []))
                category = str(payload.get("category") or "STUDY").upper()
                if category not in ("STUDY", "BLOCKED"):
                    category = "STUDY"
                saved = 0
                for host in hosts:
                    site = sitecatalog.find(host)
                    label = site.name if site else host
                    self.store.upsert_site_profile(host, label, category)
                    saved += 1
                if category == "BLOCKED":
                    # Rozpraszacze trafiaja do listy blokowanej (hosts/proxy), a nie na białą listę.
                    merged = list(dict.fromkeys(list(self.settings.network.blocklist) + hosts))
                    self.settings.network.blocklist = merged
                    self.settings.save(self.store)
                    self.store.log_audit("blocklist_update", None, {"hosts": hosts})
                    return {"ok": True, "saved": saved, "hosts": hosts, "category": category,
                            "blocklist_size": len(merged)}
                return {"ok": True, "saved": saved, "hosts": hosts, "category": category}

            if action == "clear_blocklist":
                self.settings.network.blocklist = list(sitecatalog.default_block_hosts())
                self.settings.save(self.store)
                return {"ok": True, "blocklist_size": len(self.settings.network.blocklist)}

            if action == "subject_suggestions":
                subject = str(payload.get("subject") or "")
                hosts = sitecatalog.suggest_for(subject)
                return {
                    "ok": True,
                    "subject": subject,
                    "hosts": hosts,
                    "sites": [site.to_dict() for site in sitecatalog.search(subject)] or [],
                }

            if action == "onboarding_apps":
                apps = payload.get("apps") or []
                added = 0
                for item in apps:
                    name = str(item if isinstance(item, str) else item.get("name", "")).strip()
                    if not name:
                        continue
                    self.store.upsert_app_profile(name, "name", name.lower(), "STUDY")
                    added += 1
                self.settings.onboarding_done = True
                self.settings.save(self.store)
                return {"ok": True, "added": added}

            if action in ("bank_adjust", "adjust_bank"):
                minutes = int(payload.get("minutes", 0) or 0)
                if minutes == 0:
                    return {"ok": False, "errors": ["podaj liczbe minut"]}
                seconds = minutes * 60
                note = str(payload.get("note", "reczna korekta"))
                if seconds > 0:
                    self.store.adjust_bank(seconds, note=note)
                else:
                    self.store.spend(-seconds, note=note)
                self.store.log_audit("bank_adjust", None, {"minutes": minutes, "note": note})
                return {"ok": True, "balance": self.store.bank_balance(), "minutes": minutes}

            if action == "refresh_bank":
                lots = self.store.lots()
                next_expiry = min((lot["expires_at"] for lot in lots if lot.get("expires_at")), default=0.0)
                return {
                    "ok": True,
                    "balance": self.store.bank_balance(),
                    "earned_today": self.store.earned_today(),
                    "lots": lots,
                    "ledger": self.store.ledger(100),
                    "next_expiry": next_expiry,
                    "economy": self.economy.summary() if self.economy else {},
                }

            if action == "return_free":
                state = self.engine.state() if self.engine else {}
                if state.get("mode") != "FREE" or state.get("phase") in ("IDLE", "DONE"):
                    return {"ok": False, "errors": ["tryb wolny nie jest aktywny"]}
                result = self._finish("user")
                return {**result, "message": f"Zwrócono do banku {int(result.get('refunded', 0) // 60)} min."}

            if action == "extend_free":
                seconds = int(payload.get("seconds", 0) or 0)
                if seconds <= 0:
                    return {"ok": False, "errors": ["podaj dlugosc w sekundach"]}
                return self.extend_free(seconds)

            if action in ("refresh_stats", "stats_period"):
                period = str(payload.get("period") or "tydzien").strip().lower()
                # UI wysyla "today/week/month/quarter", API/dokumentacja: "dzis/tydzien/...".
                days = {
                    "today": 1, "dzis": 1,
                    "week": 7, "tydzien": 7,
                    "month": 30, "miesiac": 30,
                    "quarter": 90, "kwartal": 90,
                }.get(period, 7)
                return {
                    "ok": True,
                    "period": period,
                    "days": days,
                    "series": self.store.daily_series(days),
                    "totals": self.store.totals(),
                    "top_blocked": self.store.top_blocked(),
                    "top_processes": self.store.top_processes(8),
                    "streak": self.store.get_streak("study"),
                }

            if action == "refresh_journal":
                return {"ok": True, "events": self.store.events(limit=300)}

            if action in ("export_stats", "export_journal", "export_data"):
                return self.export_data(kind=action, payload=payload)

            if action in ("backup_db", "backup_database"):
                return self.backup_db()

            if action == "restore_database":
                return self.stage_database_restore(str(payload.get("path") or ""))

            if action == "diagnostics":
                return self.diagnostics()

            if action in ("toggle_autostart", "autostart_status"):
                return self.autostart(payload)

            if action == "reset_settings":
                return self.reset_settings()

            if action in ("open_home", "open_composer", "open_running", "open_break", "open_free",
                          "open_bank", "open_stats", "open_settings", "open_onboarding",
                          "open_summary", "open_journal"):
                # Nawigacje obsluguje okno (app.py); tutaj zwracamy cel dla innych wywolujacych.
                return {"ok": True, "navigate": action[len("open_"):]}

            if action == "test_lock":
                return self.test_lock()

            if action == "restore_everything":
                return self.restore_everything()

            if action == "save_app":
                return self.save_app_profile(payload)

            if action == "save_site":
                return self.save_site_profile(payload)

            if action == "delete_app":
                profile_id = int(payload.get("id") or 0)
                if not profile_id:
                    return {"ok": False, "errors": ["brak id profilu"]}
                self.store.delete_app_profile(profile_id)
                return {"ok": True, "deleted": profile_id}

            if action == "delete_site":
                profile_id = int(payload.get("id") or 0)
                if not profile_id:
                    return {"ok": False, "errors": ["brak id profilu"]}
                self.store.delete_site_profile(profile_id)
                return {"ok": True, "deleted": profile_id}

            if action == "wipe_data":
                if not payload.get("confirm"):
                    return {"ok": False, "errors": ["brak potwierdzenia (zaznacz pole)"]}
                for table in ("session_events", "usage", "bank_ledger", "bank_lots", "sessions", "audit"):
                    self.store.execute(f"DELETE FROM {table}")  # noqa: S608 - nazwy tabel ze stalej listy
                self.store.log_audit("wipe_data", None, {"by": "user"})
                return {"ok": True, "wiped": True, "balance": self.store.bank_balance()}

            if action == "helper_start":
                ok = self.bridge.ensure()
                return {"ok": bool(ok), "helper_online": self.bridge.online, "errors": [] if ok else [self.bridge.last_error]}

            if action in ("kill_process", "suspend_process", "resume_process"):
                pid = int(payload.get("pid", 0) or 0)
                verb = {"kill_process": "kill", "suspend_process": "suspend", "resume_process": "resume"}[action]
                if not pid:
                    return {"ok": False, "errors": ["brak pid"]}
                return self.bridge.call("process_action", pid=pid, action=verb)

            if action == "guard_stats":
                return self.bridge.call("guard_stats")

            return {"ok": False, "errors": [f"nieznana akcja: {action}"]}
        except Exception as exc:  # noqa: BLE001 - akcje UI nie moga wywalac aplikacji
            self.log(f"akcja {action} nieudana: {exc!r}")
            return {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}

    def export_data(self, kind: str = "export_data", payload: Optional[dict] = None) -> dict:
        """Eksport danych do pliku (JSON, a dla statystyk takze CSV) w katalogu danych."""
        import csv
        import json

        wanted_format = str((payload or {}).get("format") or "").strip().lower()
        target_dir = paths.data_dir() / "eksport"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if kind == "export_stats" and wanted_format == "csv":
            target = target_dir / f"statystyki-{stamp}.csv"
            rows = self.store.daily_series(90)
            columns = ["day", "study_seconds", "pomodoros_done", "pomodoros_aborted", "sessions"]
            try:
                with target.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle, delimiter=";")
                    writer.writerow(columns)
                    for row in rows:
                        writer.writerow([row.get(name, 0) for name in columns])
            except OSError as exc:
                return {"ok": False, "errors": [str(exc)]}
            return {"ok": True, "path": str(target), "kind": kind, "format": "csv", "rows": len(rows)}
        if kind == "export_journal":
            target = target_dir / f"dziennik-{stamp}.json"
            payload = {"wygenerowano": time.strftime("%Y-%m-%d %H:%M:%S"), "dziennik": self.store.events(limit=2000)}
        elif kind == "export_stats":
            target = target_dir / f"statystyki-{stamp}.json"
            payload = {
                "wygenerowano": time.strftime("%Y-%m-%d %H:%M:%S"),
                "podsumowanie": self.summary(),
                "seria_dzienna": self.store.daily_series(90),
                "top_blokad": self.store.top_blocked(),
                "top_procesow": self.store.top_processes(20),
                "seria": self.store.get_streak("study"),
            }
        else:
            target = target_dir / f"cisza-{stamp}.json"
            payload = {
                "wygenerowano": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ustawienia": self.settings.to_dict(),
                "podsumowanie": self.summary(),
                "sesje": self.store.recent_sessions(500),
                "bank": self.store.lots(include_empty=True),
                "kronika_banku": self.store.ledger(500),
                "dziennik": self.store.events(limit=500),
            }
        try:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "path": str(target), "kind": kind}

    def backup_db(self) -> dict:
        import shutil

        source = self.store.path
        target = paths.backup_dir() / f"cisza-{time.strftime('%Y%m%d-%H%M%S')}.db"
        try:
            self.store.execute("PRAGMA wal_checkpoint(FULL)")
            shutil.copy2(source, target)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "path": str(target)}

    # ----------------------------------------------------- tryb wolny i katalogi
    def extend_free(self, seconds: int) -> dict:
        """Dokłada minuty z banku do trwajacego trybu wolnego."""
        if self.engine is None:
            return {"ok": False, "errors": ["silnik sesji niedostepny"]}
        state = self.engine.state()
        if state.get("mode") != "FREE" or state.get("phase") in ("IDLE", "DONE"):
            return {"ok": False, "errors": ["tryb wolny nie jest aktywny"]}
        session_id = state.get("session_id")
        bank = self.economy.balance() if self.economy else 0
        if bank < seconds:
            return {"ok": False, "errors": [f"za malo w banku: {bank // 60} min < {seconds // 60} min"]}
        spent = self.economy.spend_free(seconds, session_id) if self.economy else 0
        left = int(state.get("free_seconds_left", 0) or 0) or self._free_budget
        budget = left + spent
        plan = self.build_plan(
            {"mode": "FREE", "free_minutes": budget // 60, "tag": state.get("tag", ""), "arm_seconds": 0}
        )
        self.engine.start(plan, session_id)
        self._free_budget = budget
        if session_id:
            self.store.update_session(int(session_id), status="RUNNING")
            self.store.execute("UPDATE sessions SET plan_seconds = plan_seconds + ? WHERE id = ?", (spent, int(session_id)))
            self.store.add_event(int(session_id), "FREE_EXTEND", {"seconds": spent})
        return {"ok": True, "spent": spent, "balance": self.store.bank_balance(),
                "state": self.engine.state()}

    def save_app_profile(self, payload: dict) -> dict:
        label = str(payload.get("label") or payload.get("match_value") or "").strip()
        value = str(payload.get("match_value") or "").strip().lower()
        kind = str(payload.get("match_kind") or "name")
        raw_cat = str(payload.get("category") or "STUDY").upper()
        category = "BLOCKED" if raw_cat in ("BLOCK", "BLOCKED") else "STUDY"
        if not value:
            return {"ok": False, "errors": ["podaj nazwe procesu lub sciezke"]}
        if kind not in ("name", "path", "signature"):
            kind = "name"
        if not label:
            label = appcatalog.prettify_process_name(value)
        profile_id = self.store.upsert_app_profile(label, kind, value, category)
        return {"ok": True, "id": profile_id, "label": label, "value": value, "category": category}

    def save_site_profile(self, payload: dict) -> dict:
        host = sitecatalog.normalize(str(payload.get("host") or ""))
        if not host:
            return {"ok": False, "errors": ["podaj host"]}
        label = str(payload.get("label") or "").strip()
        raw_cat = str(payload.get("category") or "wlasne").strip()
        raw_kind = str(payload.get("kind") or "").upper()
        if not raw_kind:
            raw_kind = (
                "BLOCKED"
                if raw_cat.upper() in ("BLOCK", "BLOCKED", "WLASNE_BLOK", "ROZRYWKA", "SPOLECZNOSC", "WIADOMOSCI", "ZAKUPY")
                else "STUDY"
            )
        if raw_cat.upper() in ("BLOCK", "BLOCKED"):
            category = "BLOCKED"
        elif raw_cat.upper() in ("STUDY", ""):
            category = "STUDY"
        else:
            category = raw_cat
        if not label:
            found = sitecatalog.find(host)
            label = found.name if found else host
        profile_id = self.store.upsert_site_profile(host, label, category)
        if raw_kind == "BLOCKED" or category == "BLOCKED":
            self.settings.network.blocklist = list(dict.fromkeys(list(self.settings.network.blocklist) + [host]))
            self.settings.save(self.store)
        return {"ok": True, "id": profile_id, "host": host, "label": label, "category": category}

    def is_elevated(self) -> bool:
        """Zwraca True, gdy GUI lub helper posiada uprawnienia administratora."""
        if self._is_admin():
            return True
        return bool(self.bridge.online and getattr(self.bridge, "helper_admin", False))


    # ------------------------------------------------------------- diagnostyka
    def diagnostics(self) -> dict:
        """Stan aplikacji do zakladki SYSTEM - bez zmieniania czegokolwiek."""
        import platform

        try:
            from . import __version__
        except Exception:  # pragma: no cover
            __version__ = "?"
        cached = appcatalog.load_cache()
        lock = lockstate.read()
        autostart = self._autostart_module()
        autostart_state = "?"
        if autostart is not None:
            try:
                autostart_state = "wlaczony" if autostart.shortcut_exists() else "wylaczony"
            except Exception:
                autostart_state = "blad"
        return {
            "ok": True,
            "wersja": __version__,
            "python": platform.python_version(),
            "system": platform.platform(),
            "admin": self._is_admin(),
            "helper_online": self.bridge.online,
            "helper_error": self.bridge.last_error,
            "dry_run": self.dry_run,
            "safe_mode": self.safe_mode,
            "katalog_danych": str(paths.data_dir()),
            "baza": str(self.store.path),
            "aplikacji_w_cache": len(cached) if cached else 0,
            "cache_apps": str(appcatalog.cache_path()),
            "stron_w_katalogu": len(sitecatalog.all_sites()),
            "sesji": self.store.totals().get("sessions", 0),
            "bank_min": self.store.bank_balance() // 60,
            "lockstate": lock.to_json() if lock else None,
            "autostart": autostart_state,
            "tryb_blokady": self.settings.lock.mode,
        }

    @staticmethod
    def _is_admin() -> bool:
        try:
            from .shell import elevate

            return bool(elevate.is_admin())
        except Exception:
            return False

    @staticmethod
    def _autostart_module():
        """Wczytuje narzedzie autostartu z tools/ (bez instalowania pakietu)."""
        try:
            import importlib.util

            tool = Path(__file__).resolve().parent.parent / "tools" / "install_autostart.py"
            if not tool.exists():
                return None
            spec = importlib.util.spec_from_file_location("cisza_install_autostart", tool)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception:
            return None

    def autostart(self, payload: Optional[dict] = None) -> dict:
        """Status albo zastosowanie aktualnej wartosci `system.autostart`."""
        payload = payload or {}
        module = self._autostart_module()
        if module is None:
            return {"ok": False, "errors": ["nie znaleziono tools/install_autostart.py"]}
        try:
            if payload.get("action") == "status" or payload.get("status_only"):
                exists = bool(module.shortcut_exists())
                return {"ok": True, "autostart": exists, "status": "wlaczony" if exists else "wylaczony"}
            if self.settings.system.autostart:
                ok, message = module.create_shortcut(dry_run=self.dry_run)
            else:
                ok, message = module.remove_shortcut(dry_run=self.dry_run)
            self.settings.save(self.store)
            self.store.log_audit("autostart", None, {"enabled": self.settings.system.autostart, "message": message})
            return {
                "ok": bool(ok),
                "autostart": bool(self.settings.system.autostart),
                "message": message,
                "errors": [] if ok else [message],
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "errors": [str(exc)]}

    def reset_settings(self) -> dict:
        """Przywraca ustawienia domyslne, zachowujac PIN i dane."""
        pin_hash, pin_salt = self.settings.lock.pin_hash, self.settings.lock.pin_salt
        onboarding = self.settings.onboarding_done
        fresh = Settings()
        fresh.lock.pin_hash, fresh.lock.pin_salt = pin_hash, pin_salt
        fresh.onboarding_done = onboarding
        fresh.env_overrides()
        self.settings = fresh
        self.settings.save(self.store)
        self.store.log_audit("settings_reset", None, None)
        self._refresh_economy()
        self._emit("settings_changed", self.settings.to_dict())
        return {"ok": True, "settings": self.settings.to_dict()}

    def test_lock(self) -> dict:
        """Probny lockdown w trybie dry-run - nic nie zmienia w systemie."""
        settings_payload = {
            "black_wallpaper": self.settings.lock.black_wallpaper,
            "hide_desktop_icons": self.settings.lock.hide_desktop_icons,
            "hide_taskbar": self.settings.lock.hide_taskbar,
            "taskbar_mode": str(getattr(self.settings.lock, "taskbar_mode", "filtered") or "filtered"),
            "block_hotkeys": self.settings.lock.block_hotkeys,
            "mute_toasts": self.settings.lock.mute_toasts,
            "prevent_sleep": self.settings.lock.prevent_sleep,
        }
        shell_result = self.bridge.call("shell_lock", settings=settings_payload, hardcore=False, dry_run=True)
        allow = self.study_rules()
        guard_result = self.bridge.call(
            "guard_start",
            allow=allow,
            block=self.block_rules(),
            action="suspend",
            dry_run=True,
        )
        network_result = self.bridge.call(
            "network_enable",
            mode=self.settings.network.mode,
            allow_hosts=self.study_hosts(),
            block_hosts=self.break_hosts(),
            safe_hosts=self.settings.network.safe_hosts,
            browser_exes=list(BROWSER_EXES),
            port=self.settings.network.proxy_port,
            dry_run=True,
        )
        cleanup = self.bridge.call("guard_stop")
        ok = bool(self.bridge.online)
        return {
            "ok": ok,
            "helper_online": self.bridge.online,
            "shell": shell_result,
            "guard": guard_result,
            "network": network_result,
            "cleanup": cleanup,
            "errors": [] if ok else [f"helper niedostepny: {self.bridge.last_error or 'brak polaczenia z UAC'}"],
        }

    def restore_everything(self) -> dict:
        """Reczne, awaryjne przywrocenie powloki i sieci (Ustawienia -> System)."""
        helper_result = self.bridge.call("restore_all", reason="ui", timeout=60.0)
        local_result = recovery.restore_everything(reason="ui")
        ok = bool(helper_result.get("ok")) or bool(local_result.get("ok"))
        return {
            "ok": ok,
            "helper": helper_result,
            "local": local_result,
            "errors": list(helper_result.get("errors", [])) + list(local_result.get("errors", [])),
        }

    def stage_database_restore(self, path: str) -> dict:
        """Przygotowuje przywrocenie bazy z kopii - podmiana przy nastepnym starcie."""
        import shutil

        backups = sorted(paths.backup_dir().glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not path:
            return {
                "ok": False,
                "errors": ["wskaz plik kopii"],
                "backups": [{"path": str(b), "kiedy": time.strftime("%Y-%m-%d %H:%M", time.localtime(b.stat().st_mtime))} for b in backups[:10]],
            }
        source = Path(path)
        if not source.exists():
            return {"ok": False, "errors": [f"nie ma pliku {path}"]}
        try:
            target = paths.data_dir() / "restore-pending.db"
            shutil.copy2(source, target)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "restart_required": True, "path": str(source),
                "message": "Kopia zostanie wczytana po restarcie Ciszy."}

    def apply_settings(self, patch: dict) -> dict:
        """Czastkowa aktualizacja ustawien (plytkie scalanie w obrebie grup)."""
        for group, values in (patch or {}).items():
            target = getattr(self.settings, group, None)
            if target is None:
                setattr(self.settings, group, values)
                continue
            if isinstance(values, dict) and hasattr(target, "__dataclass_fields__"):
                for key, value in values.items():
                    if key in target.__dataclass_fields__:
                        setattr(target, key, value)
            else:
                setattr(self.settings, group, values)
        if isinstance((patch or {}).get("lock"), dict) and "taskbar_mode" in patch["lock"]:
            mode = str(getattr(self.settings.lock, "taskbar_mode", "") or "").strip().lower()
            if mode in ("filtered", "hide", "keep"):
                # `hide_taskbar` zostaje jako lustro dla starszych sciezek.
                self.settings.lock.hide_taskbar = mode == "hide"
            if self._locked:
                # Zmiana w trakcie sesji: tryb "filtered" dokladamy od razu,
                # a wyjscie z filtrowania cofa przyciski. Pelne ukrycie paska
                # zadziala od nastepnej sesji.
                if mode == "filtered":
                    self.refresh_taskbar_filter(force=True)
                elif mode == "hide":
                    self._stop_taskbar_filter()
        self.settings.save(self.store)
        self.store.log_audit("settings_update", None, patch)
        self._refresh_economy()
        self._emit("settings_changed", patch)
        return {"ok": True, "settings": self.settings.to_dict()}

    def _refresh_economy(self) -> None:
        """Economy cache'uje konfiguracje, wiec po zmianie ustawien tworzymy je od nowa."""
        module = _try_import("focuslock.economy")
        if module is not None:
            try:
                self.economy = module.Economy(self.store, self.settings)
            except Exception as exc:  # noqa: BLE001
                self.log(f"nie udalo sie odswiezyc ekonomii: {exc!r}")

    def check_pin(self, pin: str) -> bool:
        return self.settings.check_pin(pin)

    def set_pin(self, pin: str) -> dict:
        self.settings.set_pin(pin)
        self.settings.save(self.store)
        self.store.log_audit("pin_set")
        return {"ok": True}

    def summary(self) -> dict:
        economy = self.economy.summary() if self.economy else {}
        daily = self.store.daily_stats()
        try:
            presets = self.store.list_presets()
        except Exception:
            presets = []
        try:
            lots = self.store.lots()
        except Exception:
            lots = []
        try:
            ledger = self.store.ledger(limit=100)
        except Exception:
            ledger = []
        try:
            series = self.store.daily_series(days=30)
        except Exception:
            series = []
        try:
            recent_events = self.store.events(limit=100)
        except Exception:
            recent_events = []
        try:
            top_blocked = self.store.top_blocked(limit=10)
        except Exception:
            top_blocked = []

        engine_state = self.engine.state() if self.engine else {}

        return {
            "summary": economy,
            "economy": economy,
            "daily": daily,
            "today": daily,
            "totals": self.store.totals(),
            "presets": presets,
            "lots": lots,
            "ledger": ledger,
            "series": series,
            "events": recent_events,
            "blocked": top_blocked,
            "state": engine_state,
            "helper_online": self.bridge.online,
            "is_admin": self.is_elevated(),
            "control_level": "PEŁNA (ADMIN)" if self.is_elevated() else "PODSTAWOWA",
            "guard": self.last_guard_stats,
            "settings": self.settings.to_dict(),
        }

    def startup_recovery(self) -> dict:
        restore_shell = bool(self.settings.system.restore_on_boot)
        result = recovery.handle_startup_state(self.store, restore_shell=restore_shell)
        if result.get("recovered"):
            self._emit("recovered_after_crash", result)
        return result

    def shutdown(self) -> None:
        try:
            if self.engine is not None and self.engine.state().get("phase") not in ("IDLE", "DONE"):
                self._finish("shutdown")
        except Exception:
            pass
        try:
            self.bridge.call("restore_all", reason="shutdown", timeout=20.0)
        except Exception:
            pass
        if getattr(self, "_watchdog_proc", None) is not None:
            try:
                self._watchdog_proc.terminate()
            except Exception:
                pass
            self._watchdog_proc = None
        self.bridge.close()

    # ------------------------------------------------------------------------ eventy
    def _emit(self, event: str, data: dict) -> None:
        try:
            self.emit_hook(event, data)
        except Exception:
            pass


_ = (config, SYSTEM_SAFE_PROCESSES)
