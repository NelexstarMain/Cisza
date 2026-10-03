"""Test zgodnosci z kontraktem: importy i obecnosc symboli z docs/INTERFACES.md.

Sprawdza trzy rzeczy, ktorych nie widza testy jednostkowe poszczegolnych modulow:

1. kazdy modul pakietu ``focuslock`` daje sie zaimportowac (bez wywolan systemowych),
2. symbole zadeklarowane w kontrakcie naprawde istnieja,
3. PyQt6 wystepuje tylko w ``focuslock/ui/**`` i ``focuslock/shell/overlay.py``.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

CONTRACT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "focuslock.block.processes": (
        "MatchRule", "ProcessInfo", "SYSTEM_SAFE", "list_processes", "match_process",
        "is_system_safe", "compute_signature", "ProcessGuard",
    ),
    "focuslock.block.launchwatch": ("LaunchWatcher",),
    "focuslock.block.hosts": (
        "BLOCK_BEGIN", "BLOCK_END", "block_domains", "unblock", "is_blocked",
        "backup", "restore_backup", "flush_dns",
    ),
    "focuslock.block.proxy": ("Decision", "host_allowed", "normalize_host", "AllowlistProxy"),
    "focuslock.block.firewall": ("apply_browser_blocks", "remove_blocks", "is_active"),
    "focuslock.block.networklock": ("NetworkLock",),
    "focuslock.shell.elevate": ("is_admin", "helper_running", "launch_helper", "python_launch_command"),
    "focuslock.shell.desktop": ("DesktopState", "capture", "apply_black", "restore"),
    "focuslock.shell.taskbar": ("hide", "show", "is_hidden", "list_taskbars"),
    "focuslock.shell.taskbarfilter": (
        "TaskbarList", "allowed", "apply", "restore", "plan", "list_windows",
        "is_active", "filtered_windows", "status",
    ),
    "focuslock.shell.hotkeys": ("BLOCKED_KEYS", "HotkeyBlocker"),
    "focuslock.shell.overlay": ("OverlayManager",),
    "focuslock.shell.dnd": ("mute_toasts", "mute_sound", "prevent_sleep"),
    "focuslock.shell.watchdog": ("run_watchdog", "heartbeat_touch", "is_heartbeat_fresh"),
    "focuslock.recovery": ("restore_everything",),
    "focuslock.economy": ("Economy",),
    "focuslock.session": ("Phase", "Plan", "SessionEngine"),
    "focuslock.timer": ("Countdown", "format_seconds"),
    "focuslock.presets": (),
    "focuslock.ui.theme": ("COLORS", "qss", "is_gray"),
    "focuslock.ui.tray": ("Tray",),
}

SESSION_ENGINE_METHODS = (
    "start", "tick", "start_break", "skip_break", "finish", "abort_pomodoro",
    "state", "pause", "resume", "serialize", "deserialize",
)
ECONOMY_METHODS = (
    "credit_pomodoro", "spend_free", "refund", "balance", "earned_today",
    "daily_cap_left", "sweep_expired", "register_study_day", "focus_score", "summary",
)


def _all_focuslock_modules() -> list[str]:
    import focuslock

    modules = ["focuslock"]
    for info in pkgutil.walk_packages(focuslock.__path__, prefix="focuslock."):
        modules.append(info.name)
    return sorted(set(modules))


def test_every_module_imports():
    failures: list[str] = []
    for name in _all_focuslock_modules():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - raportujemy kazdy blad importu
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert failures == [], "moduly nie importuja sie:\n" + "\n".join(failures)


@pytest.mark.parametrize(("module_name", "symbols"), sorted(CONTRACT_SYMBOLS.items()))
def test_contract_symbols_exist(module_name, symbols):
    module = importlib.import_module(module_name)
    missing = [name for name in symbols if not hasattr(module, name)]
    assert missing == [], f"{module_name}: brakuje {missing}"


def test_session_engine_api():
    from focuslock.session import SessionEngine

    missing = [name for name in SESSION_ENGINE_METHODS if not callable(getattr(SessionEngine, name, None))]
    assert missing == []


def test_economy_api():
    from focuslock.economy import Economy

    missing = [name for name in ECONOMY_METHODS if not callable(getattr(Economy, name, None))]
    assert missing == []


#: Moduly warstwy GUI - patrz docs/INTERFACES.md (sekcja "Konwencje ogolne"):
#: PyQt jest dozwolony w ``focuslock/ui/**``, ``focuslock/shell/overlay.py`` oraz
#: w warstwie GUI ``focuslock/app.py`` (okno glowne, tray, QTimer). Logika
#: (controller, session, economy, block/**, helper, recovery) musi dzialac bez PyQt.
GUI_MODULES: frozenset[str] = frozenset({"focuslock/app.py", "focuslock/shell/overlay.py"})


def test_pyqt_only_in_gui_modules():
    offenders: list[str] = []
    for path in (ROOT / "focuslock").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "PyQt6" not in text:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("focuslock/ui/") or relative in GUI_MODULES:
            continue
        offenders.append(relative)
    assert offenders == [], f"PyQt6 poza warstwa GUI: {offenders}"


def test_ui_screens_expose_contract_signals():
    """`focuslock/ui/screens/__init__.py` eksportuje wszystkie ekrany z kontraktu."""
    screens = importlib.import_module("focuslock.ui.screens")
    names = (
        "HomeScreen", "ComposerScreen", "RunningScreen", "BreakScreen", "FreeScreen",
        "BankScreen", "StatsScreen", "SettingsScreen", "OnboardingScreen", "PinDialog",
        "SummaryScreen", "JournalScreen",
    )
    missing = [name for name in names if not hasattr(screens, name)]
    assert missing == [], f"brak ekranow: {missing}"
