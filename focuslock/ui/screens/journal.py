"""Dziennik zdarzen: filtry i eksport listy.

Brak zdarzen to krotki komunikat w miejscu listy, a dlugie nazwy procesow
i hostow sa elidowane (pelny tekst w tooltipie).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem

from .. import charts
from ..widgets import Card, ChoiceGroup, EmptyState, GhostButton, labels
from .base import Screen, as_dict, as_int, as_list

JOURNAL_EMPTY = "Dziennik jest pusty"
JOURNAL_EMPTY_DETAIL = "Zdarzenia zapisują się, gdy Cisza coś zablokuje albo skończy sesję."


class JournalScreen(Screen):
    """Surowy zapis tego, co Cisza zablokowala i kiedy."""

    TITLE = "DZIENNIK ZDARZEŃ"
    FILTERS = (
        ("all", "WSZYSTKIE"),
        ("BLOKADA", "BLOKADY"),
        ("SESJA", "SESJE"),
        ("INNE", "INNE"),
    )

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        self._filter = ChoiceGroup(list(self.FILTERS))
        self._filter.changed.connect(lambda _key: self._apply_filter())
        self.header_widget(self._filter)

        body = self.make_scroll_body(spacing=16)

        card = Card("ZDARZENIA", "NAJNOWSZE NA GÓRZE")
        self._counter = labels.caption("0 ZDARZEŃ")
        card.add(self._counter)
        self._empty = EmptyState(JOURNAL_EMPTY, JOURNAL_EMPTY_DETAIL)
        self._empty.setMinimumHeight(200)
        card.add(self._empty)
        self._list = QListWidget()
        self._list.setMinimumHeight(320)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setWordWrap(False)
        self._list.setAlternatingRowColors(False)
        self._list.setVisible(False)
        card.add(self._list)
        card.body.setStretchFactor(self._list, 1)
        body.addWidget(card, 1)

        actions = self.action_bar()
        refresh = GhostButton("ODŚWIEŻ")
        refresh.clicked.connect(lambda: self.request_action.emit("refresh_journal", {}))
        export = GhostButton("EKSPORTUJ DZIENNIK")
        export.clicked.connect(lambda: self.request_action.emit("export_journal", {}))
        self._limit = labels.caption("")
        actions.addWidget(refresh)
        actions.addWidget(export)
        actions.addStretch(1)
        actions.addWidget(self._limit)

        self._events: list[dict] = []

    # ------------------------------------------------------------------ dane
    def render(self, data: dict) -> None:
        events = data.get("events")
        if events is not None:
            self._events = [dict(row) for row in as_list(events) if isinstance(row, dict)]
        if data.get("filter"):
            self._filter.set_value(str(data["filter"]))
        if data.get("limit"):
            self._limit.setText(f"POKAZANO {as_int(data['limit'])} OSTATNICH")
        self._apply_filter()

    def render_event(self, event: dict) -> None:
        """Zdarzenie z kontrolera dolacza do dziennika bez pelnego odswiezenia."""
        if isinstance(event, dict):
            self._events.insert(0, dict(event))
            del self._events[400:]
        self._apply_filter()

    def _apply_filter(self) -> None:
        key = self._filter.value() or "all"
        groups = charts.journal_groups(self._events)
        if key == "all":
            rows = self._events
        else:
            rows = groups.get(key, [])
        self._list.clear()
        for event in rows[:400]:
            stamp = f"{charts.fmt_date(event.get('ts'))} {charts.fmt_time_short(event.get('ts'))}"
            kind = str(event.get("kind") or "ZDARZENIE")
            detail = event.get("detail") or {}
            if isinstance(detail, dict):
                extra = detail.get("name") or detail.get("host") or detail.get("reason") or ""
            else:
                extra = str(detail)
            line = f"{stamp}   {kind}"
            if extra:
                line += f"   ·  {extra}"
            item = QListWidgetItem(line)
            item.setToolTip(line)
            self._list.addItem(item)
        self._counter.setText(f"{len(rows)} ZDARZEŃ" if key == "all" else f"{len(rows)} Z FILTREM {key}")
        has_rows = self._list.count() > 0
        self._list.setVisible(has_rows)
        self._empty.setVisible(not has_rows)
        self._empty.set_text(JOURNAL_EMPTY if key == "all" else f"Brak zdarzen w filtrze {key}")

    # ------------------------------------------------------------- wyniki akcji
    def on_action_result(self, action: str, result: dict) -> None:
        """`ODŚWIEŻ` wczytuje zdarzenia z kontrolera bez czekania na kolejny tick."""
        if action != "refresh_journal":
            return
        data = as_dict(result)
        if not data.get("ok") or data.get("events") is None:
            return
        self.render({"events": as_list(data.get("events"))})
