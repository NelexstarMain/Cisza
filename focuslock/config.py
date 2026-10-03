"""Konfiguracja aplikacji + domyslne wartosci.

Zamrozony kontrakt (patrz docs/INTERFACES.md): Settings.load(store) /
Settings.save(store) oraz grupy pol dostepne jako atrybuty.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

SETTINGS_KEY = "app"

# Tryby paska zadan w sesji (LockConfig.taskbar_mode).
TASKBAR_MODES: tuple[str, ...] = ("filtered", "hide", "keep")

# Domeny, ktore NIGDY nie sa blokowane (Windows Update, czas, podstawowa telemetria).
SAFE_HOSTS: tuple[str, ...] = (
    "windowsupdate.microsoft.com",
    "*.windowsupdate.microsoft.com",
    "update.microsoft.com",
    "*.update.microsoft.com",
    "*.delivery.mp.microsoft.com",
    "time.windows.com",
    "time.google.com",
    "*.microsoft.com",
    "*.msftconnecttest.com",
    "*.msftncsi.com",
    "*.live.com",
    "*.office.net",
    "*.windows.com",
    "localhost",
    "127.0.0.1",
)

# Domyslna lista rozpraszaczy (tryb przerwy / wolny wg ustawien).
DEFAULT_BLOCKLIST: tuple[str, ...] = (
    "youtube.com",
    "*.youtube.com",
    "youtu.be",
    "facebook.com",
    "*.facebook.com",
    "instagram.com",
    "*.instagram.com",
    "tiktok.com",
    "*.tiktok.com",
    "x.com",
    "twitter.com",
    "*.twitter.com",
    "reddit.com",
    "*.reddit.com",
    "netflix.com",
    "*.netflix.com",
    "twitch.tv",
    "*.twitch.tv",
    "9gag.com",
    "*.9gag.com",
    "wykop.pl",
    "*.wykop.pl",
    "discord.com",
    "*.discord.com",
    "vk.com",
    "pinterest.com",
    "*.pinterest.com",
    "spotify.com",
    "*.spotify.com",
    "steampowered.com",
    "*.steampowered.com",
)

# Procesy, ktorych nie wolno dotykac (system + wlasne procesy Ciszy).
SYSTEM_SAFE_PROCESSES: tuple[str, ...] = (
    "system",
    "system idle process",
    "registry",
    "memory compression",
    "secure system",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "explorer.exe",
    "sihost.exe",
    "taskhostw.exe",
    "ctfmon.exe",
    "audiodg.exe",
    "conhost.exe",
    "runtimebroker.exe",
    "searchindexer.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "textinputhost.exe",
    "applicationframehost.exe",
    "widgets.exe",
    "widgetservice.exe",
    "securityhealthsystray.exe",
    "securityhealthservice.exe",
    "msmpeng.exe",
    "nissrv.exe",
    "wmiprvse.exe",
    "dllhost.exe",
    "spoolsv.exe",
    "pythonw.exe",
    "python.exe",
    "cisza.exe",
    "cisza-helper.exe",
    "cisza-watchdog.exe",
    # Wspoldzielony runtime WebView2: korzysta z niego Windows Search, widzety,
    # Teams, WhatsApp i inne aplikacje. Blokowanie go psuje powloke i aplikacje,
    # a nie daje zadnego efektu "skupienia" (to nie jest samodzielna przegladarka).
    "msedgewebview2.exe",
    "identity_helper.exe",
)


@dataclass
class SessionConfig:
    study_minutes: int = 25
    break_minutes: int = 5
    long_break_minutes: int = 15
    long_break_every: int = 4
    arming_seconds: int = 3
    auto_start_break: bool = False
    auto_start_next_study: bool = False
    allow_end_early: bool = True


@dataclass
class EconomyConfig:
    earn_ratio: float = 0.5
    round_seconds: int = 15
    daily_cap_minutes: int = 60
    bank_ttl_days: int = 7
    abort_penalty_minutes: int = 0
    min_free_block_minutes: int = 10
    streak_min_study_minutes: int = 25
    require_full_pomodoro: bool = True


@dataclass
class LockConfig:
    mode: str = "hard"  # "soft" | "hard" | "hardcore"
    hardcore_max_minutes: int = 180
    suspend_instead_of_kill: bool = True
    block_task_manager: bool = True
    block_hotkeys: bool = True
    # Pasek zadan w sesji:
    #   "filtered" - zostaje widoczny, ale przyciski aplikacji spoza allowlisty
    #                znikaja (focuslock/shell/taskbarfilter.py),
    #   "hide"     - ukryty (dawne zachowanie hard/hardcore),
    #   "keep"     - nietkniety (bez filtrowania i bez ukrywania).
    taskbar_mode: str = "filtered"
    # Pole zgodnosci ze starymi ustawieniami: lustro taskbar_mode == "hide".
    # Nie ustawiaj go recznie - patrz Settings.from_dict (migracja).
    hide_taskbar: bool = False
    black_wallpaper: bool = True
    hide_desktop_icons: bool = True
    mute_toasts: bool = True
    mute_sound: bool = False
    prevent_sleep: bool = True
    emergency_hold_key: bool = True
    exit_cooldown_seconds: int = 60
    pin_hash: str = ""
    pin_salt: str = ""
    panic_requires_pin: bool = True
    # Przepuszczaj takze procesy potomne dozwolonych aplikacji (np. gra odpalona
    # z launchera, pomocnicze procesy przegladarki). Domyslnie wlaczone:
    # zbieranie przodkow jest zbiorcze (jedno ppid_map na iteracje) i ma twarde
    # limity czasu, wiec nie zatrzymuje petli guardu.
    allow_child_processes: bool = True

    def __post_init__(self) -> None:
        mode = str(self.taskbar_mode or "").strip().lower()
        if mode not in TASKBAR_MODES:
            mode = "filtered"
        object.__setattr__(self, "taskbar_mode", mode)
        self.hide_taskbar = mode == "hide"


@dataclass
class NetworkConfig:
    mode: str = "allowlist"  # "allowlist" | "blocklist" | "off"
    proxy_port: int = 8765
    # Reguly zapory dla przegladarek wymagaja administratora i - jesli zostana po
    # nieczystym zamknieciu - potrafia zablokowac internet na stale (usuniecie
    # wymaga admina). Dlatego domyslnie WYLACZONE; wlacz swiadomie w Ustawieniach.
    block_browser_direct: bool = False
    block_non_allowlisted_dns: bool = False
    blocklist: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCKLIST))
    safe_hosts: list[str] = field(default_factory=lambda: list(SAFE_HOSTS))
    blocklist_mode: str = "study_and_break"  # "study_only" | "study_and_break"


@dataclass
class UiConfig:
    language: str = "pl"
    sound_enabled: bool = True
    sound_volume: int = 30
    breathing_break: bool = True
    # Domyslnie okno pokazuje sie przy starcie; "w tle" (zminimalizowane) tylko
    # gdy uzytkownik sam tak wybierze w Ustawieniach -> Wyglad.
    start_minimized: bool = False
    tray_icon: bool = True
    confirm_before_start: bool = True
    event_log_visible: bool = True
    # Ikony aplikacji w kreatorze sesji: domyslnie odbarwione (monochrom),
    # mozna przelaczyc na oryginalne kolory producenta.
    color_app_icons: bool = False
    app_icon_size: int = 40


@dataclass
class SystemConfig:
    autostart: bool = False
    launch_helper_on_start: bool = True
    restore_on_boot: bool = True
    dry_run: bool = False
    safe_mode: bool = False


@dataclass
class Settings:
    session: SessionConfig = field(default_factory=SessionConfig)
    economy: EconomyConfig = field(default_factory=EconomyConfig)
    lock: LockConfig = field(default_factory=LockConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    onboarding_done: bool = False
    schema_version: int = 1

    # ------------------------------------------------------------------ serializacja
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        groups = {f.name: f.type for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            if f.name in ("session", "economy", "lock", "network", "ui", "system") and isinstance(value, dict):
                target = {
                    "session": SessionConfig,
                    "economy": EconomyConfig,
                    "lock": LockConfig,
                    "network": NetworkConfig,
                    "ui": UiConfig,
                    "system": SystemConfig,
                }[f.name]
                valid = {k: v for k, v in value.items() if k in {ff.name for ff in fields(target)}}
                kwargs[f.name] = target(**valid)
            else:
                kwargs[f.name] = value
        _ = groups
        settings = cls(**kwargs)
        cls._migrate_taskbar(data, settings)
        return settings

    @staticmethod
    def _migrate_taskbar(data: dict, settings: "Settings") -> None:
        """Stare ustawienia mialy tylko `hide_taskbar` (bool) zamiast taskbar_mode.

        Mapowanie: brak/zablokowany pasek (True) -> "filtered", bo od tej wersji
        sesja zostawia pasek widoczny i tylko filtruje przyciski; odkryty pasek
        (False) -> "keep" (bez zmian). Kto chce pelnego ukrycia, wybiera `hide`.
        """
        raw = data.get("lock") if isinstance(data, dict) else None
        if not isinstance(raw, dict) or "taskbar_mode" in raw or "hide_taskbar" not in raw:
            return
        mode = "keep" if raw.get("hide_taskbar") is False else "filtered"
        settings.lock.taskbar_mode = mode
        settings.lock.hide_taskbar = mode == "hide"

    @classmethod
    def load(cls, store) -> "Settings":
        raw = store.get_setting(SETTINGS_KEY)
        if not raw:
            return cls()
        if isinstance(raw, str):
            raw = json.loads(raw)
        return cls.from_dict(raw)

    def save(self, store) -> None:
        store.set_setting(SETTINGS_KEY, self.to_dict())

    # ------------------------------------------------------------------------ PIN
    @staticmethod
    def hash_pin(pin: str, salt: Optional[str] = None) -> tuple[str, str]:
        salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 120_000)
        return digest.hex(), salt

    def set_pin(self, pin: str) -> None:
        self.lock.pin_hash, self.lock.pin_salt = self.hash_pin(pin)

    def check_pin(self, pin: str) -> bool:
        if not self.lock.pin_hash or not self.lock.pin_salt:
            return True
        digest, _ = self.hash_pin(pin, self.lock.pin_salt)
        return hmac.compare_digest(digest, self.lock.pin_hash)

    @property
    def has_pin(self) -> bool:
        return bool(self.lock.pin_hash)

    # ---------------------------------------------------------------------- helper
    @property
    def hardcore(self) -> bool:
        return self.lock.mode == "hardcore"

    def effective_earn_ratio(self) -> float:
        ratio = float(self.economy.earn_ratio)
        return max(0.0, min(10.0, ratio))

    def env_overrides(self) -> None:
        if os.environ.get("CISZA_DRY_RUN"):
            self.system.dry_run = True
        if os.environ.get("CISZA_SAFE"):
            self.system.safe_mode = True


def default_presets() -> dict[str, dict]:
    """Presety zakladane przy pierwszym uruchomieniu."""
    return {
        "Matura - matematyka": {
            "study_apps": ["chrome.exe", "notepad.exe"],
            "study_sites": ["*.wikipedia.org", "cke.gov.pl", "*.wolframalpha.com", "pl.khanacademy.org"],
            "break_sites": [],
            "study_minutes": 25,
            "break_minutes": 5,
            "long_break_minutes": 15,
            "tag": "matematyka",
        },
        "Programowanie": {
            "study_apps": ["code.exe", "windowsterminal.exe", "python.exe"],
            "study_sites": ["stackoverflow.com", "*.github.com", "docs.python.org", "developer.mozilla.org"],
            "break_sites": [],
            "study_minutes": 50,
            "break_minutes": 10,
            "long_break_minutes": 20,
            "tag": "informatyka",
        },
        "Czytanie": {
            "study_apps": ["sumatrapdf.exe", "acrord32.exe"],
            "study_sites": ["*.wolnelektury.pl"],
            "break_sites": [],
            "study_minutes": 25,
            "break_minutes": 5,
            "long_break_minutes": 15,
            "tag": "czytanie",
        },
    }
