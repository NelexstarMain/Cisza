"""Ekran domowy: karta startowa, szybki start z presetow, bank/dzis/seria.

Uklad: naglowek strony, przewijalna tresc (start + bank + presety + kafle)
i zadnych pustych list - brak presetow konczy sie krotkim komunikatem.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout

from .. import charts
from ..widgets import (
    Card,
    EmptyState,
    GhostButton,
    PrimaryButton,
    RingProgress,
    StatTile,
    labels,
)
from .base import PHASE_LABELS, Screen, as_dict, as_float, as_int, as_list, clear_layout

PRESET_MAX_WIDTH = 300
PRESETS_EMPTY = "Brak zapisanych presetów"
PRESETS_EMPTY_DETAIL = "Zbuduj plan w kreatorze sesji i zapisz go jako preset."


class HomeScreen(Screen):
    """Punkt wyjscia: jedna decyzja — rozpoczac sesje."""

    TITLE = "START"

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        # Bez napisu "GOTOWY DO NAUKI": naglowek pokazuje tylko to, czego nie
        # widac gdzie indziej - poziom kontroli nad systemem (albo faze sesji).
        self._status = labels.caption("KONTROLA: PODSTAWOWA")
        self.header_widget(self._status)

        body = self.make_scroll_body(spacing=16)

        top = QHBoxLayout()
        top.setSpacing(16)

        self._start_card = Card()
        self._timer = labels.timer_label("25:00")
        self._timer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plan_caption = labels.caption("25 MIN NAUKI · 5 MIN PRZERWY")
        self._plan_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._goal = labels.body("")
        self._goal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._goal.setVisible(False)
        self._tag = labels.elided("", role="caption")
        self._tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tag.setVisible(False)
        self._start_button = PrimaryButton("ROZPOCZNIJ SESJĘ")
        self._start_button.clicked.connect(lambda: self.emit_start(self._plan))
        self._free_button = GhostButton("TRYB WOLNY (Z BANKU)")
        self._free_button.clicked.connect(lambda: self.emit_start({**self._plan, "mode": "FREE"}))
        self._compose_button = GhostButton("KREATOR SESJI")
        self._compose_button.clicked.connect(lambda: self.request_action.emit("open_composer", {}))
        self._start_card.add(self._timer)
        self._start_card.add(self._plan_caption)
        self._start_card.add(self._goal)
        self._start_card.add(self._tag)
        self._start_card.add(self._start_button)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self._free_button)
        actions.addWidget(self._compose_button)
        actions.addStretch(1)
        self._start_card.add_layout(actions)
        self._start_card.body.addStretch(1)

        self._ring_card = Card("BANK MINUT", "LIMIT DNIA")
        self._ring = RingProgress(thickness=12)
        self._ring.set_text("0 min")
        self._ring.set_caption("DOSTĘPNE")
        self._ring_note = labels.hint("")
        self._ring_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ring_card.add(self._ring)
        self._ring_card.add(self._ring_note)
        self._ring_card.body.addStretch(1)

        top.addWidget(self._start_card, 3)
        top.addWidget(self._ring_card, 2)
        body.addLayout(top)

        self._preset_card = Card("SZYBKI START", "PRESETY")
        self._preset_empty = EmptyState(PRESETS_EMPTY, PRESETS_EMPTY_DETAIL, margins=(18, 14, 18, 14))
        self._preset_empty.setMinimumHeight(76)
        self._preset_card.add(self._preset_empty)
        self._preset_grid = QGridLayout()
        self._preset_grid.setSpacing(8)
        self._preset_card.add_layout(self._preset_grid)
        body.addWidget(self._preset_card)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._tile_bank = StatTile("BANK", "0 min")
        self._tile_today = StatTile("DZIŚ", "0 min")
        self._tile_streak = StatTile("SERIA", "0 dni")
        for tile in (self._tile_bank, self._tile_today, self._tile_streak):
            stats.addWidget(tile, 1)
        body.addLayout(stats)
        body.addStretch(1)

        self._plan: dict = {}
        self._presets: list[dict] = []

    # ------------------------------------------------------------------ dane
    def render(self, data: dict) -> None:
        state = as_dict(data.get("state"))
        summary = as_dict(data.get("summary"))
        daily = as_dict(data.get("daily"))
        plan = as_dict(data.get("plan")) or as_dict(self.data("last_plan"))
        presets = data.get("presets")
        if presets is not None:
            self._presets = [as_dict(item) for item in as_list(presets)]
            self._rebuild_presets()
            if not self._plan and self._presets:
                self._plan = dict(self._presets[0].get("payload") or {})

        if plan:
            self._plan = dict(plan)

        phase = str(state.get("phase") or "IDLE").upper()
        running = phase not in ("IDLE", "DONE")
        control = "KONTROLA: PEŁNA (ADMIN)" if data.get("is_admin") else "KONTROLA: PODSTAWOWA"
        if running and state.get("remaining") is not None:
            self._timer.setText(charts.fmt_clock(state.get("remaining") or 0))
            self._status.setText(f"{PHASE_LABELS.get(phase, phase)} · {control}")
        else:
            study_minutes = as_int(self._plan.get("study_minutes"), 25)
            self._timer.setText(charts.fmt_clock(study_minutes * 60))
            self._status.setText(control)

        tag = str(self._plan.get("tag") or "")
        self._tag.set_full_text(tag.upper())
        self._tag.setVisible(bool(tag))
        goal = str(self._plan.get("goal_note") or "").strip()
        self._goal.setText(goal)
        self._goal.setVisible(bool(goal))
        self._plan_caption.setText(
            f"{as_int(self._plan.get('study_minutes'), 25)} MIN NAUKI · "
            f"{as_int(self._plan.get('break_minutes'), 5)} MIN PRZERWY"
        )
        self._start_button.setEnabled(not running)
        self._free_button.setEnabled(not running)

        balance = as_int(summary.get("balance"))
        cap_minutes = as_int(summary.get("cap_minutes"), 60)
        earned_today = as_int(summary.get("earned_today"))
        cap_left = as_int(summary.get("cap_left"))
        ttl_days = as_int(summary.get("ttl_days"))
        study_today = as_float(daily.get("study_seconds"))
        self._tile_bank.set_value(charts.fmt_minutes(balance, short=True))
        self._tile_bank.set_note(f"na dziś {charts.fmt_minutes(cap_left, short=True)}")
        self._tile_today.set_value(charts.fmt_minutes(study_today, short=True))
        self._tile_today.set_note(f"pomodoro: {as_int(daily.get('pomodoros_done'))}")
        self._tile_today.set_progress(study_today / 60.0 / cap_minutes if cap_minutes else None)
        streak = as_int(summary.get("streak"))
        best_streak = as_int(summary.get("best_streak"))
        self._tile_streak.set_value(f"{streak} dni")
        self._tile_streak.set_note(f"rekord: {best_streak}")

        used = max(0.0, min(1.0, earned_today / (cap_minutes * 60.0))) if cap_minutes else 0.0
        self._ring.set_value(used)
        self._ring.set_text(charts.fmt_minutes(balance, short=True))
        self._ring.set_caption("DOSTĘPNE")
        parts = [
            f"dziś zarobione: {charts.fmt_minutes(earned_today)}",
            f"wykorzystano {int(used * 100)}% dziennego limitu",
        ]
        if ttl_days:
            parts.append(f"minuty wygasają po {ttl_days} dniach")
        self._ring_note.setText(" · ".join(parts))

    def _rebuild_presets(self) -> None:
        clear_layout(self._preset_grid)
        self._preset_empty.setVisible(not self._presets)
        limit = 6
        columns = 3
        for index, preset in enumerate(self._presets[:limit]):
            name = str(preset.get("name") or f"PRESET {index + 1}")
            payload = dict(preset.get("payload") or {})
            button = GhostButton(name.upper())
            button.setMinimumHeight(40)
            button.setMaximumWidth(PRESET_MAX_WIDTH)
            button.setToolTip(name)
            button.setText(labels.elide_text(button, name.upper(), PRESET_MAX_WIDTH - 36, role="button"))
            button.clicked.connect(lambda _checked=False, p=payload: self.emit_start(p))
            self._preset_grid.addWidget(button, index // columns, index % columns)
        if len(self._presets) > limit:
            more = labels.hint(f"+ {len(self._presets) - limit} kolejnych w kreatorze sesji")
            self._preset_grid.addWidget(more, (limit + 2) // 3, 0, 1, columns)
        for column in range(columns):
            self._preset_grid.setColumnStretch(column, 1)
