"""Przyciski i przelacznik: wylacznie tokeny motywu.

Przyciski maja "substancje": pod kursorem rozlewa sie animowana plama
(rozblysk zalezny od miejsca wejscia myszy), a przelacznik - lepki knob,
ktory przy zmianie stanu rozciaga sie i odbija.
"""
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
from PyQt6.QtGui import QBrush, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from ..theme import FONT_SIZES, RADIUS, SIZES, TYPO
from . import texture
from .paint import clamp01, dither_brush, gray_level, pen, qcolor, ui_font

#: Ziarno powierzchni kontrolek (ten sam material, co karty i tlo).
BUTTON_GRAIN: tuple[str, ...] = ("pepper", "sand")


def _paint_button_grain(
    painter: QPainter,
    widget: QWidget,
    *,
    tint: str = "bg",
    opacity: float = 0.35,
    radius: float = 0.0,
) -> None:
    """Ziarno wewnatrz konturki widgetu (przyciete do zaokraglenia)."""
    rect = QRectF(widget.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
    if rect.width() < 4.0 or rect.height() < 4.0:
        return
    texture.paint_surface(
        painter,
        rect,
        radius=float(radius) if radius > 0.0 else max(0.0, float(RADIUS["md"]) - 1.0),
        names=BUTTON_GRAIN,
        opacity=opacity,
        tint=tint,
    )


class _BloomMixin:
    """Wspolny rozblysk ("substancja") dla przyciskow.

    Nie zmienia stylu z QSS: plama leci pod lub nad tlem przycisku, zaleznie od
    roli (ghost jest przezroczysty, primary ma jasne tlo).
    """

    BLOOM_MS = 200

    def _setup_bloom(self) -> None:
        self._bloom = 0.0
        self._bloom_x = 0.5
        self._bloom_anim = QPropertyAnimation(self, b"bloom", self)
        self._bloom_anim.setDuration(self.BLOOM_MS)
        self._bloom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_bloom(self) -> float:
        return float(self._bloom)

    def _set_bloom(self, value: float) -> None:
        self._bloom = clamp01(value)
        self.update()

    bloom = pyqtProperty(float, fget=_get_bloom, fset=_set_bloom)

    def _animate_bloom(self, target: float) -> None:
        self._bloom_anim.stop()
        self._bloom_anim.setStartValue(float(self._bloom))
        self._bloom_anim.setEndValue(float(target))
        self._bloom_anim.start()

    def enterEvent(self, event) -> None:  # noqa: N802 (API Qt)
        try:
            position = event.position()
            self._bloom_x = clamp01(position.x() / max(1.0, float(self.width())))
        except Exception:  # noqa: BLE001 - starsze zdarzenia bez pozycji
            self._bloom_x = 0.5
        self._animate_bloom(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self._animate_bloom(0.0)
        super().leaveEvent(event)

    def _paint_bloom(self, painter: QPainter, *, dark: bool) -> None:
        if self._bloom <= 0.02:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = max(8.0, rect.height() * 0.75 * (0.7 + 1.5 * self._bloom))
        center = QPointF(rect.left() + self._bloom_x * rect.width(), rect.center().y())
        pen_color = qcolor("bg", int(46 * self._bloom)) if dark else qcolor("text", int(26 * self._bloom))
        clear = qcolor("bg", 0) if dark else qcolor("text", 0)
        gradient = QRadialGradient(center, radius)
        gradient.setColorAt(0.0, pen_color)
        gradient.setColorAt(1.0, clear)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.setClipRect(rect)
        painter.drawEllipse(center, radius, radius)
        painter.restore()


class PrimaryButton(_BloomMixin, QPushButton):
    """Glowne wezwanie do dzialania (jasne tlo, ciemny tekst)."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "primary")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(SIZES["primary"])
        self._setup_bloom()

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        # Tlo i tekst rysuje QSS, wiec plama ("substancja") leci na wierzch -
        # krycie jest tak niskie, ze napis pozostaje czytelny.
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Jasne tlo dostaje ciemne ziarno - jak swiatlo na papierze, nie szum.
        _paint_button_grain(painter, self, tint="bg", opacity=0.35)
        self._paint_bloom(painter, dark=True)
        painter.end()


class GhostButton(_BloomMixin, QPushButton):
    """Przycisk drugorzedny: obramowanie, a pod kursorem rozlewa sie plama."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "ghost")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(SIZES["button"])
        self._setup_bloom()

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().paintEvent(event)
        painter = QPainter(self)
        self._paint_bloom(painter, dark=False)
        painter.end()


class DangerButton(_BloomMixin, QPushButton):
    """Akcja nieodwracalna — bez koloru, sam kontrast obramowania."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "danger")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(SIZES["button"])
        self._setup_bloom()

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().paintEvent(event)
        painter = QPainter(self)
        self._paint_bloom(painter, dark=False)
        painter.end()


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
    """Przelacznik rysowany w kodzie: tor + lepki knob.

    Knob nie przeskakuje: plynie z animacja, po drodze rozciaga sie i splaszcza
    (jak kropla ciagnieta palcem), a na koncu delikatnie odbija (OutBack).
    """

    KNOB_MS = 200

    def __init__(self, text: str = "", parent: QWidget | None = None, checked: bool = False) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._knob = 1.0 if checked else 0.0
        self._knob_target = self._knob
        self._knob_anim = QPropertyAnimation(self, b"knob", self)
        self._knob_anim.setDuration(self.KNOB_MS)
        self._knob_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.toggled.connect(self._animate_knob)

    def _animate_knob(self, checked: bool) -> None:
        self._knob_target = 1.0 if checked else 0.0
        self._knob_anim.stop()
        if not self.isVisible():
            # Stan ustawiany z kodu (np. wczytanie ustawien) nie animuje sie.
            self._knob = self._knob_target
            self.update()
            return
        self._knob_anim.setStartValue(float(self._knob))
        self._knob_anim.setEndValue(float(self._knob_target))
        self._knob_anim.start()

    def _get_knob(self) -> float:
        return float(self._knob)

    def _set_knob(self, value: float) -> None:
        self._knob = clamp01(value)
        self.update()

    knob = pyqtProperty(float, fget=_get_knob, fset=_set_knob)

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
        progress = clamp01(self._knob)
        painter.setPen(pen("text_dim" if checked else "line_strong", 1.0))
        # Tor tez "plynie": im blizej konca, tym jasniejszy.
        painter.setBrush(gray_level(0.25 + 0.35 * progress, "surface2", "surface3"))
        painter.drawRoundedRect(track, radius, radius)
        # Tor jest materialem tej samej rodziny co tlo (drobne ziarno).
        texture.paint_surface(
            painter,
            track.adjusted(1.0, 1.0, -1.0, -1.0),
            radius=max(0.0, radius - 1.0),
            names=texture.SOFT_STACK,
            opacity=0.75,
        )

        knob_d = track_h - 6.0
        # Rozciagniecie w polowie drogi (0 na koncach, 1 w srodku) - lepki ruch.
        stretch = 4.0 * progress * (1.0 - progress)
        knob_w = knob_d * (1.0 + 0.30 * stretch)
        knob_h = knob_d * (1.0 - 0.16 * stretch)
        travel = track_w - 6.0 - knob_d
        center_x = track.left() + 3.0 + knob_d / 2.0 + travel * progress
        center_y = track.center().y()
        knob = QRectF(center_x - knob_w / 2.0, center_y - knob_h / 2.0, knob_w, knob_h)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("accent"))
        else:
            # Knob "wylaczony": polton w drobnej siatce 2x2 (wczesniej 4x4 czytal
            # sie jak szachownica) + cienki pierscien, zeby mial wyrazna krawedz.
            painter.setPen(pen("line_strong", 1.0))
            painter.setBrush(dither_brush("text_mute", 0.5, 2, bg="surface3"))
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
        self._stretch = 0.0
        self._ready = False
        self._animation = QPropertyAnimation(self, b"blob", self)
        self._animation.setDuration(self.ANIM_MS)
        # OutBack: pigulka lekko przelatuje cel i wraca - to ten "lepy" moment.
        self._animation.setEasingCurve(QEasingCurve.Type.OutBack)
        self._stretch_anim = QPropertyAnimation(self, b"stretch", self)
        self._stretch_anim.setDuration(self.ANIM_MS)
        self._stretch_anim.setKeyValueAt(0.0, 0.0)
        self._stretch_anim.setKeyValueAt(0.45, 1.0)
        self._stretch_anim.setKeyValueAt(1.0, 0.0)
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
            self._stretch_anim.stop()
            self._blob = self._target
            self._stretch = 0.0
            self.update()
            return
        if not animate or abs(self._target - self._blob) < 1.0:
            self._animation.stop()
            self._stretch_anim.stop()
            self._blob = self._target
            self._stretch = 0.0
            self.update()
            return
        self._animation.stop()
        self._animation.setStartValue(float(self._blob))
        self._animation.setEndValue(self._target)
        self._animation.start()
        self._stretch_anim.stop()
        self._stretch_anim.start()

    # ------------------------------------------------------- wlasciwosc animacji
    def _get_blob(self) -> float:
        return float(self._blob)

    def _set_blob(self, value: float) -> None:
        self._blob = float(value)
        self.update()

    blob = pyqtProperty(float, fget=_get_blob, fset=_set_blob)

    def _get_stretch(self) -> float:
        return float(self._stretch)

    def _set_stretch(self, value: float) -> None:
        self._stretch = clamp01(value)
        self.update()

    stretch = pyqtProperty(float, fget=_get_stretch, fset=_set_stretch)

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        button = self._buttons.get(self._value)
        if button is None:
            return
        self._animation.stop()
        self._stretch_anim.stop()
        self._target = float(button.geometry().center().x())
        self._blob = self._target
        self._stretch = 0.0
        self.update()

    # -------------------------------------------------------------- malowanie
    def _pill_path(self, button: GhostButton) -> QPainterPath:
        geometry = button.geometry()
        # Podczas ruchu pigulka rozciaga sie w poziomie i splaszcza (lepki plyn),
        # a po dojsciu wraca do rozmiaru przycisku.
        width = float(geometry.width()) * (1.0 + 0.22 * self._stretch)
        height = float(geometry.height()) * (1.0 - 0.18 * self._stretch)
        top = float(geometry.top()) + (float(geometry.height()) - height) / 2.0
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
        path = self._pill_path(button)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)
        # Pigulka to ta sama "substancja" co pasek boczny - drobne ziarno pod obramowaniem.
        texture.paint_grain(painter, path.boundingRect(), "sand", phase=4, opacity=0.75, clip=path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen("line_strong"))
        painter.drawPath(path)
        painter.end()
