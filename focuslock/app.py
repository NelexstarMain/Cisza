"""Bootstrap GUI: okno glowne, ekrany, tray, petla czasu.

Ekrany z focuslock/ui/screens sa opcjonalne - jesli ich jeszcze nie ma, aplikacja
dziala z ekranem zastepczym (dzieki temu moduly moga powstawac rownolegle).
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QPixmap, QPainter, QColor, QShortcut
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from . import paths  # noqa: F401  (uzywane przez tryb awaryjny/inne moduly)
from . import recovery
from .config import Settings
from .controller import Controller
from .helperclient import HelperBridge
from .store import Store
from .ui import sound, theme
from .ui.widgets.paint import BAYER4, qcolor

APP_ID = "cisza-gui"

# Akcje, ktore moga potrwac (skan instalowanych aplikacji) - uruchamiamy je w tle,
# zeby okno nie zamarzalo na kilka sekund.
HEAVY_ACTIONS = {"refresh_apps", "installed_apps", "catalog_apps_force"}

# Akcje nawigacyjne obsluguje samo okno (nie kontroler).
NAVIGATION_ACTIONS = {
    "open_home": "home",
    "open_composer": "composer",
    "open_running": "running",
    "open_break": "break",
    "open_free": "free",
    "open_bank": "bank",
    "open_stats": "stats",
    "open_settings": "settings",
    "open_onboarding": "onboarding",
    "open_summary": "summary",
    "open_journal": "journal",
}


class _ActionSignals(QObject):
    finished = pyqtSignal(str, dict)


class _ActionWorker(QRunnable):
    """Uruchamia akcje kontrolera poza watkiem GUI i zwraca wynik sygnalem."""

    def __init__(self, action: str, function, signals: _ActionSignals) -> None:
        super().__init__()
        self.action = action
        self.function = function
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401 - API Qt
        try:
            result = self.function()
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        try:
            self.signals.finished.emit(self.action, result)
        except RuntimeError:
            pass

SCREEN_MODULES = {
    "home": ("HomeScreen", ("focuslock.ui.screens.home",)),
    "composer": ("ComposerScreen", ("focuslock.ui.screens.composer",)),
    "running": ("RunningScreen", ("focuslock.ui.screens.running",)),
    "break": ("BreakScreen", ("focuslock.ui.screens.break_screen", "focuslock.ui.screens.break_", "focuslock.ui.screens.breakscreen")),
    "free": ("FreeScreen", ("focuslock.ui.screens.free",)),
    "bank": ("BankScreen", ("focuslock.ui.screens.bank",)),
    "stats": ("StatsScreen", ("focuslock.ui.screens.stats",)),
    "settings": ("SettingsScreen", ("focuslock.ui.screens.settings",)),
    "onboarding": ("OnboardingScreen", ("focuslock.ui.screens.onboarding",)),
    "summary": ("SummaryScreen", ("focuslock.ui.screens.summary",)),
    "journal": ("JournalScreen", ("focuslock.ui.screens.journal",)),
}


def make_app_icon(size: int = 64) -> QIcon:
    """Monochromatyczna ikona rysowana w kodzie (pierscien + kropka)."""
    pix = QPixmap(size, size)
    pix.fill(QColor(theme.COLORS["bg"]))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = painter.pen()
    pen.setColor(QColor(theme.COLORS["text"]))
    pen.setWidth(max(2, size // 16))
    painter.setPen(pen)
    painter.drawEllipse(size // 8, size // 8, size * 3 // 4, size * 3 // 4)
    painter.setBrush(QColor(theme.COLORS["accent"]))
    painter.drawEllipse(size // 2 - size // 10, size // 2 - size // 10, size // 5, size // 5)
    painter.end()
    return QIcon(pix)


class FallbackScreen(QWidget):
    """Ekran zastepczy, gdy modul UI jeszcze nie istnieje."""

    def __init__(self, name: str, message: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("C I S Z A")
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption = QLabel(message or f"ekran '{name}' w budowie")
        caption.setProperty("role", "caption")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(caption)


class _Backdrop(QWidget):
    """Tlo okna: czern + drobna faktura i prawie przezroczysta siatka.

    Ekrany sa przezroczyste (patrz `theme.qss()`), a karty maluja wlasne tlo,
    wiec faktura i siatka widac tylko w odstepach - to tlo, a nie tapeta pod
    tekstem. Faktura to kafelek 48x48 z wzorem Bayera (te same "piksele", co
    w wykresach), rysowany raz i powielany `drawTiledPixmap`.
    """

    STEP = 32
    TEXTURE = 48

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, qcolor("bg"))
        painter.drawTiledPixmap(rect, _backdrop_texture(self.TEXTURE))
        step = self.STEP
        minor = qcolor("text", 9)
        major = qcolor("text", 16)
        for x in range(0, self.width(), step):
            painter.setPen(major if (x // step) % 4 == 0 else minor)
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.setPen(major if (y // step) % 4 == 0 else minor)
            painter.drawLine(0, y, self.width(), y)
        painter.end()


_BACKDROP_TEXTURES: dict[int, QPixmap] = {}


def _backdrop_texture(size: int = 48, threshold: int = 3, alpha: int = 10) -> QPixmap:
    """Kafelek faktury: rozsypane piksele wg wzoru Bayera 4x4 (bez kolorow)."""
    key = hash((int(size), int(threshold), int(alpha)))
    cached = _BACKDROP_TEXTURES.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(int(size), int(size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(qcolor("text", int(alpha)))
    for y in range(int(size)):
        for x in range(int(size)):
            if BAYER4[y % 4][x % 4] < int(threshold):
                painter.drawPoint(x, y)
    painter.end()
    _BACKDROP_TEXTURES[key] = pixmap
    return pixmap


class MainWindow(QMainWindow):
    event_received = pyqtSignal(str, dict)

    def __init__(self, controller: Controller, settings: Settings, store: Store) -> None:
        super().__init__()
        self.controller = controller
        self.settings = settings
        self.store = store
        self.setWindowTitle("Cisza")
        self.setWindowIcon(make_app_icon())
        # Wieksze czcionki i pasek boczny wymagaja troche miejsca - ponizej tego
        # rozmiaru siatki formularzy zaczynaly sie ucinaly.
        self.setMinimumSize(960, 620)
        self.stack = QStackedWidget()
        self._nav = self._build_nav_rail()
        container = _Backdrop()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if self._nav is not None:
            layout.addWidget(self._nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(container)

        self.screens: dict[str, QWidget] = {}
        self._pool = QThreadPool.globalInstance()
        self._pending_actions: set[str] = set()
        self._ending = False
        self._tray = None
        self._action_signals = _ActionSignals()
        self._action_signals.finished.connect(self._dispatch_action_result)
        self._build_screens()
        self.event_received.connect(self._on_event)
        controller.attach_window(self)
        self._install_exit_shortcut()

    def _build_nav_rail(self):
        """Pasek nawigacji po ekranach (klik = `show_screen`).

        Wcześniej widget `NavRail` istniał, ale nikt go nie tworzył - jego sygnał
        `navigate` był martwym połączeniem.
        """
        try:
            module = importlib.import_module("focuslock.ui.widgets.nav")
            rail = module.NavRail(width=168)
            rail.set_items(
                [
                    ("home", "START"),
                    ("composer", "KREATOR"),
                    ("bank", "BANK"),
                    ("stats", "STATYSTYKI"),
                    ("journal", "DZIENNIK"),
                    ("settings", "USTAWIENIA"),
                ]
            )
            rail.navigate.connect(self.show_screen)
            return rail
        except Exception:  # noqa: BLE001 - brak widgetu nie moze blokowac okna
            return None

    def _install_exit_shortcut(self) -> None:
        """Sekretna kombinacja wyjscia (Ctrl+Alt+Shift+X) - otwiera okno PIN-u.

        Dziala, gdy okno Ciszy ma fokus (podczas sesji jest zawsze na wierzchu).
        Globalny wariant tej kombinacji instaluje helper przez hooki klawiatury.
        """
        try:
            shortcuts = []
            for sequence in ("Ctrl+Alt+Shift+X", "Ctrl+Alt+Shift+Q"):
                shortcut = QShortcut(QKeySequence(sequence), self)
                shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
                shortcut.activated.connect(lambda: self._request_end("user"))
                shortcuts.append(shortcut)
            self._exit_shortcuts = shortcuts
        except Exception:
            self._exit_shortcuts = []

    # -------------------------------------------------------------------- ekrany
    def _build_screens(self) -> None:
        for name, (class_name, modules) in SCREEN_MODULES.items():
            widget: Optional[QWidget] = None
            last_error: Optional[Exception] = None
            for module_name in modules:
                try:
                    module = importlib.import_module(module_name)
                    cls = getattr(module, class_name)
                    try:
                        widget = cls(self.store, self.settings)
                    except TypeError:
                        widget = cls()
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    widget = None
            if widget is None:
                widget = FallbackScreen(name, f"{name}: brak modulu ({type(last_error).__name__ if last_error else '?'})")
            widget.setObjectName(f"screen_{name}")
            self.screens[name] = widget
            self.stack.addWidget(widget)
            self._wire(name, widget)
        self.show_screen("home")

    def _wire(self, name: str, widget: QWidget) -> None:
        """Podlacza sygnaly ekranu, jesli istnieja."""
        handlers = {
            "request_start": lambda payload: self._start_from_payload(payload),
            "request_free": lambda value: self._free_request(value),
            "request_end": lambda reason: self._request_end(reason),
            "request_settings": lambda patch: self._run_action("settings", patch),
            "request_pin_check": lambda pin: self._check_pin(pin),
            # rozszerzenia ekranow (ui-screens) - wszystkie ida przez jedna sciezke akcji
            "request_pause": lambda *_: self._run_action("toggle_pause", {}),
            "request_break": lambda *_: self._run_action("start_break", {}),
            "request_preset": lambda payload: self._run_action("save_preset", payload),
            "request_rating": lambda value: self._run_action("rating", {"value": value}),
            "request_set_pin": lambda pin: self._run_action("set_pin", {"pin": pin}),
            "request_refresh_apps": lambda *_: self._run_action("refresh_apps", {}),
        }
        for signal_name, handler in handlers.items():
            signal = getattr(widget, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(handler)
            except Exception:
                pass
        action_signal = getattr(widget, "request_action", None)
        if action_signal is not None:
            try:
                action_signal.connect(lambda action, payload=None: self._run_action(str(action), payload or {}))
            except Exception:
                pass
        # przyciski pomocnicze: show_screen
        show = getattr(widget, "request_screen", None)
        if show is not None:
            try:
                show.connect(lambda target: self.show_screen(str(target)))
            except Exception:
                pass

    def _run_action(self, action: str, payload: dict) -> dict:
        """Wykonuje akcje zadaną przez ekran i rozsyła wynik z powrotem do ekranów.

        Ciężkie akcje (skan instalowanych aplikacji) lecą do puli wątków - wynik
        wraca sygnałem `action_finished`, a UI dostaje go przez `on_action_result`.
        """
        payload = payload or {}

        # Nawigacja miedzy ekranami (przyciski typu "KREATOR SESJI").
        if action in NAVIGATION_ACTIONS:
            target = NAVIGATION_ACTIONS[action]
            self.show_screen(target)
            return self._dispatch_action_result(action, {"ok": True, "screen": target})

        if action == "open_pin_dialog":
            return self._dispatch_action_result(action, self._open_pin_dialog())

        if action == "settings":
            return self._dispatch_action_result(action, self.controller.apply_settings(payload))

        heavy = action in HEAVY_ACTIONS or (action == "catalog_apps" and payload.get("force"))
        if heavy:
            self._pending_actions.add(action)
            self._notify_busy(action, True)
            worker = _ActionWorker(
                action,
                lambda: self.controller.handle_action(action, payload),
                self._action_signals,
            )
            self._pool.start(worker)
            return {"ok": True, "pending": True, "action": action}
        return self._dispatch_action_result(action, self.controller.handle_action(action, payload))

    def _open_pin_dialog(self) -> dict:
        """Ustawienie PIN-u z Ustawien: okno modalne + zapis przez kontroler."""
        holder: dict = {}
        captured: dict = {"pin": ""}

        def _submit(pin: str) -> None:
            captured["pin"] = pin
            holder["dialog"].notify_result(len(pin) >= 4)

        dialog = self._pin_dialog("NOWY PIN (MIN. 4 ZNAKI)", "USTAW PIN", on_submit=_submit)
        if dialog is None:
            return {"ok": False, "errors": ["brak modulu okna PIN"]}
        holder["dialog"] = dialog
        try:
            if not dialog.exec():
                return {"ok": False, "errors": ["anulowano"]}
            pin = str(captured.get("pin") or "")
            if len(pin) < 4:
                self._toast("PIN musi miec co najmniej 4 znaki")
                return {"ok": False, "errors": ["PIN musi miec co najmniej 4 znaki"]}
            result = self.controller.set_pin(pin)
            if result.get("ok"):
                self._toast("PIN zostal ustawiony")
            return result
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}

    def _dispatch_action_result(self, action: str, result: dict) -> dict:
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        self._pending_actions.discard(action)
        self._notify_busy(action, False)
        for widget in self.screens.values():
            handler = getattr(widget, "on_action_result", None)
            if callable(handler):
                try:
                    handler(action, result)
                except Exception:
                    pass
        # Nieudana akcja nie moze znikac bez sladu - pokazujemy komunikat.
        if result.get("ok") is False:
            errors = result.get("errors") or ["nie udalo sie wykonac akcji"]
            self._toast(f"{action}: {'; '.join(str(e) for e in errors)[:160]}")
        return result

    def _notify_busy(self, action: str, busy: bool) -> None:
        for widget in self.screens.values():
            handler = getattr(widget, "on_action_busy", None)
            if callable(handler):
                try:
                    handler(action, busy)
                except Exception:
                    pass

    def show_screen(self, name: str, extra: Optional[dict] = None) -> None:
        """Przelacza ekran i przekazuje mu dane (opcjonalnie wzbogacone o `extra`)."""
        widget = self.screens.get(name)
        if widget is None:
            return
        self.stack.setCurrentWidget(widget)
        set_data = getattr(widget, "set_data", None)
        if callable(set_data):
            try:
                payload = self.controller.summary()
                if extra:
                    payload.update(extra)
                set_data(payload)
            except Exception:
                pass
        nav = getattr(self, "_nav", None)
        if nav is not None:
            try:
                if name in nav.keys():
                    nav.set_current(name)
            except Exception:  # noqa: BLE001
                pass

    def _summary_payload(self, event_data: dict) -> dict:
        """Dane dla ekranu podsumowania: wiersz sesji z bazy + stan banku.

        Bez tego ekran dostawal tylko `controller.summary()` i pokazywal same zera.
        """
        data = dict(event_data or {})
        economy = dict(data.get("economy") or {})
        session_id = data.get("session_id")
        session: dict = {}
        if session_id:
            try:
                session = dict(self.controller.store.get_session(int(session_id)) or {})
            except Exception:  # noqa: BLE001
                session = {}
        state = dict(data.get("state") or {})
        result = {
            "session_id": session_id,
            "status": str(session.get("status") or ""),
            "reason": str(data.get("reason") or ""),
            "study_seconds": session.get("actual_seconds", data.get("actual_seconds", 0)),
            "plan_seconds": session.get("plan_seconds", 0),
            "tag": session.get("tag", ""),
            "goal_note": session.get("goal_note", ""),
            "pomodoros_done": session.get("pomodoros_done", 0),
            "pomodoros_aborted": session.get("pomodoros_aborted", 0),
            "balance": economy.get("balance"),
            "blocked": self.controller.blocked_attempts,
        }
        return {"result": result, "bank": economy, "session": data, "state": state}

    # ------------------------------------------------------------------ akcje sesji
    def _start_from_payload(self, payload: dict) -> None:
        """Przycisk startu obsluguje oba tryby: nauke i wolne (mode=FREE)."""
        payload = dict(payload or {})
        mode = str(payload.get("mode") or "STUDY").upper()
        if mode == "FREE":
            self._start_free(int(payload.get("free_minutes") or 0), payload)
            return
        self._start_study(payload)

    def _start_study(self, payload: dict) -> None:
        result = self.controller.start_study(payload or {})
        if result.get("ok"):
            self.show_screen("running")
            for warning in result.get("warnings") or []:
                self._toast(str(warning))
            if self.settings.system.safe_mode:
                return
            if self.settings.lock.taskbar_mode == "hide":
                # Pelne ukrycie paska zadan: okno Ciszy zajmuje caly ekran.
                self._go_kiosk()
            else:
                # Tryb "filtered": pasek zadan zostaje widoczny (z przyciskami
                # tylko dozwolonych aplikacji), wiec okno nie moze byc fullscreen.
                self._show_session_window()
        else:
            self._toast("nie udalo sie zaczac sesji: " + "; ".join(result.get("errors", [])))

    def _start_free(self, minutes: int, payload: Optional[dict] = None) -> None:
        """Tryb wolny: bez podanej dlugosci bierzemy minimalny blok z banku."""
        payload = payload or {}
        minimum = int(self.settings.economy.min_free_block_minutes)
        minutes = int(minutes or 0)
        if minutes <= 0:
            balance_min = self.controller.store.bank_balance() // 60
            minutes = min(minimum, balance_min)
        if minutes <= 0:
            self._toast("Bank jest pusty - najpierw ukoncz pomodoro, zeby zarobic minuty.")
            return
        result = self.controller.start_free(
            minutes, tag=str(payload.get("tag") or "wolne"), goal=str(payload.get("goal_note") or "")
        )
        if result.get("ok"):
            self.show_screen("free")
        else:
            self._toast("; ".join(str(e) for e in result.get("errors", ["nie udalo sie"])))

    def _free_request(self, value: int) -> None:
        """Sygnał request_free: sekundy dokladane do trwajacego trybu wolnego
        albo (gdy tryb nie jest aktywny) start nowej sesji wolnej."""
        try:
            seconds = int(value or 0)
        except (TypeError, ValueError):
            seconds = 0
        state = self.controller.engine.state() if self.controller.engine else {}
        active_free = state.get("mode") == "FREE" and state.get("phase") not in ("IDLE", "DONE")
        if active_free:
            if seconds <= 0:
                seconds = int(self.settings.economy.min_free_block_minutes) * 60
            result = self.controller.handle_action("extend_free", {"seconds": seconds})
            if result.get("ok"):
                self._toast(f"Dodano {seconds // 60} min z banku.")
            else:
                self._toast("; ".join(str(e) for e in result.get("errors", ["nie udalo sie"])))
            return
        self._start_free(seconds // 60 if seconds else 0)

    def _request_end(self, reason: str) -> None:
        """Koniec sesji: natychmiastowy odzew w UI i obsluga kazdego wyniku.

        Wczesniej brak PIN-u w oknie (albo wyjatek przy jego tworzeniu) konczyl sie
        cichym powrotem - "naciskam ZAKONCZ i nic sie nie dzieje".
        """
        if getattr(self, "_ending", False):
            return
        engine = self.controller.engine
        state = engine.state() if engine is not None else {}
        if state.get("phase") in ("IDLE", "DONE", ""):
            self._toast("Nie ma aktywnej sesji.")
            return

        self._ending = True
        self._ensure_window_visible()
        self._toast("KOŃCZĘ SESJĘ…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            result = self.controller.request_end(reason or "user")
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
        finally:
            QApplication.restoreOverrideCursor()
            self._ending = False

        if result.get("requires_pin"):
            self._ask_end_pin(reason or "user")
            return
        errors = [str(item) for item in (result.get("errors") or [])]
        if errors and not result.get("ok"):
            self._toast(errors[0][:200])

    def _ask_end_pin(self, reason: str) -> None:
        """Okno PIN-u przed zakonczeniem sesji (z widocznym oknem i bez cichego wyjscia)."""
        holder: dict = {}
        captured: dict = {"pin": ""}

        def _submit(pin: str) -> None:
            captured["pin"] = pin
            try:
                holder["dialog"].notify_result(self.controller.check_pin(pin))
            except Exception:
                holder["dialog"].notify_result(False)

        dialog = self._pin_dialog("PODAJ PIN, ŻEBY ZAKOŃCZYĆ SESJĘ", "WERYFIKACJA", on_submit=_submit)
        if dialog is None:
            self._toast("Nie mogę pokazać okna PIN-u — sesja trwa dalej.")
            return
        holder["dialog"] = dialog
        if not dialog.exec():
            self._toast("Anulowano — sesja trwa dalej.")
            return
        pin = str(captured.get("pin") or "")
        result = self.controller.confirm_end(pin, reason)
        if not result.get("ok"):
            errors = [str(item) for item in (result.get("errors") or ["nie udalo sie zakonczyc"])]
            self._toast(errors[0][:200])

    def _check_pin(self, pin: str) -> None:
        ok = self.controller.check_pin(pin)
        for widget in self.screens.values():
            result_signal = getattr(widget, "pin_result", None)
            if result_signal is not None:
                try:
                    result_signal.emit(bool(ok))
                except Exception:
                    pass

    def _pin_dialog(self, prompt: str = "PODAJ PIN", title: str = "WERYFIKACJA", on_submit=None):
        """Tworzy modalne okno PIN (rodzic = okno glowne) z podpieta obsluga.

        `on_submit(pin)` dostaje wpisany PIN; domyslnie sprawdzamy go w kontrolerze.
        Wczesniej przekazywano tu `Settings` jako rodzica Qt, co konczylo sie
        wyjatkiem i cichym brakiem okna.
        """
        try:
            module = importlib.import_module("focuslock.ui.screens.pin")
            dialog = module.PinDialog(self, prompt, title)
        except Exception as exc:  # noqa: BLE001
            self._toast(f"Okno PIN niedostepne: {exc}")
            return None
        self._ensure_window_visible()
        handler = on_submit or (lambda pin: dialog.notify_result(self.controller.check_pin(pin)))
        try:
            dialog.request_pin_check.connect(handler)
        except Exception:  # noqa: BLE001
            pass
        return dialog

    def _ensure_window_visible(self) -> None:
        """Okno Ciszy (i dialogi) musza byc widoczne - nigdy schowane w zasobniku."""
        try:
            if not self.isVisible() or self.isMinimized():
                self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:  # noqa: BLE001
            pass

    def restore_from_tray(self) -> None:
        """Przywraca okno z zasobnika i aktywuje odpowiedni ekran."""
        self._ensure_window_visible()
        engine = self.controller.engine
        state = engine.state() if engine is not None else {}
        phase = str(state.get("phase") or "").upper()
        if phase in ("STUDY", "PAUSED"):
            self.show_screen("running")
        elif phase in ("BREAK", "LONG_BREAK"):
            self.show_screen("break")
        elif state.get("mode") == "FREE" and phase not in ("IDLE", "DONE", ""):
            self.show_screen("free")
        else:
            self.show_screen("home")


    def _go_kiosk(self) -> None:
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def _show_session_window(self) -> None:
        """Pokazuje okno sesji bez fullscreen (pasek zadan ma zostac klikalny)."""
        try:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            if self.isMinimized():
                self.showNormal()
        except Exception:
            pass
        self.show()
        self.raise_()
        self.activateWindow()

    def leave_kiosk(self) -> None:
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.showNormal()

    # -------------------------------------------------------------------- zdarzenia
    def _on_event(self, event: str, data: dict) -> None:
        if event == "blocked_app":
            self._toast(f"ZABLOKOWANO: {data.get('name', '?')}")
        elif event == "blocked_site":
            self._toast(f"ZABLOKOWANO STRONE: {data.get('host', '?')}")
        elif event == "exit_request":
            how = str((data or {}).get("how", ""))
            if how == "hold" and not self.settings.lock.panic_requires_pin:
                # Awaryjne wyjscie bez PIN-u (ustawienie "wyjscie awaryjne wymaga PIN-u" wylaczone).
                result = self.controller.request_end("panic")
                if result.get("ok") and not result.get("requires_pin"):
                    self.show_screen("summary")
            else:
                self._request_end("user")
        elif event == "phase":
            phase = str((data or {}).get("phase") or "").upper()
            if phase in ("BREAK", "LONG_BREAK"):
                self.show_screen("break")
                if self.settings.ui.sound_enabled and not self.settings.lock.mute_sound:
                    sound.play_sound("break_start")
            elif phase == "STUDY":
                self.show_screen("running")
                if self.settings.ui.sound_enabled and not self.settings.lock.mute_sound:
                    sound.play_sound("study_start")
        elif event == "pomodoro_end":
            if bool(data.get("completed", False)):
                if self.settings.ui.sound_enabled and not self.settings.lock.mute_sound:
                    sound.play_sound("pomodoro_end")
        elif event == "session_finished":
            self.leave_kiosk()
            self.show_screen("summary", self._summary_payload(data))
        for widget in self.screens.values():
            handler = getattr(widget, "on_app_event", None)
            if callable(handler):
                try:
                    handler(event, data)
                except Exception:
                    pass

    def _toast(self, message: str) -> None:
        """Pokazuje komunikat na aktualnie widocznym ekranie (albo na pierwszym, ktory potrafi)."""
        current = self.stack.currentWidget()
        candidates = [current] + [w for w in self.screens.values() if w is not current]
        for widget in candidates:
            handler = getattr(widget, "show_toast", None)
            if callable(handler):
                try:
                    handler(message)
                    return
                except Exception:
                    continue

    def _shutdown_and_quit(self) -> None:
        """'Zamknij' z zasobnika: najpierw zwolnij blokady, dopiero potem wyjdz."""
        try:
            self.controller.shutdown()
        except Exception:  # noqa: BLE001
            pass
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        """Zamkniecie okna: koniec programu, chyba ze trwa sesja (wtedy zasobnik)."""
        engine = self.controller.engine
        state = engine.state() if engine is not None else {}
        active = state.get("phase") not in ("IDLE", "DONE", "")
        tray = getattr(self, "_tray", None)
        if active and tray is not None:
            # Sesja trwa - blokady musza zostac, wiec chowamy okno do zasobnika.
            event.ignore()
            self.hide()
            try:
                tray.notify("Cisza", "Sesja trwa — Cisza działa dalej w zasobniku.")
            except Exception:  # noqa: BLE001
                pass
            return
        self.controller.shutdown()
        event.accept()
        QApplication.quit()


def _seed_presets(store) -> None:
    """Zaklada domyslne presety przy pierwszym uruchomieniu (np. Matura - matematyka)."""
    try:
        module = importlib.import_module("focuslock.presets")
        ensure = getattr(module, "ensure_defaults", None)
        if callable(ensure):
            ensure(store)
    except Exception:
        pass


def _apply_pending_restore() -> None:
    """Podmienia baze na kopia wskazana w Ustawieniach (wykonywane przed otwarciem bazy)."""
    import shutil

    pending = paths.data_dir() / "restore-pending.db"
    if not pending.exists():
        return
    target = paths.db_path()
    try:
        for suffix in ("-wal", "-shm"):
            side = target.with_name(target.name + suffix)
            if side.exists():
                side.unlink()
        if target.exists():
            shutil.copy2(target, target.with_name(f"{target.name}.przed-przywroceniem"))
        shutil.copy2(pending, target)
        pending.unlink()
    except OSError:
        pass


def _single_instance(name: str) -> Optional[QLocalServer]:
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.close()
        return None
    server = QLocalServer()
    try:
        QLocalServer.removeServer(name)
        server.listen(name)
    except Exception:
        pass
    return server


def leftover_firewall_warning() -> str:
    """Komunikat, gdy zostaly reguly zapory Cisza (moga blokowac przegladarki).

    Reguly tworzy helper z uprawnieniami administratora; konto bez admina nie
    moze ich usunac, wiec aplikacja musi o tym wyraznie powiedziec.
    """
    try:
        from .block import firewall

        if not firewall.is_active():
            return ""
    except Exception:  # noqa: BLE001 - brak netsh/uprawnien to nie blad krytyczny
        return ""
    return (
        "Zostały reguły zapory „CiszaBlock-*” z wcześniejszej sesji.\n"
        "Mogą one blokować internet w przeglądarkach (np. Edge pokazuje\n"
        "ERR_NETWORK_ACCESS_DENIED).\n\n"
        "Aby je usunąć, kliknij prawym przyciskiem na plik\n"
        "napraw-internet.cmd (w katalogu projektu) i wybierz\n"
        "„Uruchom jako administrator”, a następnie podaj hasło konta admin."
    )


def cleanup_incomplete_message(leftovers: list, instructions: str = "") -> str:
    """Tresc okna ostrzezenia o pozostalosciach po sesji (lista + instrukcja naprawy)."""
    items = [str(item).strip() for item in (leftovers or []) if str(item).strip()]
    lines = ["Po sesji nie udało się cofnąć wszystkich zmian w systemie:"]
    lines.extend(f"  - {item}" for item in items)
    lines.append("")
    lines.append(str(instructions or "").strip() or recovery.leftovers_instructions())
    return "\n".join(lines)


def startup_leftovers_report(*, dry_run: bool = False) -> dict:
    """Ponawia sprzatanie, gdy poprzednia sesja zostawila znacznik pozostalosci.

    Zwraca raport `cleanup_leftovers` (pusty, gdy znacznika nie ma). Bez admina
    wykona to, co moze - reszta znowu trafi do znacznika i do ostrzezenia.
    """
    if recovery.read_leftovers() is None:
        return {}
    try:
        return recovery.cleanup_leftovers(dry_run=dry_run)
    except Exception:  # noqa: BLE001 - start aplikacji nie moze sie wywalic
        return {}


def data_dir_problem() -> str:
    """Zwraca komunikat, gdy katalogu danych nie da sie zapisac ("" = jest ok).

    Bez tego zamrozony .exe konczyl sie surowym dialogiem
    "attempt to write a readonly database" bez informacji, o co chodzi.
    """
    try:
        target = paths.data_dir()
    except Exception as exc:  # noqa: BLE001
        return f"Cisza nie moze utworzyc katalogu danych:\n{exc}"
    probe = target / ".zapis-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return (
            f"Cisza nie moze zapisac danych w katalogu:\n{target}\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Sprawdz uprawnienia do tego katalogu albo ustaw zmienna srodowiskowa\n"
            "CISZA_DATA_DIR na inny, zapisywalny katalog."
        )
    return ""


def show_startup_error(message: str) -> int:
    """Pokazuje blad startu oknem (albo w konsoli) i zwraca kod wyjscia 2."""
    try:
        box = QMessageBox()
        box.setWindowTitle("Cisza — blad startu")
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText(message)
        box.exec()
    except Exception:  # noqa: BLE001 - brak Qt to nie powod, by nie pokazac tekstu
        try:
            print(message)
        except Exception:  # noqa: BLE001
            pass
    return 2


def run(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("Cisza")
    app.setQuitOnLastWindowClosed(False)
    theme.apply_to(app)
    app.setWindowIcon(make_app_icon())

    problem = data_dir_problem()
    if problem:
        return show_startup_error(problem)

    name = f"{APP_ID}-{os.environ.get('USERNAME', 'user')}"
    server = _single_instance(name)
    if server is None:
        return 0

    _apply_pending_restore()
    try:
        store = Store()
    except Exception as exc:  # noqa: BLE001 - np. SQLITE_READONLY na cudzym profilu
        return show_startup_error(
            "Cisza nie moze otworzyc bazy danych.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            f"Plik: {paths.db_path()}"
        )
    settings = Settings.load(store)
    settings.env_overrides()
    _seed_presets(store)

    events: list[tuple[str, dict]] = []

    def emit(event: str, data: dict) -> None:
        events.append((event, dict(data or {})))

    controller = Controller(store, settings, emit=emit, bridge=HelperBridge(dry_run=settings.system.dry_run))
    controller.startup_recovery()
    if controller.economy is not None:
        controller.economy.sweep_expired()

    window = MainWindow(controller, settings, store)

    if server is not None:
        def _on_instance_activated() -> None:
            client = server.nextPendingConnection()
            if client:
                client.close()
            window.showNormal()
            window.raise_()
            window.activateWindow()

        server.newConnection.connect(_on_instance_activated)
        app._single_instance_server = server  # trzymamy referencje przez czas zycia aplikacji

    tray = None
    if settings.ui.tray_icon:
        try:
            tray_module = importlib.import_module("focuslock.ui.tray")
            tray = tray_module.Tray(window, "Cisza", controller, app)
            tray.show_requested.connect(window.restore_from_tray)
            tray.start_requested.connect(lambda: window._start_study({}))
            tray.end_requested.connect(lambda: window._request_end("user"))
            tray.stats_requested.connect(lambda: window.show_screen("stats"))
            tray.quit_requested.connect(window._shutdown_and_quit)
            tray.show()
        except Exception:
            tray = _fallback_tray(app, window)
    window._tray = tray  # okno wie, czy moze schowac sie do zasobnika

    timer = QTimer()
    timer.setInterval(1000)

    # Okno ostrzezenia o pozostalosciach pokazujemy raz na zdarzenie o danej
    # tresci - inaczej kolejne ticki powtarzalyby ten sam komunikat.
    cleanup_warned: set[tuple] = set()

    def show_cleanup_incomplete(data: dict) -> None:
        items = [str(item).strip() for item in (data.get("leftovers") or []) if str(item).strip()]
        if not items:
            return
        key = tuple(items)
        if key in cleanup_warned:
            return
        cleanup_warned.add(key)
        QMessageBox.warning(
            window,
            "Cisza — nie wszystko posprzątane po sesji",
            cleanup_incomplete_message(items, str(data.get("instructions") or "")),
        )

    def on_timer() -> None:
        state = controller.tick()
        window.setWindowTitle("Cisza")
        for event, data in events[-20:]:
            if event == "cleanup_incomplete":
                try:
                    show_cleanup_incomplete(data)
                except Exception:  # noqa: BLE001 - okno nie moze zatrzymac petli
                    pass
            window.event_received.emit(event, data)
        events.clear()
        current = window.screens.get("running")
        if current is not None and hasattr(current, "update_state"):
            try:
                current.update_state(state)
            except Exception:
                pass
        free_screen = window.screens.get("free")
        if free_screen is not None and hasattr(free_screen, "update_state"):
            try:
                free_screen.update_state(state)
            except Exception:
                pass
        break_screen = window.screens.get("break")
        if break_screen is not None and hasattr(break_screen, "update_state"):
            try:
                break_screen.update_state(state)
            except Exception:
                pass
        if tray is not None and hasattr(tray, "set_running"):
            try:
                tray.set_running(state.get("phase") not in ("IDLE", "DONE"))
            except Exception:
                pass

    timer.timeout.connect(on_timer)
    timer.start()

    if not settings.onboarding_done and "onboarding" in window.screens:
        window.show_screen("onboarding")

    # Okno pokazujemy od razu; do zasobnika chowamy sie tylko na wyraźne zyczenie
    # uzytkownika (Ustawienia -> Wyglad -> "START ZMINIMALIZOWANY") i tylko wtedy,
    # gdy istnieje ikona w zasobniku, ktora pozwoli wrocic.
    if settings.ui.start_minimized and settings.ui.tray_icon and settings.onboarding_done:
        window.showMinimized()
    else:
        window.show()

    # Katalog aplikacji buduje sie w tle od razu po starcie (KREATOR i Ustawienia
    # korzystaja z gotowego cache, bez zamrazania okna).
    _warm_app_catalog(window, controller)

    # Pozostalosci po poprzedniej sesji: najpierw ponawiamy sprzatanie (to, co
    # da sie bez admina), a dopiero potem pokazujemy ostrzezenie z instrukcja.
    retry = startup_leftovers_report(dry_run=bool(settings.system.dry_run))
    startup_warning = ""
    if retry.get("leftovers"):
        startup_warning = cleanup_incomplete_message(
            retry.get("leftovers") or [], recovery.leftovers_instructions()
        )
    if not startup_warning:
        startup_warning = leftover_firewall_warning()
    if startup_warning:
        title = "Cisza — nie wszystko posprzątane" if retry.get("leftovers") else "Cisza — pozostałe reguły zapory"
        QTimer.singleShot(
            2000,
            lambda: QMessageBox.warning(window, title, startup_warning),
        )

    return app.exec()


def _warm_app_catalog(window, controller) -> None:
    """Uzupelnia katalog aplikacji w tle, zeby KREATOR nie zamarzl przy wejsciu.

    Pelny skan Start Menu (PowerShell) i rejestru trwa kilkanascie sekund, wiec
    robimy go raz po starcie w watku puli. Ekrany dostaja gotowy cache przez
    `catalog_apps` (bez force), a nie przez blokujacy skan w watku GUI.
    """
    try:
        module = importlib.import_module("focuslock.appcatalog")
        if module.load_cache() is not None:
            return
    except Exception:  # noqa: BLE001 - brak katalogu nie moze blokowac startu
        return
    worker = _ActionWorker(
        "refresh_apps",
        lambda: controller.handle_action("refresh_apps", {"force": True}),
        window._action_signals,
    )
    QTimer.singleShot(1500, lambda: window._pool.start(worker))


def _fallback_tray(app_or_window, other) -> QSystemTrayIcon:
    if isinstance(app_or_window, QApplication):
        app, window = app_or_window, other
    else:
        window, app = app_or_window, other
    tray = QSystemTrayIcon(make_app_icon(), app)
    menu = QMenu()
    show_action = QAction("Pokaz Cisze", menu)
    show_action.triggered.connect(window.show)
    quit_action = QAction("Zamknij", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(show_action)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.setToolTip("Cisza")
    tray.show()
    return tray
