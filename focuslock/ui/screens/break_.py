"""Ekran przerwy: oddechowy okrag i czas do konca przerwy.

Tresc (okrag, podpowiedz oddechu, licznik cykli) jest wysrodkowana i przewijalna,
a przyciski zostaja na dole - w niskim oknie nic nie ucieka poza ekran.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout

from .. import charts
from ..widgets import Card, GhostButton, PrimaryButton, RingProgress, labels
from .base import Screen, as_dict

INHALE = 4.0
HOLD = 4.0
EXHALE = 6.0
REST = 2.0
CYCLE = INHALE + HOLD + EXHALE + REST

BREATH_HINT = "Odsuń wzrok od ekranu. Wdech 4 s, zatrzymaj 4 s, wydech 6 s — powtarzaj do końca przerwy."


class BreakScreen(Screen):
    """Oddech: okrag rosnie i opada, tekst podpowiada faze; zero koloru."""

    TITLE = "PRZERWA"

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        # Rodzaj przerwy pokazujemy w tytule strony (bez dubletu w naglowku).
        self._kind = labels.caption("PRZERWA")

        body = self.make_scroll_body(spacing=16)

        card = Card()
        self._ring = RingProgress(thickness=12)
        self._ring.set_text("05:00")
        self._ring.set_caption("ODDECH")
        self._breath = labels.subtitle("WDECH")
        self._breath.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = labels.hint(BREATH_HINT)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setMinimumWidth(420)
        self._hint.setMaximumWidth(560)
        self._cycle_note = labels.caption("")
        self._cycle_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cycle_note.setVisible(False)

        ring_row = QHBoxLayout()
        ring_row.addStretch(1)
        ring_row.addWidget(self._ring)
        ring_row.addStretch(1)
        card.body.addStretch(1)
        card.add_layout(ring_row)
        card.add(self._breath)
        hint_row = QHBoxLayout()
        hint_row.addStretch(1)
        hint_row.addWidget(self._hint)
        hint_row.addStretch(1)
        card.add_layout(hint_row)
        card.add(self._cycle_note)
        card.body.addStretch(1)
        body.addWidget(card, 1)

        actions = self.action_bar()
        self._skip = GhostButton("POMIŃ PRZERWĘ")
        self._skip.clicked.connect(lambda: self.request_action.emit("skip_break", {}))
        self._resume = PrimaryButton("WRÓĆ DO NAUKI")
        self._resume.clicked.connect(lambda: self.request_action.emit("skip_break", {}))
        actions.addWidget(self._skip)
        actions.addStretch(1)
        actions.addWidget(self._resume)

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick_breath)
        self._started_at = time.monotonic()
        self._cycles = 0

    # ------------------------------------------------------------------ cykl
    def showEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().showEvent(event)
        self._started_at = time.monotonic()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self._timer.stop()
        super().hideEvent(event)

    def _tick_breath(self) -> None:
        elapsed = (time.monotonic() - self._started_at) % CYCLE
        if elapsed < INHALE:
            amount = elapsed / INHALE
            phase = "WDECH"
        elif elapsed < INHALE + HOLD:
            amount = 1.0
            phase = "ZATRZYMAJ"
        elif elapsed < INHALE + HOLD + EXHALE:
            amount = 1.0 - (elapsed - INHALE - HOLD) / EXHALE
            phase = "WYDECH"
        else:
            amount = 0.0
            phase = "ODPOCZYNEK"
        self._ring.set_value(amount)
        if phase != self._breath.text():
            self._breath.setText(phase)
        cycles = int((time.monotonic() - self._started_at) // CYCLE)
        if cycles != self._cycles:
            self._cycles = cycles
            self._cycle_note.setText(f"Ukończone cykle oddechu: {cycles}")
            self._cycle_note.setVisible(True)

    # ------------------------------------------------------------------ dane
    def render(self, data: dict) -> None:
        state = as_dict(data.get("state"))
        if not state and ("phase" in data or "remaining" in data):
            state = as_dict(data)
        phase = str(state.get("phase") or "BREAK").upper()
        remaining = float(state.get("remaining") or 0.0)
        self._kind.setText("DŁUGA PRZERWA" if phase == "LONG_BREAK" else "PRZERWA")
        self.set_page_title(self._kind.text())
        self._ring.set_text(charts.fmt_clock(remaining))
        if not self._timer.isActive():
            self._started_at = time.monotonic()
        if bool(data.get("breathing", True)) is False:
            self._timer.stop()
            self._ring.set_value(0.0)
            self._breath.setText("ODDECH WYŁĄCZONY")
            self._hint.setText("Ekran oddechu jest wyłączony w ustawieniach wyglądu.")
            self._cycle_note.setVisible(False)
