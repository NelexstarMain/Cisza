#!/usr/bin/env python3
"""install_autostart - uruchamiaj Cisze razem z Windows.

Dwa niezalezne mechanizmy:

1. **Skrot w folderze Startup** (domyslnie) - ``%APPDATA%\\Microsoft\\Windows\\
   Start Menu\\Programs\\Startup\\Cisza.lnk``. Nie wymaga administratora.
2. **Zadanie w Harmonogramie zadan** (``--task``) - trigger ``AtLogOn`` dla
   biezacego uzytkownika. Nie wymaga administratora; ``--run-level highest``
   (podniesione uprawnienia) moze wymagac UAC.

Uzycie:
    python tools/install_autostart.py               # skrot w Startup
    python tools/install_autostart.py --task        # skrot + zadanie w Harmonogramie
    python tools/install_autostart.py --task-only   # tylko zadanie
    python tools/install_autostart.py --remove      # usun oba wpisy
    python tools/install_autostart.py --status      # pokaz, co jest zainstalowane
    python tools/install_autostart.py --dry-run     # tylko plan

Wskazowka: jesli istnieje ``dist\\Cisza.exe``, skrot/zadanie wskazuje na .exe;
w przeciwnym razie na ``pythonw.exe`` + ``run.pyw`` z tego katalogu.
"""
from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHORTCUT_NAME = "Cisza.lnk"
TASK_NAME = "Cisza"
DESCRIPTION = "Cisza - monochromatyczna aplikacja wymuszajaca nauke"
FROZEN_CANDIDATE = PROJECT_ROOT / "dist" / "Cisza.exe"
ICON_CANDIDATE = PROJECT_ROOT / "dist" / "cisza.ico"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------- narzedzia
def is_windows() -> bool:
    return os.name == "nt"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def _ps_quote(value: str) -> str:
    """Bezpieczny literal tekstowy dla PowerShell (pojedyncze cudzyslowy)."""
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str, *, dry_run: bool = False) -> tuple[bool, str]:
    if dry_run:
        return True, "dry-run"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, creationflags=_CREATE_NO_WINDOW, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - zalezy od systemu
        return False, str(exc)
    output = (proc.stdout or "").strip()
    errors = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return False, errors or output or f"kod {proc.returncode}"
    return True, output


def launch_command(exe: Path | None = None) -> tuple[str, str, str]:
    """Zwraca (program, argumenty, katalog_roboczy) dla autostartu."""
    if exe is not None:
        resolved = Path(exe).resolve()
        return str(resolved), "", str(resolved.parent)
    if getattr(sys, "frozen", False):
        resolved = Path(sys.executable).resolve()
        return str(resolved), "", str(resolved.parent)
    if FROZEN_CANDIDATE.exists():
        return str(FROZEN_CANDIDATE), "", str(FROZEN_CANDIDATE.parent)
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    program = str(pythonw if pythonw.exists() else executable)
    return program, f'"{PROJECT_ROOT / "run.pyw"}"', str(PROJECT_ROOT)


# ----------------------------------------------------------------------- skrot
def create_shortcut(*, exe: Path | None = None, dry_run: bool = False, icon: Path | None = None) -> tuple[bool, str]:
    link = shortcut_path()
    program, arguments, workdir = launch_command(exe)
    icon_path = icon if icon is not None else (ICON_CANDIDATE if ICON_CANDIDATE.exists() else None)

    if dry_run:
        return True, f"utworzylbym {link} -> {program} {arguments}".strip()

    link.parent.mkdir(parents=True, exist_ok=True)
    script = "\n".join(
        [
            "$ws = New-Object -ComObject WScript.Shell",
            f"$lnk = $ws.CreateShortcut({_ps_quote(str(link))})",
            f"$lnk.TargetPath = {_ps_quote(program)}",
            f"$lnk.Arguments = {_ps_quote(arguments)}",
            f"$lnk.WorkingDirectory = {_ps_quote(workdir)}",
            f"$lnk.Description = {_ps_quote(DESCRIPTION)}",
        ]
        + ([f"$lnk.IconLocation = {_ps_quote(str(icon_path))}"] if icon_path else [])
        + ["$lnk.Save()", "Write-Output 'OK'"]
    )
    ok, detail = _run_powershell(script, dry_run=dry_run)
    if ok and link.exists():
        return True, str(link)
    if ok:
        return False, detail or "skrot nie powstal"
    return False, detail


def remove_shortcut(*, dry_run: bool = False) -> tuple[bool, str]:
    link = shortcut_path()
    if dry_run:
        return True, f"usunalbym {link}"
    if not link.exists():
        return True, "brak skrotu"
    try:
        link.unlink()
    except OSError as exc:
        return False, str(exc)
    return True, f"usunieto {link}"


def shortcut_exists() -> bool:
    try:
        return shortcut_path().exists()
    except OSError:  # pragma: no cover
        return False


# ------------------------------------------------------------- harmonogram zadan
def create_task(
    *, exe: Path | None = None, dry_run: bool = False, run_level: str = "limited"
) -> tuple[bool, str]:
    program, arguments, workdir = launch_command(exe)
    level = "Highest" if run_level.lower() == "highest" else "Limited"
    if dry_run:
        return True, f"zarejestrowalbym zadanie {TASK_NAME}: {program} {arguments} (RunLevel={level})"

    script = "\n".join(
        [
            f"$action = New-ScheduledTaskAction -Execute {_ps_quote(program)} "
            f"-Argument {_ps_quote(arguments)} -WorkingDirectory {_ps_quote(workdir)}",
            '$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"',
            "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
            "-DontStopIfGoingOnBatteries -StartWhenAvailable",
            f"$principal = New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" "
            f"-LogonType Interactive -RunLevel {level}",
            f"Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $action "
            f"-Trigger $trigger -Settings $settings -Principal $principal "
            f"-Description {_ps_quote(DESCRIPTION)} -Force | Out-Null",
            "Write-Output 'OK'",
        ]
    )
    ok, detail = _run_powershell(script, dry_run=dry_run)
    return (True, f"zarejestrowano zadanie {TASK_NAME}") if ok else (False, detail)


def remove_task(*, dry_run: bool = False) -> tuple[bool, str]:
    if dry_run:
        return True, f"usunalbym zadanie {TASK_NAME}"
    script = (
        f"$t = Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -ErrorAction SilentlyContinue; "
        "if ($t) { Unregister-ScheduledTask -TaskName "
        f"{_ps_quote(TASK_NAME)} -Confirm:$false; Write-Output 'REMOVED' }} else {{ Write-Output 'MISSING' }}"
    )
    ok, detail = _run_powershell(script)
    if not ok:
        return False, detail
    return True, "usunieto zadanie" if "REMOVED" in detail else "brak zadania"


def task_exists() -> bool | None:
    """True/False, albo None gdy nie da sie sprawdzic."""
    script = (
        f"if (Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -ErrorAction SilentlyContinue) "
        "{ Write-Output 'YES' } else { Write-Output 'NO' }"
    )
    ok, detail = _run_powershell(script)
    if not ok:
        return None
    return "YES" in detail


# ------------------------------------------------------------------------- CLI
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_autostart", description="Autostart Ciszy: skrot w Startup i/lub zadanie."
    )
    parser.add_argument("--exe", default="", help="sciezka do Cisza.exe (domyslnie wykrywana)")
    parser.add_argument("--task", action="store_true", help="dodaj tez zadanie w Harmonogramie")
    parser.add_argument("--task-only", action="store_true", help="tylko zadanie (bez skrotu)")
    parser.add_argument("--run-level", choices=("limited", "highest"), default="limited",
                        help="poziom uprawnien zadania (highest moze wymagac UAC)")
    parser.add_argument("--remove", action="store_true", help="usun skrot i zadanie")
    parser.add_argument("--remove-task", action="store_true", help="usun tylko zadanie")
    parser.add_argument("--status", action="store_true", help="pokaz stan autostartu")
    parser.add_argument("--dry-run", action="store_true", help="pokaz plan bez zmian")
    return parser


def _status() -> int:
    print(f"projekt        : {PROJECT_ROOT}")
    print(f"skrot Startup  : {shortcut_path()} [{'JEST' if shortcut_exists() else 'brak'}]")
    state = task_exists()
    label = {True: "JEST", False: "brak", None: "nie mozna sprawdzic"}[state]
    print(f"zadanie {TASK_NAME:<7}: {label}")
    program, arguments, workdir = launch_command()
    print(f"cel            : {program} {arguments}".rstrip())
    print(f"katalog roboczy: {workdir}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    if not is_windows():
        print("install_autostart: autostart jest obslugiwany tylko na Windows.", file=sys.stderr)
        return 2

    if args.status:
        return _status()

    exe = Path(args.exe) if args.exe else None
    if exe is not None and not exe.exists():
        print(f"install_autostart: nie ma pliku {exe}", file=sys.stderr)
        return 2

    if args.remove or args.remove_task:
        ok_shortcut, msg_shortcut = (True, "pominieto")
        if args.remove:
            ok_shortcut, msg_shortcut = remove_shortcut(dry_run=args.dry_run)
        ok_task, msg_task = remove_task(dry_run=args.dry_run)
        print(f"skrot  : {msg_shortcut}")
        print(f"zadanie: {msg_task}")
        return 0 if (ok_shortcut and ok_task) else 1

    results: list[tuple[str, bool, str]] = []
    if not args.task_only:
        ok, msg = create_shortcut(exe=exe, dry_run=args.dry_run)
        results.append(("skrot", ok, msg))
    if args.task or args.task_only:
        ok, msg = create_task(exe=exe, dry_run=args.dry_run, run_level=args.run_level)
        results.append(("zadanie", ok, msg))

    if not results:
        print("install_autostart: nic do zrobienia (uzyj --task, --status albo --remove).")
        return 0

    for label, ok, msg in results:
        print(f"{label:<7}: {'OK' if ok else 'BLAD'} - {msg}")
    if args.dry_run:
        print("install_autostart: tryb --dry-run - nic nie zmieniono.")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
