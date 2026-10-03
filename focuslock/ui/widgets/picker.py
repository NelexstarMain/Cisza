"""Modalne okno wyboru z katalogu (aplikacje, strony, podpowiedzi).

Kontrakt:
* katalog pobieramy RAZ (poza tym oknem) i wrzucamy tu przez `set_entries`,
* filtrowanie jest lokalne - zadne zapytanie nie leci do kontrolera na znak,
* lista obsluguje klawiature: Enter = dodaj zaznaczone, Esc = zamknij,
* Ctrl/Shift zaznacza wiele pozycji; przycisk DODAJ zwraca komplet,
* stopka "NIE MA TEGO NA LIŚCIE? WPISZ RĘCZNIE" zwraca tekst z wyszukiwarki.

Widget jest monochromatyczny (tokeny motywu) i nie zawiera logiki systemowej -
tylko zbiera wybor i oddaje go ekranowi.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..theme import SIZES
from . import labels
from .buttons import GhostButton, PrimaryButton

#: Rola danych wpisu (dict) na wierszu listy.
DATA_ROLE = int(Qt.ItemDataRole.UserRole)
#: Rola znacznika naglowka grupy.
GROUP_ROLE = DATA_ROLE + 1

MANUAL_HINT = "NIE MA TEGO NA LIŚCIE? WPISZ RĘCZNIE"
EMPTY_HINT = "BRAK WYNIKÓW — UŻYJ PRZYCISKU NA DOLE, ABY WPISAĆ RĘCZNIE"
SELECT_HINT = "ZAZNACZ POZYCJĘ NA LIŚCIE (CTRL/SHIFT = WIELE)"


class CatalogPicker(QDialog):
    """Wyszukiwalny wybor z katalogu: lista + filtr lokalny + stopka reczna.

    `entries` to lista słownikow:
    * `{"kind": "group", "label": "NAUKA"}` - naglowek kategorii (nieklikalny),
    * `{"kind": "item", "key": ..., "label": ..., "detail": ..., "tooltip": ...}`
      - pozycja do zaznaczenia; caly słownik wraca w `picked_entries()`,
    * `{"kind": "note", "label": ...}` - wiersz informacyjny (nieklikalny).
    """

    picked = pyqtSignal(list)
    manual = pyqtSignal(str)

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        multi: bool = True,
        search_placeholder: str = "SZUKAJ…",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(str(title))
        self.setModal(True)
        self._multi = bool(multi)
        self._entries: list[dict] = []
        self._manual_text = ""
        self._shown = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        root.addWidget(labels.subtitle(str(title)))
        if subtitle:
            root.addWidget(labels.caption(str(subtitle)))

        self._search = QLineEdit()
        self._search.setPlaceholderText(str(search_placeholder))
        self._search.setMinimumHeight(SIZES["input"])
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self.refresh)
        self._search.returnPressed.connect(self.confirm_selection)
        root.addWidget(self._search)

        self._count = labels.caption("WYNIKI: 0")
        self._selected_count = labels.caption("ZAZNACZONO: 0")
        counters = QHBoxLayout()
        counters.setSpacing(8)
        counters.addWidget(self._count)
        counters.addStretch(1)
        counters.addWidget(self._selected_count)
        root.addLayout(counters)

        self.list = QListWidget()
        self.list.setMinimumHeight(220)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setWordWrap(False)
        self.list.setAlternatingRowColors(False)
        self.list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
            if self._multi
            else QListWidget.SelectionMode.SingleSelection
        )
        self.list.itemSelectionChanged.connect(self._sync_selected)
        self.list.itemDoubleClicked.connect(self._on_item_activated)
        root.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        cancel = GhostButton("ZAMKNIJ")
        cancel.clicked.connect(self.reject)
        add = PrimaryButton("DODAJ")
        add.clicked.connect(self.confirm_selection)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        buttons.addWidget(add)
        root.addLayout(buttons)

        self._message = labels.hint("")
        self._message.setVisible(False)
        root.addWidget(self._message)

        self._manual_button = GhostButton(MANUAL_HINT)
        self._manual_button.clicked.connect(self.use_manual)
        root.addWidget(self._manual_button)

        self.resize(620, 540)
        self.refresh()

    # ------------------------------------------------------------------ API
    def set_entries(self, entries: Any) -> None:
        """Podmienia caly zbior pozycji (grupy + wpisy)."""
        self._entries = [dict(entry) for entry in (entries or []) if isinstance(entry, dict)]
        self.refresh()

    def entries(self) -> list[dict]:
        return [dict(entry) for entry in self._entries]

    def set_query(self, text: str) -> None:
        """Ustawia filtr (odswiezenie robi sie samo)."""
        self._search.setText(str(text))

    def query(self) -> str:
        return self._search.text().strip().lower()

    def refresh(self) -> None:
        """Przebudowuje liste z lokalnym filtrem (bez zadan do kontrolera)."""
        query = self.query()
        self.list.clear()
        shown = 0
        for group_label, items in self._grouped():
            matched = [entry for entry in items if not query or query in self._haystack(entry)]
            if not matched:
                continue
            if group_label:
                self._add_group(group_label)
            for entry in matched:
                self._add_entry(entry)
                shown += 1
        if shown == 0:
            self._add_note(EMPTY_HINT)
        self._shown = shown
        self._count.setText(f"WYNIKI: {shown}")
        self._select_first()
        self._sync_selected()

    def shown_count(self) -> int:
        return int(self._shown)

    def visible_labels(self) -> list[str]:
        """Teksty klikalnych wierszy (naglowki i noty pomijamy)."""
        out: list[str] = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                out.append(item.text())
        return out

    def keys(self) -> list[str]:
        """Klucze klikalnych wpisow w kolejnosci na liscie."""
        out: list[str] = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if not (item.flags() & Qt.ItemFlag.ItemIsSelectable):
                continue
            data = item.data(DATA_ROLE)
            if isinstance(data, dict):
                out.append(str(data.get("key") or ""))
        return out

    def selected_entries(self) -> list[dict]:
        out: list[dict] = []
        for item in self.list.selectedItems():
            data = item.data(DATA_ROLE)
            if isinstance(data, dict):
                out.append(dict(data))
        return out

    def picked_entries(self) -> list[dict]:
        """Wybor zatwierdzony przez DODAJ/Enter (puste, gdy okno zamknieto)."""
        if self._manual_text or self.result() != QDialog.DialogCode.Accepted:
            return []
        return self.selected_entries()

    def manual_text(self) -> str:
        """Tekst zwrocony stopka 'WPISZ RĘCZNIE' (pusty, gdy wybrano z listy)."""
        return str(self._manual_text)

    def select_keys(self, keys: list[str]) -> None:
        """Programowe zaznaczenie wpisow po kluczu (Ctrl/Shift zastepuje test)."""
        wanted = {str(key) for key in (keys or [])}
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(DATA_ROLE)
            if isinstance(data, dict) and str(data.get("key") or "") in wanted:
                item.setSelected(True)

    # ----------------------------------------------------------------- akcje
    def confirm_selection(self) -> None:
        """DODAJ/Enter: zwraca zaznaczone wpisy i zamyka okno."""
        picked = self.selected_entries()
        if not picked:
            self._set_message(SELECT_HINT)
            return
        self._manual_text = ""
        self.picked.emit(picked)
        self.accept()

    def use_manual(self) -> None:
        """Stopka reczna: zwraca wpisany tekst zamiast pozycji z listy."""
        text = self._search.text().strip()
        if not text:
            self._set_message("WPISZ NAJPIERW NAZWĘ ALBO HOST, A POTEM KLIKNIJ TEN PRZYCISK")
            return
        self._manual_text = text
        self.manual.emit(text)
        self.accept()

    # ---------------------------------------------------------------- czastki
    def _grouped(self) -> list[tuple[str, list[dict]]]:
        groups: list[tuple[str, list[dict]]] = []
        current: list[dict] | None = None
        label = ""
        for entry in self._entries:
            if str(entry.get("kind") or "") == "group":
                current = []
                label = str(entry.get("label") or "")
                groups.append((label, current))
                continue
            if current is None:
                current = []
                groups.append(("", current))
            current.append(entry)
        return groups

    @staticmethod
    def _haystack(entry: dict) -> str:
        return " ".join(
            str(entry.get(key) or "") for key in ("label", "detail", "search", "tooltip")
        ).lower()

    def _add_group(self, text: str) -> None:
        item = QListWidgetItem(f"— {str(text).upper()} —")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(GROUP_ROLE, True)
        self.list.addItem(item)

    def _add_note(self, text: str) -> None:
        item = QListWidgetItem(str(text))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(GROUP_ROLE, False)
        self.list.addItem(item)

    def _add_entry(self, entry: dict) -> None:
        name = str(entry.get("label") or entry.get("key") or "")
        detail = str(entry.get("detail") or "")
        text = f"{name}  ·  {detail}" if detail else name
        item = QListWidgetItem(text)
        item.setData(DATA_ROLE, dict(entry))
        tip = str(entry.get("tooltip") or "")
        if not tip:
            tip = text
        item.setToolTip(tip)
        self.list.addItem(item)

    def _select_first(self) -> None:
        if not self._multi:
            return
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self.list.setCurrentRow(index)
                return

    def _sync_selected(self) -> None:
        self._selected_count.setText(f"ZAZNACZONO: {len(self.selected_entries())}")

    def _set_message(self, text: str) -> None:
        self._message.setText(str(text))
        self._message.setVisible(bool(text))

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        if item.flags() & Qt.ItemFlag.ItemIsSelectable:
            self.confirm_selection()

    # ------------------------------------------------------------ klawiatura
    def keyPressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm_selection()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)
