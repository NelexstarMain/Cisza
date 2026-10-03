"""HUD sesji: timer, tryb, pomodoro, licznik blokad, ostatnie zdarzenia.

Uklad: naglowek strony (faza + tag), przewijalna tresc (timer + cykl + kafle +
dziennik na zywo) i przyklejony pasek akcji. Dzieki przewijaniu nic nie ucina sie
w niskim oknie, a puste stany maja krotki komunikat zamiast pustej listy.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout

from .. import charts
from ..widgets import (
    Card,
    DangerButton,
    EmptyState,
    GhostButton,
    RingProgress,
    StatTile,
    labels,
)
from .base import MODE_LABELS, PHASE_LABELS, Screen, as_dict, as_float, as_int, as_list

EVENTS_EMPTY = "Brak zdarzen"
EVENTS_EMPTY_DETAIL = "Gdy Cisza cos zablokuje, wpis pojawi sie tutaj od razu."


class RunningScreen(Screen):
    """Widok kontroli: jedno miejsce z czasem, postępem i dowodami blokad."""

    TITLE = "SESJA W TOKU"

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        self._tag = labels.elided("BEZ TAGU", role="caption")
        self._tag.setMaximumWidth(260)
        self._tag.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.header_widget(self._tag)

        body = self.make_scroll_body(spacing=16)

        top = QHBoxLayout()
        top.setSpacing(16)

        timer_card = Card()
        self._ring = RingProgress(thickness=14)
        self._ring.set_text("25:00")
        self._ring.set_caption("POZOSTAŁO")
        self._pomodoro_caption = labels.caption("POMODORO 1")
        self._pomodoro_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._goal = labels.body("")
        self._goal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        timer_card.add(self._ring)
        timer_card.add(self._pomodoro_caption)
        timer_card.add(self._goal)
        timer_card.body.addStretch(1)
        top.addWidget(timer_card, 3)

        right = QVBoxLayout()
        right.setSpacing(16)
        self._cycle_card = Card("CYKL POMODORO", "POSTĘP W BIEŻĄCEJ SERII")
        self._cycle = labels.subtitle("□□□□")
        self._cycle_note = labels.caption("")
        self._cycle_card.add(self._cycle)
        self._cycle_card.add(self._cycle_note)
        right.addWidget(self._cycle_card)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self._tile_blocked = StatTile("BLOKADY", "0")
        self._tile_study = StatTile("NAUKA", "0 min")
        self._tile_free = StatTile("WOLNE", "—")
        for tile in (self._tile_blocked, self._tile_study, self._tile_free):
            tiles.addWidget(tile, 1)
        right.addLayout(tiles)

        self._events_card = Card("OSTATNIE ZDARZENIA", "DZIENNIK NA ŻYWO")
        self._events_empty = EmptyState(EVENTS_EMPTY, EVENTS_EMPTY_DETAIL, margins=(18, 16, 18, 16))
        self._events_card.add(self._events_empty)
        self._events = QListWidget()
        self._events.setMinimumHeight(160)
        self._events.setAlternatingRowColors(False)
        self._events.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._events.setWordWrap(False)
        self._events_card.add(self._events)
        self._events_card.body.setStretchFactor(self._events, 1)
        right.addWidget(self._events_card, 1)
        top.addLayout(right, 2)
        body.addLayout(top, 1)

        actions = self.action_bar()
        self._pause = GhostButton("PAUZA")
        self._pause.clicked.connect(self._toggle_pause)
        self._break = GhostButton("POMIŃ DO PRZERWY")
        self._break.clicked.connect(lambda: self.request_break.emit(True))
        self._end = DangerButton("ZAKOŃCZ SESJĘ")
        self._end.clicked.connect(lambda: self.request_end.emit("user"))
        self._panic = GhostButton("WYJŚCIE AWARYJNE (PIN)")
        self._panic.clicked.connect(lambda: self.request_end.emit("panic"))
        actions.addWidget(self._pause)
        actions.addWidget(self._break)
        actions.addStretch(1)
        actions.addWidget(self._panic)
        actions.addWidget(self._end)

        self._paused = False

    # ------------------------------------------------------------------ dane
    def render(self, data: dict) -> None:
        state = as_dict(data.get("state"))
        if not state and ("phase" in data or "remaining" in data):
            state = as_dict(data)
        blocked = as_dict(data.get("blocked"))

        phase = str(state.get("phase") or "STUDY").upper()
        mode = str(state.get("mode") or "STUDY").upper()
        remaining = as_float(state.get("remaining"))
        total = as_float(state.get("total"))
        phase_label = PHASE_LABELS.get(phase, phase)
        mode_label = MODE_LABELS.get(mode, mode)
        self.set_page_title(phase_label if phase_label == mode_label else f"{phase_label} · {mode_label}")
        self._ring.set_text(charts.fmt_clock(remaining))
        label = "PRZERWA" if phase in ("BREAK", "LONG_BREAK") else "POZOSTAŁO"
        self._ring.set_caption(label)
        progress = 1.0 - (remaining / total) if total > 0 else 0.0
        self._ring.set_value(progress)

        tag = str(state.get("tag") or self.data("tag") or "")
        self._tag.set_full_text(f"TAG: {tag.upper()}" if tag else "BEZ TAGU")
        goal = str(state.get("goal_note") or self.data("goal_note") or "").strip()
        self._goal.setText(goal or "Bez zapisanego celu — dopisz go w kreatorze sesji.")

        every = max(1, as_int(state.get("long_break_every") or self.data("long_break_every"), 4))
        index = as_int(state.get("pomodoro_index"))
        position = max(1, min(every, index + 1))
        done = as_int(state.get("pomodoros_done"))
        marks = ["▣" if step < position - 1 else ("▮" if step == position - 1 else "□") for step in range(every)]
        self._cycle.setText(" ".join(marks))
        self._cycle_note.setText(
            f"pomodoro {position} z {every} · ukończone {done} · przerwane {as_int(state.get('pomodoros_aborted'))}"
        )
        self._pomodoro_caption.setText(f"POMODORO {position}")

        self._paused = bool(state.get("paused"))
        self._pause.setText("WZNÓW" if self._paused else "PAUZA")

        study_seconds = as_float(state.get("study_seconds_done"))
        free_left = state.get("free_seconds_left")
        self._tile_blocked.set_value(str(as_int(blocked.get("blocked"))))
        self._tile_blocked.set_note(
            f"uśp. {as_int(blocked.get('suspended'))} · ubite {as_int(blocked.get('killed'))}"
        )
        self._tile_study.set_value(charts.fmt_minutes(study_seconds, short=True))
        self._tile_study.set_progress(study_seconds / (total or study_seconds or 1.0) if total else None)
        if free_left is None:
            self._tile_free.set_value("—")
            self._tile_free.set_note("tryb nauki")
        else:
            self._tile_free.set_value(charts.fmt_clock(as_float(free_left)))
            self._tile_free.set_note("pozostało z banku")

        events = data.get("events")
        if events is not None:
            self._fill_events(as_list(events))
        recent = blocked.get("recent")
        if recent and not events:
            self._fill_events(
                [
                    as_dict(row) | {"kind": str(as_dict(row).get("action") or "BLOKADA").upper(), "detail": as_dict(row)}
                    for row in as_list(recent)
                ]
            )

    def _fill_events(self, events) -> None:
        self._events.clear()
        for event in list(events)[:40]:
            if not isinstance(event, dict):
                continue
            stamp = charts.fmt_time_short(event.get("ts"))
            kind = str(event.get("kind") or "ZDARZENIE")
            detail = event.get("detail") or {}
            if isinstance(detail, dict):
                extra = detail.get("name") or detail.get("host") or detail.get("reason") or ""
            else:
                extra = str(detail)
            text = f"{stamp}  {kind}"
            if extra:
                text += f"  ·  {extra}"
            self._add_event_item(text)
        self._sync_empty()

    def _add_event_item(self, text: str) -> None:
        item = QListWidgetItem(str(text))
        item.setToolTip(str(text))
        self._events.addItem(item)

    def _sync_empty(self) -> None:
        empty = self._events.count() == 0
        self._events_empty.setVisible(empty)
        self._events.setVisible(not empty)

    # ------------------------------------------------------------------ akcje
    def render_event(self, event: dict) -> None:
        """Zdarzenie z kontrolera pojawia sie od razu na gorze listy."""
        detail = event.get("detail") if isinstance(event, dict) else None
        extra = ""
        if isinstance(detail, dict):
            extra = str(detail.get("name") or detail.get("host") or detail.get("reason") or "")
        stamp = charts.fmt_time_short((event or {}).get("ts"))
        line = f"{stamp}  {str((event or {}).get('kind') or 'ZDARZENIE')}"
        if extra:
            line += f"  ·  {extra}"
        item = QListWidgetItem(line)
        item.setToolTip(line)
        self._events.insertItem(0, item)
        while self._events.count() > 60:
            self._events.takeItem(self._events.count() - 1)
        self._sync_empty()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self._pause.setText("WZNÓW" if self._paused else "PAUZA")
        self.request_pause.emit(self._paused)
