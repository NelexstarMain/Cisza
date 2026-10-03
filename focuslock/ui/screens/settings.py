"""Ustawienia: 8 zakladek (Sesja, Aplikacje, Strony, Ekonomia, Blokada, Wyglad,
Statystyki i dane, System). Ekran tylko zbiera wartosci i emituje sygnaly.

Wyglad: wspolny naglowek strony, zakladki z trescia wyrównana do tych samych
24 px marginesow co reszta ekranow, etykiety pol czytelne (mala litera, bez
rozstrzelenia) i puste stany list zamiast pustych tabel.
"""
from __future__ import annotations

import os
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ... import sitecatalog
from ..theme import SIZES
from ..widgets import Card, DangerButton, EmptyState, GhostButton, PrimaryButton, Toggle, labels
from ..widgets.picker import CatalogPicker
from .base import Screen, as_dict, as_int, as_list

TAB_TITLES = (
    "Sesja",
    "Aplikacje",
    "Strony",
    "Ekonomia",
    "Blokada",
    "Wygląd",
    "Statystyki i dane",
    "System",
)

#: Wcięcie tresci zakladek: 6 + 18 (margines karty) = 24 px, jak w `Screen.MARGINS`.
TAB_MARGINS = (6, 4, 6, 12)
TAB_SPACING = 12
#: Szerokosc kolumny etykiet w formularzach - kontrolki ukladaja sie w jednej linii.
LABEL_WIDTH = 250

# (nazwa pola, typ, etykieta, ...dodatki)
FIELD_SPECS: dict[str, list[tuple]] = {
    "session": [
        ("study_minutes", "int", "DŁUGOŚĆ POMODORO (MIN)", 1, 240),
        ("break_minutes", "int", "PRZERWA (MIN)", 1, 60),
        ("long_break_minutes", "int", "DŁUGA PRZERWA (MIN)", 1, 120),
        ("long_break_every", "int", "DŁUGA CO ILE POMODORO", 0, 12),
        ("arming_seconds", "int", "ODLICZANIE STARTU (S)", 0, 30),
        ("auto_start_break", "bool", "AUTOSTART PRZERWY"),
        ("auto_start_next_study", "bool", "AUTOSTART KOLEJNEGO POMODORO"),
        ("allow_end_early", "bool", "POZWÓL KOŃCZYĆ SESJĘ WCZEŚNIEJ"),
    ],
    "economy": [
        ("earn_ratio", "float", "PRZELICZNIK: MINUTY WOLNEGO ZA MINUTĘ NAUKI", 0.0, 10.0, 0.05, 2),
        ("round_seconds", "int", "ZAOKRĄGLANIE NAGRODY (S)", 5, 300),
        ("daily_cap_minutes", "int", "DZIENNY LIMIT ZAROBKU (MIN)", 0, 600),
        ("bank_ttl_days", "int", "WAŻNOŚĆ MINUT (DNI, 0 = bezterminowo)", 0, 90),
        ("abort_penalty_minutes", "int", "KARA ZA PRZERWANIE (MIN)", 0, 120),
        ("min_free_block_minutes", "int", "MINIMALNY BLOK WOLNEGO (MIN)", 1, 240),
        ("streak_min_study_minutes", "int", "MINIMUM NAUKI DO SERII (MIN)", 0, 240),
        ("require_full_pomodoro", "bool", "NAGRODA TYLKO ZA PEŁNE POMODORO"),
    ],
    "lock": [
        ("mode", "choice", "TRYB BLOKADY", (("soft", "MIĘKKI"), ("hard", "TWARDY"), ("hardcore", "HARDCORE"))),
        ("hardcore_max_minutes", "int", "MAKS. CZAS HARDCORE (MIN)", 15, 600),
        ("suspend_instead_of_kill", "bool", "USYPNIAJ ZAMIAST ZABIJAĆ"),
        ("block_task_manager", "bool", "BLOKUJ MENEDŻER ZADAŃ"),
        ("block_hotkeys", "bool", "BLOKUJ SKRÓTY SYSTEMOWE"),
        (
            "taskbar_mode",
            "choice",
            "PASEK ZADAŃ W SESJI",
            (
                ("filtered", "TYLKO DOZWOLONE APLIKACJE"),
                ("hide", "UKRYJ CAŁKIEM"),
                ("keep", "NIE RUSZAJ"),
            ),
        ),
        ("black_wallpaper", "bool", "CZARNA TAPETA"),
        ("hide_desktop_icons", "bool", "UKRYJ IKONY PULPITU"),
        ("mute_toasts", "bool", "WYCISZ POWIADOMIENIA"),
        ("mute_sound", "bool", "WYCISZ DŹWIĘK (BEST-EFFORT)"),
        ("prevent_sleep", "bool", "BLOKUJ USPIONY TRYB"),
        ("emergency_hold_key", "bool", "AWARYJNE WYJŚCIE PRZYTRZYMANIEM"),
        ("exit_cooldown_seconds", "int", "CISZA PO WYJŚCIU (S)", 0, 600),
        ("panic_requires_pin", "bool", "WYJŚCIE AWARYJNE WYMAGA PIN-U"),
    ],
    "network": [
        ("mode", "choice", "TRYB SIECI", (("allowlist", "BIAŁA LISTA"), ("blocklist", "CZARNA LISTA"), ("off", "WYŁĄCZONA"))),
        ("proxy_port", "int", "PORT PROXY", 1024, 65535),
        ("block_browser_direct", "bool", "BLOKUJ POŁĄCZENIA POZA PROXY"),
        ("block_non_allowlisted_dns", "bool", "BLOKUJ DNS SPOZA LISTY"),
        ("blocklist_mode", "choice", "CZARNA LISTA DZIAŁA", (("study_only", "TYLKO NAUKA"), ("study_and_break", "NAUKA I PRZERWA"))),
    ],
    "ui": [
        ("language", "choice", "JĘZYK", (("pl", "POLSKI"), ("en", "ENGLISH"))),
        ("sound_enabled", "bool", "DŹWIĘK INTERFEJSU"),
        ("sound_volume", "int", "GŁOŚNOŚĆ", 0, 100),
        ("breathing_break", "bool", "ODDECHOWY EKRAN PRZERWY"),
        ("start_minimized", "bool", "START ZMINIMALIZOWANY"),
        ("tray_icon", "bool", "IKONA W ZASOBNIKU SYSTEMOWYM"),
        ("confirm_before_start", "bool", "POTWIERDZAJ START SESJI"),
        ("event_log_visible", "bool", "POKAZUJ DZIENNIK ZDARZEŃ"),
        ("color_app_icons", "bool", "KOLOROWE IKONY APLIKACJI"),
        ("app_icon_size", "int", "ROZMIAR IKONY APLIKACJI (PX)", 24, 64),
    ],
    "system": [
        ("autostart", "bool", "URUCHAMIAJ Z SYSTEMEM"),
        ("launch_helper_on_start", "bool", "URUCHAMIAJ HELPER (UAC)"),
        ("restore_on_boot", "bool", "PRZYWRACAJ SYSTEM PO STARCIE"),
        ("dry_run", "bool", "TRYB PRÓBNY (BEZ ZMIAN W SYSTEMIE)"),
        ("safe_mode", "bool", "TRYB BEZPIECZNY"),
    ],
}

APPLY_LABELS = {
    "session": "SESJA",
    "economy": "EKONOMIA",
    "lock": "BLOKADA",
    "network": "SIEĆ",
    "ui": "WYGLĄD",
    "system": "SYSTEM",
}

APPS_EMPTY = "Brak profili aplikacji"
APPS_EMPTY_DETAIL = (
    "Kliknij WYBIERZ Z KATALOGU…, żeby wybrać program z listy, albo "
    "wpisz nazwę procesu (np. chrome.exe) i wybierz, czy ma być dozwolony."
)
SITES_EMPTY = "Brak profili stron"
SITES_EMPTY_DETAIL = (
    "Kliknij WYBIERZ Z KATALOGU…, żeby dodać host z katalogu, albo "
    "wpisz host (np. *.wikipedia.org) i wybierz kategorię."
)
#: Katalog aplikacji buduje sie w tle - zamiast pustej listy pokazujemy komunikat.
CATALOG_BUILDING = (
    "Katalog aplikacji buduje się w tle — spróbuj za chwilę albo wpisz wartość ręcznie."
)
#: Kategorie katalogu stron, ktorych nie ma w combo - przyblizamy do najblizszej.
SITE_CATEGORY_FALLBACK = {
    "szkola": "nauka",
    "jezyki": "nauka",
    "muzyka": "nauka",
    "wiadomosci": "rozrywka",
    "wlasne": "wlasne",
    "wlasne_blok": "wlasne_blok",
}
#: Rodzaje dopasowania w combo profilu aplikacji.
APP_KINDS = (("name", "NAZWA"), ("path", "ŚCIEŻKA"), ("signature", "SYGNATURA"))


class SettingsScreen(Screen):
    """Centralne ustawienia; zapis idzie jednym sygnalem `request_settings`."""

    TITLE = "USTAWIENIA"

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        self._status = labels.caption("ZMIANY ZAPISZ NA DOLE")
        self.header_widget(self._status)

        self._widgets: dict[str, dict[str, tuple[str, QWidget]]] = {}
        #: Katalog aplikacji pobierany raz (puste = trzeba jeszcze poprosić kontroler).
        self._app_catalog: list[dict] = []
        #: Katalog stron per rodzaj ("STUDY"/"BLOCKED") - tez pobierany raz.
        self._site_catalog: dict[str, dict] = {}
        self._awaiting_app_catalog = False
        self._awaiting_site_catalog = False
        self._tabs = QTabWidget()
        self._tabs.addTab(self.scroll(self._tab_session()), TAB_TITLES[0])
        self._tabs.addTab(self.scroll(self._tab_apps()), TAB_TITLES[1])
        self._tabs.addTab(self.scroll(self._tab_sites()), TAB_TITLES[2])
        self._tabs.addTab(self.scroll(self._tab_economy()), TAB_TITLES[3])
        self._tabs.addTab(self.scroll(self._tab_lock()), TAB_TITLES[4])
        self._tabs.addTab(self.scroll(self._tab_ui()), TAB_TITLES[5])
        self._tabs.addTab(self.scroll(self._tab_data()), TAB_TITLES[6])
        self._tabs.addTab(self.scroll(self._tab_system()), TAB_TITLES[7])
        self.root.addWidget(self._tabs, 1)

        actions = self.action_bar()
        save = PrimaryButton("ZAPISZ USTAWIENIA")
        save.clicked.connect(self._save)
        reset = GhostButton("PRZYWRÓĆ DOMYŚLNE")
        reset.clicked.connect(lambda: self.request_action.emit("reset_settings", {}))
        self._pin_status = labels.caption("PIN: NIE USTAWIONO")
        actions.addWidget(save)
        actions.addWidget(reset)
        actions.addStretch(1)
        actions.addWidget(self._pin_status)

    # ------------------------------------------------------- fabryki zakladek
    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        """Strona zakladki z marginesami wspolnymi dla calego UI."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(*TAB_MARGINS)
        layout.setSpacing(TAB_SPACING)
        return page, layout

    def _form(self, title: str, subtitle: str, group: str, specs: list[tuple]) -> Card:
        card = Card(title, subtitle)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self._widgets.setdefault(group, {})
        for spec in specs:
            name, kind = spec[0], spec[1]
            label = labels.field(str(spec[2]))
            label.setMinimumWidth(LABEL_WIDTH)
            label.setMaximumWidth(LABEL_WIDTH)
            widget = self._make_widget(kind, spec[3:])
            form.addRow(label, widget)
            self._widgets[group][name] = (kind, widget)
        card.add_layout(form)
        return card

    @staticmethod
    def _make_widget(kind: str, extra: tuple) -> QWidget:
        if kind == "int":
            spin = QSpinBox()
            spin.setRange(int(extra[0]), int(extra[1]))
            spin.setMinimumWidth(120)
            spin.setMaximumWidth(200)
            spin.setMinimumHeight(SIZES["input"])
            return spin
        if kind == "float":
            spin = QDoubleSpinBox()
            spin.setRange(float(extra[0]), float(extra[1]))
            spin.setSingleStep(float(extra[2]))
            spin.setDecimals(int(extra[3]))
            spin.setMinimumWidth(120)
            spin.setMaximumWidth(200)
            spin.setMinimumHeight(SIZES["input"])
            return spin
        if kind == "bool":
            return Toggle("")
        if kind == "choice":
            combo = QComboBox()
            for value, text in extra[0]:
                combo.addItem(str(text), str(value))
            combo.setMinimumWidth(220)
            combo.setMaximumWidth(340)
            combo.setMinimumHeight(SIZES["input"])
            return combo
        field = QLineEdit()
        field.setMinimumHeight(SIZES["input"])
        return field

    @staticmethod
    def _field_row(*widgets: QWidget, stretch: dict | None = None) -> QHBoxLayout:
        """Rzad pol formularza z jednym rytmem odstepow i wysokosci."""
        row = QHBoxLayout()
        row.setSpacing(8)
        for index, widget in enumerate(widgets):
            row.addWidget(widget, (stretch or {}).get(index, 0))
        return row

    def _tab_session(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(self._form("SESJA", "POMODORO, PRZERWY, START", "session", FIELD_SPECS["session"]))
        layout.addStretch(1)
        return page

    def _tab_apps(self) -> QWidget:
        page, layout = self._page()
        card = Card("PROFILE APLIKACJI", "CO MOŻE DZIAŁAĆ, A CO JEST BLOKOWANE")
        self._apps_count = labels.caption("WPISÓW: 0")
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self._apps_count)
        header.addStretch(1)
        browse = GhostButton("WYBIERZ Z KATALOGU…")
        browse.clicked.connect(self._open_app_picker)
        pick_file = GhostButton("WSKAŻ PLIK .EXE…")
        pick_file.clicked.connect(self._pick_app_exe)
        header.addWidget(browse)
        header.addWidget(pick_file)
        card.add_layout(header)
        self._apps_empty = EmptyState(APPS_EMPTY, APPS_EMPTY_DETAIL)
        card.add(self._apps_empty)
        self._apps_list = QListWidget()
        self._apps_list.setMinimumHeight(200)
        self._apps_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._apps_list.setWordWrap(False)
        self._apps_list.setAlternatingRowColors(False)
        self._apps_list.setVisible(False)
        card.add(self._apps_list)
        card.body.setStretchFactor(self._apps_list, 1)
        card.add(labels.caption("RĘCZNIE: NAZWA + WARTOŚĆ, POTEM DODAJ / ZAPISZ"))
        self._app_label = QLineEdit()
        self._app_label.setPlaceholderText("nazwa, np. Chrome")
        self._app_value = QLineEdit()
        self._app_value.setPlaceholderText("wartość, np. chrome.exe")
        self._app_kind = QComboBox()
        for value, text in APP_KINDS:
            self._app_kind.addItem(text, value)
        self._app_category = QComboBox()
        for value, text in (("STUDY", "DOZWOLONA (NAUKA)"), ("BLOCKED", "BLOKOWANA")):
            self._app_category.addItem(text, value)
        card.add_layout(self._field_row(self._app_label, self._app_value, self._app_kind, self._app_category,
                                        stretch={0: 2, 1: 3}))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        add = GhostButton("DODAJ / ZAPISZ")
        add.clicked.connect(self._save_app)
        remove = GhostButton("USUŃ ZAZNACZONE")
        remove.clicked.connect(self._delete_app)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        card.add_layout(buttons)
        card.set_hint("Dopasowanie po nazwie procesu działa szybko; po sygnaturze — odpornej na zmianę nazwy pliku.")
        layout.addWidget(card, 1)
        return page

    def _tab_sites(self) -> QWidget:
        page, layout = self._page()
        card = Card("PROFILE STRON", "HOSTY DOZWOLONE I BLOKOWANE")
        self._sites_count = labels.caption("WPISÓW: 0")
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self._sites_count)
        header.addStretch(1)
        browse = GhostButton("WYBIERZ Z KATALOGU…")
        browse.clicked.connect(self._open_site_picker)
        header.addWidget(browse)
        card.add_layout(header)
        self._sites_empty = EmptyState(SITES_EMPTY, SITES_EMPTY_DETAIL)
        card.add(self._sites_empty)
        self._sites_list = QListWidget()
        self._sites_list.setMinimumHeight(200)
        self._sites_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._sites_list.setWordWrap(False)
        self._sites_list.setAlternatingRowColors(False)
        self._sites_list.setVisible(False)
        card.add(self._sites_list)
        card.body.setStretchFactor(self._sites_list, 1)
        self._site_subject = QLineEdit()
        self._site_subject.setPlaceholderText("przedmiot, np. matematyka")
        subject_row = QHBoxLayout()
        subject_row.setSpacing(8)
        subject_row.addWidget(self._site_subject, 1)
        suggest = GhostButton("PODPOWIEDZI DLA PRZEDMIOTU")
        suggest.clicked.connect(self._suggest_sites)
        subject_row.addWidget(suggest)
        card.add_layout(subject_row)
        card.add(labels.caption("RĘCZNIE: HOST + OPIS, POTEM DODAJ / ZAPISZ"))
        self._site_host = QLineEdit()
        self._site_host.setPlaceholderText("host, np. *.wikipedia.org")
        self._site_label = QLineEdit()
        self._site_label.setPlaceholderText("opis (opcjonalnie)")
        self._site_category = QComboBox()
        self._site_category.setEditable(True)
        self._site_category.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for value, text in (
            ("wlasne", "DOZWOLONA: WŁASNA"),
            ("nauka", "DOZWOLONA: NAUKA"),
            ("matura", "DOZWOLONA: MATURA"),
            ("kod", "DOZWOLONA: PROGRAMOWANIE"),
            ("narzedzia", "DOZWOLONA: NARZĘDZIA"),
            ("wlasne_blok", "BLOKOWANA: WŁASNA"),
            ("rozrywka", "BLOKOWANA: ROZRYWKA"),
            ("spolecznosc", "BLOKOWANA: SOCIAL MEDIA"),
            ("zakupy", "BLOKOWANA: ZAKUPY"),
        ):
            self._site_category.addItem(text, value)
        card.add_layout(self._field_row(self._site_host, self._site_label, self._site_category, stretch={0: 3, 1: 2, 2: 3}))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        add = GhostButton("DODAJ / ZAPISZ")
        add.clicked.connect(self._save_site)
        remove = GhostButton("USUŃ ZAZNACZONE")
        remove.clicked.connect(self._delete_site)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        card.add_layout(buttons)
        card.set_hint("Wpisy z gwiazdką (*.example.com) obejmują subdomeny. Domeny systemowe z listy bezpiecznej nigdy nie są blokowane.")
        layout.addWidget(card, 1)
        return page

    def _tab_economy(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(self._form("EKONOMIA", "ILE WOLNEGO ZA ILE NAUKI", "economy", FIELD_SPECS["economy"]))
        card = Card("KOREKTA BANKU", "RĘCZNA ZMIANA SALDA (ŚCIEŻKA AUDYTOWANA)")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._adjust_minutes = QSpinBox()
        self._adjust_minutes.setRange(-600, 600)
        self._adjust_minutes.setValue(15)
        self._adjust_minutes.setMinimumWidth(120)
        self._adjust_minutes.setMinimumHeight(SIZES["input"])
        self._adjust_note = QLineEdit()
        self._adjust_note.setPlaceholderText("powód korekty")
        plus = GhostButton("DODAJ DO BANKU")
        plus.clicked.connect(lambda: self._adjust(1))
        minus = GhostButton("ZABIERZ Z BANKU")
        minus.clicked.connect(lambda: self._adjust(-1))
        row.addWidget(self._adjust_minutes)
        row.addWidget(self._adjust_note, 1)
        row.addWidget(plus)
        row.addWidget(minus)
        card.add_layout(row)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _tab_lock(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(self._form("BLOKADA", "CO ZNIKA W TRAKCIE SESJI", "lock", FIELD_SPECS["lock"]))
        layout.addWidget(self._form("SIEĆ", "PROXY, HOSTS, ZAPORA", "network", FIELD_SPECS["network"]))
        card = Card("NARZĘDZIA BLOKADY")
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        pin = GhostButton("USTAW / ZMIEŃ PIN")
        pin.clicked.connect(lambda: self.request_action.emit("open_pin_dialog", {}))
        test = GhostButton("TEST NA SUCHO")
        test.clicked.connect(lambda: self.request_action.emit("test_lock", {"dry_run": True}))
        restore = DangerButton("PRZYWRÓĆ SYSTEM TERAZ")
        restore.clicked.connect(lambda: self.request_action.emit("restore_everything", {"reason": "ui"}))
        buttons.addWidget(pin)
        buttons.addWidget(test)
        buttons.addStretch(1)
        buttons.addWidget(restore)
        card.add_layout(buttons)
        card.set_hint("Test na sucho nic nie zmienia w systemie — pokazuje, co zostałoby zrobione.")
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _tab_ui(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(self._form("WYGLĄD", "MONOCHROMATYCZNY INTERFEJS", "ui", FIELD_SPECS["ui"]))
        layout.addStretch(1)
        return page

    def _tab_data(self) -> QWidget:
        page, layout = self._page()
        card = Card("DANE I STATYSTYKI", "EKSPORT, KOPIE, CZYSZCZENIE")
        self._data_info = labels.hint("Brak informacji o bazie.")
        card.add(self._data_info)
        row_one = QHBoxLayout()
        row_one.setSpacing(8)
        refresh = GhostButton("ODŚWIEŻ STATYSTYKI")
        refresh.clicked.connect(lambda: self.request_action.emit("refresh_stats", {}))
        export = GhostButton("EKSPORTUJ CSV")
        export.clicked.connect(lambda: self.request_action.emit("export_stats", {"format": "csv"}))
        backup = GhostButton("KOPIA ZAPASOWA")
        backup.clicked.connect(lambda: self.request_action.emit("backup_database", {}))
        row_one.addWidget(refresh)
        row_one.addWidget(export)
        row_one.addWidget(backup)
        row_one.addStretch(1)
        card.add_layout(row_one)
        row_two = QHBoxLayout()
        row_two.setSpacing(8)
        restore_backup = GhostButton("PRZYWRÓĆ Z KOPII")
        restore_backup.clicked.connect(self._pick_restore_backup)
        self._confirm_wipe = Toggle("POTWIERDZAM, ŻE CHCĘ USUNĄĆ DANE")
        wipe = DangerButton("WYCZYŚĆ DANE")
        wipe.clicked.connect(self._wipe)
        row_two.addWidget(restore_backup)
        row_two.addStretch(1)
        row_two.addWidget(self._confirm_wipe)
        row_two.addWidget(wipe)
        card.add_layout(row_two)
        card.set_hint("Kopia zapasowa zawiera bazę sesji, banku i ustawień. Czyszczenie jest nieodwracalne.")
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _tab_system(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(self._form("SYSTEM", "AUTOSTART, HELPER, TRYBY AWARYJNE", "system", FIELD_SPECS["system"]))
        card = Card("DIAGNOSTYKA")
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        diagnostics = GhostButton("SPRAWDŹ ŚRODOWISKO")
        diagnostics.clicked.connect(lambda: self.request_action.emit("diagnostics", {}))
        autostart = GhostButton("PRZEŁĄCZ AUTOSTART")
        autostart.clicked.connect(lambda: self.request_action.emit("toggle_autostart", {}))
        helper = GhostButton("URUCHOM HELPER (UAC)")
        helper.clicked.connect(lambda: self.request_action.emit("helper_start", {}))
        buttons.addWidget(diagnostics)
        buttons.addWidget(autostart)
        buttons.addWidget(helper)
        buttons.addStretch(1)
        card.add_layout(buttons)
        self._diag_info = labels.hint("Diagnostyka nie została jeszcze uruchomiona.")
        card.add(self._diag_info)
        card.set_hint(
            "Helper działa z uprawnieniami administratora — UAC może poprosić o zgodę przy starcie. "
            "Bez niego blokada procesów, sieci i skrótów nie działa (pasek zadań filtruje samo GUI)."
        )
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------ dane
    def values(self) -> dict:
        """Biezace wartosci z widgetow (grupa -> pole -> wartosc)."""
        out: dict[str, dict[str, Any]] = {}
        for group, fields in self._widgets.items():
            group_values: dict[str, Any] = {}
            for name, (kind, widget) in fields.items():
                group_values[name] = _read_widget(kind, widget)
            out[group] = group_values
        return out

    def apply_settings(self, settings: dict) -> None:
        settings = as_dict(settings)
        for group, fields in self._widgets.items():
            group_values = settings.get(group)
            if not isinstance(group_values, dict):
                continue
            for name, (kind, widget) in fields.items():
                if name in group_values:
                    _write_widget(kind, widget, group_values[name])

    def render(self, data: dict) -> None:
        settings = data.get("settings")
        if isinstance(settings, dict):
            self.apply_settings(settings)
        if data.get("apps") is not None:
            self._fill_apps(as_list(data.get("apps")))
        if data.get("sites") is not None:
            self._fill_sites(as_list(data.get("sites")))
        if data.get("has_pin") is not None or data.get("pin_set") is not None:
            has_pin = bool(data.get("has_pin") or data.get("pin_set"))
            self._pin_status.setText("PIN: USTAWIONY" if has_pin else "PIN: NIE USTAWIONO")
        info = data.get("storage")
        if isinstance(info, dict):
            self._data_info.setText(
                f"Baza: {info.get('path', '—')} · sesje: {info.get('sessions', 0)} · "
                f"wpisy dziennika: {info.get('events', 0)} · rozmiar: {info.get('size', '—')}"
            )
        if data.get("status"):
            self._status.setText(str(data["status"]))
        is_admin = bool(data.get("is_admin"))
        control_level = data.get("control_level", "PEŁNA (ADMIN)" if is_admin else "PODSTAWOWA")
        helper_state = "POŁĄCZONY" if data.get("helper_online") else "ROZŁĄCZONY"
        self._diag_info.setText(
            f"KONTROLA SYSTEMU: {control_level}  ·  HELPER: {helper_state}"
        )

    def _fill_apps(self, rows: list[dict]) -> None:
        self._apps_list.clear()
        for raw in as_list(rows):
            row = as_dict(raw)
            category = "BLOK" if str(row.get("category", "")).upper() in ("BLOCK", "BLOCKED") else "NAUKA"
            value = str(row.get("match_value") or "—")
            kind = str(row.get("match_kind") or "—")
            text = f"{row.get('label') or value}  ·  {kind}: {value}  ·  [{category}]"
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setData(Qt.ItemDataRole.UserRole, row.get("id"))
            self._apps_list.addItem(item)
        count = self._apps_list.count()
        self._apps_count.setText(f"WPISÓW: {count}")
        empty = count == 0
        self._apps_empty.setVisible(empty)
        self._apps_list.setVisible(not empty)

    def _fill_sites(self, rows: list[dict]) -> None:
        self._sites_list.clear()
        for raw in as_list(rows):
            row = as_dict(raw)
            raw_cat = str(row.get("category", "")).strip()
            cat_upper = raw_cat.upper()
            is_blocked = cat_upper in ("BLOCK", "BLOCKED", "WLASNE_BLOK", "ROZRYWKA", "SPOLECZNOSC", "WIADOMOSCI", "ZAKUPY")
            kind_tag = "BLOK" if is_blocked else "NAUKA"
            label = f"  ·  {row.get('label')}" if row.get("label") else ""
            cat_disp = f"  [{raw_cat}]" if raw_cat and raw_cat not in ("STUDY", "BLOCKED") else ""
            text = f"{row.get('host')}{label}  ·  {kind_tag}{cat_disp}"
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setData(Qt.ItemDataRole.UserRole, row.get("id"))
            self._sites_list.addItem(item)
        count = self._sites_list.count()
        self._sites_count.setText(f"WPISÓW: {count}")
        empty = count == 0
        self._sites_empty.setVisible(empty)
        self._sites_list.setVisible(not empty)

    # ------------------------------------------------------------------ akcje
    def _save(self) -> None:
        payload = self.values()
        self.request_settings.emit(payload)
        self._status.setText("ZAPISANO USTAWIENIA")
        self.show_toast("Ustawienia zapisane.")

    def _save_app(self) -> None:
        value = self._app_value.text().strip()
        if not value:
            self.show_toast("Podaj nazwę procesu lub ścieżkę.")
            return
        self.request_action.emit(
            "save_app",
            {
                "label": self._app_label.text().strip() or value,
                "match_kind": self._app_kind.currentData(),
                "match_value": value,
                "category": self._app_category.currentData(),
            },
        )
        self._app_label.clear()
        self._app_value.clear()

    def _delete_app(self) -> None:
        item = self._apps_list.currentItem()
        if item is None:
            self.show_toast("Zaznacz profil do usunięcia.")
            return
        self.request_action.emit("delete_app", {"id": item.data(Qt.ItemDataRole.UserRole)})

    def _save_site(self) -> None:
        host = self._site_host.text().strip()
        if not host:
            self.show_toast("Podaj host.")
            return
        cat_data = self._site_category.currentData()
        cat_text = self._site_category.currentText().strip()
        category = cat_data if cat_data else cat_text
        self.request_action.emit(
            "save_site",
            {
                "host": host,
                "label": self._site_label.text().strip(),
                "category": category or "wlasne",
            },
        )
        self._site_host.clear()
        self._site_label.clear()

    def _delete_site(self) -> None:
        item = self._sites_list.currentItem()
        if item is None:
            self.show_toast("Zaznacz profil do usunięcia.")
            return
        self.request_action.emit("delete_site", {"id": item.data(Qt.ItemDataRole.UserRole)})

    # ---------------------------------------------------- pickery z katalogu
    def _open_app_picker(self) -> None:
        """Otwiera modalny wybor aplikacji (katalog pobierany raz)."""
        if not self._app_catalog:
            self._awaiting_app_catalog = True
            self._status.setText("POBIERAM KATALOG APLIKACJI…")
            self.request_action.emit("catalog_apps", {"query": ""})
            return
        dialog = self._make_app_picker()
        if not dialog.exec():
            return
        picked = dialog.picked_entries()
        if picked:
            self._apply_app_picks(picked)
            return
        manual = dialog.manual_text()
        if manual:
            self._fill_app_manual(manual)

    def _make_app_picker(self) -> CatalogPicker:
        """Buduje okno wyboru z aktualnego katalogu (nazwa + proces + źródło)."""
        dialog = CatalogPicker(
            "WYBIERZ APLIKACJE",
            "ZAZNACZ JEDNĄ ALBO WIELE (CTRL/SHIFT), POTEM DODAJ",
            self,
            multi=True,
            search_placeholder="SZUKAJ NAZWY LUB PROCESU…",
        )
        entries: list[dict] = []
        for raw in as_list(self._app_catalog):
            row = as_dict(raw)
            exe = str(row.get("exe") or "").strip()
            path = str(row.get("path") or "").strip()
            value = exe or path
            if not value:
                continue
            name = str(row.get("name") or value).strip()
            source = str(row.get("source") or "").strip()
            detail = "  ·  ".join(part for part in (exe, source) if part)
            entries.append(
                {
                    "key": value,
                    "label": name,
                    "detail": detail,
                    "tooltip": "\n".join(part for part in (name, value, path) if part),
                    "name": name,
                    "exe": exe,
                    "path": path,
                    "source": source,
                }
            )
        dialog.set_entries(entries)
        return dialog

    def _apply_app_picks(self, entries: list[dict]) -> None:
        """Wybór z katalogu: jeden wpis wypełnia pola, wiele zapisuje od razu."""
        rows = [as_dict(entry) for entry in as_list(entries)]
        rows = [row for row in rows if str(row.get("exe") or row.get("path") or "").strip()]
        if not rows:
            self.show_toast("Nie wybrano żadnej aplikacji.")
            return
        category = self._app_category.currentData()
        if len(rows) > 1:
            for row in rows:
                value = str(row.get("exe") or row.get("path")).strip()
                self.request_action.emit(
                    "save_app",
                    {
                        "label": str(row.get("name") or value),
                        "match_kind": "name" if row.get("exe") else "path",
                        "match_value": value,
                        "category": category,
                    },
                )
            self.show_toast(f"DODANO APLIKACJE: {len(rows)}")
            return
        row = rows[0]
        value = str(row.get("exe") or row.get("path")).strip()
        self._select_app_kind("name" if row.get("exe") else "path")
        self._app_label.setText(str(row.get("name") or value))
        self._app_value.setText(value)
        self.show_toast("WYBRANO Z KATALOGU — SPRAWDŹ DOPASOWANIE I KLIKNIJ DODAJ / ZAPISZ.")

    def _fill_app_manual(self, text: str) -> None:
        """Ścieżka 'wpisz ręcznie' z okna pickera: wypełnia wartość dopasowania."""
        value = str(text or "").strip()
        if not value:
            return
        self._app_value.setText(value)
        if not self._app_label.text().strip():
            self._app_label.setText(value)
        self._select_app_kind("path" if os.path.sep in value or ":" in value else "name")
        self.show_toast("WPISANO RĘCZNIE — SPRAWDŹ DOPASOWANIE I KLIKNIJ DODAJ / ZAPISZ.")

    def _select_app_kind(self, kind: str) -> None:
        index = self._app_kind.findData(str(kind))
        if index >= 0:
            self._app_kind.setCurrentIndex(index)

    def _pick_app_exe(self) -> None:
        """Alternatywa dla katalogu: wskazanie pliku .exe z dysku."""
        chosen, _filter = QFileDialog.getOpenFileName(
            self,
            "Wybierz program do profilu",
            "",
            "Programy (*.exe);;Wszystkie pliki (*)",
        )
        if not chosen:
            self.show_toast("Nie wybrano pliku.")
            return
        stem = os.path.splitext(os.path.basename(str(chosen)))[0]
        self._app_value.setText(str(chosen))
        if not self._app_label.text().strip():
            self._app_label.setText(stem)
        self._select_app_kind("path")
        self.show_toast("WSKAZANO PLIK .EXE — DOPASOWANIE PO ŚCIEŻCE.")

    def _open_site_picker(self) -> None:
        """Otwiera wybor stron z katalogu (pobieranego raz, oba rodzaje)."""
        missing = [kind for kind in ("STUDY", "BLOCKED") if not self._site_catalog.get(kind)]
        if missing:
            self._awaiting_site_catalog = True
            self._status.setText("POBIERAM KATALOG STRON…")
            for kind in missing:
                self.request_action.emit("catalog_sites", {"kind": kind, "query": ""})
            return
        entries = self._site_entries()
        if not entries:
            self.show_toast("Katalog stron jest pusty — wpisz host ręcznie.")
            return
        dialog = self._make_site_picker(entries)
        if not dialog.exec():
            return
        picked = dialog.picked_entries()
        if picked:
            self._apply_site_picks(picked)
            return
        manual = dialog.manual_text()
        if manual:
            self._fill_site_manual(manual)

    def _site_entries(self) -> list[dict]:
        """Katalog stron pogrupowany po kategoriach (oba rodzaje razem)."""
        entries: list[dict] = []
        for kind in ("STUDY", "BLOCKED"):
            payload = as_dict(self._site_catalog.get(kind))
            suffix = "DOZWOLONE" if kind == "STUDY" else "BLOKOWANE"
            for raw_cat in as_list(payload.get("categories")):
                category = as_dict(raw_cat)
                key = str(category.get("key") or "").strip()
                sites = [as_dict(site) for site in as_list(category.get("sites"))]
                if not sites:
                    continue
                label = str(category.get("label") or key or suffix).upper()
                entries.append({"kind": "group", "label": f"{label}  ·  {suffix}"})
                for site in sites:
                    host = str(site.get("host") or "").strip()
                    if not host:
                        continue
                    name = str(site.get("name") or host).strip()
                    note = str(site.get("note") or "").strip()
                    entries.append(
                        {
                            "kind": "item",
                            "key": host,
                            "label": name,
                            "detail": "  ·  ".join(part for part in (host, note) if part),
                            "tooltip": "\n".join(part for part in (name, host, note) if part),
                            "host": host,
                            "name": name,
                            "note": note,
                            "category": key or ("wlasne_blok" if kind == "BLOCKED" else "wlasne"),
                            "kind_hint": kind,
                        }
                    )
        return entries

    def _entries_for_hosts(self, hosts: list[str]) -> list[dict]:
        """Lokalne podpowiedzi dla przedmiotu: hosty -> wpisy pickera."""
        entries: list[dict] = []
        for raw_host in as_list(hosts):
            host = str(raw_host or "").strip()
            if not host:
                continue
            site = sitecatalog.find(host)
            name = site.name if site is not None else host
            category = site.category if site is not None else "wlasne"
            kind = site.kind if site is not None else "STUDY"
            entries.append(
                {
                    "kind": "item",
                    "key": host,
                    "label": str(name),
                    "detail": host,
                    "tooltip": f"{name}\n{host}",
                    "host": host,
                    "name": str(name),
                    "note": "",
                    "category": category,
                    "kind_hint": kind,
                }
            )
        return entries

    def _make_site_picker(self, entries: list[dict], title: str = "WYBIERZ STRONY") -> CatalogPicker:
        dialog = CatalogPicker(
            title,
            "ZAZNACZ JEDNĄ ALBO WIELE (CTRL/SHIFT), POTEM DODAJ",
            self,
            multi=True,
            search_placeholder="SZUKAJ NAZWY LUB HOSTA…",
        )
        dialog.set_entries(entries)
        return dialog

    def _apply_site_picks(self, entries: list[dict]) -> None:
        """Wybór z katalogu: jeden wpis wypełnia pola, wiele zapisuje od razu."""
        rows = [as_dict(entry) for entry in as_list(entries)]
        rows = [row for row in rows if str(row.get("host") or "").strip()]
        if not rows:
            self.show_toast("Nie wybrano żadnej strony.")
            return
        if len(rows) > 1:
            for row in rows:
                self.request_action.emit(
                    "save_site",
                    {
                        "host": str(row.get("host")).strip(),
                        "label": str(row.get("name") or ""),
                        "category": str(row.get("category") or "wlasne"),
                    },
                )
            self.show_toast(f"DODANO STRONY: {len(rows)}")
            return
        row = rows[0]
        self._site_host.setText(str(row.get("host")).strip())
        self._site_label.setText(str(row.get("name") or ""))
        self._select_site_category(str(row.get("category") or ""), str(row.get("kind_hint") or ""))
        self.show_toast("WYBRANO Z KATALOGU — SPRAWDŹ KATEGORIĘ I KLIKNIJ DODAJ / ZAPISZ.")

    def _select_site_category(self, category: str, kind_hint: str = "") -> None:
        """Ustawia kategorię w combo; brakującą przybliża do najbliższej."""
        key = str(category or "").strip().lower()
        index = self._site_category.findData(key)
        if index < 0:
            key = SITE_CATEGORY_FALLBACK.get(key, "")
        if key:
            index = self._site_category.findData(key)
        if index < 0:
            key = "wlasne_blok" if str(kind_hint).upper() == "BLOCKED" else "nauka"
            index = self._site_category.findData(key)
        if index >= 0:
            self._site_category.setCurrentIndex(index)

    def _fill_site_manual(self, text: str) -> None:
        """Ścieżka 'wpisz ręcznie' z okna pickera: wypełnia host."""
        host = str(text or "").strip()
        if not host:
            return
        self._site_host.setText(host)
        self.show_toast("WPISANO HOST RĘCZNIE — WYBIERZ KATEGORIĘ I KLIKNIJ DODAJ / ZAPISZ.")

    def _suggest_sites(self) -> None:
        """Lokalne podpowiedzi dla wpisanego przedmiotu (bez nowych akcji)."""
        subject = self._site_subject.text().strip()
        if not subject:
            self.show_toast("Wpisz przedmiot, np. matematyka.")
            return
        hosts = sitecatalog.suggest_for(subject)
        if not hosts:
            self.show_toast("Brak podpowiedzi dla tego przedmiotu — wybierz z katalogu.")
            return
        dialog = self._make_site_picker(
            self._entries_for_hosts(hosts), title=f"PODPOWIEDZI: {subject.upper()}"
        )
        if not dialog.exec():
            return
        picked = dialog.picked_entries()
        if picked:
            self._apply_site_picks(picked)
            return
        manual = dialog.manual_text()
        if manual:
            self._fill_site_manual(manual)

    def _adjust(self, sign: int) -> None:
        minutes = abs(int(self._adjust_minutes.value())) * (1 if sign >= 0 else -1)
        if minutes == 0:
            self.show_toast("Korekta nie może być zerowa.")
            return
        self.request_action.emit(
            "adjust_bank", {"minutes": minutes, "note": self._adjust_note.text().strip() or "korekta ręczna"}
        )
        self._adjust_note.clear()

    def _wipe(self) -> None:
        if not self._confirm_wipe.isChecked():
            self.show_toast("Najpierw zaznacz potwierdzenie.")
            return
        self.request_action.emit("wipe_data", {"confirm": True})

    def _pick_restore_backup(self) -> None:
        """Wybór pliku kopii przed przywróceniem (wcześniej wysyłano pusty payload)."""
        directory = ""
        try:
            from ... import paths

            directory = str(paths.backup_dir())
        except Exception:  # noqa: BLE001
            directory = ""
        chosen, _filter = QFileDialog.getOpenFileName(
            self,
            "Wybierz kopię bazy Ciszy",
            directory,
            "Kopie Ciszy (*.db);;Wszystkie pliki (*)",
        )
        if not chosen:
            self.show_toast("Nie wybrano pliku kopii.")
            return
        self.request_action.emit("restore_database", {"path": chosen})

    # ------------------------------------------------------------- wyniki akcji
    def on_action_result(self, action: str, result: dict) -> None:
        """Wyniki akcji: katalog pickerów oraz `SPRAWDŹ ŚRODOWISKO` (diagnostics)."""
        if action == "catalog_apps":
            self._receive_app_catalog(as_dict(result))
            return
        if action == "catalog_sites":
            self._receive_site_catalog(as_dict(result))
            return
        if action in ("save_app", "save_site"):
            if as_dict(result).get("ok"):
                self._status.setText("ZAPISANO PROFIL — LISTA ODŚWIEŻY SIĘ PO ZAPISIE")
            return
        if action in ("delete_app", "delete_site"):
            if as_dict(result).get("ok"):
                self._status.setText("USUNIĘTO PROFIL — LISTA ODŚWIEŻY SIĘ PO ZAPISIE")
            return
        if action != "diagnostics":
            return
        data = as_dict(result)
        if not data.get("ok"):
            self._diag_info.setText("Nie udało się sprawdzić środowiska.")
            self._status.setText("DIAGNOSTYKA: BŁĄD")
            return
        helper = "online" if data.get("helper_online") else f"offline ({data.get('helper_error') or 'brak'})"
        parts = [
            f"wersja {data.get('wersja', '?')}",
            f"Python {data.get('python', '?')}",
            f"helper: {helper}",
            f"administrator: {'tak' if data.get('admin') else 'nie'}",
            f"autostart: {data.get('autostart', '?')}",
            f"bank: {as_int(data.get('bank_min'))} min",
            f"sesje: {as_int(data.get('sesji'))}",
        ]
        self._diag_info.setText(" · ".join(parts))
        self._status.setText("DIAGNOSTYKA: OK")

    # --------------------------------------------------- wyniki katalogow
    def _receive_app_catalog(self, data: dict) -> None:
        """Zapisuje katalog aplikacji raz i otwiera picker, gdy o niego proszono."""
        if not data.get("ok"):
            if self._awaiting_app_catalog:
                self._awaiting_app_catalog = False
                self._status.setText("KATALOG APLIKACJI: BŁĄD")
                self.show_toast("Nie udało się pobrać katalogu aplikacji.")
            return
        query = str(data.get("query") or "").strip()
        apps = as_list(data.get("apps"))
        if not query:
            self._app_catalog = apps
        if not self._awaiting_app_catalog:
            return
        self._awaiting_app_catalog = False
        if not apps:
            self._status.setText("KATALOG APLIKACJI: PUSTY")
            self.show_toast(CATALOG_BUILDING)
            return
        self._status.setText(f"KATALOG APLIKACJI: {len(apps)}")
        # Okno otwieramy po powrocie do petli zdarzen - inaczej modalne exec()
        # wstrzymuje rozsyłanie wyniku do pozostalych ekranow (np. kreatora).
        QTimer.singleShot(0, self._open_app_picker)

    def _receive_site_catalog(self, data: dict) -> None:
        """Zapisuje katalog stron per rodzaj (STUDY/BLOCKED); picker otwiera przycisk."""
        kind = str(data.get("kind") or "").upper()
        if kind not in ("STUDY", "BLOCKED"):
            return
        query = str(data.get("query") or "").strip()
        if data.get("ok") and not query:
            self._site_catalog[kind] = data
        if not self._awaiting_site_catalog:
            return
        self._awaiting_site_catalog = False
        if self._site_entries():
            self._status.setText("KATALOG STRON GOTOWY — KLIKNIJ WYBIERZ Z KATALOGU…")
        else:
            self._status.setText("KATALOG STRON: PUSTY")
            self.show_toast("Katalog stron jest pusty — wpisz host ręcznie.")


def _read_widget(kind: str, widget: QWidget):
    if kind == "int":
        return int(widget.value())  # type: ignore[attr-defined]
    if kind == "float":
        return float(widget.value())  # type: ignore[attr-defined]
    if kind == "bool":
        return bool(widget.isChecked())  # type: ignore[attr-defined]
    if kind == "choice":
        return str(widget.currentData())  # type: ignore[attr-defined]
    return str(widget.text())  # type: ignore[attr-defined]


def _write_widget(kind: str, widget: QWidget, value) -> None:
    if kind == "int":
        widget.setValue(int(value))  # type: ignore[attr-defined]
    elif kind == "float":
        widget.setValue(float(value))  # type: ignore[attr-defined]
    elif kind == "bool":
        widget.setChecked(bool(value))  # type: ignore[attr-defined]
    elif kind == "choice":
        index = widget.findData(str(value))  # type: ignore[attr-defined]
        if index >= 0:
            widget.setCurrentIndex(index)  # type: ignore[attr-defined]
    else:
        widget.setText(str(value))  # type: ignore[attr-defined]
