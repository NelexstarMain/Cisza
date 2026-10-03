"""Statystyki: dziś/tydzień/miesiąc, heatmapa, top blokad, focus score, oceny."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout

from .. import charts
from ..widgets import (
    Card,
    ChoiceGroup,
    EmptyState,
    Heatmap,
    MonochromeChart,
    RingProgress,
    StatTile,
    labels,
)
from .base import Screen, as_dict, as_float, as_int, as_list


class StatsScreen(Screen):
    """Wszystkie liczby w jednym miejscu; zero koloru, sama gestosc i glify."""

    TITLE = "STATYSTYKI"
    WINDOW = {"today": 1, "week": 7, "month": 30, "quarter": 90}

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        # Zawartosc w obszarze przewijania: przy niskim oknie nic nie jest ucinane,
        # a karty zachowuja swoje minimalne wysokosci (m.in. pierscien FOCUS SCORE).
        layout = self.make_scroll_body(spacing=16)

        self._period = ChoiceGroup(
            [("today", "DZIŚ"), ("week", "TYDZIEŃ"), ("month", "MIESIĄC"), ("quarter", "KWARTAŁ")]
        )
        self._period.changed.connect(self._change_period)
        self.header_widget(self._period)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self._tile_study = StatTile("NAUKA", "0 min")
        self._tile_pomodoro = StatTile("POMODORO", "0")
        self._tile_blocked = StatTile("BLOKADY", "0")
        self._tile_sessions = StatTile("SESJE", "0")
        for tile in (self._tile_study, self._tile_pomodoro, self._tile_blocked, self._tile_sessions):
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)

        top = QHBoxLayout()
        top.setSpacing(16)
        chart_card = Card("PRZEBIEG NAUKI", "MINUTY NAUKI W WYBRANYM OKRESIE")
        self._chart = MonochromeChart(kind="bar", height=200)
        chart_card.add(self._chart)
        # Wykres rosnie razem z karta (zamiast pustego marginesu pod spodem).
        chart_card.body.setStretchFactor(self._chart, 1)
        top.addWidget(chart_card, 3)

        score_card = Card("FOCUS SCORE", "0–100")
        score_row = QHBoxLayout()
        score_row.setSpacing(16)
        self._score_ring = RingProgress(thickness=8)
        # Minimum zamiast sztywnego rozmiaru: pierscien rosnie razem z karta,
        # a czcionki i geometria skaluja sie same (charts-dev: dziala do 320 px).
        self._score_ring.setMinimumSize(124, 124)
        self._score_ring.set_text("0")
        self._score_ring.set_caption("PUNKTY")
        score_row.addWidget(self._score_ring, 0)
        captions = QVBoxLayout()
        captions.setSpacing(6)
        captions.setContentsMargins(0, 8, 0, 0)
        self._score_band = labels.caption("BRAK DANYCH")
        self._score_band.setWordWrap(True)
        self._rating = labels.hint("OCENY: BRAK")
        captions.addWidget(self._score_band)
        captions.addWidget(self._rating)
        captions.addStretch(1)
        score_row.addLayout(captions, 1)
        score_card.add_layout(score_row)
        score_card.setMinimumHeight(196)
        top.addWidget(score_card, 2)
        layout.addLayout(top)

        middle = QHBoxLayout()
        middle.setSpacing(16)
        heat_card = Card("AKTYWNOŚĆ GODZINOWA", "GDZIE ZBIERA SIĘ PRACA")
        self._heatmap = Heatmap(cell=16)
        self._heatmap.set_legend(True)
        heat_card.add(self._heatmap)
        # Siatka heatmapy wypelnia karte na calej szerokosci i wysokosci.
        heat_card.body.setStretchFactor(self._heatmap, 1)
        middle.addWidget(heat_card, 3)

        blocked_card = Card("TOP BLOKAD", "CO NAJCZĘŚCIEJ PRÓBOWAŁO WEJŚĆ")
        self._blocked_chart = MonochromeChart(kind="bar", height=150)
        blocked_card.add(self._blocked_chart)
        self._blocked_empty = EmptyState(
            "Brak zablokowanych prób",
            "Gdy coś próbuje wejść w trakcie sesji, zobaczysz to tutaj.",
            margins=(18, 12, 18, 12),
        )
        self._blocked_empty.setMinimumHeight(72)
        blocked_card.add(self._blocked_empty)
        self._blocked_list = QListWidget()
        self._blocked_list.setMaximumHeight(110)
        self._blocked_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._blocked_list.setWordWrap(False)
        self._blocked_list.setVisible(False)
        blocked_card.add(self._blocked_list)
        middle.addWidget(blocked_card, 2)
        layout.addLayout(middle)

        processes_card = Card("TOP PROCESY", "CZAS AKTYWNOŚCI W MINUTACH")
        self._process_chart = MonochromeChart(kind="bar", height=150)
        processes_card.add(self._process_chart)
        processes_card.body.setStretchFactor(self._process_chart, 1)
        layout.addWidget(processes_card)
        layout.addStretch(1)

        self._heatmap_data: dict = {}

    # ------------------------------------------------------------------ dane
    def _change_period(self, key: str) -> None:
        self.render(self._data)
        self.request_action.emit("stats_period", {"period": key})

    def render(self, data: dict) -> None:
        series = [as_dict(row) for row in as_list(data.get("series"))]
        totals = as_dict(data.get("totals"))
        summary = as_dict(data.get("summary"))
        window = self.WINDOW.get(self._period.value(), 7)
        window_series = series[-max(1, window) :]
        agg = charts.series_summary(window_series)

        chart_data = charts.bars_from_daily(series, limit=max(7, window))
        self._chart.set_data(chart_data)
        self._chart.set_highlight(len(chart_data.get("values", [])) - 1)

        self._tile_study.set_value(charts.fmt_minutes(agg["total"] * 60, short=True))
        self._tile_study.set_note(f"dziś {charts.fmt_minutes(agg['today'] * 60)}")
        self._tile_study.set_spark(agg.get("spark"))
        self._tile_pomodoro.set_value(str(as_int(agg.get("pomodoros"))))
        self._tile_pomodoro.set_note(f"przerwane: {as_int(agg.get('pomodoros_aborted'))}")
        blocked_rows = [as_dict(row) for row in as_list(data.get("blocked"))]
        blocked_total = charts.blocked_total(blocked_rows) or as_int(agg["blocked"])
        self._tile_blocked.set_value(str(blocked_total))
        self._tile_blocked.set_note("próby zablokowane")
        self._tile_sessions.set_value(str(as_int(agg["sessions"]) or as_int(totals.get("sessions"))))
        self._tile_sessions.set_note(f"rekord dnia: {charts.fmt_minutes(agg['best_minutes'] * 60, short=True)}")

        score = as_float(data.get("score") if data.get("score") is not None else summary.get("focus_score"))
        band = charts.score_band(score)
        self._score_ring.set_value(band["level"])
        self._score_ring.set_text(f"{int(band['score'])}")
        self._score_band.setText(f"{band['glyph']}  {band['label']}")
        ratings = charts.rating_summary([as_dict(row) for row in as_list(data.get("sessions"))])
        if ratings["count"]:
            self._rating.setText(
                f"OCENY: {ratings['last_text']}  ·  średnia {ratings['average']:.1f} z {ratings['count']} sesji"
            )
        else:
            self._rating.setText("OCENY: BRAK")

        heatmap = data.get("heatmap")
        if isinstance(heatmap, dict) and heatmap.get("matrix"):
            self._heatmap_data = heatmap
        elif data.get("events") is not None or "series" in data:
            self._heatmap_data = charts.heatmap_from_events(as_list(data.get("events")))
        if self._heatmap_data:
            self._heatmap.set_data(self._heatmap_data)

        blocked_chart = charts.bars_from_blocked(blocked_rows)
        self._blocked_chart.set_data(blocked_chart)
        self._blocked_list.clear()
        for row in list(blocked_chart.get("rows") or [])[:8]:
            text = f"{row.get('label')}  ·  {as_int(row.get('count'))}"
            item = QListWidgetItem(text)
            item.setToolTip(text)
            self._blocked_list.addItem(item)
        has_blocked = self._blocked_list.count() > 0
        self._blocked_list.setVisible(has_blocked)
        self._blocked_empty.setVisible(not has_blocked)
        self._process_chart.set_data(charts.bars_from_processes([as_dict(row) for row in as_list(data.get("processes"))]))

    # ------------------------------------------------------------- wyniki akcji
    def on_action_result(self, action: str, result: dict) -> None:
        """Odswiezenie i zmiana okresu podmieniaja serie bez gubienia ocen i heatmapy."""
        if action not in ("refresh_stats", "stats_period"):
            return
        data = as_dict(result)
        if not data.get("ok"):
            return
        merged = dict(self._data)
        if data.get("series") is not None:
            merged["series"] = as_list(data.get("series"))
        if data.get("totals") is not None:
            merged["totals"] = as_dict(data.get("totals"))
        if data.get("top_blocked") is not None:
            merged["blocked"] = as_list(data.get("top_blocked"))
        if data.get("top_processes") is not None:
            merged["processes"] = as_list(data.get("top_processes"))
        self._data.update(merged)
        self.render(merged)
