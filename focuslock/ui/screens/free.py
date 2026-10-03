"""Tryb wolny: licznik zuzycia minut z banku.

Uklad: naglowek z podpisem stanu, przewijalna tresc (duzy licznik + kafle)
i pasek akcji na dole - w niskim oknie nic sie nie ucina.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout

from .. import charts
from ..widgets import Card, DangerButton, DitheredBar, GhostButton, StatTile, labels
from .base import Screen, as_dict, as_float, as_int

FREE_HINT = (
    "Czas wolny schodzi z banku minut zarobionych w nauce. "
    "Gdy się skończy, Cisza wraca do blokady."
)


class FreeScreen(Screen):
    """Wydawanie banku: jedno spojrzenie na licznik i jasny sposob zakonczenia."""

    TITLE = "TRYB WOLNY"

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        self._status = labels.caption("LICZNIK ZUŻYCIA BANKU")
        self.header_widget(self._status)

        body = self.make_scroll_body(spacing=16)

        card = Card()
        self._timer = labels.timer_label("00:00")
        self._timer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar = DitheredBar(bar_height=12)
        self._progress_caption = labels.caption("WYKORZYSTANO 0 MIN Z 0 MIN")
        self._progress_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = labels.hint(FREE_HINT)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.add(self._timer)
        card.add(self._bar)
        card.add(self._progress_caption)
        card.add(self._hint)
        body.addWidget(card)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self._tile_bank = StatTile("SALDO BANKU", "0 min")
        self._tile_used = StatTile("ZUŻYTE", "0 min")
        self._tile_left = StatTile("POZOSTAŁO", "0 min")
        for tile in (self._tile_bank, self._tile_used, self._tile_left):
            tiles.addWidget(tile, 1)
        body.addLayout(tiles)
        body.addStretch(1)

        actions = self.action_bar()
        self._plus5 = GhostButton("+5 MIN")
        self._plus5.clicked.connect(lambda: self.request_free.emit(300))
        self._plus15 = GhostButton("+15 MIN")
        self._plus15.clicked.connect(lambda: self.request_free.emit(900))
        self._return = GhostButton("ODDAJ RESZTĘ")
        self._return.clicked.connect(lambda: self.request_action.emit("return_free", {}))
        self._end = DangerButton("ZAKOŃCZ TRYB WOLNY")
        self._end.clicked.connect(lambda: self.request_end.emit("free_done"))
        actions.addWidget(self._plus5)
        actions.addWidget(self._plus15)
        actions.addWidget(self._return)
        actions.addStretch(1)
        actions.addWidget(self._end)

    # ------------------------------------------------------------------ dane
    def render(self, data: dict) -> None:
        state = as_dict(data.get("state"))
        if not state and ("free_seconds_left" in data or "remaining" in data):
            state = as_dict(data)
        summary = as_dict(data.get("summary"))

        left = state.get("free_seconds_left")
        if left is None:
            left = data.get("free_seconds_left")
        left_seconds = as_float(left)
        granted = as_float(state.get("total") or data.get("granted_seconds") or data.get("free_seconds"))
        if granted <= 0:
            granted = max(left_seconds, as_float(summary.get("balance")))
        used = max(0.0, granted - left_seconds)

        self._timer.setText(charts.fmt_clock(left_seconds))
        self._bar.set_value(left_seconds / granted if granted > 0 else 0.0)
        self._progress_caption.setText(
            f"WYKORZYSTANO {charts.fmt_minutes(used)} Z {charts.fmt_minutes(granted)}"
        )
        self._tile_bank.set_value(charts.fmt_minutes(as_int(summary.get("balance")), short=True))
        self._tile_bank.set_note("dostępne minuty")
        self._tile_used.set_value(charts.fmt_minutes(used, short=True))
        self._tile_used.set_note("w tej sesji")
        self._tile_left.set_value(charts.fmt_clock(left_seconds))
        self._tile_left.set_note("do końca trybu wolnego")
        self._status.setText("LICZNIK ZUŻYCIA BANKU")
        self._end.setEnabled(left_seconds > 0 or bool(state))
