"""Podniesienie uprawnien (UAC) i szybki test zycia helpera.

Zamrozony kontrakt (docs/INTERFACES.md, sekcja 7):
    is_admin() -> bool
    helper_running(pipe_name, authkey, token) -> bool
    launch_helper(*, dry_run=False) -> {"ok", "pid", "warnings", "errors"}
    python_launch_command(args) -> list[str]   # pythonw + "-m focuslock" + args

Uruchomienie: ShellExecuteW(None, "runas", pythonw,
"-m focuslock --helper --token <token> --authkey <hex>", None, SW_HIDE).
Kod zwrotny <= 32 to blad; kod 5 to odmowa UAC.

Poswiadczenia sa wspoldzielone z `focuslock.helperclient` i `focuslock.helper`:
  - zmienne srodowiskowe CISZA_HELPER_TOKEN / CISZA_HELPER_AUTHKEY (ustawiane przez GUI
    tuz przed launch_helper - maja pierwszenstwo i sa dziedziczone przez proces z UAC),
  - plik paths.rpc_token_path() w formacie JSON {"token": ..., "authkey": ...}.
Kompatybilnie obslugiwany jest tez stary format: sam token w pliku (bez authkey).
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import subprocess
import sys
import threading
from multiprocessing.connection import Client
from pathlib import Path
from typing import Optional, Sequence

from .. import paths

SW_HIDE = 0
SHELL_EXECUTE_MIN_SUCCESS = 32
SE_ERR_ACCESSDENIED = 5


def is_admin() -> bool:
    """True, gdy proces ma podniesione uprawnienia. Nigdy nie rzuca."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - brak shell32 / brak uprawnien
        return False


def pythonw_path() -> str:
    """Sciezka pythonw.exe (fallback: biezacy interpreter)."""
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        return str(executable)
    candidate = executable.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return str(executable)


def python_launch_command(args: Sequence[str]) -> list[str]:
    """Pelne polecenie uruchomienia modulu: [pythonw, "-m", "focuslock", *args]."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable)), *[str(item) for item in args]]
    return [pythonw_path(), "-m", "focuslock", *[str(item) for item in args]]


def load_credentials() -> tuple[str, str]:
    """Zwraca (token, authkey_hex) helpera.

    Kolejnosc zrodel: zmienne srodowiskowe -> wspolny plik JSON -> nowe poswiadczenia
    (zapisane w tym samym formacie, ktorego uzywa focuslock.helperclient).
    """
    token = (os.environ.get("CISZA_HELPER_TOKEN") or "").strip()
    authkey = (os.environ.get("CISZA_HELPER_AUTHKEY") or "").strip()

    path = paths.rpc_token_path()
    from_file: dict = {}
    try:
        raw = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    except OSError:
        raw = ""
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            from_file = parsed
        else:
            from_file = {"token": raw, "authkey": ""}  # stary format: sam token

    token = token or str(from_file.get("token") or "")
    authkey = authkey or str(from_file.get("authkey") or "")
    if not token or not authkey:
        token = token or secrets.token_urlsafe(32)
        authkey = authkey or secrets.token_hex(32)
        try:
            path.write_text(json.dumps({"token": token, "authkey": authkey}), encoding="utf-8")
        except OSError:
            pass
    return token, authkey


def helper_token() -> str:
    """Token RPC helpera (zgodny z focuslock.helperclient)."""
    return load_credentials()[0]


def _helper_args(token: str, authkey: str) -> list[str]:
    return ["--helper", "--token", token, "--authkey", authkey]


def _shell_execute(params: str) -> int:
    """ShellExecuteW("runas", pythonw, params, None, SW_HIDE); zwraca kod (<=32 to blad)."""
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    result = shell32.ShellExecuteW(None, "runas", pythonw_path(), params, None, SW_HIDE)
    return 0 if result is None else int(result)


def launch_helper(*, dry_run: bool = False) -> dict:
    """Uruchamia helper z UAC. Zwraca {"ok", "pid", "applied", "warnings", "errors"}."""
    report = {"ok": True, "pid": None, "applied": [], "warnings": [], "errors": []}
    token, authkey = load_credentials()
    args = _helper_args(token, authkey)
    command = python_launch_command(args)
    # ShellExecuteW dostaje sama liste argumentow (bez sciezki pliku), czyli "-m focuslock ...".
    params = subprocess.list2cmdline(command[1:])
    report["applied"].append(f"ShellExecuteW runas: {command[0]} {params}")

    if dry_run:
        report["applied"].append("dry-run: helper nie zostal uruchomiony")
        report["warnings"].append("dry-run: pominieto UAC")
        return report

    try:
        code = _shell_execute(params)
    except Exception as exc:  # noqa: BLE001 - ShellExecuteW moze rzucic na nietypowych systemach
        report["ok"] = False
        report["errors"].append(f"ShellExecuteW nie powiodlo sie: {exc}")
        return report

    if code <= SHELL_EXECUTE_MIN_SUCCESS:
        report["ok"] = False
        if code == SE_ERR_ACCESSDENIED:
            report["errors"].append("UAC odrzucone - uzytkownik nie wyrazil zgody na podniesienie uprawnien")
        else:
            report["errors"].append(f"ShellExecuteW zwrocil kod {code} (nie udalo sie uruchomic helpera)")
        return report

    report["applied"].append("helper uruchomiony przez UAC (PID niedostepny przez ShellExecuteW)")
    return report


def helper_running(pipe_name: str, authkey: bytes, token: str) -> bool:
    """Szybki test: czy na potoku RPC odpowiada zywy helper (limit ~2.5 s)."""
    outcome = {"running": False}

    def probe() -> None:
        connection = None
        try:
            connection = Client(pipe_name, family="AF_PIPE", authkey=authkey)
            connection.send({"id": 0, "method": "ping", "params": {}, "token": token})
            if connection.poll(1.0):
                message = connection.recv()
                outcome["running"] = isinstance(message, dict)
        except Exception:  # noqa: BLE001 - brak potoku, zly klucz, zamkniecie
            outcome["running"] = False
        finally:
            try:
                if connection is not None:
                    connection.close()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=probe, name="cisza-helper-probe", daemon=True)
    thread.start()
    thread.join(timeout=2.5)
    return bool(outcome["running"])


def service_identity() -> Optional[str]:
    """Informacyjnie: nazwa konta procesu (przydatne w logach)."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return None
