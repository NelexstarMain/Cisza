"""Modalny dialog PIN: bez ramki systemowej, tylko szarosci."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ..theme import FONT_SIZES, TYPO
from ..widgets import Card, GhostButton, PrimaryButton, labels
from ..widgets.paint import mono_font


class PinDialog(QDialog):
    """Weryfikacja PIN-u; decyzje podejmuje kontroler przez `notify_result`."""

    request_pin_check = pyqtSignal(str)
    pin_result = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None, prompt: str = "PODAJ PIN", title: str = "WERYFIKACJA") -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(True)
        self.setMinimumWidth(420)
        self._attempts = 0

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        card = Card(title, "BEZ PIN-U NIE MA WYJŚCIA")
        self._prompt = labels.field(prompt)
        self._pin = QLineEdit()
        self._pin.setEchoMode(QLineEdit.EchoMode.Password)
        self._pin.setMaxLength(16)
        self._pin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pin.setMinimumHeight(56)
        font = mono_font(FONT_SIZES["xl"], 400)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 8.0)
        self._pin.setFont(font)
        self._pin.returnPressed.connect(self._submit)
        self._error = labels.caption("")
        self._error.setWordWrap(True)
        self._attempts_label = labels.caption("")

        actions = QHBoxLayout()
        actions.setSpacing(10)
        cancel = GhostButton("ANULUJ")
        cancel.clicked.connect(self._cancel)
        confirm = PrimaryButton("POTWIERDŹ")
        confirm.clicked.connect(self._submit)
        actions.addWidget(cancel)
        actions.addStretch(1)
        actions.addWidget(confirm)

        card.add(self._prompt)
        card.add(self._pin)
        card.add(self._error)
        card.add(self._attempts_label)
        card.add_layout(actions)
        shell.addWidget(card)

    # ------------------------------------------------------------------ API
    def set_prompt(self, text: str) -> None:
        self._prompt.setText(text)

    def set_error(self, text: str) -> None:
        self._error.setText(text)

    def set_attempts(self, count: int) -> None:
        self._attempts = max(0, int(count))
        self._attempts_label.setText(f"PRÓBY: {self._attempts}" if self._attempts else "")

    def attempts(self) -> int:
        return self._attempts

    def reset(self) -> None:
        self._pin.clear()
        self.set_error("")
        self._pin.setFocus()

    def notify_result(self, ok: bool) -> None:
        """Slot kontrolera: wynik sprawdzenia PIN-u."""
        if ok:
            self.pin_result.emit(True)
            self.accept()
            return
        self.set_attempts(self._attempts + 1)
        self.set_error("BŁĘDNY PIN — SPRÓBUJ PONOWNIE")
        self.pin_result.emit(False)
        self.reset()

    # -------------------------------------------------------------- wewnetrzne
    def _submit(self) -> None:
        pin = self._pin.text().strip()
        if not pin:
            self.set_error("PODAJ PIN, ŻEBY KONTYNUOWAĆ")
            return
        self.set_error("SPRAWDZAM…")
        self.request_pin_check.emit(pin)

    def _cancel(self) -> None:
        self.pin_result.emit(False)
        self.reject()

    def showEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            geometry = parent.window().geometry() if parent.window() else parent.geometry()
            self.move(
                geometry.center().x() - self.width() // 2,
                geometry.center().y() - self.height() // 2,
            )
        self._pin.setFocus()
