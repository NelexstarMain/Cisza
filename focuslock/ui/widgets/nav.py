"""Nawigacja boczna: plynna ("gooey") pigulka pod aktywna pozycja.

Pigulka nie skacze - przesuwa sie z animacja, a przy scianie paska zostaje
lepka smuga. Efekt "goo" robimy tak, jak robi sie go w aplikacji natywnej
(Qt nie obsluguje filtrow SVG, ``QSvgRenderer`` nie zna ``feGaussianBlur``):

1. **Wklęsłe łuki** - ``_liquid_path`` rysuje jedna ciagla sciezke: od sciany
   paska wklęslym lukiem w gore, zaokraglony guzik, wklęsly luk w dol i powrot
   po scianie. To ten sam kształt, ktory w CSS daje ``filter: url(#gooey-filter)``
   na styku zakladki z kontenerem - tutaj jawnie, wiec jest ostry i tani.
2. **Rozciaganie cieczy** - ``_substance_path`` animuje przod i tyl osobno:
   przednia krawedz wyprzedza srodek, tyl zostaje z tylu, a talia zweza sie
   o ``WAIST`` (zachowanie objetosci). Smuga na scianie (``_residue_path``)
   ciagnie sie od pozycji startowej.

Trzy warstwy animacji:
1. ``blob`` - srodek pigulki; przejscie 280 ms, OutCubic.
2. ``stretch`` - rozciagniecie substancji; profil ``_stretch_profile()``
   (polowka sinusa) rosnie w pierwszej polowie lotu i wraca w drugiej;
   animacja 320 ms z InOutCubic.
3. ``hover`` - przygaszona kropla pod kursorem (160 ms, OutCubic, miękka maska
   z ``_goo_mask``) oraz delikatny "oddech" pigulki w bezruchu (QTimer 50 ms,
   cykl 3 s, amplituda 1.5 px). Timer oddechu zyje wylacznie wtedy, gdy widget
   jest widoczny i zadne przejscie nie trwa (zatrzymuje go ``hideEvent``).

Etykiety rysuja dzieci (``GhostButton``) nad warstwa paska, wiec tekst zostaje
poza kształtem i pozostaje ostry. ``_blob_path`` zostaje dla kontraktu z testem
regresyjnym (`tests/test_ui_fixes_round3.py`).
"""
from __future__ import annotations

import math

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QImage, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .buttons import GhostButton
from .paint import pen, qcolor
from . import texture


class NavRail(QWidget):
    """Pionowy pasek nawigacji; aktywny element podswietla plynna pigulka."""

    navigate = pyqtSignal(str)

    #: Czas przejscia pigulki (ms) - krotko, zeby UI nie "czekalo" na animacje.
    ANIM_MS = 280
    #: Czas rozciagniecia substancji (ms) - miesci sie w 260-340 ms z opisu zadania.
    STRETCH_MS = 320
    #: Czas dogonienia kursora przez kropelke hover (ms).
    HOVER_MS = 160
    #: Krok "oddechu" pigulki (ms) - ok. 20 Hz, bardzo tanie przerysowanie.
    BREATH_MS = 50
    #: Pelny cykl oddechu (s).
    BREATH_PERIOD_S = 3.0
    #: Amplituda oddechu (px) - swiadomie subtelna (1-2 px).
    BREATH_AMP = 1.5
    #: Promien wklęslego luku, ktorym pigulka zlewa sie ze sciana paska.
    FILLET_R = 12.0
    #: Maksymalne zwezenie talii przy pelnym rozciagnieciu (ulamek szerokosci).
    WAIST = 0.18
    #: Jaka czesc przebytego dystansu przednia krawedz wyprzedza srodek (przy pelnym rozciagnieciu).
    STRETCH_LEAD = 0.30
    #: Sciana paska: cienka listwa przy lewej krawedzi, z ktorej "wycieka" pigulka.
    WALL_X = 2.0
    WALL_W = 2.0
    #: Filtr goo (kropelka hover): maska liczona w skali 1/N, rozmywana przeskalowaniem.
    GOO_SCALE = 4

    def __init__(self, parent: QWidget | None = None, width: int = 190) -> None:
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFixedWidth(int(width))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 12, 10, 12)
        self._layout.setSpacing(6)
        self._buttons: dict[str, GhostButton] = {}
        self._current = ""
        self._blob = 0.0
        self._target = 0.0
        self._start = 0.0
        self._stretch = 0.0
        self._ready = False

        self._animation = QPropertyAnimation(self, b"blob", self)
        self._animation.setDuration(self.ANIM_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_move_finished)

        self._stretch_animation = QPropertyAnimation(self, b"stretch", self)
        self._stretch_animation.setDuration(self.STRETCH_MS)
        # InOutCubic: nitka rosnie w pierwszej polowie lotu i wciaga sie w drugiej
        # (OutBack dochodzil do 1 zbyt wczesnie i profil gasl w polowie ruchu).
        self._stretch_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._stretch_animation.finished.connect(self._on_stretch_finished)

        self._hover = 0.0
        self._hover_from = QPointF()
        self._hover_to = QPointF()
        self._hover_animation = QPropertyAnimation(self, b"hover", self)
        self._hover_animation.setDuration(self.HOVER_MS)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Krotkie opoznienie gaszenia kropelki: przejscie miedzy przyciskami
        # (Leave + Enter) nie moze wygladac jak mruganie.
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(120)
        self._hover_timer.timeout.connect(self._fade_hover_out)

        self._breath = 0.0
        self._breath_phase = 0.0
        self._breath_timer = QTimer(self)
        self._breath_timer.setInterval(self.BREATH_MS)
        self._breath_timer.timeout.connect(self._tick_breath)

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
            f"QPushButton:hover {{ color: {c('text')}; }}"
            f"QPushButton:checked {{ color: {c('text')}; border-color: transparent; font-weight: 600; }}"
            f"QPushButton:focus {{ border-color: {c('line_strong')}; }}"
        )

    def set_items(self, items: list[tuple[str, str]]) -> None:
        # Przebudowa listy moze wypasc w trakcie lotu pigulki - najpierw
        # zatrzymujemy ruch, zeby stare animacje nie pisaly po nowych geometriach.
        self._stop_motion()
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
            button.setMouseTracking(True)
            button.installEventFilter(self)
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
            self._stop_motion()
            self._blob = self._target
            self._start = self._target
            self.update()
            self._ensure_breath()
            return
        if abs(self._target - self._blob) < 1.0:
            self._blob = self._target
            self._start = self._target
            self._stretch = 0.0
            self.update()
            self._ensure_breath()
            return
        # W bezruchu pigulka "oddycha" - na czas lotu oddech musi zamilknac,
        # zeby drobna amplituda nie mieszala sie z przejsciem.
        self._breath_timer.stop()
        self._breath = 0.0
        self._animation.stop()
        self._stretch_animation.stop()
        self._start = float(self._blob)
        self._animation.setStartValue(float(self._blob))
        self._animation.setEndValue(self._target)
        self._animation.start()
        self._stretch_animation.setStartValue(0.0)
        self._stretch_animation.setEndValue(1.0)
        self._stretch_animation.start()

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
        self._stretch_animation.stop()
        self._stretch = 0.0
        self._target = float(button.geometry().center().y())
        self._blob = self._target
        self._start = self._target
        self.update()

    # ------------------------------------------------------------ animacje
    def _transition_active(self) -> bool:
        running = QAbstractAnimation.State.Running
        return (
            self._animation.state() == running
            or self._stretch_animation.state() == running
        )

    def _stop_motion(self) -> None:
        """Zatrzymuje przejscie i gasi kropelke (uzywane m.in. przy przebudowie)."""
        self._animation.stop()
        self._stretch_animation.stop()
        self._stretch = 0.0
        self._start = float(self._blob)
        self._reset_hover()

    def _on_move_finished(self) -> None:
        self._start = float(self._target)
        self._ensure_breath()

    def _on_stretch_finished(self) -> None:
        # Po animacji substancja wraca do zera (profil i tak wygasa na koncu).
        self._stretch = 0.0
        self.update()
        self._ensure_breath()

    # ---------------------------------------------------------- oddychanie
    def _ensure_breath(self) -> None:
        """Oddech tylko na widocznym, ustabilizowanym pasku."""
        ready = self._buttons.get(self._current) is not None
        if self.isVisible() and ready and not self._transition_active():
            if not self._breath_timer.isActive():
                self._breath_timer.start()
        else:
            self._breath_timer.stop()

    def _tick_breath(self) -> None:
        if not self.isVisible() or self._transition_active():
            self._breath_timer.stop()
            return
        self._breath_phase = (self._breath_phase + self.BREATH_MS / 1000.0) % self.BREATH_PERIOD_S
        value = math.sin(2.0 * math.pi * self._breath_phase / self.BREATH_PERIOD_S)
        if abs(value - self._breath) < 0.02:
            return  # brak istotnej zmiany - bez zbednego przerysowania
        self._breath = value
        self.update()

    # ------------------------------------------------------------- kropelka
    def _drop_center(self) -> QPointF:
        progress = min(1.0, max(0.0, float(self._hover)))
        return QPointF(
            self._hover_from.x() + (self._hover_to.x() - self._hover_from.x()) * progress,
            self._hover_from.y() + (self._hover_to.y() - self._hover_from.y()) * progress,
        )

    def _pointer_to(self, pos: QPointF) -> None:
        """Ustawia kropelke pod wskaznikiem (takze z filtru zdarzen przyciskow)."""
        if self._hover_timer.isActive():
            self._hover_timer.stop()
        target = QPointF(pos)
        if self._hover >= 0.99 and (target - self._hover_to).manhattanLength() < 1.0:
            return
        self._hover_from = QPointF(target) if self._hover <= 0.02 else self._drop_center()
        self._hover_to = QPointF(target)
        self._hover_animation.stop()
        self._hover_animation.setStartValue(float(self._hover))
        self._hover_animation.setEndValue(1.0)
        self._hover_animation.start()

    def _schedule_hover_exit(self) -> None:
        if self._hover > 0.02:
            self._hover_timer.start()

    def _fade_hover_out(self) -> None:
        self._hover_timer.stop()
        if self._hover <= 0.02:
            self._reset_hover()
            self.update()
            return
        center = self._drop_center()
        self._hover_from = QPointF(center)
        self._hover_to = QPointF(center)
        self._hover_animation.stop()
        self._hover_animation.setStartValue(float(self._hover))
        self._hover_animation.setEndValue(0.0)
        self._hover_animation.start()

    def _reset_hover(self) -> None:
        self._hover_timer.stop()
        self._hover_animation.stop()
        self._hover = 0.0
        self._hover_from = QPointF()
        self._hover_to = QPointF()

    # ------------------------------------------------------- wlasciwosci animacji
    def _get_blob(self) -> float:
        return float(self._blob)

    def _set_blob(self, value: float) -> None:
        self._blob = float(value)
        self.update()

    blob = pyqtProperty(float, fget=_get_blob, fset=_set_blob)

    def _get_stretch(self) -> float:
        return float(self._stretch)

    def _set_stretch(self, value: float) -> None:
        self._stretch = float(value)
        self.update()

    #: Postep animacji substancji (0..1); ksztalt bierze sie z `_stretch_profile`.
    stretch = pyqtProperty(float, fget=_get_stretch, fset=_set_stretch)

    def _get_hover(self) -> float:
        return float(self._hover)

    def _set_hover(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    #: Obecnosc kropelki pod kursorem (0..1).
    hover = pyqtProperty(float, fget=_get_hover, fset=_set_hover)

    def _stretch_profile(self) -> float:
        """0..1: sila nitki; szczyt w polowie ruchu, zero na obu koncach."""
        value = min(1.0, max(0.0, float(self._stretch)))
        return math.sin(math.pi * value)

    # --------------------------------------------------------------- zdarzenia
    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._snap_to_current()

    def showEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().showEvent(event)
        self._snap_to_current()
        self._ensure_breath()

    def hideEvent(self, event) -> None:  # noqa: N802 (API Qt)
        # Po ukryciu nie moze zostac zaden zywy timer ani animacja.
        self._breath_timer.stop()
        self._breath = 0.0
        self._stop_motion()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self._breath_timer.stop()
        self._breath = 0.0
        self._stop_motion()
        super().closeEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().enterEvent(event)
        position = getattr(event, "position", None)
        if position is not None:
            self._pointer_to(QPointF(position()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().mouseMoveEvent(event)
        self._pointer_to(QPointF(event.position()))

    def leaveEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().leaveEvent(event)
        self._schedule_hover_exit()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (API Qt)
        # Pozycje to dzieci paska, wiec to one dostaja ruch myszy - bez filtru
        # kropelka nie pojawialaby sie nad przyciskami.
        if obj in self._buttons.values():
            kind = event.type()
            if kind in (QEvent.Type.MouseMove, QEvent.Type.Enter):
                position = getattr(event, "position", None)
                if position is not None:
                    self._pointer_to(QPointF(obj.mapTo(self, position().toPoint())))
            elif kind == QEvent.Type.Leave:
                self._schedule_hover_exit()
        return super().eventFilter(obj, event)

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

    def _liquid_path(self, top: float, bottom: float, right: float) -> QPainterPath:
        """Pigulka z wklęsłymi łukami przy scianie paska (jedna ciagla sciezka).

        Ksztalt idzie tak jak w rysunku "kropli": od sciany po lewej, wklęsly
        luk w gore, zaokraglony guzik, wklęsly luk w dol i powrot po scianie.
        Wklęsle luki (``quadTo`` z narożnikiem jako punktem kontrolnym) sprawiaja,
        ze guzik i sciana zlewaja sie w jedna substancje - to natywny odpowiednik
        tego, co w CSS robi ``filter: url(#gooey-filter)``.
        """
        left = float(self.WALL_X)
        height = max(1.0, bottom - top)
        fillet = min(self.FILLET_R, height * 0.45)
        corner = min(14.0, height / 2.0)
        path = QPainterPath()
        path.moveTo(left, top - fillet)
        path.quadTo(left, top, left + fillet, top)
        path.lineTo(right - corner, top)
        path.quadTo(right, top, right, top + corner)
        path.lineTo(right, bottom - corner)
        path.quadTo(right, bottom, right - corner, bottom)
        path.lineTo(left + fillet, bottom)
        path.quadTo(left, bottom, left, bottom + fillet)
        path.closeSubpath()
        return path

    def _residue_path(self, top: float, bottom: float) -> QPainterPath:
        """Smuga na scianie: substancja zostawiona miedzy startem a pigulka."""
        profile = self._stretch_profile()
        strand = max(2.0, self.FILLET_R * 0.85 * profile)
        low = min(self._start, self._blob)
        high = max(self._start, self._blob)
        residue = QPainterPath()
        residue.addRoundedRect(
            QRectF(self.WALL_X, low, strand, max(1.0, high - low)), strand / 2.0, strand / 2.0
        )
        radius = max(3.0, self.FILLET_R * 0.75 * profile)
        residue.addEllipse(QPointF(self.WALL_X + radius, self._start), radius, radius)
        return residue

    def _substance_path(self, right: float, height: float) -> QPainterPath:
        """Ksztalt substancji: pigulka + smuga, rozciagniete w kierunku ruchu.

        Dwie krawedzie (przod i tyl) jada inaczej: przod wyprzedza srodek, tyl
        zostaje z tylu - ksztalt rozciaga sie jak kropla miodu, a talia zweza sie
        o ``WAIST``, zeby objetosc wygladala na zachowana.
        """
        profile = self._stretch_profile()
        direction = 1.0 if self._target >= self._start else -1.0
        lead = abs(self._target - self._start) * self.STRETCH_LEAD * profile
        front = self._blob + direction * (height / 2.0 + lead)
        back = self._blob - direction * (height / 2.0 + lead * 0.45)
        top = min(front, back)
        bottom = max(front, back)
        waist = 1.0 - self.WAIST * profile
        path = self._liquid_path(top, bottom, self.WALL_X + (right - self.WALL_X) * waist)
        if profile <= 0.01:
            return path
        return path.united(self._residue_path(top, bottom))

    # ------------------------------------------------------- filtr goo (rozmycie)
    def _goo_mask(self, path: QPainterPath) -> QImage:
        """Rozmyta maska ksztaltu - miekkie tlo dla pojedynczej kropelki.

        Odpowiednik pierwszego stopnia filtra goo (``feGaussianBlur``): masa
        rysowana jest na przezroczystym obrazie i rozmywana przeskalowaniem w
        dol i w gore (w C++, bez petli po pikselach). Sam ksztalt substancji
        (pigulka przy scianie) rysujemy wektorowo z wklęsłymi łukami, wiec progu
        alfy nie potrzebuje - tutaj miekkosc jest pozadana.
        """
        width = max(1, self.width())
        height = max(1, self.height())
        source = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(Qt.GlobalColor.transparent)
        painter = QPainter(source)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("accent"))
        painter.drawPath(path)
        painter.end()
        scale = max(2, int(self.GOO_SCALE))
        small = source.scaled(
            max(1, width // scale),
            max(1, height // scale),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return small.scaled(
            width,
            height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def _fill_masked(self, mask: QImage, brush: QBrush) -> QImage:
        """Wypelnia maske pedzlem (gradient/kolor), zachowujac jej alfe."""
        layer = QImage(mask.size(), QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        painter = QPainter(layer)
        painter.fillRect(layer.rect(), brush)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask)
        painter.end()
        return layer

    def _pill_gradient(self, top: float, bottom: float) -> QLinearGradient:
        """Szklisty gradient pigulki (dwa odcienie szarosci), bez ditheringu."""
        gradient = QLinearGradient(QPointF(0.0, top), QPointF(0.0, bottom))
        gradient.setColorAt(0.0, qcolor("surface3"))
        gradient.setColorAt(1.0, qcolor("surface2"))
        return gradient

    def _paint_wall(self, painter: QPainter) -> None:
        """Cienka listwa przy lewej krawedzi - sciana, po ktorej splywa substancja."""
        margins = self._layout.contentsMargins()
        top = float(margins.top())
        height = max(1.0, float(self.height() - margins.top() - margins.bottom()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("line"))
        painter.drawRoundedRect(QRectF(self.WALL_X, top, self.WALL_W, height), 1.0, 1.0)

    def _paint_rail_texture(self, painter: QPainter) -> None:
        """Materiał paska: ziarno + dwa rastry, a przy scianie gestszy rastr.

        Pasek lezy na tle okna, wiec dodatkowe warstwy robia z niego odrebny
        "panel": ziarno, drobny rastr i gruby rastr o innej siatce, a przy
        scianie rastr gestnieje plynnie (maska), bez widocznego progu.
        """
        rect = QRectF(self.rect())
        if rect.width() < 8.0 or rect.height() < 8.0:
            return
        texture.paint_stack(painter, rect, ("medium", "fine", "sand", "clump"), opacity=0.55)
        texture.paint_screen_stack(
            painter, rect, ("veil", "coarse"), opacity=0.8, alpha=texture.SCREEN_ALPHA_BACKDROP
        )
        brush_width = min(24.0, max(10.0, rect.width() * 0.24))
        texture.paint_halftone_fade(
            painter,
            QRectF(rect.left(), rect.top(), brush_width, rect.height()),
            "soft",
            fade="right",
            span=1.0,
            opacity=0.7,
            level=12,
        )
        texture.paint_grain_fade(
            painter,
            QRectF(rect.left(), rect.top(), brush_width, rect.height()),
            "fine",
            fade="right",
            span=1.0,
            opacity=0.5,
        )

    def _paint_hover(self, painter: QPainter, height: float) -> None:
        """Przygaszona plama pod kursorem - rysowana POD pigulka, wiec tekst czyta sie dalej."""
        progress = min(1.0, max(0.0, float(self._hover)))
        if progress <= 0.02:
            return
        center = self._drop_center()
        radius = max(5.0, height * 0.34) * (0.72 + 0.28 * progress)
        drop = QPainterPath()
        drop.addEllipse(center, radius, radius)
        mask = self._goo_mask(drop)
        fill = qcolor("surface3", int(150 * progress))
        painter.drawImage(0, 0, self._fill_masked(mask, QBrush(fill)))

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        button = self._buttons.get(self._current)
        if button is None:
            return
        geometry = button.geometry()
        base_height = float(max(28, geometry.height()))
        # Oddech rozciaga kapsule o maksymalnie +-1.5 px wokol jej srodka.
        height = base_height + self._breath * self.BREATH_AMP
        right = float(geometry.left() + max(24, geometry.width()))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_rail_texture(painter)
        self._paint_wall(painter)
        self._paint_hover(painter, base_height)
        path = self._substance_path(right, height)
        bounds = path.boundingRect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._pill_gradient(bounds.top(), bounds.bottom())))
        painter.drawPath(path)
        # Pigulka jest z tego samego materialu co pasek - drobne ziarno i rastr
        # pod jej obramowaniem, wiec nie wyglada jak plastikowy prostokat.
        texture.paint_grain(painter, bounds, "sand", phase=3, opacity=0.75, clip=path)
        texture.paint_halftone(painter, bounds, "hair", phase=2, opacity=0.8, clip=path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen("line_strong"))
        painter.drawPath(path)
        # Znacznik aktywnosci na scianie paska (przy samej pozycji, nie przy luku).
        marker = QRectF(
            self.WALL_X,
            self._blob - height / 2.0 + 8.0,
            self.WALL_W,
            max(6.0, height - 16.0),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("accent"))
        painter.drawRoundedRect(marker, 1.0, 1.0)
        painter.end()
