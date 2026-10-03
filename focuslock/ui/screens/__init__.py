"""Ekrany interfejsu (kontrakt: docs/INTERFACES.md sekcja 17)."""
from __future__ import annotations

from .bank import BankScreen
from .base import MODE_LABELS, PHASE_LABELS, Screen, plan_payload
from .break_ import BreakScreen
from .composer import ComposerScreen
from .free import FreeScreen
from .home import HomeScreen
from .journal import JournalScreen
from .onboarding import OnboardingScreen
from .pin import PinDialog
from .running import RunningScreen
from .settings import SettingsScreen
from .stats import StatsScreen
from .summary import SummaryScreen

__all__ = [
    "BankScreen",
    "BreakScreen",
    "ComposerScreen",
    "FreeScreen",
    "HomeScreen",
    "JournalScreen",
    "MODE_LABELS",
    "OnboardingScreen",
    "PHASE_LABELS",
    "PinDialog",
    "RunningScreen",
    "Screen",
    "SettingsScreen",
    "StatsScreen",
    "SummaryScreen",
    "plan_payload",
]
