"""Baza ekranow: sygnaly z kontraktu + wspolne pomocniki.

Kontrakt (docs/INTERFACES.md sekcja 17): kazdy ekran to `QWidget` z sygnalami
`request_start(dict)`, `request_free(int)`, `request_end(str)`,
`request_settings(dict)`, `request_pin_check(str)`. Ekrany nie zawieraja logiki
systemowej — tylko zbieraja dane i emituja sygnaly.
"""
from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLayout, QScrollArea, QVBoxLayout, QWidget

from ..widgets import labels
from ..widgets import texture
from ..widgets.primitives import EmptyState
from ..widgets.tiles import Toast

PHASE_LABELS: dict[str, str] = {
    "IDLE": "GOTOWY",
    "ARMING": "START ZA CHWILĘ",
    "STUDY": "NAUKA",
    "BREAK": "PRZERWA",
    "LONG_BREAK": "DŁUGA PRZERWA",
    "DONE": "ZAKOŃCZONO",
}

MODE_LABELS: dict[str, str] = {
    "STUDY": "NAUKA",
    "FREE": "TRYB WOLNY",
}


def clear_layout(layout: QLayout) -> None:
    """Usuwa wszystkie elementy z layoutu (takze zagniezdzone)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)


def as_dict(value: Any) -> dict:
    """Bezpieczne rzutowanie danych kontrolera na dict (odporne na dziwne kształty)."""
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
        except Exception:  # pragma: no cover - dane z zewnatrz
            return {}
        if isinstance(result, dict):
            return dict(result)
    return {}


def as_list(value: Any) -> list:
    """Bezpieczne rzutowanie na liste: None -> [], dict -> [dict], skalar -> []."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, (list, tuple, set)):
        return [item for item in value]
    return []


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def plan_payload(source: dict | None = None) -> dict:
    """Normalizuje plan sesji do payloadu `request_start(dict)`."""
    src = dict(source or {})
    apps = [str(x) for x in (src.get("study_apps") or src.get("apps") or []) if str(x).strip()]
    sites = [str(x) for x in (src.get("study_sites") or src.get("sites") or []) if str(x).strip()]
    break_sites = [str(x) for x in (src.get("break_sites") or []) if str(x).strip()]
    block_sites = [str(x) for x in (src.get("block_sites") or []) if str(x).strip()]
    mode = str(src.get("mode") or "STUDY").upper()
    if mode not in ("STUDY", "FREE"):
        mode = "STUDY"
    return {
        "mode": mode,
        "study_minutes": max(1, int(src.get("study_minutes") or 25)),
        "break_minutes": max(0, int(src.get("break_minutes") or 5)),
        "long_break_minutes": max(0, int(src.get("long_break_minutes") or 15)),
        "long_break_every": max(0, int(src.get("long_break_every") or 4)),
        "free_minutes": max(0, int(src.get("free_minutes") or 0)),
        "tag": str(src.get("tag") or ""),
        "goal_note": str(src.get("goal_note") or ""),
        "hardcore": bool(src.get("hardcore") or False),
        "allowlist": {"apps": apps, "sites": sites, "break_sites": break_sites, "block_sites": block_sites},
        "preset": str(src.get("preset") or src.get("name") or ""),
    }


class Screen(QWidget):
    """Bazowy ekran: puste sygnaly kontraktu + `set_data(...)` dla kontrolera.

    Kazdy ekran dostaje ten sam szkielet wygladu:
    * `self.header` - pasek tytulu (tytul po lewej, akcje po prawej),
    * `self.make_scroll_body()` - przewijalna tresc (nic sie nie ucina w niskim oknie),
    * `self.action_bar()` - pasek akcji przyklejony do dolu ekranu.
    Marginesy i odstepy sa wspolne, wiec karty nie "skacza" miedzy ekranami.
    """

    # ------------------------------------------------------------ kontrakt
    request_start = pyqtSignal(dict)
    request_free = pyqtSignal(int)
    request_end = pyqtSignal(str)
    request_settings = pyqtSignal(dict)
    request_pin_check = pyqtSignal(str)
    pin_result = pyqtSignal(bool)
    request_screen = pyqtSignal(str)

    # ------------------------------- rozszerzenia uzywane przez ekrany (poza sekcja 17)
    request_preset = pyqtSignal(dict)
    request_rating = pyqtSignal(int)
    request_pause = pyqtSignal(bool)
    request_break = pyqtSignal(bool)
    request_set_pin = pyqtSignal(str)
    request_action = pyqtSignal(str, dict)
    request_refresh_apps = pyqtSignal()

    TITLE = ""
    #: Odstepy wspolne dla wszystkich ekranow (px).
    MARGINS = (24, 20, 24, 20)
    SPACING = 16
    HEADER_SPACING = 12
    ACTION_SPACING = 10

    def __init__(self, store=None, settings=None, parent: QWidget | None = None) -> None:
        """Kontroler buduje ekrany jako `cls(store, settings)` albo `cls()`."""
        super().__init__(parent)
        self.store = store
        self.settings = settings
        self.setObjectName(type(self).__name__)
        root = QVBoxLayout(self)
        root.setContentsMargins(*self.MARGINS)
        root.setSpacing(self.SPACING)
        self.root = root

        # Wspolny naglowek strony: tytul + miejsce na akcje po prawej.
        self.header = QHBoxLayout()
        self.header.setSpacing(self.HEADER_SPACING)
        self._title_label = labels.subtitle(self.TITLE)
        self._title_label.setWordWrap(False)
        self._title_label.setVisible(bool(self.TITLE))
        self.header.addWidget(self._title_label)
        self.header.addStretch(1)
        root.addLayout(self.header)

        self._data: dict = {}
        self._toast: Toast | None = None
        self._scroll_area: QScrollArea | None = None
        self._build()

    # ------------------------------------------------------------------ API
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        """Pasek naglowka kazdego ekranu dostaje wlasny poziom ziarna.

        Tlo okna ma stos ziarna; naglowek strony dokłada drobniejsze ziarno,
        ktore wygasa w dol (maska gradientowa), wiec tytul strony siedzi na
        "materiale", a nie na plaskiej czerni - i nie ma widocznego progu.
        """
        super().paintEvent(event)
        painter = QPainter(self)
        rect = QRectF(self.rect())
        if rect.width() < 16.0 or rect.height() < 16.0:
            painter.end()
            return
        band = QRectF(rect.left(), rect.top(), rect.width(), min(72.0, rect.height() * 0.3))
        texture.paint_grain_fade(painter, band, "fine", fade="bottom", span=1.0, opacity=0.5)
        texture.paint_grain_fade(painter, band, "clump", fade="bottom", span=0.7, opacity=0.4)
        painter.end()

    def _build(self) -> None:
        """Budowa widoku (nadpisywana przez ekrany)."""

    # ------------------------------------------------------- szkielet wygladu
    def set_page_title(self, text: str) -> None:
        """Ustawia tytul strony (pusty tekst chowa etykiete)."""
        self._title_label.setText(str(text))
        self._title_label.setVisible(bool(str(text)))

    def page_title(self) -> str:
        return self._title_label.text()

    def header_widget(self, widget: QWidget) -> QWidget:
        """Dodaje widget po prawej stronie naglowka strony."""
        self.header.addWidget(widget)
        return widget

    def make_scroll_body(self, *, spacing: int | None = None, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QVBoxLayout:
        """Tworzy przewijalna tresc ekranu i zwraca layout do wypelnienia."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(*margins)
        layout.setSpacing(self.SPACING if spacing is None else int(spacing))
        self._scroll_area = self.scroll(content)
        self.root.addWidget(self._scroll_area, 1)
        return layout

    def action_bar(self, *, spacing: int | None = None) -> QHBoxLayout:
        """Pasek akcji przyklejony do dolu ekranu (poza obszarem przewijania)."""
        bar = QHBoxLayout()
        bar.setSpacing(self.ACTION_SPACING if spacing is None else int(spacing))
        self.root.addLayout(bar)
        return bar

    @staticmethod
    def empty_state(text: str, detail: str = "") -> EmptyState:
        """Pusty stan w jednym stylu (ramka + krotki komunikat)."""
        return EmptyState(text, detail)

    def render(self, data: dict) -> None:
        """Aktualizacja widoku po `set_data` (nadpisywana przez ekrany)."""

    def set_data(self, data: dict | None = None, **kwargs) -> dict:
        """Slot kontrolera: przyjmuje dane, scala je i odswieza widok."""
        payload: dict = as_dict(data)
        if data is not None and not isinstance(data, dict) and not payload:
            payload["value"] = data
        payload.update(kwargs)
        self._data.update(payload)
        self.render(payload)
        return payload

    def update_state(self, state: dict | None = None) -> None:
        """Slot kontrolera: sam stan sesji (bez pelnego `set_data`)."""
        payload = as_dict(state)
        self._data.update(payload)
        self._data["state"] = payload
        self.render({**payload, "state": payload})

    def on_app_event(self, event: str, data: dict | None = None) -> None:
        """Slot kontrolera: zdarzenie z sesji/blokady; przechowuje je i renderuje."""
        entry = {"kind": str(event), "detail": as_dict(data), "ts": time.time()}
        events = self._data.get("events")
        if not isinstance(events, list):
            events = []
            self._data["events"] = events
        events.insert(0, entry)
        del events[200:]
        self.render_event(entry)

    def render_event(self, event: dict) -> None:
        """Reakcja na pojedyncze zdarzenie (nadpisywana np. w HUD i dzienniku)."""

    def on_action_result(self, action: str, result: dict) -> None:
        """Slot kontrolera: wynik akcji wyslanej przez `request_action`."""

    def on_action_busy(self, action: str, busy: bool) -> None:
        """Slot kontrolera: ciezkie akcje (skan katalogu) w tle."""

    def data(self, key: str, default=None):
        return self._data.get(key, default)

    def show_toast(self, text: str, ms: int = 2800) -> None:
        if self._toast is None:
            self._toast = Toast(self)
        self._toast.show_message(text, ms)

    def emit_start(self, source: dict | None = None) -> None:
        self.request_start.emit(plan_payload(source))

    @staticmethod
    def scroll(widget: QWidget) -> QScrollArea:
        """Obszar przewijania bez ramki i bez poziomego paska (tresc dopasowuje sie do szerokosci)."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(widget)
        return area
