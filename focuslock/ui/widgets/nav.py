"""Nawigacja boczna: plynna ("gooey") pigulka pod aktywna pozycja.

Pigulka nie skacze - przesuwa sie z animacja, a podczas ruchu rysujemy
polaczona krople (dwie zaokraglone kapsuly + przewezona szyjka). To daje
efekt lepkiego plynu bez zadnych filtrow SVG i bez kolorow: ksztalt bierze
sie z boolowskiej sumy QPainterPath, a faktura z ditheringu.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .buttons import GhostButton
from .paint import pen, qcolor


class NavRail(QWidget):
    """Pionowy pasek nawigacji; aktywny element podswietla plynna pigulka."""

    navigate = pyqtSignal(str)

    #: Czas przejscia pigulki (ms) - krotko, zeby UI nie "czekalo" na animacje.
    ANIM_MS = 280

    def __init__(self, parent: QWidget | None = None, width: int = 190) -> None:
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFixedWidth(int(width))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 12, 10, 12)
        self._layout.setSpacing(6)
        self._buttons: dict[str, GhostButton] = {}
        self._current = ""
        self._blob = 0.0
        self._target = 0.0
        self._ready = False
        self._animation = QPropertyAnimation(self, b"blob", self)
        self._animation.setDuration(self.ANIM_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._layout.addStretch(1)

    # ------------------------------------------------------------------ API
    @staticmethod
    def _style_button(button: GhostButton) -> None:
        """Wyglad pozycji paska: tekst (bez tla), bo tlo maluje pigulka.

        Styl jest wstawiony wprost w przycisk, a nie w globalny QSS: selektor po
        dynamicznej wlasciwosci `role` bywa zawodny, gdy wartosc zmienia sie po
        utworzeniu widgetu.
        """
        from ..theme import RADIUS, c

        button.setStyleSheet(
            "QPushButton {"
            " background: transparent;"
            " border: 1px solid transparent;"
            f" border-radius: {RADIUS['md']}px;"
            f" color: {c('text_dim')};"
            " text-align: left;"
            " padding: 10px 14px;"
            " letter-spacing: 0.6px;"
            "}"
            f"QPushButton:hover {{ color: {c('text')}; border-color: {c('line')}; }}"
            f"QPushButton:checked {{ color: {c('text')}; border-color: transparent; font-weight: 600; }}"
            f"QPushButton:focus {{ border-color: {c('line_strong')}; }}"
        )

    def set_items(self, items: list[tuple[str, str]]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()
        for key, label in items:
            button = GhostButton(label)
            self._style_button(button)
            button.setCheckable(True)
            button.setMinimumHeight(40)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self._activate(k))
            self._layout.addWidget(button)
            self._buttons[key] = button
        self._layout.addStretch(1)
        # Po przebudowie listy pozycja pigulki musi trafic na nowe geometrie.
        self._ready = False
        if items:
            self.set_current(self._current if self._current in self._buttons else items[0][0])

    def set_current(self, key: str) -> None:
        self._current = str(key)
        for item_key, button in self._buttons.items():
            button.setChecked(item_key == self._current)
        button = self._buttons.get(self._current)
        if button is None:
            self.update()
            return
        self._target = float(button.geometry().center().y())
        if not self._ready:
            # Pierwsze ustawienie (albo przebudowa listy) - bez lotu w pustke.
            self._ready = True
            self._animation.stop()
            self._blob = self._target
            self.update()
            return
        if abs(self._target - self._blob) < 1.0:
            self.update()
            return
        self._animation.stop()
        self._animation.setStartValue(float(self._blob))
        self._animation.setEndValue(self._target)
        self._animation.start()

    def current(self) -> str:
        return self._current

    def keys(self) -> list[str]:
        return list(self._buttons)

    def _activate(self, key: str) -> None:
        self.set_current(key)
        self.navigate.emit(key)

    def _snap_to_current(self) -> None:
        """Po zmianie geometrii pigulka musi trafic na aktualna pozycje pozycji.

        Bez tego pierwszy `set_current` (przed pokazaniem okna) zapamietuje
        geometry z zerowa wysokoscia i pigulka rozciaga sie przez caly pasek.
        """
        button = self._buttons.get(self._current)
        if button is None:
            return
        self._animation.stop()
        self._target = float(button.geometry().center().y())
        self._blob = self._target
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._snap_to_current()

    def showEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().showEvent(event)
        self._snap_to_current()

    # ------------------------------------------------------- wlasciwosc animacji
    def _get_blob(self) -> float:
        return float(self._blob)

    def _set_blob(self, value: float) -> None:
        self._blob = float(value)
        self.update()

    blob = pyqtProperty(float, fget=_get_blob, fset=_set_blob)

    # -------------------------------------------------------------- malowanie
    def _blob_path(self, x: float, width: float, height: float) -> QPainterPath:
        """Kropla: kapsula w biezacej pozycji, kapsula w docelowej i szyjka."""
        radius = min(14.0, height / 2.0)
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, self._blob - height / 2.0, width, height), radius, radius)
        gap = self._target - self._blob
        if abs(gap) < 2.0:
            return path
        target = QPainterPath()
        target.addRoundedRect(QRectF(x, self._target - height / 2.0, width, height), radius, radius)
        top = min(self._blob, self._target)
        bottom = max(self._blob, self._target)
        waist = QPainterPath()
        neck_width = width * 0.32
        neck_x = x + (width - neck_width) / 2.0
        neck_top = top - height * 0.18
        neck_height = (bottom - top) + height * 0.36
        waist.addRoundedRect(QRectF(neck_x, neck_top, neck_width, max(1.0, neck_height)), 16.0, 16.0)
        return path.united(waist).united(target)

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        button = self._buttons.get(self._current)
        if button is None:
            return
        geometry = button.geometry()
        height = float(max(28, geometry.height()))
        width = float(max(24, geometry.width()))
        left = float(geometry.left())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = self._blob_path(left, width, height)
        # Szklisty gradient (dwa odcienie szarosci) + cienka obwodka. Bez
        # ditheringu: na duzej plamie wzor Bayera czytal sie jak szum.
        top = self._blob - height / 2.0
        gradient = QLinearGradient(QPointF(0.0, top), QPointF(0.0, top + height))
        gradient.setColorAt(0.0, qcolor("surface3"))
        gradient.setColorAt(1.0, qcolor("surface2"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen("line_strong"))
        painter.drawPath(path)
        # Znacznik aktywnosci po lewej stronie paska.
        marker = QRectF(left - 8.0, self._blob - height / 2.0 + 8.0, 2.0, max(6.0, height - 16.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("accent"))
        painter.drawRoundedRect(marker, 1.0, 1.0)
        painter.end()
