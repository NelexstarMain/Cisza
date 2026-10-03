"""Nawigacja boczna: lista pozycji z glifem aktywnego elementu."""
from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .buttons import GhostButton
from .paint import qcolor


class NavRail(QWidget):
    """Pionowy pasek nawigacji; aktywny element oznaczony kreska (bez koloru)."""

    navigate = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, width: int = 190) -> None:
        super().__init__(parent)
        self.setFixedWidth(int(width))
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(4)
        self._buttons: dict[str, GhostButton] = {}
        self._current = ""
        self._layout.addStretch(1)

    # ------------------------------------------------------------------ API
    def set_items(self, items: list[tuple[str, str]]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()
        for key, label in items:
            button = GhostButton(label)
            button.setCheckable(True)
            button.setMinimumHeight(38)
            button.clicked.connect(lambda _checked=False, k=key: self._activate(k))
            self._layout.addWidget(button)
            self._buttons[key] = button
        self._layout.addStretch(1)
        if items:
            self.set_current(self._current if self._current in self._buttons else items[0][0])

    def set_current(self, key: str) -> None:
        self._current = str(key)
        for item_key, button in self._buttons.items():
            button.setChecked(item_key == self._current)
        self.update()

    def current(self) -> str:
        return self._current

    def keys(self) -> list[str]:
        return list(self._buttons)

    def _activate(self, key: str) -> None:
        self.set_current(key)
        self.navigate.emit(key)

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        button = self._buttons.get(self._current)
        if button is not None:
            geometry = button.geometry()
            marker = QRect(0, geometry.top() + 7, 3, max(4, geometry.height() - 14))
            painter.fillRect(marker, qcolor("accent"))
        painter.end()
