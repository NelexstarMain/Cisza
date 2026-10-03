"""Widgety katalogow: kafel aplikacji, chipy wyboru, wiersz strony, lista wyboru."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..theme import FONT_SIZES, SIZES, TYPO
from . import labels, texture
from .buttons import GhostButton
from .paint import pen, qcolor, ui_font


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_layout(child)


class AppTile(QWidget):
    """Kafel aplikacji: ikona + nazwa (2 linie) + proces; klik przelacza zaznaczenie.

    Kafel ma stala wielkosc (rowna siatka), a nazwa jest zawijana do dwoch linii
    z elidowaniem i pelnym tekstem w tooltipie. Zaznaczenie jest subtelne: tlo
    `surface3`, cienka jasna obwodka i maly znacznik w rogu - bez jaskrawych ramek.
    Ikona dociaga sie leniwie (`ensure_icon`), wiec budowa kafli nie zamraza watku
    GUI na ekstrakcji ikon z powloki Windows.
    """

    clicked = pyqtSignal(str)

    #: Spojne z reszta UI zaokraglenie kafla (RADIUS["md"]).
    RADIUS = 8.0
    NAME_LINES = 2
    NAME_LINE_HEIGHT = 15.0
    PROCESS_HEIGHT = 14.0
    PAD_TOP = 12.0
    PAD_BOTTOM = 10.0
    ICON_GAP = 8.0
    TEXT_PAD = 8.0
    #: Znacznik zaznaczenia w rogu (znak tekstowy, bez emoji i kolorow).
    MARKER_GLYPH = "▣"

    def __init__(
        self,
        key: str,
        name: str = "",
        process: str = "",
        icon: QPixmap | None = None,
        icon_size: int = 40,
        width: int = 172,
        parent: QWidget | None = None,
        *,
        icon_path: str = "",
        gray: bool = True,
    ) -> None:
        super().__init__(parent)
        self._key = str(key)
        self._name = str(name or key)
        self._process = str(process or "")
        self._icon_path = str(icon_path or "")
        self._icon_size = max(16, int(icon_size))
        self._gray = bool(gray)
        self._icon = icon if isinstance(icon, QPixmap) else QPixmap()
        self._icon_loaded = (not self._icon.isNull()) or not self._icon_path
        self._selected = False
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedSize(int(width), self._height_for(self._icon_size))
        self.setToolTip(f"{self._name}\n{self._process}" if self._process else self._name)

    # ------------------------------------------------------------------ API
    def key(self) -> str:
        return self._key

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        selected = bool(selected)
        if selected != self._selected:
            self._selected = selected
            self.update()

    def set_icon(self, icon: QPixmap) -> None:
        if isinstance(icon, QPixmap) and not icon.isNull():
            self._icon = icon
            self._icon_loaded = True
            self.update()

    def set_icon_path(self, icon_path: str, gray: bool | None = None) -> None:
        """Podmienia zrodlo ikony; kolejna aktualizacja dociaga ja leniwie."""
        path = str(icon_path or "")
        if gray is not None:
            self._gray = bool(gray)
        if path == self._icon_path and self._icon_loaded:
            return
        self._icon_path = path
        self._icon = QPixmap()
        self._icon_loaded = not path
        self.update()

    def ensure_icon(self) -> None:
        """Dociaga ikone co najwyzej raz; nigdy nie wolno wolac tego w paintEvent."""
        if self._icon_loaded:
            return
        self._icon_loaded = True
        if not self._icon_path:
            return
        try:  # import leniwy: widgets.catalog nie zalezy od icons na poziomie modulu
            from ..icons import ICONS

            pixmap = ICONS.pixmap(self._icon_path, self._icon_size, self._gray)
        except Exception:  # pragma: no cover - zalezy od platformy
            pixmap = QPixmap()
        if not pixmap.isNull():
            self._icon = pixmap
            self.update()

    def set_hover(self, hover: bool) -> None:
        """Stan hover (takze programowo - uzywany przez testy i podglady)."""
        hover = bool(hover)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def is_hover(self) -> bool:
        return self._hover

    @classmethod
    def _height_for(cls, icon_size: int) -> int:
        """Stala wysokosc: ikona + dwie linie nazwy + podpis procesu."""
        return int(
            cls.PAD_TOP
            + int(icon_size)
            + cls.ICON_GAP
            + cls.NAME_LINES * cls.NAME_LINE_HEIGHT
            + 2.0
            + cls.PROCESS_HEIGHT
            + cls.PAD_BOTTOM
        )

    # -------------------------------------------------------------- zdarzenia
    def showEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().showEvent(event)
        self.ensure_icon()

    def enterEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self.set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self.set_hover(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit(self._key)
            event.accept()
            return
        super().keyPressEvent(event)

    # -------------------------------------------------------------- malowanie
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self.RADIUS

        if self._selected or self._hover:
            background = "surface3"
        else:
            background = "surface2"
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(background))
        painter.drawRoundedRect(rect, radius, radius)
        # Kafel jest materialem: ziarno (z grudkami) pod ramka i nazwa - bez
        # Bayera na calym polu, bo ten zaslanial ikone i tekst.
        texture.paint_surface(
            painter,
            rect.adjusted(1.0, 1.0, -1.0, -1.0),
            radius=max(0.0, radius - 1.0),
            names=("fine", "sand", "clump"),
            opacity=0.65,
        )

        if self._selected:
            # Zaznaczenie: cienka ramka + maly znacznik w rogu. Wzór Bayera na
            # całym kaflu zasnuwał nazwę i ikonę, dlatego zostaje samo jaśniejsze
            # tło (`surface3`) i akcent na krawędzi.
            border = "text" if self._hover else "text_dim"
        elif self._hover:
            border = "line_strong"
        else:
            border = "line"
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen(border, 1.0))
        painter.drawRoundedRect(rect, radius, radius)

        self._paint_icon(painter)
        self._paint_text(painter)
        if self._selected:
            self._paint_marker(painter)
        painter.end()

    def _paint_icon(self, painter: QPainter) -> None:
        if self._icon.isNull():
            return
        scaled = self._icon.scaled(
            self._icon_size,
            self._icon_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) / 2.0
        y = self.PAD_TOP + (self._icon_size - scaled.height()) / 2.0
        painter.drawPixmap(int(round(x)), int(round(y)), scaled)

    def _paint_text(self, painter: QPainter) -> None:
        available = max(20.0, self.width() - 2.0 * self.TEXT_PAD)
        painter.setFont(ui_font(FONT_SIZES["xs"]))
        metrics = painter.fontMetrics()
        top = self.PAD_TOP + self._icon_size + self.ICON_GAP
        first, second = self._two_lines(metrics, self._name, available)
        painter.setPen(qcolor("text" if self._selected else "text_dim"))
        for index, line in enumerate((first, second)):
            if not line:
                continue
            line_rect = QRectF(
                self.TEXT_PAD,
                top + index * self.NAME_LINE_HEIGHT,
                available,
                self.NAME_LINE_HEIGHT,
            )
            painter.drawText(
                line_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                line,
            )
        if not self._process:
            return
        painter.setFont(ui_font(max(9, FONT_SIZES["xs"] - 1)))
        painter.setPen(qcolor("text_mute"))
        process_rect = QRectF(
            self.TEXT_PAD,
            top + self.NAME_LINES * self.NAME_LINE_HEIGHT + 2.0,
            available,
            self.PROCESS_HEIGHT,
        )
        painter.drawText(
            process_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            painter.fontMetrics().elidedText(self._process, Qt.TextElideMode.ElideRight, int(available)),
        )

    def _paint_marker(self, painter: QPainter) -> None:
        """Maly znacznik zaznaczenia w prawym gornym rogu (znak tekstowy)."""
        size = 16.0
        margin = 6.0
        badge = QRectF(self.width() - margin - size, margin, size, size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("surface2"))
        painter.drawRoundedRect(badge, 4.0, 4.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen("text_dim", 1.0))
        painter.drawRoundedRect(badge, 4.0, 4.0)
        painter.setFont(ui_font(max(9, FONT_SIZES["xs"] - 1)))
        painter.setPen(qcolor("text"))
        painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), self.MARKER_GLYPH)

    @staticmethod
    def _two_lines(metrics, text: str, width: float) -> tuple[str, str]:
        """Dzieli nazwe na dwie linie; druga (i kazda dluzsza) jest elidowana."""
        words = str(text or "").split()
        if not words:
            return "", ""
        limit = int(max(20.0, width))
        first = ""
        index = 0
        while index < len(words):
            candidate = f"{first} {words[index]}".strip()
            if first and metrics.horizontalAdvance(candidate) > limit:
                break
            first = candidate
            index += 1
        if metrics.horizontalAdvance(first) > limit:
            first = metrics.elidedText(first, Qt.TextElideMode.ElideRight, limit)
        if index >= len(words):
            return first, ""
        tail = " ".join(words[index:])
        return first, metrics.elidedText(tail, Qt.TextElideMode.ElideRight, limit)


def _as_items(value) -> list:
    """Bezpieczne rzutowanie danych z kontrolera na liste wpisow."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [item for item in value]
    return []


MAX_CHIP_WIDTH = 240
MAX_HOST_WIDTH = 220


class ChipRow(QWidget):
    """Siatka chipow wyboru z mozliwoscia usuniecia (`label  ×`).

    Dlugie etykiety sa elidowane (pelny tekst w tooltipie), dzieki czemu chipy
    nie rozpychaja karty i nie łamią siatki.
    """

    removed = pyqtSignal(str)

    def __init__(self, columns: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = max(1, int(columns))
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def set_items(self, items: list[tuple[str, str]]) -> None:
        _clear_layout(self._grid)
        for index, (key, label) in enumerate(items):
            text = str(label or key)
            chip = GhostButton(f"{text}  ×")
            chip.setToolTip(f"Usuń z wyboru: {text}")
            chip.setMaximumWidth(MAX_CHIP_WIDTH)
            chip.setMinimumHeight(SIZES["chip"])
            chip.setText(labels.elide_text(chip, f"{text}  ×", MAX_CHIP_WIDTH - 28, role="button"))
            chip.clicked.connect(lambda _checked=False, k=key: self.removed.emit(k))
            self._grid.addWidget(chip, index // self._columns, index % self._columns)
        self.setVisible(bool(items))

    def count(self) -> int:
        return self._grid.count()


class SiteRow(QWidget):
    """Wiersz strony: checkbox z nazwa, notka i host (wyciszony).

    Nazwa i host eliduja sie z pelnym tekstem w tooltipie - dlugie domeny nie
    rozciagaja karty na boki.
    """

    toggled = pyqtSignal(str, bool)

    def __init__(
        self,
        host: str,
        name: str = "",
        note: str = "",
        checked: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = str(host)
        self._name = str(name or host)
        self._check = QCheckBox(self._name)
        self._check.setToolTip(self._name)
        self._check.setChecked(bool(checked))
        self._check.toggled.connect(lambda value: self.toggled.emit(self._host, bool(value)))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)
        row.addWidget(self._check, 1)
        if note:
            row.addWidget(labels.caption(str(note)))
        row.addStretch(1)
        host_label = labels.elided(self._host)
        host_label.setMaximumWidth(MAX_HOST_WIDTH)
        host_label.setMinimumWidth(120)
        host_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(host_label)

    def host(self) -> str:
        return self._host

    def name(self) -> str:
        return self._name

    def is_checked(self) -> bool:
        return self._check.isChecked()

    def set_checked(self, value: bool) -> None:
        blocked = self._check.blockSignals(True)
        self._check.setChecked(bool(value))
        self._check.blockSignals(blocked)


class PickList(QWidget):
    """Lista wyboru z polem dodawania (uzywana m.in. w ekranie wstepnym)."""

    def __init__(self, placeholder: str, empty_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setWordWrap(False)
        self.field = QLineEdit()
        self.field.setPlaceholderText(placeholder)
        self.field.setMinimumHeight(SIZES["input"])
        self.field.returnPressed.connect(self._add_from_field)
        add = GhostButton("DODAJ")
        add.clicked.connect(self._add_from_field)
        remove = GhostButton("USUŃ ZAZNACZONE")
        remove.clicked.connect(self._remove_selected)
        self.empty_hint = labels.hint(empty_text)
        self.empty_hint.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.empty_hint)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.field, 1)
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        self._sync_empty()

    # ------------------------------------------------------------------ API
    def add(self, text: str, checked: bool = True) -> None:
        text = str(text).strip()
        if not text:
            return
        for index in range(self.list.count()):
            if self.list.item(index).text() == text:
                return
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.list.addItem(item)
        self._sync_empty()

    def set_items(self, items) -> None:
        self.list.clear()
        for item in _as_items(items):
            if isinstance(item, dict):
                self.add(str(item.get("value") or item.get("label") or ""), bool(item.get("checked", True)))
            else:
                self.add(str(item), True)
        self._sync_empty()

    def count(self) -> int:
        return self.list.count()

    def selected(self) -> list[str]:
        out: list[str] = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.text())
        return out

    def all_items(self) -> list[str]:
        return [self.list.item(index).text() for index in range(self.list.count())]

    # ----------------------------------------------------------------- czastki
    def _sync_empty(self) -> None:
        empty = self.list.count() == 0
        has_hint = bool(self.empty_hint.text())
        self.empty_hint.setVisible(empty and has_hint)
        self.list.setVisible(not (empty and has_hint))

    def _add_from_field(self) -> None:
        self.add(self.field.text())
        self.field.clear()

    def _remove_selected(self) -> None:
        for item in list(self.list.selectedItems()):
            self.list.takeItem(self.list.row(item))
        self._sync_empty()


def elided(label: QLabel, text: str, width: int) -> str:
    """Skrot: tekst przycięty do szerokosci (dla dlugich nazw aplikacji)."""
    return label.fontMetrics().elidedText(str(text), Qt.TextElideMode.ElideRight, int(width))
