"""Kafle statystyk i komunikaty typu toast."""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QFrame, QGraphicsOpacityEffect, QWidget

from ..theme import FONT_SIZES, TYPO
from . import labels
from .indicators import DitheredBar, Sparkline
from .paint import pen, qcolor, ui_font
from .primitives import Card


class StatTile(Card):
    """Kafel: podpis + duza wartosc + notka + opcjonalny pasek postepu.

    Podpis i notka eliduja sie z pelnym tekstem w tooltipie - dlugie nazwy
    (np. powod blokady) nie rozciagaja rzedu kafli.
    """

    def __init__(self, caption: str = "", value: str = "-", parent: QWidget | None = None) -> None:
        super().__init__(parent=parent, margins=(16, 14, 16, 14), spacing=6)
        self.setMinimumWidth(132)
        self._caption = labels.ElidedLabel(caption, role="caption")
        self._value = labels.value_label(value)
        self._note = labels.ElidedLabel("", role="caption")
        self._bar = DitheredBar(bar_height=6)
        self._spark = Sparkline(height=22)
        self.body.addWidget(self._caption)
        self.body.addWidget(self._value)
        self.body.addWidget(self._note)
        self.body.addWidget(self._bar)
        self.body.addWidget(self._spark)
        self._bar.hide()
        self._spark.hide()

    # ------------------------------------------------------------------ API
    def set_caption(self, text: str) -> None:
        self._caption.set_full_text(text)

    def set_value(self, text: str) -> None:
        self._value.setText(str(text))
        self._value.setToolTip(str(text))

    def set_note(self, text: str) -> None:
        self._note.set_full_text(text)
        self._note.setVisible(bool(str(text or "").strip()))

    def set_progress(self, value: float | None) -> None:
        if value is None:
            self._bar.hide()
            return
        self._bar.set_value(value)
        if self._bar.isHidden():
            self._bar.show()

    def set_spark(self, values: list[float] | None) -> None:
        if not values:
            self._spark.hide()
            return
        self._spark.set_values(values)
        if self._spark.isHidden():
            self._spark.show()

    def set_glyph_state(self, state: str) -> None:
        """Stan bez koloru: glif dopisywany do wartosci (np. trend)."""
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


class Toast(QFrame):
    """Krotki komunikat na wierzchu widoku; pojawia sie i znika z animacja."""

    def __init__(self, parent: QWidget | None = None, margin: int = 24) -> None:
        super().__init__(parent)
        self._text = ""
        self._margin = int(margin)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(42)
        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(0.0)
        self.setGraphicsEffect(self._fade)
        self._fade_anim = QPropertyAnimation(self._fade, b"opacity", self)
        self._fade_anim.setDuration(160)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide_anim = QPropertyAnimation(self, b"pos", self)
        self._slide_anim.setDuration(160)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._target_pos = QPoint(self._margin, self._margin)
        self.hide()

    # ------------------------------------------------------------------ API
    def show_message(self, text: str, ms: int = 2800) -> None:
        self._text = str(text)
        width = self.fontMetrics().horizontalAdvance(self._text) + 40
        parent = self.parentWidget()
        limit = (parent.width() - 2 * self._margin) if parent is not None else 520
        self.setFixedWidth(max(160, min(int(width), max(160, int(limit)))))
        self._reposition()
        self.show()
        self.raise_()
        self._animate_in()
        self._timer.start(max(400, int(ms)))
        self.update()

    def hide_message(self) -> None:
        self._timer.stop()
        self.hide()

    def message(self) -> str:
        return self._text

    def _animate_in(self) -> None:
        """Krotki wjazd z dolu + rozjasnienie (tanie: widget jest maly)."""
        self._fade_anim.stop()
        self._slide_anim.stop()
        self._fade.setOpacity(0.0)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        self._slide_anim.setStartValue(QPoint(self._target_pos.x(), self._target_pos.y() + 8))
        self._slide_anim.setEndValue(self._target_pos)
        self._slide_anim.setDuration(180)
        self._slide_anim.start()

    def _fade_out(self) -> None:
        self._fade_anim.stop()
        self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(float(self._fade.opacity()))
        self._fade_anim.setEndValue(0.0)
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._fade_anim.finished.connect(self._finish_hide)
        self._fade_anim.start()

    def _finish_hide(self) -> None:
        try:
            self._fade_anim.finished.disconnect(self._finish_hide)
        except TypeError:
            pass
        self._fade_anim.setDuration(160)
        self.hide()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self._target_pos = QPoint(
            max(self._margin, parent.width() - self.width() - self._margin), self._margin
        )
        if not self.isVisible():
            self.move(self._target_pos)

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._reposition()

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(qcolor("surface3"))
        painter.setPen(pen("text_mute", 1.0, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(rect, 8.0, 8.0)
        painter.setFont(ui_font(FONT_SIZES["sm"]))
        painter.setPen(qcolor("text"))
        text_rect = rect.adjusted(14.0, 0.0, -14.0, 0.0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            painter.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, int(text_rect.width())),
        )
        painter.end()


class Badge(QFrame):
    """Malutka plakietka tekstowa (np. TRYB, HARDCORE) — sam glif i obramowanie."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "panel")
        self._label = labels.caption(text)
        layout = labels.hbox(self._label, spacing=0, margins=(0, 0, 0, 0))
        self.setLayout(layout)
        self.setFixedHeight(22)

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def text(self) -> str:
        return self._label.text()
