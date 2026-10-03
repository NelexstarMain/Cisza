"""Ikona w zasobniku systemowym i menu tray (monochromatyczne, bez plikow PNG)."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .widgets.paint import fill_dither, pen, qcolor


def monochrome_icon(size: int = 32, running: bool = False, paused: bool = False) -> QIcon:
    """Ikona rysowana w kodzie: pierscien + wskaznik stanu (dithering/kreski)."""
    pixmap = QPixmap(int(size), int(size))
    pixmap.fill(qcolor("bg", 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    inset = max(1.5, size * 0.09)
    circle = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(pen("text", max(1.5, size * 0.075)))
    painter.drawEllipse(circle)
    inner = circle.adjusted(size * 0.18, size * 0.18, -size * 0.18, -size * 0.18)
    if running:
        path = QPainterPath()
        path.addEllipse(inner)
        fill_dither(painter, path, "text", 0.45 if not paused else 0.2, cell=3)
        painter.setPen(pen("accent", max(1.0, size * 0.05)))
        painter.drawLine(
            int(inner.center().x()),
            int(inner.top() + inner.height() * 0.28),
            int(inner.center().x()),
            int(inner.center().y()),
        )
    else:
        painter.setPen(pen("text_mute", max(1.0, size * 0.06)))
        painter.drawLine(
            int(inner.left()),
            int(inner.center().y()),
            int(inner.right()),
            int(inner.center().y()),
        )
    painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    """Menu w zasobniku: Pokaż / Start / Zakończ / Statystyki / Zamknij."""

    show_requested = pyqtSignal()
    start_requested = pyqtSignal()
    end_requested = pyqtSignal()
    stats_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, title: str = "Cisza", *args, **kwargs) -> None:
        super().__init__(monochrome_icon(32), parent)
        self._title = str(title)
        self._running = False
        self._paused = False
        self.setToolTip(self._title)

        self._menu = QMenu()
        self._action_show = self._menu.addAction("Pokaż Ciszę")
        self._action_start = self._menu.addAction("Start sesji")
        self._action_end = self._menu.addAction("Zakończ sesję")
        self._action_stats = self._menu.addAction("Statystyki")
        self._menu.addSeparator()
        self._action_quit = self._menu.addAction("Zamknij")
        self._action_show.triggered.connect(lambda _checked=False: self.show_requested.emit())
        self._action_start.triggered.connect(lambda _checked=False: self.start_requested.emit())
        self._action_end.triggered.connect(lambda _checked=False: self.end_requested.emit())
        self._action_stats.triggered.connect(lambda _checked=False: self.stats_requested.emit())
        self._action_quit.triggered.connect(lambda _checked=False: self.quit_requested.emit())
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)
        self.set_running(False)

    # ------------------------------------------------------------------ API
    def actions(self) -> dict[str, object]:
        return {
            "show": self._action_show,
            "start": self._action_start,
            "end": self._action_end,
            "stats": self._action_stats,
            "quit": self._action_quit,
        }

    def set_running(self, running: bool, paused: bool = False) -> None:
        self._running = bool(running)
        self._paused = bool(paused)
        self._action_start.setEnabled(not self._running)
        self._action_end.setEnabled(self._running)
        state = "PRZERWA" if self._paused else ("SESJA TRWA" if self._running else "GOTOWA")
        self.setToolTip(f"{self._title} — {state}")
        self.setIcon(monochrome_icon(32, self._running, self._paused))

    def running(self) -> bool:
        return self._running

    def notify(self, title: str, message: str, ms: int = 5000) -> None:
        """Powiadomienie systemowe — tylko gdy platforma je wspiera."""
        if not QSystemTrayIcon.supportsMessages():
            return
        self.showMessage(str(title), str(message), self.icon(), int(ms))

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()

