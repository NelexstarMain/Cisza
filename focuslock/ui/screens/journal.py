"""Dziennik zdarzen: os czasu z podzialem na dni, filtry, wyszukiwanie i eksport.

Widok czyta zdarzenia z `controller.summary()["events"]` (ostatnie 100) i potrafi
dociagnac wieksza liste akcja `refresh_journal`. Zdarzenia sa grupowane po dniach:
naglowek dnia niesie liczbe wpisow i laczny czas nauki dnia, a kazdy wiersz ma
godzine, znak kategorii, czytelna polska etykiete (`BLOCKED_APP` -> "Zablokowana
aplikacja") i szczegol (nazwa procesu, host, powod).

Wydajnosc: wiersze to lekkie `QListWidgetItem` (dane, nie widgety), a widok
pokazuje najwyzej `PAGE_SIZE` zdarzen na raz - reszte odslania "POKAŻ WIĘCEJ".
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
)

from .. import charts
from ..theme import c
from ..widgets import Card, ChoiceGroup, EmptyState, GhostButton, StatTile, labels
from .base import Screen, as_dict, as_int, as_list

JOURNAL_EMPTY = "Dziennik jest pusty"
JOURNAL_EMPTY_DETAIL = "Zdarzenia zapiszą się, gdy Cisza coś zablokuje albo skończy sesję."
FILTER_EMPTY = "Brak wyników"
FILTER_EMPTY_DETAIL = "Zmień filtr albo wyczyść wyszukiwanie, żeby zobaczyć pozostałe zdarzenia."

#: Twarde limity widoku: mniej wiecej tyle zdarzen wraca z bazy i tyle pokazuje jedna porcja.
MAX_EVENTS = 400
PAGE_SIZE = 80

#: kind -> czytelna etykieta (bez emoji, jezyk uzytkownika).
KIND_LABELS: dict[str, str] = {
    "BLOCKED_APP": "Zablokowana aplikacja",
    "BLOCKED_SITE": "Zablokowana strona",
    "BLOCKED_PROCESS": "Zablokowany proces",
    "BLOCKED_ATTEMPT": "Próba obejścia blokady",
    "ATTEMPT_EXIT": "Próba wyjścia z sesji",
    "EXIT_REQUEST": "Prośba o wyjście",
    "PANIC_EXIT": "Wyjście awaryjne",
    "RATING": "Ocena sesji",
    "SESSION_START": "Start sesji",
    "SESSION_END": "Koniec sesji",
    "SESSION_FINISHED": "Zakończenie sesji",
    "FREE_START": "Start trybu wolnego",
    "FREE_EXTEND": "Przedłużenie trybu wolnego",
    "FREE_DONE": "Koniec trybu wolnego",
    "POMODORO_END": "Koniec pomodoro",
    "PHASE": "Zmiana fazy",
    "GUARD_SKIPPED": "Strażnik pominięty",
    "CLEANUP_INCOMPLETE": "Niedokończone porządki",
    "CRASH_RECOVERED": "Odzyskanie po awarii",
}

#: Znak kategorii (ASCII/box-drawing, zero emoji).
GROUP_SIGNS: dict[str, str] = {
    "block": "×",
    "session": "▸",
    "rating": "▮",
    "other": "·",
}

_BLOCK_KINDS = frozenset({"BLOCK", "BLOCKED"})
_RATING_KINDS = frozenset({"RATING", "RATE", "SCORE", "OCENA"})
_SESSION_PREFIXES = (
    "SESSION",
    "FREE",
    "POMODORO",
    "PHASE",
    "ATTEMPT_EXIT",
    "PANIC_EXIT",
    "EXIT_REQUEST",
    "GUARD",
    "CLEANUP",
    "CRASH",
)

MONTHS_PL = (
    "STYCZNIA",
    "LUTEGO",
    "MARCA",
    "KWIETNIA",
    "MAJA",
    "CZERWCA",
    "LIPCA",
    "SIERPNIA",
    "WRZEŚNIA",
    "PAŹDZIERNIKA",
    "LISTOPADA",
    "GRUDNIA",
)
WEEKDAYS_PL = (
    "PONIEDZIAŁEK",
    "WTOREK",
    "ŚRODA",
    "CZWARTEK",
    "PIĄTEK",
    "SOBOTA",
    "NIEDZIELA",
)

#: Klucz -> pole `detail`, ktore jest najbardziej czytelnym szczegolem zdarzenia.
_DETAIL_KEYS = ("name", "host", "process", "process_name", "url", "path", "title")
_REASON_KEYS = ("reason", "how", "label", "note", "message")
_DURATION_KEYS = ("study_seconds", "actual_seconds", "seconds", "duration", "elapsed")

#: Wartosci techniczne z `detail` po polsku (bez nich w dzienniku bylo "pin",
#: "auto" albo "user" - czytelne tylko dla autora kodu).
_VALUE_LABELS: dict[str, dict[str, str]] = {
    "how": {
        "pin": "przez PIN",
        "hold": "przytrzymanie klawiszy",
        "shortcut": "skrót klawiszowy",
        "tray": "z zasobnika",
        "user": "z okna Ciszy",
    },
    "reason": {
        "auto": "koniec czasu",
        "user": "ręcznie",
        "panic": "wyjście awaryjne",
        "complete": "pomodoro domknięte",
        "abort": "pomodoro przerwane",
        "free_end": "koniec trybu wolnego",
        "free_done": "tryb wolny domknięty",
        "empty-allowlist": "brak wybranych aplikacji",
    },
    "mode": {"study": "nauka", "free": "tryb wolny"},
}


# --------------------------------------------------------------------- pomocniki
def _kind_key(kind: object) -> str:
    return str(kind or "").strip().upper().replace(" ", "_")


def _humanize(key: str) -> str:
    text = key.replace("_", " ").strip()
    if not text:
        return "Zdarzenie"
    return text[:1] + text[1:].lower()


def event_label(kind: object) -> str:
    """Czytelna polska etykieta zdarzenia (nieznane rodzaje lagodnie uogolniamy)."""
    key = _kind_key(kind)
    if not key:
        return "Zdarzenie"
    label = KIND_LABELS.get(key)
    return label if label else _humanize(key)


def event_group(kind: object) -> str:
    """Grupa filtru: block / session / rating / other."""
    key = _kind_key(kind)
    if key.startswith("BLOCK") or key in _BLOCK_KINDS:
        return "block"
    if "RATING" in key or key in _RATING_KINDS:
        return "rating"
    if key.startswith(_SESSION_PREFIXES):
        return "session"
    return "other"


def event_sign(kind: object) -> str:
    return GROUP_SIGNS.get(event_group(kind), GROUP_SIGNS["other"])


def _event_ts(event: dict) -> float:
    try:
        return float(event.get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _event_seconds(event: dict) -> float:
    """Realny czas nauki zapisany w zdarzeniu (bez zgadywania z planu sesji)."""
    detail = event.get("detail")
    if not isinstance(detail, dict):
        return 0.0
    for key in _DURATION_KEYS:
        if key not in detail:
            continue
        try:
            return max(0.0, float(detail.get(key) or 0.0))
        except (TypeError, ValueError):
            continue
    return 0.0


def _reason_text(detail: dict) -> str:
    """Powod/rodzaj zdarzenia po polsku; odrzucona proba dostaje dopisek."""
    for key in _REASON_KEYS:
        value = detail.get(key)
        if not value or isinstance(value, (dict, list, tuple)):
            continue
        raw = str(value).strip()
        text = _VALUE_LABELS.get(key, {}).get(raw.lower(), raw)
        if detail.get("ok") is False:
            text = f"{text} · odrzucone"
        return text
    return ""


def _detail_text(detail: object) -> str:
    """Szczegol zdarzenia: proces/host/powod/ocena (odporny na smieci)."""
    if isinstance(detail, str):
        return detail.strip()
    if not isinstance(detail, dict):
        return ""
    for key in _DETAIL_KEYS:
        value = detail.get(key)
        if value and not isinstance(value, (dict, list, tuple)):
            return str(value)
    reason = _reason_text(detail)
    if reason:
        return reason
    value = detail.get("value")
    if value is not None and not isinstance(value, (dict, list, tuple)):
        return f"ocena {value}"
    plan = detail.get("plan")
    if isinstance(plan, dict):
        mode = str(plan.get("mode") or "").upper()
        minutes = plan.get("study_minutes")
        if minutes:
            return f"{'tryb wolny' if mode == 'FREE' else 'nauka'} {minutes} min"
        if mode:
            return mode.lower()
    return ""


def _haystack(event: dict) -> str:
    """Tekst do wyszukiwania: rodzaj, etykieta i wszystkie plaskie pola szczegolow."""
    parts = [str(event.get("kind") or ""), event_label(event.get("kind"))]
    detail = event.get("detail")
    if isinstance(detail, dict):
        parts.extend(str(value) for value in detail.values() if not isinstance(value, (dict, list, tuple)))
        parts.append(str(detail))
    elif detail:
        parts.append(str(detail))
    return " ".join(parts).casefold()


def _plural(count: int, one: str, few: str, many: str) -> str:
    number = abs(int(count))
    if number == 1:
        return one
    if number % 10 in (2, 3, 4) and number % 100 not in (12, 13, 14):
        return few
    return many


def _day_key(ts: float) -> tuple[int, int, int] | None:
    if not ts:
        return None
    local = time.localtime(ts)
    return (local.tm_year, local.tm_mon, local.tm_mday)


def _day_title(day: tuple[int, int, int] | None, ts: float) -> str:
    if day is None:
        return "BEZ DATY"
    year, month, day_number = day
    today = time.localtime()
    if (year, month, day_number) == (today.tm_year, today.tm_mon, today.tm_mday):
        prefix = "DZIŚ"
    else:
        yesterday = time.localtime(time.time() - 86400)
        if (year, month, day_number) == (yesterday.tm_year, yesterday.tm_mon, yesterday.tm_mday):
            prefix = "WCZORAJ"
        elif ts:
            prefix = WEEKDAYS_PL[time.localtime(ts).tm_wday]
        else:
            prefix = "DZIEŃ"
    month_name = MONTHS_PL[month - 1] if 1 <= month <= 12 else ""
    return f"{prefix} · {day_number} {month_name}".strip()


def _day_header(day: tuple[int, int, int] | None, ts: float, count: int, seconds: float) -> str:
    title = _day_title(day, ts)
    counts = f"{count} {_plural(count, 'ZDARZENIE', 'ZDARZENIA', 'ZDARZEŃ')}"
    if seconds >= 60:
        counts += f" · {charts.fmt_minutes(seconds).upper()} NAUKI"
    return f"{title}     {counts}"


def _line(event: dict) -> str:
    stamp = charts.fmt_time_short(_event_ts(event))
    line = f"{stamp}   {event_sign(event.get('kind'))}   {event_label(event.get('kind'))}"
    detail = _detail_text(event.get("detail"))
    if detail:
        line += f"   ·   {detail}"
    return line


def _tooltip(event: dict) -> str:
    ts = _event_ts(event)
    parts = [
        f"{charts.fmt_date(ts)} {charts.fmt_time_short(ts)}",
        event_label(event.get("kind")),
        f"RODZAJ: {_kind_key(event.get('kind')) or '?'}",
    ]
    detail = _detail_text(event.get("detail"))
    if detail:
        parts.append(f"SZCZEGÓŁ: {detail}")
    session = event.get("session_id")
    if session:
        parts.append(f"SESJA: {session}")
    return "\n".join(parts)


class JournalScreen(Screen):
    """Os czasu zdarzen z grupowaniem po dniach, filtrami i eksportem."""

    TITLE = "DZIENNIK ZDARZEŃ"
    FILTERS = (
        ("all", "WSZYSTKIE"),
        ("block", "BLOKADY"),
        ("session", "SESJE"),
        ("rating", "OCENY"),
        ("other", "INNE"),
    )
    PAGE_SIZE = PAGE_SIZE
    MAX_EVENTS = MAX_EVENTS

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        self._filter = ChoiceGroup(list(self.FILTERS))
        self._filter.changed.connect(self._on_filter_changed)
        self.header_widget(self._filter)

        body = self.make_scroll_body(spacing=16)

        summary = QHBoxLayout()
        summary.setSpacing(12)
        self._tile_events = StatTile("ZDARZENIA", "0")
        self._tile_blocked = StatTile("BLOKADY", "0")
        self._tile_days = StatTile("DNI Z WPISAMI", "0")
        for tile in (self._tile_events, self._tile_blocked, self._tile_days):
            summary.addWidget(tile)
        summary.addStretch(1)
        body.addLayout(summary)

        card = Card("OŚ CZASU", "NAJNOWSZE NA GÓRZE")

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self._search = QLineEdit()
        self._search.setPlaceholderText("SZUKAJ: NAZWA, HOST, RODZAJ ZDARZENIA")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_query_changed)
        self._counter = labels.caption("0 ZDARZEŃ")
        search_row.addWidget(self._search, 1)
        search_row.addWidget(self._counter)
        card.add_layout(search_row)

        self._empty = EmptyState(JOURNAL_EMPTY, JOURNAL_EMPTY_DETAIL)
        self._empty_filter = EmptyState(FILTER_EMPTY, FILTER_EMPTY_DETAIL)
        card.add(self._empty)
        card.add(self._empty_filter)

        self._list = QListWidget()
        self._list.setMinimumHeight(300)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setWordWrap(False)
        self._list.setAlternatingRowColors(False)
        self._list.setUniformItemSizes(True)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setVisible(False)
        card.add(self._list)
        card.body.setStretchFactor(self._list, 1)
        body.addWidget(card, 1)

        actions = self.action_bar()
        refresh = GhostButton("ODŚWIEŻ")
        refresh.clicked.connect(self._refresh)
        export = GhostButton("EKSPORTUJ DZIENNIK")
        export.clicked.connect(self._export)
        clear = GhostButton("WYCZYŚĆ FILTRY")
        clear.clicked.connect(self._clear_filters)
        more = GhostButton("POKAŻ WIĘCEJ")
        more.clicked.connect(self._show_more)
        self._clear = clear
        self._more = more
        actions.addWidget(refresh)
        actions.addWidget(export)
        actions.addWidget(clear)
        actions.addWidget(more)
        actions.addStretch(1)
        self._page_note = labels.caption("")
        actions.addWidget(self._page_note)

        self._events: list[dict] = []
        self._page = self.PAGE_SIZE
        self._shown = 0
        self._source_note = ""
        self._header_font = QFont()
        self._header_font.setBold(True)

    # ------------------------------------------------------------------ akcje
    def _refresh(self) -> None:
        self.request_action.emit("refresh_journal", {})

    def _export(self) -> None:
        self.request_action.emit("export_journal", {})

    def _show_more(self) -> None:
        if self._shown >= len(self._visible_events()):
            return
        self._page = min(self.MAX_EVENTS, self._page + self.PAGE_SIZE)
        self._apply_filter()

    def _clear_filters(self) -> None:
        """Powrot do pelnego widoku: czysci wyszukiwanie i wybiera WSZYSTKIE."""
        self._search.blockSignals(True)
        self._search.setText("")
        self._search.blockSignals(False)
        self._filter.set_value("all")
        self._page = self.PAGE_SIZE
        self._apply_filter()

    def _on_filter_changed(self, _key: str) -> None:
        self._page = self.PAGE_SIZE
        self._apply_filter()

    def _on_query_changed(self, _text: str) -> None:
        self._page = self.PAGE_SIZE
        self._apply_filter()

    # ------------------------------------------------------------------ dane
    def render(self, data: dict) -> None:
        payload = as_dict(data)
        events = payload.get("events")
        if events is not None:
            self._store_events(as_list(events))
            self._page = self.PAGE_SIZE
        if payload.get("filter"):
            self._filter.set_value(str(payload["filter"]))
        limit = as_int(payload.get("limit"))
        self._source_note = f"OSTATNIE {limit} Z BAZY" if limit > 0 else ""
        self._apply_filter()

    def render_event(self, event: dict) -> None:
        """Zdarzenie z kontrolera (np. blokada w trakcie sesji) trafia na os czasu."""
        if not isinstance(event, dict):
            return
        entry = dict(event)
        entry.setdefault("kind", "ZDARZENIE")
        if not entry.get("ts"):
            entry["ts"] = time.time()
        self._events.append(entry)
        self._events.sort(key=_event_ts, reverse=True)
        del self._events[self.MAX_EVENTS :]
        self._apply_filter()

    def _store_events(self, events: list) -> None:
        rows = [dict(row) for row in events if isinstance(row, dict)]
        rows.sort(key=_event_ts, reverse=True)
        self._events = rows[: self.MAX_EVENTS]

    def _visible_events(self) -> list[dict]:
        key = self._filter.value() or "all"
        tokens = [token for token in self._search.text().strip().casefold().split() if token]
        rows: list[dict] = []
        for event in self._events:
            if key != "all" and event_group(event.get("kind")) != key:
                continue
            if tokens:
                haystack = _haystack(event)
                if any(token not in haystack for token in tokens):
                    continue
            rows.append(event)
        return rows

    # ------------------------------------------------------------------ widok
    def _apply_filter(self) -> None:
        rows = self._visible_events()
        total = len(self._events)
        key = self._filter.value() or "all"
        query = self._search.text().strip()
        active = key != "all" or bool(query)

        blocked = sum(1 for event in rows if event_group(event.get("kind")) == "block")
        days = {_day_key(_event_ts(event)) for event in rows}
        seconds = sum(_event_seconds(event) for event in rows)

        self._tile_events.set_value(str(len(rows)))
        self._tile_events.set_note(f"Z {total} W DZIENNIKU" if total else "")
        self._tile_blocked.set_value(str(blocked))
        if rows:
            share = blocked / len(rows)
            self._tile_blocked.set_progress(share)
            self._tile_blocked.set_note(f"{int(round(share * 100))}% ZDARZEŃ")
        else:
            self._tile_blocked.set_progress(None)
            self._tile_blocked.set_note("")
        self._tile_days.set_value(str(len(days)))
        self._tile_days.set_note(f"{charts.fmt_minutes(seconds).upper()} NAUKI" if seconds >= 60 else "")

        has_events = total > 0
        self._empty.setVisible(not has_events)
        self._empty_filter.setVisible(has_events and not rows)
        self._list.setVisible(bool(rows))
        self._clear.setVisible(active)

        if active:
            self._counter.setText(f"WYNIKI: {len(rows)} Z {total}")
        else:
            self._counter.setText(
                f"{len(rows)} {_plural(len(rows), 'ZDARZENIE', 'ZDARZENIA', 'ZDARZEŃ')}"
            )
        self._counter.setToolTip(self._source_note)

        self._populate(rows)

    def _populate(self, rows: list[dict]) -> None:
        self._list.clear()
        self._shown = 0
        if not rows:
            self._more.setVisible(False)
            self._page_note.setText("")
            return

        groups: dict[tuple[int, int, int] | None, dict] = {}
        order: list[tuple[int, int, int] | None] = []
        for event in rows:
            ts = _event_ts(event)
            day = _day_key(ts)
            bucket = groups.get(day)
            if bucket is None:
                bucket = {"ts": ts, "events": []}
                groups[day] = bucket
                order.append(day)
            bucket["events"].append(event)

        for day in order:
            if self._shown >= self._page:
                break
            bucket = groups[day]
            group = bucket["events"]
            take = group[: max(0, self._page - self._shown)]
            seconds = sum(_event_seconds(event) for event in take)
            header = QListWidgetItem(_day_header(day, bucket["ts"], len(take), seconds))
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            header.setFont(self._header_font)
            header.setForeground(QBrush(QColor(c("text_mute"))))
            header.setToolTip("DZIEŃ Z WPISAMI W DZIENNIKU")
            self._list.addItem(header)
            for event in take:
                item = QListWidgetItem(_line(event))
                item.setToolTip(_tooltip(event))
                item.setData(Qt.ItemDataRole.UserRole, str(event.get("kind") or ""))
                self._list.addItem(item)
            self._shown += len(take)

        remaining = len(rows) - self._shown
        self._more.setVisible(remaining > 0)
        self._more.setText(f"POKAŻ WIĘCEJ ({remaining})")
        self._page_note.setText(f"POKAZANO {self._shown} Z {len(rows)}" if remaining > 0 else "")

    # ------------------------------------------------------------- wyniki akcji
    def on_action_result(self, action: str, result: dict) -> None:
        """`ODŚWIEŻ` wczytuje zdarzenia z kontrolera, `EKSPORTUJ` potwierdza plik."""
        data = as_dict(result)
        if action == "refresh_journal":
            if not data.get("ok") or data.get("events") is None:
                if data.get("ok") is False and not data.get("errors"):
                    self.show_toast("NIE UDAŁO SIĘ ODŚWIEŻYĆ DZIENNIKA")
                return
            self._store_events(as_list(data.get("events")))
            self._page = self.PAGE_SIZE
            self._apply_filter()
            total = len(self._events)
            self.show_toast(
                f"ODŚWIEŻONO DZIENNIK: {total} "
                f"{_plural(total, 'ZDARZENIE', 'ZDARZENIA', 'ZDARZEŃ')}"
            )
        elif action == "export_journal":
            if not data.get("ok"):
                self.show_toast("EKSPORT DZIENNIKA NIE UDAŁ SIĘ")
                return
            path = str(data.get("path") or "")
            name = Path(path).name if path else ""
            self.show_toast(f"ZAPISANO DZIENNIK: {name}" if name else "DZIENNIK ZAPISANY")
