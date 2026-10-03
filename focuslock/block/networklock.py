"""Sieciowa blokada: proxy systemowe + hosts + zapora (orkiestracja).

Zamrozony kontrakt (docs/INTERFACES.md, sekcja 6):
    NetworkLock(port=8765, dry_run=False, on_event=None)
    enable_allowlist(allow_hosts, safe_hosts=(), browser_exes=(), block_browser_direct=True)
    enable_blocklist(block_hosts, *, browser_exes=(), block_browser_direct=False)
    disable()
    status() -> {"active", "mode", "blocked_attempts", "port"}

Co robi:
  - enable_allowlist: lokalny proxy w trybie allowlist + proxy systemowy w HKCU
    (ProxyEnable/ProxyServer/ProxyOverride, usuniecie AutoConfigURL) + zapis poprzedniej
    konfiguracji do lockstate + netsh winhttp + opcjonalne reguly zapory + flush_dns.
  - enable_blocklist: proxy w trybie blocklist + proxy systemowy + sekcja hosts.
  - disable: pelne, idempotentne cofniecie (proxy stop, przywrocenie rejestru, winhttp reset,
    hosts restore/unblock, firewall remove, flush_dns, wyczyszczenie lockstate.network).

Uprawnienia: proxy + HKCU dzialaja bez administratora. `netsh winhttp`, zapora i plik hosts
wymagaja podniesienia uprawnien - ich blad trafia do "errors"/"warnings", ale nie przewraca
calego raportu (ok=False tylko wtedy, gdy zawiodl podstawowy mechanizm, tj. proxy albo
konfiguracja proxy w HKCU).
"""
from __future__ import annotations

import ctypes
import subprocess
import threading
from typing import Callable, Optional, Sequence

from .. import lockstate
from . import firewall, hosts
from .proxy import AllowlistProxy, normalize_host

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
PROXY_KEYS = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL")
DEFAULT_OVERRIDE = "localhost;127.*;<local>"
DEFAULT_FIREWALL_PROFILE = firewall.DEFAULT_PROFILE

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _new_report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


def _clean_list(values: Optional[Sequence[str]]) -> list[str]:
    result: list[str] = []
    for item in values or ():
        text = normalize_host(item)
        if text and text not in result:
            result.append(text)
    return result


def _short(text: str, limit: int = 200) -> str:
    clean = " ".join((text or "").split())
    return clean[:limit]


# ------------------------------------------------------------------ rejestr (HKCU)
def _read_proxy_settings() -> dict:
    """Poprzednie wartosci ProxyEnable/ProxyServer/ProxyOverride/AutoConfigURL (None = brak)."""
    values: dict = {name: None for name in PROXY_KEYS}
    try:
        import winreg
    except ImportError:
        return values
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
            for name in PROXY_KEYS:
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                    values[name] = value
                except FileNotFoundError:
                    values[name] = None
    except OSError:
        pass
    return values


def _write_proxy_settings(values: dict) -> list[str]:
    """Zapisuje wartosci (None = usun wartosc). Rzuca OSError przy bledzie."""
    applied: list[str] = []
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
        for name, value in values.items():
            if value is None:
                try:
                    winreg.DeleteValue(key, name)
                    applied.append(f"rejestr: usunieto {name}")
                except FileNotFoundError:
                    pass
            else:
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, kind, value)
                applied.append(f"rejestr: {name}={value}")
    return applied


def _notify_settings_change() -> None:
    """Best-effort: powiadom WinINET i okna o zmianie ustawien proxy."""
    try:
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
        wininet.InternetSetOptionW(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, REG_PATH, 0x0002, 1000, None)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ netsh (winhttp/zapora)
def _run_netsh(args: Sequence[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["netsh", *args],
            capture_output=True,
            timeout=30,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return 127, "nie znaleziono netsh"
    except subprocess.TimeoutExpired:
        return 124, "przekroczono czas netsh"
    except OSError as exc:
        return 126, str(exc)
    output = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    error = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    return int(proc.returncode), (output + (" " + error if error else "")).strip()


def _winhttp_set(port: int) -> tuple[bool, str]:
    code, output = _run_netsh(["winhttp", "set", "proxy", f"127.0.0.1:{port}", DEFAULT_OVERRIDE])
    return code == 0, output


def _winhttp_reset() -> tuple[bool, str]:
    code, output = _run_netsh(["winhttp", "reset", "proxy"])
    return code == 0, output


# ------------------------------------------------------------------------- raporty
def _merge(report: dict, sub: dict) -> dict:
    for key in ("applied", "warnings", "errors"):
        report[key].extend(sub.get(key) or [])
    if not sub.get("ok", True):
        report["ok"] = False
    return report


def _merge_optional(report: dict, sub: dict, label: str) -> dict:
    """Czesciowy blad (np. brak admina) nie przewraca raportu - trafia do errors."""
    report["applied"].extend(sub.get("applied") or [])
    report["warnings"].extend(f"{label}: {item}" for item in (sub.get("warnings") or []))
    report["errors"].extend(f"{label}: {item}" for item in (sub.get("errors") or []))
    if not sub.get("ok", True):
        report["warnings"].append(f"{label}: czesc zmian nie zostala zastosowana (wymaga administratora)")
    return report


class NetworkLock:
    """Orkiestracja blokady sieci (proxy, hosts, zapora)."""

    def __init__(
        self,
        *,
        port: int = 8765,
        dry_run: bool = False,
        on_event: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self.port = int(port)
        self.dry_run = bool(dry_run)
        self.on_event = on_event
        self._proxy: Optional[AllowlistProxy] = None
        self._mode = ""
        self._previous: dict = {}
        self._firewall_profile = ""
        self._hosts_backup = ""
        self._lock = threading.Lock()

    # --------------------------------------------------------------------- pomoc
    @property
    def proxy(self) -> Optional[AllowlistProxy]:
        """Aktywny lokalny proxy (None, gdy wylaczony) - API dodatkowe, nie zamrozone."""
        return self._proxy

    def _start_proxy(self, allowlist, blocklist, safe_hosts, mode: str) -> dict:
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None
        try:
            proxy = AllowlistProxy(
                allowlist,
                blocklist,
                safe_hosts=safe_hosts,
                port=self.port,
                mode=mode,
                dry_run=False,
                on_event=self.on_event,
            )
            report = proxy.start()
        except Exception as exc:  # noqa: BLE001 - konstrukcja serwera nie moze wywalic aplikacji
            return {"ok": False, "applied": [], "warnings": [], "errors": [f"nie mozna utworzyc proxy: {exc}"]}
        if report.get("ok"):
            self._proxy = proxy
            self._mode = mode
            self.port = proxy.port
        return report

    def _apply_system_proxy(self) -> list[str]:
        """Ustawia proxy systemowe; zwraca liste wykonanych zmian. Rzuca OSError."""
        self._previous = _read_proxy_settings()
        values = {
            "ProxyEnable": 1,
            "ProxyServer": f"127.0.0.1:{self.port}",
            "ProxyOverride": DEFAULT_OVERRIDE,
            "AutoConfigURL": None,
        }
        applied = _write_proxy_settings(values)
        _notify_settings_change()
        return applied

    def _save_state(self, *, mode: str, hosts_patched: bool, hosts_backup: str, browsers, profile: str) -> list[str]:
        try:
            lockstate.update(
                network={
                    "mode": mode,
                    "proxy_port": int(self.port),
                    "proxy_enabled_by_us": True,
                    "proxy_previous": dict(self._previous or {}),
                    "firewall_profile": profile,
                    "hosts_patched": bool(hosts_patched),
                    "hosts_backup": hosts_backup,
                    "browsers": list(browsers or []),
                }
            )
        except OSError as exc:
            return [f"nie mozna zapisac lockstate: {exc}"]
        return []

    # ---------------------------------------------------------------- wlaczanie
    def enable_allowlist(
        self,
        allow_hosts,
        safe_hosts=(),
        browser_exes=(),
        block_browser_direct: bool = True,
    ) -> dict:
        report = _new_report()
        allow = _clean_list(allow_hosts)
        safe = _clean_list(safe_hosts)
        exes = _clean_list(browser_exes)

        if self.dry_run:
            report["applied"].extend(
                [
                    f"proxy allowlist na 127.0.0.1:{self.port} ({len(allow)} domen dozwolonych)",
                    f"rejestr HKCU: ProxyEnable=1, ProxyServer=127.0.0.1:{self.port}, "
                    f"ProxyOverride={DEFAULT_OVERRIDE}, AutoConfigURL usuniety",
                    f"netsh winhttp set proxy 127.0.0.1:{self.port}",
                ]
            )
            if block_browser_direct and exes:
                report["applied"].append(f"zapora: blokada outbound 80,443 dla {len(exes)} programow")
            report["applied"].append("ipconfig /flushdns")
            report["applied"].append("dry-run: system nie zostal zmieniony")
            return report

        with self._lock:
            proxy_report = self._start_proxy(allow, (), safe, "allowlist")
            _merge(report, proxy_report)
            if not proxy_report.get("ok"):
                return report

            try:
                report["applied"].extend(self._apply_system_proxy())
            except OSError as exc:
                report["ok"] = False
                report["errors"].append(f"nie mozna ustawic proxy systemowego w HKCU: {exc}")
                return report

            ok_winhttp, output_winhttp = _winhttp_set(self.port)
            if ok_winhttp:
                report["applied"].append(f"netsh winhttp set proxy 127.0.0.1:{self.port}")
            else:
                report["errors"].append(f"netsh winhttp set proxy: {_short(output_winhttp)}")
                report["warnings"].append("konfiguracja winhttp wymaga administratora - pominieto")

            profile = ""
            if block_browser_direct and exes:
                try:
                    firewall_report = firewall.apply_browser_blocks(
                        exes, profile=DEFAULT_FIREWALL_PROFILE, block_quic=True
                    )
                except TypeError:
                    firewall_report = firewall.apply_browser_blocks(
                        exes, profile=DEFAULT_FIREWALL_PROFILE
                    )
                _merge_optional(report, firewall_report, "zapora")
                if any("add rule" in item for item in firewall_report.get("applied", [])):
                    # Nawet przy czesciowym bledzie zapisujemy profil, zeby disable posprzatal.
                    profile = DEFAULT_FIREWALL_PROFILE

            dns_report = hosts.flush_dns()
            _merge_optional(report, dns_report, "dns")

            self._firewall_profile = profile
            report["errors"].extend(self._save_state(mode="allowlist", hosts_patched=False, hosts_backup="", browsers=exes, profile=profile))
        if report["errors"] and report["ok"]:
            report["warnings"].append("blokada aktywna, ale czesc opcjonalnych krokow sie nie powiodla")
        return report

    def enable_blocklist(
        self,
        block_hosts,
        *,
        browser_exes=(),
        block_browser_direct: bool = False,
    ) -> dict:
        report = _new_report()
        block = _clean_list(block_hosts)
        exes = _clean_list(browser_exes)

        if self.dry_run:
            report["applied"].extend(
                [
                    f"proxy blocklist na 127.0.0.1:{self.port} ({len(block)} domen blokowanych)",
                    f"rejestr HKCU: ProxyEnable=1, ProxyServer=127.0.0.1:{self.port}, "
                    f"ProxyOverride={DEFAULT_OVERRIDE}, AutoConfigURL usuniety",
                    f"hosts: sekcja Cisza z {len(block)} domen",
                ]
            )
            if block_browser_direct and exes:
                report["applied"].append(f"zapora: blokada outbound 80,443 dla {len(exes)} programow")
            report["applied"].append("ipconfig /flushdns")
            report["applied"].append("dry-run: system nie zostal zmieniony")
            return report

        with self._lock:
            proxy_report = self._start_proxy((), block, (), "blocklist")
            _merge(report, proxy_report)
            if not proxy_report.get("ok"):
                return report

            try:
                report["applied"].extend(self._apply_system_proxy())
            except OSError as exc:
                report["ok"] = False
                report["errors"].append(f"nie mozna ustawic proxy systemowego w HKCU: {exc}")
                return report

            ok_winhttp, output_winhttp = _winhttp_set(self.port)
            if ok_winhttp:
                report["applied"].append(f"netsh winhttp set proxy 127.0.0.1:{self.port}")
            else:
                report["errors"].append(f"netsh winhttp set proxy: {_short(output_winhttp)}")
                report["warnings"].append("konfiguracja winhttp wymaga administratora - pominieto")

            hosts_patched = False
            hosts_backup = ""
            if block:
                backup_path = hosts.backup()
                if backup_path:
                    hosts_backup = backup_path
                    report["applied"].append(f"kopia hosts: {backup_path}")
                else:
                    report["warnings"].append("nie udalo sie utworzyc kopii hosts")
                hosts_report = hosts.block_domains(block)
                _merge_optional(report, hosts_report, "hosts")
                hosts_patched = bool(hosts_report.get("ok") and not hosts_report.get("errors"))

            profile = ""
            if block_browser_direct and exes:
                firewall_report = firewall.apply_browser_blocks(exes, profile=DEFAULT_FIREWALL_PROFILE)
                _merge_optional(report, firewall_report, "zapora")
                if any("add rule" in item for item in firewall_report.get("applied", [])):
                    profile = DEFAULT_FIREWALL_PROFILE

            dns_report = hosts.flush_dns()
            _merge_optional(report, dns_report, "dns")

            self._firewall_profile = profile
            self._hosts_backup = hosts_backup
            report["errors"].extend(
                self._save_state(
                    mode="blocklist",
                    hosts_patched=hosts_patched,
                    hosts_backup=hosts_backup,
                    browsers=exes,
                    profile=profile,
                )
            )
        if report["errors"] and report["ok"]:
            report["warnings"].append("blokada aktywna, ale czesc opcjonalnych krokow sie nie powiodla")
        return report

    # ---------------------------------------------------------------- wylaczanie
    def disable(self) -> dict:
        report = _new_report()
        if self.dry_run:
            report["applied"].extend(
                [
                    "proxy: zatrzymanie serwera lokalnego",
                    f"rejestr HKCU {REG_PATH}: przywrocenie poprzednich wartosci proxy",
                    "netsh winhttp reset proxy",
                    "hosts: przywrocenie kopii lub usuniecie sekcji Cisza",
                    "zapora: usuniecie regul CiszaBlock-*",
                    "ipconfig /flushdns",
                    "lockstate: wyczyszczenie sekcji network",
                    "dry-run: system nie zostal zmieniony",
                ]
            )
            return report

        with self._lock:
            if self._proxy is not None:
                _merge(report, self._proxy.stop())
                self._proxy = None
            self._mode = ""

            state = lockstate.read()
            enabled_by_us = bool(state and state.network.proxy_enabled_by_us)
            previous = {}
            if state is not None and state.network.proxy_previous:
                previous = dict(state.network.proxy_previous)
            if not previous and self._previous:
                previous = dict(self._previous)

            if enabled_by_us or previous:
                if previous:
                    values = {name: previous.get(name) for name in PROXY_KEYS}
                else:
                    values = {name: None for name in PROXY_KEYS}
                    values["ProxyEnable"] = 0
                try:
                    report["applied"].extend(_write_proxy_settings(values))
                    _notify_settings_change()
                except OSError as exc:
                    report["ok"] = False
                    report["errors"].append(f"nie mozna przywrocic proxy systemowego w HKCU: {exc}")
                if enabled_by_us:
                    ok_winhttp, output_winhttp = _winhttp_reset()
                    if ok_winhttp:
                        report["applied"].append("netsh winhttp reset proxy")
                    else:
                        report["errors"].append(f"netsh winhttp reset proxy: {_short(output_winhttp)}")
                        report["warnings"].append("reset winhttp wymaga administratora - pominieto")
            else:
                report["warnings"].append("proxy systemowy nie byl zmieniany przez Cisze - pomijam")

            if state is not None and (state.network.hosts_patched or state.network.hosts_backup):
                if state.network.hosts_backup:
                    restore_report = hosts.restore_backup(state.network.hosts_backup)
                else:
                    restore_report = hosts.unblock()
                if restore_report.get("ok"):
                    report["applied"].extend(restore_report.get("applied") or [])
                else:
                    report["ok"] = False
                    report["errors"].extend(f"hosts: {item}" for item in (restore_report.get("errors") or []))
                report["warnings"].extend(f"hosts: {item}" for item in (restore_report.get("warnings") or []))
            else:
                report["warnings"].append("hosts nie byly zmieniane przez Cisze - pomijam")

            profile = (state.network.firewall_profile if state is not None else "") or self._firewall_profile
            if profile:
                firewall_report = firewall.remove_blocks(profile=profile)
                _merge_optional(report, firewall_report, "zapora")
            else:
                report["warnings"].append("zapora nie byla zmieniana przez Cisze - pomijam")

            dns_report = hosts.flush_dns()
            _merge_optional(report, dns_report, "dns")

            if state is not None:
                try:
                    network = state.network
                    network.mode = ""
                    network.proxy_port = 0
                    network.proxy_enabled_by_us = False
                    network.proxy_previous = {}
                    network.firewall_profile = ""
                    network.hosts_patched = False
                    network.hosts_backup = ""
                    network.browsers = []
                    lockstate.write(state)
                    report["applied"].append("lockstate: wyczyszczono sekcje network")
                except OSError as exc:
                    report["errors"].append(f"nie mozna zaktualizowac lockstate: {exc}")

            self._previous = {}
            self._firewall_profile = ""
            self._hosts_backup = ""
        return report

    # -------------------------------------------------------------------- status
    def status(self) -> dict:
        state = lockstate.read()
        mode = self._mode or (state.network.mode if state is not None else "")
        port = self.port
        if self._proxy is not None:
            port = self._proxy.port
        elif state is not None and state.network.proxy_port:
            port = int(state.network.proxy_port)
        active = bool(self._proxy is not None and self._proxy.running) or bool(mode)
        blocked = 0
        if self._proxy is not None:
            blocked = int(self._proxy.blocked_count)
        return {"active": active, "mode": mode, "blocked_attempts": blocked, "port": int(port)}
