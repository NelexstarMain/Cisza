"""Bank minut: saldo, historia i daty wygasania (FIFO).

Tabele maja elidowane komorki (pelna tresc w tooltipie), a brak danych konczy
sie krotkim komunikatem w miejscu listy.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QTableWidget, QTableWidgetItem

from .. import charts
from ..widgets import Card, EmptyState, GhostButton, StatTile, labels
from .base import Screen, as_dict, as_int, as_list

LOTS_EMPTY = "Bank jest pusty"
LOTS_EMPTY_DETAIL = "Minuty pojawią się tutaj po ukończonym pomodoro."
LEDGER_EMPTY = "Brak operacji na banku"
LEDGER_EMPTY_DETAIL = "Każde zasilenie i wydanie minut trafi na tę listę."


def _table(headers: list[str], rows: int = 0) -> QTableWidget:
    table = QTableWidget(rows, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.horizontalHeader().setStretchLastSection(True)
    table.setMinimumHeight(150)
    return table


def _fill(table: QTableWidget, rows: list[list[str]]) -> None:
    table.setRowCount(len(rows))
    for row_index, values in enumerate(rows):
        for col_index, value in enumerate(values):
            text = str(value)
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            table.setItem(row_index, col_index, item)


class BankScreen(Screen):
    """Skad pochodza minuty i kiedy przepadna."""

    TITLE = "BANK MINUT"

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        refresh = GhostButton("ODŚWIEŻ")
        refresh.clicked.connect(lambda: self.request_action.emit("refresh_bank", {}))
        self.header_widget(refresh)

        body = self.make_scroll_body(spacing=16)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self._tile_balance = StatTile("SALDO", "0 min")
        self._tile_expiry = StatTile("NAJBLIŻSZE WYGAŚNIĘCIE", "—")
        self._tile_earned = StatTile("DZIŚ ZAROBIONE", "0 min")
        self._tile_streak = StatTile("SERIA", "0 dni")
        for tile in (self._tile_balance, self._tile_expiry, self._tile_earned, self._tile_streak):
            tiles.addWidget(tile, 1)
        body.addLayout(tiles)

        self._lots_card = Card("MINUTY W BANKU", "KOLEJNOŚĆ FIFO — NAJSTARSZE SCHODZĄ PIERWSZE")
        self._lots_empty = EmptyState(LOTS_EMPTY, LOTS_EMPTY_DETAIL, margins=(18, 16, 18, 16))
        self._lots_empty.setMinimumHeight(96)
        self._lots_card.add(self._lots_empty)
        self._lots_table = _table(["POZYSKANO", "WYGAŚNIE", "POZOSTAŁO", "ORYGINAŁ", "NOTKA"])
        self._lots_card.add(self._lots_table)
        self._lots_card.body.setStretchFactor(self._lots_table, 1)
        body.addWidget(self._lots_card, 1)

        self._ledger_card = Card("HISTORIA", "OSTATNIE OPERACJE NA BANKU")
        self._ledger_empty = EmptyState(LEDGER_EMPTY, LEDGER_EMPTY_DETAIL, margins=(18, 16, 18, 16))
        self._ledger_empty.setMinimumHeight(96)
        self._ledger_card.add(self._ledger_empty)
        self._ledger_table = _table(["DATA", "ZMIANA", "POWÓD", "NOTKA"])
        self._ledger_card.add(self._ledger_table)
        self._ledger_card.body.setStretchFactor(self._ledger_table, 1)
        body.addWidget(self._ledger_card, 1)

        # Start bez danych: pokazujemy komunikat, nie pusta tabele.
        self._toggle_empty(self._lots_empty, self._lots_table, 0)
        self._toggle_empty(self._ledger_empty, self._ledger_table, 0)

    # ------------------------------------------------------------------ dane
    def render(self, data: dict) -> None:
        summary = as_dict(data.get("summary"))
        lots = [as_dict(row) for row in as_list(data.get("lots"))]
        ledger = [as_dict(row) for row in as_list(data.get("ledger"))]
        balance = as_int(data.get("balance") if data.get("balance") is not None else summary.get("balance"))
        ttl_days = as_int(summary.get("ttl_days"))

        self._tile_balance.set_value(charts.fmt_minutes(balance, short=True))
        self._tile_balance.set_note(f"minut dostępnych · TTL {ttl_days} dni" if ttl_days else "minuty dostępne")
        self._tile_earned.set_value(charts.fmt_minutes(as_int(summary.get("earned_today")), short=True))
        self._tile_earned.set_note(f"limit dziś: {charts.fmt_minutes(as_int(summary.get('cap_left')), short=True)}")
        streak = as_int(summary.get("streak"))
        self._tile_streak.set_value(f"{streak} dni")
        self._tile_streak.set_note(f"rekord: {as_int(summary.get('best_streak'))}")

        expiring = [lot for lot in lots if as_int(lot.get("expires_at")) > 0]
        if expiring:
            soonest = min(expiring, key=lambda lot: as_int(lot.get("expires_at")))
            self._tile_expiry.set_value(charts.fmt_date(soonest.get("expires_at")))
            self._tile_expiry.set_note(
                f"{charts.fmt_minutes(as_int(soonest.get('remaining_seconds')))} przepadnie"
            )
        else:
            self._tile_expiry.set_value("bezterminowo")
            self._tile_expiry.set_note("brak wygasających minut" if lots else "bank pusty")
        self._tile_expiry.set_progress(None)

        _fill(
            self._lots_table,
            [
                [
                    charts.fmt_date(lot.get("created_at")),
                    charts.fmt_date(lot.get("expires_at")),
                    charts.fmt_minutes(as_int(lot.get("remaining_seconds"))),
                    charts.fmt_minutes(as_int(lot.get("original_seconds"))),
                    str(lot.get("note") or "—"),
                ]
                for lot in lots
            ],
        )
        _fill(
            self._ledger_table,
            [
                [
                    f"{charts.fmt_date(row.get('ts'))} {charts.fmt_time_short(row.get('ts'))}",
                    _delta(as_int(row.get("delta_seconds"))),
                    str(row.get("reason") or "—"),
                    str(row.get("note") or "—"),
                ]
                for row in ledger
            ],
        )
        self._toggle_empty(self._lots_empty, self._lots_table, len(lots))
        self._toggle_empty(self._ledger_empty, self._ledger_table, len(ledger))

    @staticmethod
    def _toggle_empty(empty: EmptyState, table: QTableWidget, count: int) -> None:
        empty.setVisible(count == 0)
        table.setVisible(count > 0)

    # ------------------------------------------------------------- wyniki akcji
    def on_action_result(self, action: str, result: dict) -> None:
        """`ODŚWIEŻ` ma od razu podmienić dane w tabelach (wynik z kontrolera)."""
        if action != "refresh_bank":
            return
        data = as_dict(result)
        if not data.get("ok"):
            return
        self.render(
            {
                "summary": as_dict(data.get("economy")),
                "balance": data.get("balance"),
                "lots": as_list(data.get("lots")),
                "ledger": as_list(data.get("ledger")),
            }
        )


def _delta(seconds: int) -> str:
    sign = "+" if seconds >= 0 else "-"
    return f"{sign}{charts.fmt_minutes(abs(seconds), short=True)}"
