"""Podsumowanie po sesji: wynik, zarobek i ocena 1-5.

Uklad: naglowek z wynikiem sesji, przewijalna tresc (wynik + kafle + ocena)
i pasek akcji na dole. Ocena ma staly rytm przyciskow, a brak notatki celu
dostaje krotki komunikat zamiast pustego miejsca.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout

from .. import charts
from ..widgets import Card, GhostButton, PrimaryButton, RingProgress, StatTile, labels
from ..widgets.primitives import reason_label
from .base import Screen, as_dict, as_float, as_int


class SummaryScreen(Screen):
    """Domkniecie sesji: co sie udalo, ile wpadlo do banku, ocena wlasna."""

    TITLE = "PODSUMOWANIE SESJI"
    RATINGS = (
        (1, "▪····"),
        (2, "▪▪···"),
        (3, "▪▪▪··"),
        (4, "▪▪▪▪·"),
        (5, "▪▪▪▪▪"),
    )

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        self._status = labels.caption("")
        self.header_widget(self._status)

        body = self.make_scroll_body(spacing=16)

        top = QHBoxLayout()
        top.setSpacing(16)
        result_card = Card()
        self._ring = RingProgress(thickness=12)
        self._ring.set_text("—")
        self._ring.set_caption("WYKONANE")
        self._tag = labels.elided("BEZ TAGU", role="caption")
        self._tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._goal = labels.body("Brak notatki celu.")
        self._goal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        result_card.add(self._ring)
        result_card.add(self._tag)
        result_card.add(self._goal)
        result_card.body.addStretch(1)
        top.addWidget(result_card, 2)

        side = Card("WYNIK")
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self._tile_study = StatTile("CZAS NAUKI", "0 min")
        self._tile_pomodoro = StatTile("POMODORO", "0")
        self._tile_aborted = StatTile("PRZERWANE", "0")
        for tile in (self._tile_study, self._tile_pomodoro, self._tile_aborted):
            tiles.addWidget(tile, 1)
        side.add_layout(tiles)
        tiles2 = QHBoxLayout()
        tiles2.setSpacing(10)
        self._tile_earned = StatTile("DO BANKU", "0 min")
        self._tile_blocked = StatTile("BLOKADY", "0")
        self._tile_balance = StatTile("SALDO", "0 min")
        for tile in (self._tile_earned, self._tile_blocked, self._tile_balance):
            tiles2.addWidget(tile, 1)
        side.add_layout(tiles2)
        self._note = labels.hint("")
        side.add(self._note)
        side.body.addStretch(1)
        top.addWidget(side, 3)
        body.addLayout(top, 1)

        rating_card = Card("OCENA SESJI", "1 = rozproszenie, 5 = pełne skupienie")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._rating_buttons: dict[int, GhostButton] = {}
        for value, glyph in self.RATINGS:
            button = GhostButton(glyph)
            button.setCheckable(True)
            button.setMinimumWidth(96)
            button.clicked.connect(lambda _checked=False, v=value: self._set_rating(v, emit=True))
            row.addWidget(button)
            self._rating_buttons[value] = button
        row.addStretch(1)
        rating_card.add_layout(row)
        self._rating_hint = labels.hint("Kliknij ocenę, żeby domknąć statystyki tej sesji.")
        rating_card.add(self._rating_hint)
        body.addWidget(rating_card)

        actions = self.action_bar()
        self._again = GhostButton("JESZCZE RAZ")
        self._again.clicked.connect(self._repeat)
        self._close = PrimaryButton("ZAMKNIJ PODSUMOWANIE")
        self._close.clicked.connect(lambda: self.request_screen.emit("home"))
        actions.addWidget(self._again)
        actions.addStretch(1)
        actions.addWidget(self._close)

        self._rating = 0
        self._last_plan: dict = {}
        self._set_rating(0)

    # ------------------------------------------------------------------ dane
    def _set_rating(self, value: int, emit: bool = False) -> None:
        self._rating = max(0, min(5, int(value)))
        for rating, button in self._rating_buttons.items():
            button.setChecked(rating == self._rating and self._rating > 0)
            button.setProperty("role", "primary" if button.isChecked() else "ghost")
            button.style().unpolish(button)
            button.style().polish(button)
        self._rating_hint.setText(
            f"Wybrana ocena: {self._rating} z 5."
            if self._rating
            else "Kliknij ocenę, żeby domknąć statystyki tej sesji."
        )
        if emit and self._rating:
            self.request_rating.emit(self._rating)

    def _repeat(self) -> None:
        self.emit_start(self._last_plan or self.data("plan") or {})

    def on_app_event(self, event: str, data: dict | None = None) -> None:
        """Zdarzenie `session_finished` zapamietuje plan, zeby 'JESZCZE RAZ' je odtworzyl."""
        if str(event) != "session_finished":
            return
        payload = as_dict(data)
        state = as_dict(payload.get("state"))
        plan = as_dict(payload.get("plan")) or state
        if plan:
            self._last_plan = {
                "mode": plan.get("mode") or "STUDY",
                "tag": plan.get("tag") or "",
                "goal_note": plan.get("goal_note") or "",
            }

    def render(self, data: dict) -> None:
        result = as_dict(data.get("result")) or as_dict(data.get("summary"))
        balance = as_dict(data.get("bank"))
        plan_seconds = as_float(result.get("plan_seconds") if result.get("plan_seconds") is not None else data.get("plan_seconds"))
        study_seconds = as_float(result.get("study_seconds") if result.get("study_seconds") is not None else result.get("actual_seconds"))
        completed = study_seconds / plan_seconds if plan_seconds > 0 else (1.0 if study_seconds else 0.0)
        self._ring.set_value(max(0.0, min(1.0, completed)))
        self._ring.set_text(charts.fmt_minutes(study_seconds, short=True))
        status = str(result.get("status") or data.get("status") or "").upper()
        status_label = {
            "COMPLETED": "UKOŃCZONA",
            "ABORTED": "PRZERWANA",
            "RUNNING": "W TOKU",
        }.get(status, "PODSUMOWANIE")
        self._status.setText(status_label)
        tag = str(result.get("tag") or data.get("tag") or "")
        self._tag.set_full_text(f"TAG: {tag.upper()}" if tag else "BEZ TAGU")
        goal = str(result.get("goal_note") or data.get("goal_note") or "").strip()
        self._goal.setText(goal or "Brak notatki celu w tej sesji.")

        self._tile_study.set_value(charts.fmt_minutes(study_seconds, short=True))
        self._tile_study.set_note(f"plan: {charts.fmt_minutes(plan_seconds, short=True)}")
        self._tile_pomodoro.set_value(str(as_int(result.get("pomodoros_done"))))
        self._tile_aborted.set_value(str(as_int(result.get("pomodoros_aborted"))))
        earned = as_float(result.get("earned") if result.get("earned") is not None else result.get("earned_seconds"))
        penalty = as_float(result.get("penalty"))
        self._tile_earned.set_value(charts.fmt_minutes(earned, short=True))
        reason = str(result.get("reason") or "")
        self._tile_earned.set_note(
            f"kara: {charts.fmt_minutes(penalty, short=True)}"
            if penalty
            else (reason_label(reason) or "bez kar")
        )
        self._tile_blocked.set_value(str(as_int(result.get("blocked") if result.get("blocked") is not None else data.get("blocked"))))
        self._tile_balance.set_value(charts.fmt_minutes(as_int(balance.get("balance") if balance.get("balance") is not None else result.get("balance")), short=True))
        self._tile_balance.set_note(f"seria: {as_int(balance.get('streak'))} dni")

        notes = []
        if status == "ABORTED":
            notes.append("Sesja przerwana — bank nie został zasilony.")
        if result.get("capped"):
            notes.append(f"Limit dzienny obciął {charts.fmt_minutes(as_float(result['capped']))}.")
        if as_int(result.get("pomodoros_done")) and not as_int(result.get("pomodoros_aborted")):
            notes.append("Wszystkie pomodoro domknięte.")
        self._note.setText(" ".join(notes) or "Oceń sesję, żeby statystyki były pełne.")

        self._last_plan = as_dict(data.get("plan")) or self._last_plan
        if data.get("rating"):
            self._set_rating(as_int(data["rating"]))
