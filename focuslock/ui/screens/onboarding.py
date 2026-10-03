"""Pierwsze uruchomienie: PIN, autostart i lista dozwolonych aplikacji.

Uklad: naglowek z postepem krokow, tresc karty i pasek nawigacji na dole.
Pola PIN-u maja czytelne etykiety, a pusta lista aplikacji krotki komunikat.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QStackedWidget, QVBoxLayout, QWidget

from ..theme import SIZES
from ..widgets import Card, GhostButton, PickList, PrimaryButton, Toggle, labels
from .base import Screen, as_int


class OnboardingScreen(Screen):
    """Cztery kroki wstepne; nic nie zapisuje samo — wszystko idzie sygnalami."""

    TITLE = "PIERWSZE URUCHOMIENIE"
    STEPS = ("WITAJ", "PIN", "AUTOSTART", "DOZWOLONE")

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        self._markers = labels.caption("")
        self.header_widget(self._markers)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_welcome())
        self._stack.addWidget(self._page_pin())
        self._stack.addWidget(self._page_autostart())
        self._stack.addWidget(self._page_allowlist())
        self.root.addWidget(self._stack, 1)

        nav = self.action_bar()
        self._back = GhostButton("WSTECZ")
        self._back.clicked.connect(self._go_back)
        self._skip = GhostButton("POMIŃ KROK")
        self._skip.clicked.connect(self._skip_step)
        self._next = PrimaryButton("DALEJ")
        self._next.clicked.connect(self._go_next)
        nav.addWidget(self._skip)
        nav.addStretch(1)
        nav.addWidget(self._back)
        nav.addWidget(self._next)

        self._dialog: PinDialog | None = None
        self._step = 0
        self._skipped: set[int] = set()
        self._sync_step()

    @staticmethod
    def _centered(card: QWidget) -> QWidget:
        """Karta kroku wysrodkowana w pionie (kreator nie kleji sie do gory)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _page_welcome(self) -> QWidget:
        card = Card("CISZA", "NAUKA BEZ ROZPROSZENIA")
        card.add(
            labels.body(
                "Cisza ukrywa pulpit, blokuje aplikacje i strony spoza listy oraz zabiera skróty systemowe. "
                "Za każdą minutę nauki dostajesz minuty wolnego w banku — to jedyna waluta w tej aplikacji."
            )
        )
        card.add(labels.caption("CO DALEJ"))
        card.add(
            labels.hint(
                "1. Ustaw PIN wyjścia awaryjnego. 2. Zdecyduj o autostarcie. 3. Wskaż aplikacje dozwolone."
            )
        )
        return self._centered(card)

    def _page_pin(self) -> QWidget:
        card = Card("PIN WYJŚCIA AWARYJNEGO", "BEZ PIN-U NIE MA WYJŚCIA Z SESJI")
        self._pin = QLineEdit()
        self._pin.setEchoMode(QLineEdit.EchoMode.Password)
        self._pin.setPlaceholderText("PIN (min. 4 znaki)")
        self._pin.setMinimumHeight(SIZES["input"])
        self._pin_repeat = QLineEdit()
        self._pin_repeat.setEchoMode(QLineEdit.EchoMode.Password)
        self._pin_repeat.setPlaceholderText("powtórz PIN")
        self._pin_repeat.setMinimumHeight(SIZES["input"])
        self._pin_info = labels.hint("PIN będzie wymagany przy wyjściu awaryjnym i zmianie ustawień blokady.")
        card.add(labels.field("PIN"))
        card.add(self._pin)
        card.add(labels.field("POWTÓRZ PIN"))
        card.add(self._pin_repeat)
        card.add(self._pin_info)
        set_pin = GhostButton("USTAW PIN")
        set_pin.clicked.connect(self._submit_pin)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(set_pin)
        row.addStretch(1)
        card.add_layout(row)
        return self._centered(card)

    def _page_autostart(self) -> QWidget:
        card = Card("AUTOSTART", "CZY CISZA MA WSTAWAĆ RAZEM Z SYSTEMEM")
        self._autostart = Toggle("URUCHAMIAJ CISZĘ PRZY STARCIE SYSTEMU")
        self._helper = Toggle("URUCHAMIAJ HELPER Z UPRAWNIENIAMI ADMINISTRATORA")
        self._helper.setChecked(True)
        card.add(self._autostart)
        card.add(self._helper)
        card.set_hint("Autostart można wyłączyć w każdej chwili w ustawieniach systemu.")
        return self._centered(card)

    def _page_allowlist(self) -> QWidget:
        card = Card("DOZWOLONE APLIKACJE", "WSZYSTKO SPOZA LISTY ZOSTANIE ZABLOKOWANE")
        self._apps = PickList(
            "np. chrome.exe, notepad.exe",
            "Lista jest pusta — dodaj proces, który ma działać w trakcie nauki.",
        )
        card.add(self._apps)
        card.set_hint("Zaznacz tylko to, co naprawdę potrzebne do nauki. Resztę dopiszesz później.")
        return self._centered(card)

    # ------------------------------------------------------------------ kroki
    def _sync_step(self) -> None:
        self._stack.setCurrentIndex(self._step)
        self._markers.setText(" ".join("▣" if index <= self._step else "□" for index in range(4)))
        self._back.setEnabled(self._step > 0)
        self._next.setText("ZAKOŃCZ" if self._step == 3 else "DALEJ")
        self._skip.setText("KROK POMINIĘTY" if self._step in self._skipped else "POMIŃ KROK")

    def _go_back(self) -> None:
        self._step = max(0, self._step - 1)
        self._sync_step()

    def _go_next(self) -> None:
        if self._step >= 3:
            self._finish()
            return
        self._step = min(3, self._step + 1)
        self._sync_step()

    def _skip_step(self) -> None:
        """POMIŃ KROK: przechodzi dalej bez zbierania danych z tego kroku.

        Rozni sie od DALEJ: zapisuje krok jako pominiety (widac to w podpisie),
        a na kroku z PIN-em nie wymusza jego ustawienia.
        """
        self._skipped.add(self._step)
        if self._step >= 3:
            self._finish()
            return
        self._step = min(3, self._step + 1)
        self._sync_step()
        self.show_toast("Krok pominięty — możesz do niego wrócić przyciskiem WSTECZ.")

    def _submit_pin(self) -> None:
        pin = self._pin.text().strip()
        repeat = self._pin_repeat.text().strip()
        if len(pin) < 4:
            self._pin_info.setText("PIN musi mieć minimum 4 znaki.")
            return
        if pin != repeat:
            self._pin_info.setText("PIN-y się różnią — wpisz ponownie.")
            return
        self.request_set_pin.emit(pin)
        self._pin_info.setText("PIN ustawiony.")
        self._pin.clear()
        self._pin_repeat.clear()

    def _finish(self) -> None:
        payload: dict = {"onboarding_done": True}
        system: dict = {}
        if self._autostart.isChecked():
            system["autostart"] = True
        if self._helper.isChecked():
            system["launch_helper_on_start"] = True
        if system:
            payload["system"] = system
        self.request_settings.emit(payload)
        apps = self._apps.selected()
        if apps:
            self.request_action.emit("onboarding_apps", {"apps": apps})
        self.show_toast("Konfiguracja wstępna zapisana.")
        self.request_screen.emit("home")

    # ------------------------------------------------------------------ dane
    def render(self, data: dict) -> None:
        if data.get("apps") is not None:
            self._apps.set_items(data.get("apps"))
        if data.get("onboarding_done"):
            self._step = 3
            self._sync_step()
        if data.get("step") is not None:
            self._step = max(0, min(3, as_int(data["step"])))
            self._sync_step()
