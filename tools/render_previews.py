"""Podglad ekranow Ciszy bez pokazywania okien (QT_QPA_PLATFORM=offscreen).

Uruchomienie:
    python build/render_previews.py [katalog_wyjsciowy]

Skrypt tworzy QApplication w trybie offscreen, nakłada motyw i zapisuje
`QWidget.grab()` kazdego ekranu do plikow PNG. Nie nalezy do aplikacji.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Offscreen Qt nie widzi katalogu czcionek systemowych - bez tego caly tekst
# renderuje sie jako puste kwadraty.
_WINDOWS_FONTS = Path(r"C:\Windows\Fonts")
if _WINDOWS_FONTS.is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(_WINDOWS_FONTS))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from focuslock.ui import theme  # noqa: E402
from focuslock.ui.screens import (  # noqa: E402
    BankScreen,
    BreakScreen,
    ComposerScreen,
    FreeScreen,
    HomeScreen,
    JournalScreen,
    OnboardingScreen,
    RunningScreen,
    SettingsScreen,
    StatsScreen,
    SummaryScreen,
)

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "build/podglad")
SIZE = (1280, 800)

STATE_STUDY = {
    "phase": "STUDY",
    "mode": "STUDY",
    "remaining": 812,
    "total": 1500,
    "elapsed": 688,
    "pomodoro_index": 1,
    "pomodoros_done": 3,
    "pomodoros_aborted": 1,
    "study_seconds_done": 4188,
    "long_break_every": 4,
    "tag": "matematyka — ciągi i granice",
    "goal_note": "Przerobić rozdział 4 i zrobić 20 zadań z granic ciągów.",
    "blocked": {"blocked": 14, "suspended": 9, "killed": 5},
    "recent": [
        {"name": "steam.exe", "action": "suspended", "ts": 1_769_000_000},
        {"name": "discord.exe", "action": "suspended", "ts": 1_769_000_100},
        {"name": "youtube.com", "action": "blocked", "ts": 1_769_000_200},
    ],
    "summary": {"balance": 1320, "earned_today": 900, "cap_minutes": 120, "cap_left": 2100,
                "streak": 6, "best_streak": 11, "ttl_days": 30, "focus_score": 68},
    "daily": {"study_seconds": 4188, "pomodoros_done": 3},
    "lots": [
        {"created_at": 1_768_900_000, "expires_at": 1_771_500_000, "remaining_seconds": 900,
         "original_seconds": 1500, "note": "pomodoro"},
        {"created_at": 1_768_980_000, "expires_at": 1_771_600_000, "remaining_seconds": 420,
         "original_seconds": 600, "note": "korekta ręczna"},
    ],
    "ledger": [
        {"ts": 1_769_000_000, "delta_seconds": 1500, "reason": "EARNED", "note": "sesja 42"},
        {"ts": 1_769_000_500, "delta_seconds": -600, "reason": "SPENT", "note": "tryb wolny"},
        {"ts": 1_769_000_900, "delta_seconds": -300, "reason": "PENALTY",
         "note": "przerwane pomodoro po 12 minutach"},
    ],
    "presets": [
        {"name": "Matura — matematyka", "payload": {"study_minutes": 50, "break_minutes": 10}},
        {"name": "Angielski — słówka", "payload": {"study_minutes": 25, "break_minutes": 5}},
        {"name": "Bardzo długa nazwa presetu do sprawdzenia elidowania", "payload": {"study_minutes": 45}},
    ],
    "events": [
        {"kind": "BLOCKED_APP", "ts": 1_769_000_200, "detail": {"name": "steam.exe"}},
        {"kind": "BLOCKED_SITE", "ts": 1_769_000_100,
         "detail": {"host": "www.youtube.com", "reason": "not-in-allowlist"}},
        {"kind": "SESSION_START", "ts": 1_769_000_000, "detail": {}},
    ],
    "plan": {"study_minutes": 25, "break_minutes": 5, "tag": "matematyka",
             "goal_note": "Rozdział 4: granice ciągów — 20 zadań."},
    "result": {"status": "COMPLETED", "study_seconds": 4188, "plan_seconds": 4200, "pomodoros_done": 3,
               "pomodoros_aborted": 1, "earned": 1400, "penalty": 0, "blocked": 14, "balance": 1320,
               "tag": "matematyka", "goal_note": "Rozdział 4: granice ciągów — 20 zadań.",
               "reason": "completed"},
    "series": [
        {"day": f"2026-09-{day:02d}", "study_seconds": day * 240, "pomodoros_done": day % 5,
         "pomodoros_aborted": day % 2, "sessions": 1 + day % 3, "blocked_attempts": day * 2}
        for day in range(1, 31)
    ],
    "score": 68,
    "heatmap": {"matrix": [[(row * col) % 7 / 7 for col in range(24)] for row in range(7)],
                "row_labels": ["PON", "WTO", "ŚR", "CZW", "PT", "SOB", "NDZ"],
                "col_labels": [f"{hour:02d}" if hour % 3 == 0 else "" for hour in range(24)]},
    "blocked": [{"kind": "BLOCKED_SITE", "n": 42}, {"kind": "BLOCKED_APP", "n": 17}],
    "processes": [{"process_name": "chrome.exe", "seconds": 5400},
                  {"process_name": "very-long-process-name.exe", "seconds": 1800}],
    "apps": [
        {"id": 1, "label": "Google Chrome", "match_kind": "name", "match_value": "chrome.exe",
         "category": "STUDY"},
        {"id": 2, "label": "Bardzo długa nazwa profilu aplikacji do sprawdzenia elidowania",
         "match_kind": "signature", "match_value": "sha1:abcdef1234567890:123456", "category": "BLOCKED"},
    ],
    "sites": [
        {"id": 1, "host": "*.wikipedia.org", "label": "Wikipedia", "category": "STUDY"},
        {"id": 2, "host": "*.bardzo-dluga-domena-rozpraszajaca.example.com",
         "label": "Przykładowy rozpraszacz", "category": "BLOCKED"},
    ],
    "settings": {
        "session": {"study_minutes": 25, "break_minutes": 5, "long_break_minutes": 15,
                    "long_break_every": 4, "arming_seconds": 3, "auto_start_break": True,
                    "auto_start_next_study": False, "allow_end_early": False},
        "ui": {"language": "pl", "app_icon_size": 40, "color_app_icons": False},
    },
    "storage": {"path": "C:/Users/Nelek/AppData/Local/Cisza/cisza.db", "sessions": 128,
                "events": 1402, "size": "2.4 MB"},
    "has_pin": True,
    "breathing": True,
    "free_seconds_left": 640,
    "granted_seconds": 1500,
    "balance": 1320,
    "blocked_count": 14,
}


def render(app: QApplication, screen, name: str, payload: dict, size=SIZE) -> str:
    screen.resize(*size)
    screen.set_data(dict(payload))
    screen.show()
    app.processEvents()
    path = OUT / f"{name}.png"
    screen.grab().save(str(path))
    screen.hide()
    return str(path)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    theme.apply_to(app)

    plain = dict(STATE_STUDY)
    plain.pop("state", None)
    data = {**plain, "state": STATE_STUDY}

    empty_state = {"phase": "IDLE", "mode": "STUDY", "remaining": 0, "total": 0,
                   "pomodoros_done": 0, "pomodoros_aborted": 0, "study_seconds_done": 0}

    screens = [
        (HomeScreen(), "home", data),
        (RunningScreen(), "running", data),
        (BreakScreen(), "break", {**data, "state": {**STATE_STUDY, "phase": "BREAK", "remaining": 240}}),
        (FreeScreen(), "free", data),
        (BankScreen(), "bank", data),
        (StatsScreen(), "stats", data),
        (SettingsScreen(), "settings", data),
        (ComposerScreen(), "composer", data),
        (OnboardingScreen(), "onboarding", data),
        (SummaryScreen(), "summary", data),
        (JournalScreen(), "journal", data),
    ]

    written: list[str] = []
    for screen, name, payload in screens:
        written.append(render(app, screen, name, payload))

    # warianty pustych stanow (brak danych = krotki komunikat, nie pusta lista)
    empty_payload = {"state": empty_state, "summary": {}, "events": [], "lots": [], "ledger": [],
                     "presets": [], "series": [], "blocked": [], "processes": [], "apps": [], "sites": []}
    for screen, name in (
        (RunningScreen(), "running_pusty"),
        (BankScreen(), "bank_pusty"),
        (JournalScreen(), "journal_pusty"),
        (StatsScreen(), "stats_pusty"),
        (HomeScreen(), "home_pusty"),
        (SettingsScreen(), "settings_pusty"),
    ):
        written.append(render(app, screen, name, empty_payload))

    # minimalny rozmiar okna (app.py: setMinimumSize(720, 520)) - nic nie moze sie uciac
    for screen, name in (
        (RunningScreen(), "running_min"),
        (SummaryScreen(), "summary_min"),
        (SettingsScreen(), "settings_min"),
        (ComposerScreen(), "composer_min"),
        (BankScreen(), "bank_min"),
        (StatsScreen(), "stats_min"),
    ):
        written.append(render(app, screen, f"{name}_720x520", data, size=(720, 520)))

    # zakladki ustawien (Aplikacje / Strony / Dane / System)
    settings_screen = SettingsScreen()
    settings_screen.resize(*SIZE)
    settings_screen.set_data(dict(data))
    settings_screen.show()
    app.processEvents()
    for index, label in ((1, "aplikacje"), (2, "strony"), (6, "dane"), (7, "system")):
        settings_screen._tabs.setCurrentIndex(index)
        app.processEvents()
        path = OUT / f"settings_{label}.png"
        settings_screen.grab().save(str(path))
        written.append(str(path))
    settings_screen.hide()

    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
