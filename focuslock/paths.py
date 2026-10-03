"""Sciezki danych aplikacji.

Wszystkie moduly korzystaja wylacznie z tych funkcji, zeby testy i tryb
--dry-run mogly przekierowac dane zmienna srodowiskowa CISZA_DATA_DIR.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "Cisza"


def data_dir() -> Path:
    """Katalog danych aplikacji (tworzony przy pierwszym uzyciu)."""
    override = os.environ.get("CISZA_DATA_DIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / "cisza.db"


def backup_dir() -> Path:
    path = data_dir() / "backup"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return data_dir() / "cisza.log"


def lockstate_path() -> Path:
    """Stan lockdownu powloki - czytany przez watchdog i tools/restore.py."""
    return data_dir() / "lockstate.json"


def heartbeat_path() -> Path:
    """Znacznik zycia procesu GUI - watchdog pilnuje jego swiezosci."""
    return data_dir() / "heartbeat"


def crash_flag_path() -> Path:
    """Ustawiany, gdy sesja nie zostala zamknieta czysto."""
    return data_dir() / "unclean.json"


def leftovers_path() -> Path:
    """Lista rzeczy, ktorych nie udalo sie cofnac po sesji (czytana przy starcie).

    Plik istnieje tylko wtedy, gdy po sesji zostaly pozostawione zmiany systemu
    (zapora, hosts, proxy, pasek zadan, tapeta, hooki). Nastepny start aplikacji
    ponawia sprzatanie i pokazuje uzytkownikowi instrukcje.
    """
    return data_dir() / "leftovers.json"


def rpc_pipe_name() -> str:
    """Nazwa nazwanego potoku dla helpera (per uzytkownik)."""
    user = os.environ.get("USERNAME") or "user"
    return rf"\\.\pipe\cisza-helper-{user}"


def rpc_token_path() -> Path:
    return data_dir() / "helper.token"
