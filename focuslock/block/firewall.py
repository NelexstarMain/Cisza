"""Reguly zapory Windows (netsh advfirewall) blokujace ruch przegladarek.

Zamrozony kontrakt (docs/INTERFACES.md, sekcja 5):
    apply_browser_blocks(browser_exes, *, profile="CiszaBlock", dry_run=False)
    remove_blocks(*, profile="CiszaBlock", dry_run=False)
    is_active(*, profile="CiszaBlock")

Reguly: name="CiszaBlock-<n>" dir=out action=block protocol=TCP remoteport=80,443 program="<exe>".
Operacje wymagaja uprawnien administratora - brak admina => ok=False + czytelny blad
(bez wyjatkow na zewnatrz). dry_run=True nie wykonuje zadnego polecenia netsh.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional, Sequence

DEFAULT_PROFILE = "CiszaBlock"
BLOCKED_PORTS = "80,443"

_ADMIN_HINTS = (
    "denied",
    "elevat",
    "administrator",
    "requires elevation",
    "odmowa",
    "wymagane",
    "uprawnien",
)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _new_report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


def _decode(raw: bytes) -> str:
    return (raw or b"").decode("utf-8", errors="replace").strip()


def _run_netsh(args: Sequence[str]) -> tuple[int, str]:
    """Uruchamia netsh; zwraca (kod, tekst). Nie rzuca wyjatkow."""
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
    output = _decode(proc.stdout)
    error = _decode(proc.stderr)
    return int(proc.returncode), (output + (" " + error if error else "")).strip()


def _looks_like_denied(output: str) -> bool:
    lowered = (output or "").lower()
    return any(hint in lowered for hint in _ADMIN_HINTS)


def _rule_name(profile: str, index: int) -> str:
    return f"{profile}-{index}"


def _resolve_program(exe: str) -> str:
    path = str(exe).strip().strip('"')
    if "\\" in path or "/" in path:
        return path
    which = shutil.which(path)
    if which:
        return which
    lowered = path.lower()
    for root_var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(root_var)
        if not base:
            continue
        candidates = {
            "chrome.exe": [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")],
            "msedge.exe": [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")],
            "firefox.exe": [os.path.join(base, "Mozilla Firefox", "firefox.exe")],
            "brave.exe": [os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")],
            "opera.exe": [os.path.join(base, "Programs", "Opera", "launcher.exe")],
        }
        for cand in candidates.get(lowered, []):
            if os.path.exists(cand):
                return cand
    return path


def _add_args(exe: str, name: str, protocol: str = "TCP", remoteport: str = BLOCKED_PORTS) -> list[str]:
    return [
        "advfirewall",
        "firewall",
        "add",
        "rule",
        f"name={name}",
        "dir=out",
        "action=block",
        f"protocol={protocol}",
        f"remoteport={remoteport}",
        f"program={_resolve_program(exe)}",
        "enable=yes",
    ]


def _delete_args(name: str) -> list[str]:
    return ["advfirewall", "firewall", "delete", "rule", f"name={name}"]


def _render(command: Sequence[str]) -> str:
    """Czytelny zapis polecenia (do raportu applied)."""
    parts = []
    for token in command:
        if any(ch.isspace() for ch in token) or ";" in token:
            parts.append(f'"{token}"')
        else:
            parts.append(token)
    return "netsh " + " ".join(parts)


def _list_rules(profile: str) -> list[str]:
    """Nazwy istniejacych regul danego profilu (parsowanie po nazwie, odporne na jezyk)."""
    code, output = _run_netsh(["advfirewall", "firewall", "show", "rule", "name=all"])
    if code != 0 or not output:
        return []
    pattern = re.compile(r"^\s*[^:\r\n]*:\s*(" + re.escape(profile) + r"(?:-quic)?-\d+)\s*$", re.MULTILINE)
    names = set(pattern.findall(output))
    return sorted(names, key=lambda item: int(item.rsplit("-", 1)[-1]))


def _registry_rule_key() -> str:
    return r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\FirewallRules"


def quick_rule_names(*, profile: str = DEFAULT_PROFILE) -> Optional[list[str]]:
    """Nazwy regul profilu z rejestru - szybki odczyt (ok. 0,02 s).

    `netsh advfirewall firewall show rule name=all` wypisuje wszystkie reguly
    systemu i potrafi trwac kilka sekund. Sprzatanie po sesji musi byc tanie,
    wiec najpierw zagladamy do rejestru. Zwraca None, gdy nie da sie odczytac
    (wtedy wolajacy uzywa `is_active()`). Nigdy nie rzuca.
    """
    try:
        import winreg
    except ImportError:
        return None
    pattern = re.compile(r"(?:^|\|)Name=([^|]+)")
    name_pattern = re.compile(r"^" + re.escape(profile) + r"(?:-quic)?-\d+$")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _registry_rule_key()) as key:
            count = winreg.QueryInfoKey(key)[1]
            names: set[str] = set()
            for index in range(count):
                try:
                    _value_name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    continue
                match = pattern.search(str(value))
                if not match:
                    continue
                rule = match.group(1).strip()
                if name_pattern.match(rule):
                    names.add(rule)
    except OSError:
        return None
    return sorted(names, key=lambda item: int(item.rsplit("-", 1)[-1]))


def apply_browser_blocks(
    browser_exes: Sequence[str],
    *,
    profile: str = DEFAULT_PROFILE,
    dry_run: bool = False,
    block_quic: bool = False,
) -> dict:
    """Dodaje reguly blokujace outbound TCP 80,443 dla podanych programow (i opcjonalnie UDP 443 dla QUIC)."""
    report = _new_report()
    exes: list[str] = []
    for item in browser_exes or ():
        text = str(item).strip()
        if text and text not in exes:
            exes.append(text)
    if not exes:
        report["warnings"].append("brak programow do zablokowania")
        return report

    rule_cmds = []
    for index, exe in enumerate(exes, start=1):
        rule_cmds.append(_add_args(exe, _rule_name(profile, index)))
        if block_quic:
            rule_cmds.append(_add_args(exe, _rule_name(f"{profile}-quic", index), protocol="UDP", remoteport="443"))

    commands = [_render(cmd) for cmd in rule_cmds]

    if dry_run:
        report["applied"].extend(commands)
        report["applied"].append("dry-run: reguly zapory nie zostaly zmienione")
        return report

    existing = _list_rules(profile)
    for name in existing:
        code, output = _run_netsh(_delete_args(name))
        if code == 0:
            report["applied"].append(f"usunieto stara regule {name}")
        else:
            message = "brak uprawnien administratora" if _looks_like_denied(output) else output
            report["errors"].append(f"nie mozna usunac reguly {name}: {message}")

    for cmd in rule_cmds:
        code, output = _run_netsh(cmd)
        r_name = [param.split("=", 1)[1] for param in cmd if param.startswith("name=")][0]
        if code == 0:
            report["applied"].append(_render(cmd))
        else:
            message = "brak uprawnien administratora (UAC)" if _looks_like_denied(output) else output
            report["errors"].append(f"nie mozna dodac reguly {r_name}: {message}")

    if report["errors"]:
        report["ok"] = False
    elif not report["applied"]:
        report["warnings"].append("nie dodano zadnej reguly")
    return report


def remove_blocks(*, profile: str = DEFAULT_PROFILE, dry_run: bool = False) -> dict:
    """Usuwa wszystkie reguly o nazwie <profile>-<n>; idempotentne."""
    report = _new_report()
    if dry_run:
        report["applied"].append(_render(_delete_args(f"{profile}-*")) + " (wszystkie reguly profilu, dry-run)")
        return report

    existing = _list_rules(profile)
    if not existing:
        return report

    for name in existing:
        code, output = _run_netsh(_delete_args(name))
        if code == 0:
            report["applied"].append(f"usunieto regule {name}")
        else:
            message = "brak uprawnien administratora" if _looks_like_denied(output) else output
            report["errors"].append(f"nie mozna usunac reguly {name}: {message}")

    if report["errors"]:
        report["ok"] = False
    return report


def active_rule_names(*, profile: str = DEFAULT_PROFILE) -> list[str]:
    """Nazwy istniejacych regul profilu (pusta lista przy bledzie). Nigdy nie rzuca.

    Uzywane przez `recovery.cleanup_leftovers`, zeby w komunikacie dla uzytkownika
    podac, ILE regul zostalo (np. "reguly zapory CiszaBlock (14)").
    """
    try:
        return list(_list_rules(profile))
    except Exception:  # noqa: BLE001 - odpornosc na nietypowe bledy systemu
        return []


def is_active(*, profile: str = DEFAULT_PROFILE) -> bool:
    """True, gdy istnieje co najmniej jedna regula profilu. Nigdy nie rzuca."""
    try:
        return bool(_list_rules(profile))
    except Exception:  # noqa: BLE001 - odpornosc na nietypowe bledy systemu
        return False
