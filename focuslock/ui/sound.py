"""Odtwarzanie subtelnych sygnalow dzwiekowych (gong / chime konca pomodoro).

Wszystkie dzwieki odtwarzane sa w tle (daemon thread) i nie blokuja glownej petli Qt.
Brak winsound (np. na Linuxie / macOS) lub brak karty dzwiekowej konczy sie cichym
powrotem bez rzucania wyjatkow.
"""
from __future__ import annotations

import threading
from typing import Optional

try:
    import winsound
except ImportError:
    winsound = None  # noqa: F401


def _play_worker(kind: str) -> None:
    if winsound is None:
        return
    try:
        if kind in ("break_start", "pomodoro_end"):
            # Subtelny, cieply dwutonowy gong (D5 -> A5): koniec pomodoro / start przerwy
            winsound.Beep(587, 220)
            winsound.Beep(880, 450)
        elif kind == "study_start":
            # Subtelny gong powrotu do skupienia (A5 -> D6): koniec przerwy / start nauki
            winsound.Beep(880, 180)
            winsound.Beep(1175, 350)
        elif kind == "bell":
            winsound.MessageBeep(winsound.MB_OK)
    except Exception:
        pass


def play_sound(kind: str = "pomodoro_end", enabled: bool = True) -> None:
    """Odtwarza dzwiek asynchronicznie, jesli dzwieki sa wlaczone w konfiguracji."""
    if not enabled or winsound is None:
        return
    thread = threading.Thread(target=_play_worker, args=(kind,), name="cisza-sound", daemon=True)
    thread.start()
