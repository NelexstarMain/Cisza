"""Czarne nakladki na ekranach (PyQt6) - jedyny modul powloki z Qt.

Kontrakt: docs/INTERFACES.md sekcja 11.

Zasady:
- OverlayManager tworzy okna tylko w watku GUI (wymaga istniejacego
  QGuiApplication); brak aplikacji to ok=False + errors, nie wyjatek.
- Kolory wylacznie z tokenow `focuslock/ui/theme.py` (monochromatycznosc).
- dry_run=True nie tworzy zadnych okien.
"""
from __future__ import annotations

from typing import Callable

from ..ui import theme

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QGuiApplication, QPalette
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    PYQT_OK = True
    PYQT_ERROR = ""
except Exception as _exc:  # pragma: no cover - brak PyQt6 w srodowisku
    PYQT_OK = False
    PYQT_ERROR = str(_exc)
    QApplication = QColor = QGuiApplication = QPalette = None  # type: ignore[assignment]
    Qt = None  # type: ignore[assignment]
    QLabel = QVBoxLayout = QWidget = None  # type: ignore[assignment]


DEFAULT_MESSAGE = "SESJA TRWA"


def _report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


if PYQT_OK:

    class _OverlayWindow(QWidget):
        """Czarne, bezramkowe okno na calym ekranie (topmost, bez fokusa)."""

        def __init__(self, screen, message: str = DEFAULT_MESSAGE) -> None:
            super().__init__(
                None,
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowDoesNotAcceptFocus,
            )
            self.setObjectName("ciszaOverlay")
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAutoFillBackground(True)
            palette = self.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor(theme.COLORS["overlay"]))
            self.setPalette(palette)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self._label = QLabel(message, self)
            self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._label.setStyleSheet(
                "background: transparent; color: %s; %s letter-spacing: 8px;"
                % (theme.COLORS["text"], theme.TYPO.ui(theme.FONT_SIZES["xl"], 600))
            )
            layout.addWidget(self._label)
            self.apply_screen(screen)

        def apply_screen(self, screen) -> None:
            try:
                self.setGeometry(screen.geometry())
            except Exception:
                pass

        def set_message(self, text: str) -> None:
            try:
                self._label.setText(text)
            except Exception:
                pass

        def reassert(self) -> None:
            try:
                self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                if not self.isVisible():
                    self.show()
                self.raise_()
            except Exception:
                pass

else:  # pragma: no cover - placeholder, zeby modul importowal sie bez Qt
    class _OverlayWindow:  # type: ignore[no-redef]
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("PyQt6 niedostepne: " + PYQT_ERROR)


def _screen_of(window):
    """Ekran, na ktorym lezy okno `keep_window` (albo None)."""
    if window is None or not PYQT_OK:
        return None
    try:
        handle = window.windowHandle()
        if handle is not None and handle.screen() is not None:
            return handle.screen()
    except Exception:
        pass
    try:
        screen = window.screen()
        if screen is not None:
            return screen
    except Exception:
        pass
    try:
        geometry = window.frameGeometry()
        return QGuiApplication.screenAt(geometry.center())
    except Exception:
        return None


def _screens():
    if not PYQT_OK:
        return []
    try:
        if QGuiApplication.instance() is None:
            return []
        return list(QGuiApplication.screens())
    except Exception:
        return []


class OverlayManager:
    """Zarzadza zestawem czarnych nakladek - po jednej na kazdy ekran."""

    def __init__(
        self,
        *,
        dry_run: bool = False,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._dry_run = bool(dry_run)
        self.on_event = on_event
        self._overlays: list[tuple[object, object]] = []
        self._locked = False
        self._message = DEFAULT_MESSAGE
        self._keep_window = None

    # -------------------------------------------------------------- wewnetrzne
    def _emit(self, event: str, payload: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, payload)
        except Exception:
            pass

    # -------------------------------------------------------------------- API
    def lock(self, keep_window=None) -> dict:
        """Czarne okna na kazdym ekranie poza ekranem okna `keep_window`."""
        report = _report()
        self._keep_window = keep_window

        if self._dry_run:
            keep_screen = _screen_of(keep_window)
            count = sum(1 for screen in _screens() if screen is not keep_screen)
            report["applied"].append("overlay.lock:screens=%d" % count)
            report["warnings"].append("dry_run: okna nie utworzone")
            return report

        if not PYQT_OK:
            report["ok"] = False
            report["errors"].append("PyQt6 niedostepne: " + PYQT_ERROR)
            return report
        if QGuiApplication.instance() is None or QApplication.instance() is None:
            report["ok"] = False
            report["errors"].append(
                "brak QApplication (QtWidgets) - OverlayManager dziala tylko w watku GUI"
            )
            return report
        if self._overlays:
            report["applied"].append("overlay.lock:already")
            return report

        keep_screen = _screen_of(keep_window)
        created = 0
        for screen in _screens():
            if keep_screen is not None and screen is keep_screen:
                continue
            try:
                window = _OverlayWindow(screen, self._message)
                window.show()
                self._overlays.append((window, screen))
                created += 1
            except Exception as exc:
                report["errors"].append("overlay(%s): %s" % (screen.name(), exc))

        if created == 0:
            if not report["errors"]:
                report["warnings"].append(
                    "brak ekranow do przykrycia (keep_window na jedynym ekranie?)"
                )
        report["applied"].append("overlay.lock:screens=%d" % created)
        self._locked = created > 0
        report["ok"] = not report["errors"]
        self._emit("overlay_locked", {"windows": created})
        return report

    def unlock(self) -> dict:
        report = _report()
        count = len(self._overlays)
        if self._dry_run:
            report["applied"].append("overlay.unlock:screens=%d" % count)
            report["warnings"].append("dry_run: okna nietkniete")
            return report

        for window, _screen in self._overlays:
            try:
                window.hide()
                window.close()
                window.deleteLater()
            except Exception as exc:
                report["errors"].append("overlay.close: %s" % exc)
        self._overlays = []
        self._locked = False
        report["applied"].append("overlay.unlock:screens=%d" % count)
        self._emit("overlay_unlocked", {"windows": count})
        report["ok"] = not report["errors"]
        return report

    def refresh(self) -> None:
        """Reasekuracja topmost i geometrii (wolane z QTimer co 1 s)."""
        if not PYQT_OK:
            return
        for window, screen in list(self._overlays):
            try:
                window.apply_screen(screen)
                window.reassert()
            except Exception:
                continue

    def locked(self) -> bool:
        return bool(self._locked)

    def message(self, text: str) -> None:
        self._message = str(text)
        for window, _screen in self._overlays:
            try:
                window.set_message(self._message)
            except Exception:
                continue

    @property
    def count(self) -> int:
        return len(self._overlays)
