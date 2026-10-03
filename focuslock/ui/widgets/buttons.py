"""Przyciski i przelacznik: wylacznie tokeny motywu."""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from ..theme import FONT_SIZES, SIZES, TYPO
from .paint import dither_brush, pen, qcolor, ui_font


class PrimaryButton(QPushButton):
    """Glowne wezwanie do dzialania (jasne tlo, ciemny tekst)."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "primary")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(SIZES["primary"])


class GhostButton(QPushButton):
    """Przycisk drugorzedny (obramowanie, bez wypelnienia)."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "ghost")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(SIZES["button"])


class DangerButton(QPushButton):
    """Akcja nieodwracalna — bez koloru, sam kontrast obramowania."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "danger")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(SIZES["button"])


def link_button(text: str = "", parent: QWidget | None = None) -> QPushButton:
    button = QPushButton(text, parent)
    button.setFlat(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(
        f'QPushButton {{ background: transparent; border: none; color: {_mute()}; '
        f'{TYPO.ui(FONT_SIZES["sm"])} letter-spacing: 2px; }}'
        f'QPushButton:hover {{ color: {_text()}; }}'
    )
    return button


def _mute() -> str:
    from ..theme import c

    return c("text_mute")


def _text() -> str:
    from ..theme import c

    return c("text")


class Toggle(QAbstractButton):
    """Przelacznik rysowany w kodzie: tor + galka (stan = dithering vs pelny)."""

    def __init__(self, text: str = "", parent: QWidget | None = None, checked: bool = False) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        width = 44
        if self.text():
            width += 10 + self.fontMetrics().horizontalAdvance(self.text()) + 4
        return QSize(width, SIZES["toggle"])

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(44, SIZES["toggle"])

    def hitButton(self, pos) -> bool:  # type: ignore[override]
        return True

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track_w, track_h = 44.0, 22.0
        top = (self.height() - track_h) / 2.0
        track = QRectF(0.5, top + 0.5, track_w - 1.0, track_h - 1.0)
        radius = track.height() / 2.0
        checked = self.isChecked()
        painter.setPen(pen("text_dim" if checked else "line_strong", 1.0))
        painter.setBrush(qcolor("surface3" if checked else "surface2"))
        painter.drawRoundedRect(track, radius, radius)

        knob_d = track_h - 6.0
        x = track.right() - 3.0 - knob_d if checked else track.left() + 3.0
        knob = QRectF(x, top + 3.0, knob_d, knob_d)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("accent"))
        else:
            painter.setPen(pen("line_strong", 1.0))
            painter.setBrush(dither_brush("text_mute", 0.5, 4, bg="surface2"))
        painter.drawEllipse(knob)

        if self.text():
            painter.setFont(ui_font(FONT_SIZES["sm"]))
            painter.setPen(qcolor("text" if checked else "text_dim"))
            painter.drawText(
                QRectF(track_w + 10.0, 0.0, max(0.0, self.width() - track_w - 10.0), float(self.height())),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self.text(),
            )
        painter.end()


class ChoiceGroup(QWidget):
    """Segmentowany wybor jednej opcji z plynna pigulka pod wyborem.

    Tlo maluje sam widget (`paintEvent` rysuje kapsule), a przyciski sa tylko
    tekstem. Przy zmianie wyboru pigulka przejezdza z animacja i lekko sie
    rozciaga - ten sam efekt "gooey", co w pasku nawigacji.
    """

    changed = pyqtSignal(str)

    ANIM_MS = 220

    def __init__(self, options: list[tuple[str, str]] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(3, 3, 3, 3)
        self._layout.setSpacing(4)
        self._buttons: dict[str, GhostButton] = {}
        self._value = ""
        self._blob = 0.0
        self._target = 0.0
        self._ready = False
        self._animation = QPropertyAnimation(self, b"blob", self)
        self._animation.setDuration(self.ANIM_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.set_options(options or [])

    def set_options(self, options: list[tuple[str, str]]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()
        for key, label in options:
            button = GhostButton(label)
            button.setMinimumHeight(SIZES["chip"])
            self._style_button(button)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, k=key: self.set_value(k, emit=True))
            self._layout.addWidget(button)
            self._buttons[key] = button
        self._layout.addStretch(1)
        self._ready = False
        if options and self._value not in self._buttons:
            self._value = options[0][0]
        self._refresh(animate=False)

    def value(self) -> str:
        return self._value

    def set_value(self, key: str, emit: bool = False) -> None:
        if key not in self._buttons:
            return
        changed = key != self._value
        self._value = key
        self._refresh(animate=changed)
        if emit and changed:
            self.changed.emit(key)

    @staticmethod
    def _style_button(button: GhostButton) -> None:
        from ..theme import RADIUS, c

        button.setStyleSheet(
            "QPushButton {"
            " background: transparent;"
            " border: 1px solid transparent;"
            f" border-radius: {RADIUS['md']}px;"
            f" color: {c('text_dim')};"
            " padding: 6px 14px;"
            " letter-spacing: 0.5px;"
            "}"
            f"QPushButton:hover {{ color: {c('text')}; }}"
            f"QPushButton:checked {{ color: {c('text')}; font-weight: 600; }}"
            f"QPushButton:focus {{ border-color: {c('line_strong')}; }}"
        )

    def _refresh(self, *, animate: bool = True) -> None:
        for key, button in self._buttons.items():
            button.setChecked(key == self._value)
        button = self._buttons.get(self._value)
        if button is None:
            self.update()
            return
        self._target = float(button.geometry().center().x())
        if not self._ready:
            self._ready = True
            self._animation.stop()
            self._blob = self._target
            self.update()
            return
        if not animate or abs(self._target - self._blob) < 1.0:
            self._animation.stop()
            self._blob = self._target
            self.update()
            return
        self._animation.stop()
        self._animation.setStartValue(float(self._blob))
        self._animation.setEndValue(self._target)
        self._animation.start()

    # ------------------------------------------------------- wlasciwosc animacji
    def _get_blob(self) -> float:
        return float(self._blob)

    def _set_blob(self, value: float) -> None:
        self._blob = float(value)
        self.update()

    blob = pyqtProperty(float, fget=_get_blob, fset=_set_blob)

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        button = self._buttons.get(self._value)
        if button is None:
            return
        self._animation.stop()
        self._target = float(button.geometry().center().x())
        self._blob = self._target
        self.update()

    # -------------------------------------------------------------- malowanie
    def _pill_path(self, button: GhostButton) -> QPainterPath:
        geometry = button.geometry()
        width = float(geometry.width())
        height = float(geometry.height())
        top = float(geometry.top())
        radius = height / 2.0
        path = QPainterPath()
        path.addRoundedRect(QRectF(self._blob - width / 2.0, top, width, height), radius, radius)
        gap = self._target - self._blob
        if abs(gap) < 2.0:
            return path
        target = QPainterPath()
        target.addRoundedRect(QRectF(self._target - width / 2.0, top, width, height), radius, radius)
        left = min(self._blob, self._target)
        right = max(self._blob, self._target)
        neck_height = height * 0.62
        neck = QPainterPath()
        neck.addRoundedRect(
            QRectF(left, top + (height - neck_height) / 2.0, right - left, neck_height),
            neck_height / 2.0,
            neck_height / 2.0,
        )
        return path.united(neck).united(target)

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        button = self._buttons.get(self._value)
        if button is None:
            return
        geometry = button.geometry()
        height = float(max(12, geometry.height()))
        top = float(geometry.top())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        gradient = QLinearGradient(QPointF(0.0, top), QPointF(0.0, top + height))
        gradient.setColorAt(0.0, qcolor("surface3"))
        gradient.setColorAt(1.0, qcolor("surface2"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(self._pill_path(button))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen("line_strong"))
        painter.drawPath(self._pill_path(button))
        painter.end()
