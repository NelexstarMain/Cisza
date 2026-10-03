"""Podstawowe elementy: linia wlosowa, karta i pusty stan."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLayout, QSizePolicy, QVBoxLayout, QWidget

from . import labels

#: Wspolne marginesy wnetrza kart - wszystkie ekrany uzywaja tego samego rytmu.
CARD_MARGINS = (18, 16, 18, 16)
CARD_SPACING = 10


class Hairline(QFrame):
    """Jednopikselowa linia podzialu (token `line`)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "hairline")
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def hr() -> Hairline:
    return Hairline()


class Card(QFrame):
    """Karta z opcjonalnym naglowkiem; zawartosc dodawana przez `add`/`add_layout`."""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = CARD_MARGINS,
        spacing: int = CARD_SPACING,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(*margins)
        shell.setSpacing(spacing)
        self._shell = shell
        self._title = None
        self._subtitle = None
        self._hint = None
        if title:
            self._title = labels.subtitle(title)
            shell.addWidget(self._title)
        if subtitle:
            self._subtitle = labels.caption(subtitle)
            shell.addWidget(self._subtitle)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        shell.addLayout(self.body)

    # ------------------------------------------------------------------ API
    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout: QLayout) -> QLayout:
        self.body.addLayout(layout)
        return layout

    def set_title(self, text: str) -> None:
        if self._title is None:
            self._title = labels.subtitle(text)
            self._shell.insertWidget(0, self._title)
        else:
            self._title.setText(text)

    def set_subtitle(self, text: str) -> None:
        if self._subtitle is None:
            index = 1 if self._title is not None else 0
            self._subtitle = labels.caption(text)
            self._shell.insertWidget(index, self._subtitle)
        else:
            self._subtitle.setText(text)

    def set_hint(self, text: str) -> None:
        """Drobna notka pod trescia karty (np. ostrzezenie)."""
        if self._hint is None:
            self._hint = labels.hint(text)
            self._hint.setVisible(bool(text))
            self.body.addWidget(self._hint)
        else:
            self._hint.setText(text)
            self._hint.setVisible(bool(text))

    def set_active(self, active: bool) -> None:
        self.setProperty("state", "active" if active else "")
        self.style().unpolish(self)
        self.style().polish(self)


class EmptyState(QFrame):
    """Pusty stan: krotki komunikat zamiast pustej listy.

    Uzywany tam, gdzie dane moga nie istniec (brak zdarzen, presetow, wpisow banku).
    Ramka jest rozciagliwa, a tekst wysrodkowany, wiec lista nigdy nie wyglada na zepsuta.
    """

    def __init__(
        self,
        text: str = "",
        detail: str = "",
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = (18, 20, 18, 20),
    ) -> None:
        super().__init__(parent)
        # rola "emptyBox", bo QLabel tez jest QFrame - rola "empty" obramowalaby tekst
        self.setProperty("role", "emptyBox")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*margins)
        layout.setSpacing(6)
        self._label = labels.empty(text)
        self._detail = labels.empty_detail(detail)
        layout.addStretch(1)
        layout.addWidget(self._label)
        layout.addWidget(self._detail)
        layout.addStretch(1)
        self._detail.setVisible(bool(detail))
        self.setMinimumHeight(96)

    # ------------------------------------------------------------------ API
    def text(self) -> str:
        return self._label.text()

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def detail(self) -> str:
        return self._detail.text()

    def set_detail(self, detail: str) -> None:
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))

    def sizeHint(self):  # noqa: N802 (API Qt)
        hint = super().sizeHint()
        hint.setHeight(max(hint.height(), 96))
        return hint


#: Krotkie powody zakonczenia sesji w jezyku czlowieka (zamiast surowych kluczy).
REASON_LABELS: dict[str, str] = {
    "completed": "sesja domknięta",
    "user": "zakończona ręcznie",
    "abort": "przerwana",
    "aborted": "przerwana",
    "panic": "wyjście awaryjne",
    "free_done": "tryb wolny zakończony",
}


def reason_label(reason: str) -> str:
    """Czytelny opis powodu zakonczenia sesji."""
    key = str(reason or "").strip().lower()
    if not key:
        return ""
    return REASON_LABELS.get(key, key.replace("_", " "))


class Separator(QWidget):
    """Pusta przerwa o stalej wysokosci."""

    def __init__(self, height: int = 12, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(int(height))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
