"""Kreator sesji: krok 1 — aplikacje z katalogu, krok 2 — strony z katalogu,
krok 3 — czas i tryb. Ekran nie dotyka systemu: wszystko idzie sygnalami
(`request_action`, `request_refresh_apps`, `request_start`, `request_preset`).

Wyglad: wspolny naglowek strony ze znacznikiem kroku, tresc kroku w obszarze
przewijania (nawet w niskim oknie nic sie nie ucina), a na dole tylko pasek
nawigacji WSTECZ / DALEJ. Presety mieszkaja w kroku 3, blizej zapisu planu.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..icons import ICONS
from ..theme import SIZES
from ..widgets import (
    AppTile,
    Card,
    ChipRow,
    ChoiceGroup,
    EmptyState,
    GhostButton,
    PrimaryButton,
    SiteRow,
    Toggle,
    labels,
)
from .base import Screen, as_dict, as_int, as_list, clear_layout, plan_payload

STUDY = "STUDY"
BLOCKED = "BLOCKED"

APP_FILTERS = (
    ("all", "WSZYSTKIE"),
    ("running", "OTWARTE TERAZ"),
    ("startmenu", "MENU START"),
    ("store", "SKLEP"),
)
TILE_WIDTH = 172
TILE_MIN_WIDTH = 140

APPS_EMPTY = "Brak aplikacji do pokazania"
APPS_EMPTY_DETAIL = "Zmień frazę w wyszukiwarce albo odśwież listę katalogu."
SITES_EMPTY = "Brak stron do pokazania"
SITES_EMPTY_DETAIL = "Zmień szukaną frazę albo dodaj host ręcznie."
BLOCKED_EMPTY_DETAIL = "Zmień frazę albo przywróć domyślną listę rozpraszaczy."


class ComposerScreen(Screen):
    """Trzy kroki bez logiki systemowej — wynik leci sygnałem `request_start`."""

    TITLE = "KREATOR SESJI"
    STEPS = ("APLIKACJE", "STRONY", "CZAS I TRYB")

    # ------------------------------------------------------------------ budowa
    def _build(self) -> None:
        # --------------------------------------------------------------- stan
        self._step = 0
        self._presets: list[dict] = []
        self._app_catalog: list[dict] = []
        self._app_tiles: dict[str, AppTile] = {}
        self._app_query = ""
        self._app_filter = "all"
        self._app_columns = 5
        self._app_tile_width = TILE_WIDTH
        self._apps_busy = False
        self._selected_apps: dict[str, dict] = {}
        self._site_catalog: dict[str, list[dict]] = {STUDY: [], BLOCKED: []}
        self._site_query = {STUDY: "", BLOCKED: ""}
        self._site_rows: dict[str, dict[str, SiteRow]] = {STUDY: {}, BLOCKED: {}}
        self._selected_sites: dict[str, dict] = {}
        self._site_kind = STUDY
        self._suggestions: list[dict] = []
        self._seeded = False

        self._markers = labels.caption("")
        self.header_widget(self._markers)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_apps_page())
        self._stack.addWidget(self._build_sites_page())
        self._stack.addWidget(self._build_time_page())
        self.root.addWidget(self._stack, 1)

        nav = self.action_bar()
        self._back = GhostButton("WSTECZ")
        self._back.clicked.connect(self._go_back)
        self._next = PrimaryButton("DALEJ")
        self._next.clicked.connect(self._go_next)
        nav.addStretch(1)
        nav.addWidget(self._back)
        nav.addWidget(self._next)

        self._sync_step()
        self._seed_selection()
        self._update_app_selection_ui()
        self._update_site_selection_ui()
        self._request_apps()
        self._request_sites(STUDY)

    # ------------------------------------------------------------ krok 1: aplikacje
    def _build_apps_page(self) -> QWidget:
        card = Card("APLIKACJE", "ZAZNACZ, CO MOŻE DZIAŁAĆ W TRAKCIE NAUKI")

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self._app_search = QLineEdit()
        self._app_search.setPlaceholderText("Szukaj aplikacji…")
        self._app_search.textChanged.connect(self._on_app_query_changed)
        self._app_refresh = GhostButton("ODŚWIEŻ LISTĘ")
        self._app_refresh.clicked.connect(self._refresh_apps)
        self._app_status = labels.caption("")
        self._app_count = labels.caption("")
        search_row.addWidget(self._app_search, 1)
        search_row.addWidget(self._app_refresh)
        search_row.addWidget(self._app_status)
        search_row.addWidget(self._app_count)
        card.add_layout(search_row)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self._app_filter_group = ChoiceGroup(list(APP_FILTERS))
        self._app_filter_group.changed.connect(self._on_app_filter_changed)
        self._app_clear = GhostButton("WYCZYŚĆ")
        self._app_clear.clicked.connect(self._clear_apps)
        filters.addWidget(self._app_filter_group)
        filters.addWidget(self._app_clear)
        filters.addStretch(1)
        card.add_layout(filters)

        self._app_empty = EmptyState(APPS_EMPTY, APPS_EMPTY_DETAIL, margins=(18, 14, 18, 14))
        self._app_empty.setMinimumHeight(64)
        card.add(self._app_empty)

        self._app_scroll = QScrollArea()
        self._app_scroll.setWidgetResizable(True)
        self._app_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._app_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._app_grid_host = QWidget()
        self._app_grid = QGridLayout(self._app_grid_host)
        self._app_grid.setContentsMargins(0, 0, 0, 0)
        self._app_grid.setSpacing(8)
        self._app_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._app_scroll.setWidget(self._app_grid_host)
        self._app_scroll.setMinimumHeight(100)
        self._app_scroll.setVisible(False)
        card.body.addWidget(self._app_scroll, 1)

        self._app_selected_caption = labels.caption("WYBRANO: 0")
        card.add(self._app_selected_caption)
        self._app_chips = ChipRow(columns=5)
        self._app_chips.removed.connect(self._unselect_app)
        card.add(self._app_chips)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self._app_save = GhostButton("ZAPISZ JAKO DOMYŚLNE")
        self._app_save.clicked.connect(self._save_apps)
        save_row.addWidget(self._app_save)
        self._app_save_note = labels.hint("")
        save_row.addWidget(self._app_save_note, 1)
        card.add_layout(save_row)
        card.set_hint("Cisza nie zabija procesów systemowych ani własnych. Wszystko spoza listy zostanie zablokowane.")
        return card

    # ------------------------------------------------------------- krok 2: strony
    def _build_sites_page(self) -> QWidget:
        card = Card("STRONY", "KATALOG NAUKI I ROZPRASZACZY")
        self._site_tabs = QTabWidget()
        self._site_tabs.addTab(self._build_site_tab(STUDY), "NAUKA")
        self._site_tabs.addTab(self._build_site_tab(BLOCKED), "BLOKOWANE")
        self._site_tabs.currentChanged.connect(self._on_site_tab_changed)
        card.body.addWidget(self._site_tabs, 1)

        self._site_selected_caption = labels.caption("WYBRANO: 0")
        card.add(self._site_selected_caption)
        self._site_chips = ChipRow(columns=5)
        self._site_chips.removed.connect(self._unselect_site)
        card.add(self._site_chips)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self._site_save = GhostButton("ZAPISZ JAKO DOMYŚLNE")
        self._site_save.clicked.connect(self._save_sites)
        save_row.addWidget(self._site_save)
        self._site_save_note = labels.hint("")
        save_row.addWidget(self._site_save_note, 1)
        card.add_layout(save_row)
        return card

    def _build_site_tab(self, kind: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        search = QLineEdit()
        search.setPlaceholderText("Szukaj stron…")
        search.textChanged.connect(lambda _text, k=kind: self._on_site_query_changed(k))
        status = labels.caption("")
        search_row.addWidget(search, 1)
        search_row.addWidget(status)
        layout.addLayout(search_row)
        setattr(self, f"_site_search_{kind}", search)
        setattr(self, f"_site_status_{kind}", status)

        if kind == STUDY:
            subject_row = QHBoxLayout()
            subject_row.setSpacing(8)
            self._subject = QLineEdit()
            self._subject.setPlaceholderText("przedmiot, np. matematyka — pokaż podpowiedzi")
            self._subject.textChanged.connect(self._on_subject_changed)
            subject_row.addWidget(self._subject, 1)
            layout.addLayout(subject_row)
            self._suggestion_caption = labels.caption("PODPOWIEDZI DLA PRZEDMIOTU")
            layout.addWidget(self._suggestion_caption)
            self._suggestion_chips = ChipRow(columns=3)
            self._suggestion_chips.removed.connect(self._add_suggestion)
            self._suggestion_chips.setVisible(False)
            self._suggestion_caption.setVisible(False)
            layout.addWidget(self._suggestion_chips)
            empty_detail = SITES_EMPTY_DETAIL
        else:
            tools_row = QHBoxLayout()
            tools_row.setSpacing(8)
            self._blocklist_defaults = GhostButton("PRZYWRÓĆ DOMYŚLNE BLOKADY")
            self._blocklist_defaults.setToolTip("Przywraca domyślną listę rozpraszaczy")
            self._blocklist_defaults.clicked.connect(self._restore_blocklist)
            self._blocklist_status = labels.caption("")
            tools_row.addWidget(self._blocklist_defaults)
            tools_row.addWidget(self._blocklist_status)
            tools_row.addStretch(1)
            layout.addLayout(tools_row)
            empty_detail = BLOCKED_EMPTY_DETAIL

        empty = EmptyState(SITES_EMPTY, empty_detail, margins=(18, 14, 18, 14))
        empty.setMinimumHeight(64)
        layout.addWidget(empty)
        setattr(self, f"_site_empty_{kind}", empty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)
        container_layout.addStretch(1)
        scroll.setWidget(container)
        scroll.setMinimumHeight(90)
        scroll.setVisible(False)
        layout.addWidget(scroll, 1)
        setattr(self, f"_site_container_{kind}", container)
        setattr(self, f"_site_scroll_{kind}", scroll)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(8)
        manual = QLineEdit()
        manual.setPlaceholderText("dodaj host ręcznie, np. docs.python.org")
        add = GhostButton("DODAJ HOST")
        add.clicked.connect(lambda _checked=False, k=kind: self._add_manual_host(k))
        manual.returnPressed.connect(lambda k=kind: self._add_manual_host(k))
        manual_row.addWidget(manual, 1)
        manual_row.addWidget(add)
        layout.addLayout(manual_row)
        setattr(self, f"_manual_host_{kind}", manual)
        return page

    # ------------------------------------------------------ krok 3: czas i tryb
    def _build_time_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        card = Card("CZAS I TRYB", "ILE TRWA POMODORO I CO ZAWIERA SESJA")
        self._mode = ChoiceGroup([("STUDY", "NAUKA (POMODORO)"), ("FREE", "TRYB WOLNY (BANK)")])
        card.add(labels.caption("TRYB"))
        card.add(self._mode)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self._study_minutes = self._spin(1, 240, 25)
        self._break_minutes = self._spin(1, 60, 5)
        self._long_break_minutes = self._spin(1, 120, 15)
        self._long_break_every = self._spin(0, 12, 4)
        self._free_minutes = self._spin(5, 480, 10)
        spins = (
            ("POMODORO (MIN)", self._study_minutes),
            ("PRZERWA (MIN)", self._break_minutes),
            ("DŁUGA PRZERWA (MIN)", self._long_break_minutes),
            ("DŁUGA CO ILE POMODORO", self._long_break_every),
            ("WOLNE (MIN)", self._free_minutes),
        )
        for column, (caption, spin) in enumerate(spins):
            grid.addWidget(labels.field(caption), 0, column)
            grid.addWidget(spin, 1, column)
        grid.setColumnStretch(len(spins), 1)
        card.add_layout(grid)

        self._tag = QLineEdit()
        self._tag.setPlaceholderText("tag sesji, np. matematyka")
        card.add(labels.field("TAG"))
        card.add(self._tag)
        self._goal = QPlainTextEdit()
        self._goal.setPlaceholderText("cel sesji — jedno zdanie, po co siadasz")
        self._goal.setFixedHeight(72)
        card.add(labels.field("CEL SESJI"))
        card.add(self._goal)
        self._hardcore = Toggle("TRYB HARDCORE (bez wyjścia awaryjnego)")
        card.add(self._hardcore)
        layout.addWidget(card)

        preset_card = Card("PRESET", "ZAPISZ TEN PLAN, ŻEBY WRACAĆ DO NIEGO JEDNYM KLIKNIĘCIEM")
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(220)
        self._preset_combo.currentIndexChanged.connect(self._load_preset)
        self._preset_name = QLineEdit()
        self._preset_name.setPlaceholderText("nazwa presetu")
        self._preset_name.setMinimumWidth(200)
        self._save_preset = GhostButton("ZAPISZ PRESET")
        self._save_preset.clicked.connect(self._emit_preset)
        preset_row.addWidget(self._preset_combo)
        preset_row.addWidget(self._preset_name, 1)
        preset_row.addWidget(self._save_preset)
        preset_card.add_layout(preset_row)
        preset_card.set_hint("Wczytany preset wypełnia wszystkie pola powyżej — możesz go poprawić przed startem.")
        layout.addWidget(preset_card)
        layout.addStretch(1)
        return self.scroll(content)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setMinimumWidth(96)
        spin.setMinimumHeight(SIZES["input"])
        return spin

    # ------------------------------------------------------------------ kroki
    def _sync_step(self) -> None:
        self._stack.setCurrentIndex(self._step)
        self.set_page_title(f"KROK {self._step + 1} Z 3 — {self.STEPS[self._step]}")
        self._markers.setText(" ".join("▣" if index <= self._step else "□" for index in range(3)))
        self._back.setEnabled(self._step > 0)
        self._next.setText("ROZPOCZNIJ SESJĘ" if self._step == 2 else "DALEJ")

    def _go_back(self) -> None:
        self._step = max(0, self._step - 1)
        self._sync_step()

    def _go_next(self) -> None:
        if self._step >= 2:
            self.request_start.emit(self.payload())
            return
        self._step = min(2, self._step + 1)
        self._sync_step()

    # ------------------------------------------------------- preferencje ikon
    def _settings_dict(self) -> dict:
        return as_dict(self._data.get("settings"))

    def _icon_prefs(self) -> tuple[int, bool]:
        """(rozmiar, odbarwione) z ustawien lub z danych kontrolera."""
        size = 40
        color = False
        ui = getattr(self.settings, "ui", None)
        if ui is not None:
            size = as_int(getattr(ui, "app_icon_size", 40), 40)
            color = bool(getattr(ui, "color_app_icons", False))
        data_ui = as_dict(self._settings_dict().get("ui"))
        if "app_icon_size" in data_ui:
            size = as_int(data_ui["app_icon_size"], size)
        if "color_app_icons" in data_ui:
            color = bool(data_ui["color_app_icons"])
        return max(16, min(96, size)), not color

    def _apply_icon_settings(self, settings: dict) -> None:
        ui = as_dict(as_dict(settings).get("ui"))
        if not ui:
            return
        self._data["settings"] = {**self._settings_dict(), "ui": ui}
        self._rebuild_app_grid()

    # --------------------------------------------------------------- aplikacje
    def _request_apps(self, force: bool = False) -> None:
        payload: dict = {"query": self._app_query}
        if force:
            payload["force"] = True
        self.request_action.emit("catalog_apps", payload)

    def _refresh_apps(self) -> None:
        self._set_apps_busy(True)
        self._app_status.setText("SKANUJĘ PEŁNY KATALOG…")
        # Jedno zadanie: wcześniej emitowane były dwa (request_refresh_apps
        # i request_action force) i startowały dwa równoległe skany katalogu.
        self.request_action.emit("refresh_apps", {"force": True})

    def _on_app_query_changed(self, text: str) -> None:
        self._app_query = str(text or "").strip()
        if not hasattr(self, "_app_timer"):
            self._app_timer = QTimer(self)
            self._app_timer.setSingleShot(True)
            self._app_timer.setInterval(250)
            self._app_timer.timeout.connect(self._apply_app_query)
        self._app_timer.start()

    def _apply_app_query(self) -> None:
        self._rebuild_app_grid()
        self.request_action.emit("catalog_apps", {"query": self._app_query})

    def _on_app_filter_changed(self, key: str) -> None:
        self._app_filter = str(key or "all")
        self._rebuild_app_grid()

    def _filtered_apps(self) -> list[dict]:
        query = self._app_query.lower()
        out: list[dict] = []
        for app in self._app_catalog:
            data = as_dict(app)
            if self._app_filter != "all" and str(data.get("source")) != self._app_filter:
                continue
            if query:
                haystack = f"{data.get('name', '')} {data.get('exe', '')}".lower()
                if query not in haystack:
                    continue
            out.append(data)
        return out

    @staticmethod
    def _app_key(app: dict) -> str:
        return str(app.get("exe") or app.get("name") or "").strip()

    def _app_viewport_width(self) -> int:
        width = self._app_scroll.viewport().width() if hasattr(self, "_app_scroll") else 0
        if width <= 0:
            width = max(320, self.width() - 80)
        return int(width)

    def _app_columns_for_width(self) -> int:
        return max(1, min(6, (self._app_viewport_width() + 8) // (TILE_WIDTH + 8)))

    def _app_tile_width_for(self, columns: int) -> int:
        """Kafel nigdy nie wystaje poza obszar przewijania (brak poziomego paska)."""
        available = self._app_viewport_width() - 8
        width = (available - max(0, columns - 1) * 8) // max(1, columns)
        return max(TILE_MIN_WIDTH, min(TILE_WIDTH, int(width)))

    def _rebuild_app_grid(self) -> None:
        if not hasattr(self, "_app_grid"):
            return
        clear_layout(self._app_grid)
        self._app_tiles = {}
        apps = self._filtered_apps()
        icon_size, gray = self._icon_prefs()
        columns = self._app_columns_for_width()
        tile_width = self._app_tile_width_for(columns)
        self._app_columns = columns
        self._app_tile_width = tile_width
        for index, app in enumerate(apps[:300]):
            key = self._app_key(app)
            if not key:
                continue
            tile = AppTile(
                key,
                str(app.get("name") or key),
                str(app.get("exe") or ""),
                ICONS.pixmap(str(app.get("icon_path") or app.get("path") or ""), icon_size, gray),
                icon_size,
                width=tile_width,
            )
            tile.set_selected(key in self._selected_apps)
            tile.clicked.connect(self._toggle_app)
            self._app_grid.addWidget(tile, index // columns, index % columns)
            self._app_tiles[key] = tile
        self._app_empty.setVisible(not apps)
        self._app_scroll.setVisible(bool(apps))
        self._app_count.setText(f"ZNALEZIONO: {len(apps)}")

    def _toggle_app(self, key: str) -> None:
        key = str(key)
        if key in self._selected_apps:
            self._selected_apps.pop(key, None)
        else:
            entry = next((app for app in self._app_catalog if self._app_key(app) == key), None)
            data = as_dict(entry)
            self._selected_apps[key] = {
                "name": str(data.get("name") or key),
                "exe": str(data.get("exe") or key),
                "icon_path": str(data.get("icon_path") or data.get("path") or ""),
            }
        tile = self._app_tiles.get(key)
        if tile is not None:
            tile.set_selected(key in self._selected_apps)
        self._update_app_selection_ui()

    def _unselect_app(self, key: str) -> None:
        if str(key) in self._selected_apps:
            self._toggle_app(str(key))

    def _clear_apps(self) -> None:
        self._selected_apps.clear()
        for tile in self._app_tiles.values():
            tile.set_selected(False)
        self._update_app_selection_ui()

    def _update_app_selection_ui(self) -> None:
        count = len(self._selected_apps)
        self._app_selected_caption.setText(f"WYBRANO: {count}")
        items = [
            (key, str(entry.get("name") or key))
            for key, entry in self._selected_apps.items()
        ]
        self._app_chips.set_items(items)
        self._app_save.setEnabled(count > 0)
        self._app_save_note.setText("" if count else "Zaznacz aplikacje, żeby zapisać je na stałe.")

    def _save_apps(self) -> None:
        apps = list(self._selected_apps.keys())
        if not apps:
            self.show_toast("Najpierw zaznacz aplikacje.")
            return
        self.request_action.emit("save_app_selection", {"apps": apps})

    def _set_apps_busy(self, busy: bool) -> None:
        self._apps_busy = bool(busy)
        self._app_refresh.setEnabled(not busy)
        self._app_refresh.setText("SKANUJĘ…" if busy else "ODŚWIEŻ LISTĘ")
        if busy:
            self._app_status.setText("SKANUJĘ PEŁNY KATALOG…")
        elif self._app_status.text().startswith("SKANUJĘ"):
            self._app_status.setText("")

    # ------------------------------------------------------------------ strony
    def _request_sites(self, kind: str, query: str | None = None) -> None:
        payload = {"kind": kind, "query": self._site_query.get(kind, "") if query is None else query}
        self.request_action.emit("catalog_sites", payload)

    def _on_site_tab_changed(self, index: int) -> None:
        self._site_kind = STUDY if index <= 0 else BLOCKED
        self._update_site_selection_ui()
        if not self._site_catalog.get(self._site_kind):
            self._request_sites(self._site_kind)

    def _on_site_query_changed(self, kind: str) -> None:
        search = getattr(self, f"_site_search_{kind}", None)
        self._site_query[kind] = str(search.text() if search else "").strip()
        timer = getattr(self, f"_site_timer_{kind}", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(lambda k=kind: self._apply_site_query(k))
            setattr(self, f"_site_timer_{kind}", timer)
        timer.start()

    def _apply_site_query(self, kind: str) -> None:
        self._request_sites(kind, self._site_query.get(kind, ""))

    def _site_name(self, host: str) -> str:
        for kind in (STUDY, BLOCKED):
            for category in self._site_catalog.get(kind) or []:
                for site in as_list(as_dict(category).get("sites")):
                    data = as_dict(site)
                    if str(data.get("host")) == host:
                        return str(data.get("name") or host)
        return host

    @staticmethod
    def _normalize_host(value: str) -> str:
        text = str(value or "").strip().lower()
        for prefix in ("https://", "http://", "//"):
            if text.startswith(prefix):
                text = text[len(prefix) :]
        text = text.split("/")[0].split("?")[0].split(":")[0]
        return text.rstrip(".")

    def _rebuild_site_list(self, kind: str) -> None:
        container = getattr(self, f"_site_container_{kind}", None)
        if container is None:
            return
        layout = container.layout()
        clear_layout(layout)
        self._site_rows[kind] = {}
        rows = 0
        for category in self._site_catalog.get(kind) or []:
            data = as_dict(category)
            sites = [as_dict(site) for site in as_list(data.get("sites"))]
            if not sites:
                continue
            layout.addWidget(labels.caption(str(data.get("label") or data.get("key") or "")))
            for site in sites:
                host = str(site.get("host") or "")
                if not host:
                    continue
                row = SiteRow(
                    host,
                    str(site.get("name") or host),
                    str(site.get("note") or ""),
                    checked=host in self._selected_sites,
                )
                row.toggled.connect(self._toggle_site)
                layout.addWidget(row)
                self._site_rows[kind][host] = row
                rows += 1
        layout.addStretch(1)
        empty = getattr(self, f"_site_empty_{kind}", None)
        if empty is not None:
            empty.setVisible(rows == 0)
        scroll = getattr(self, f"_site_scroll_{kind}", None)
        if scroll is not None:
            scroll.setVisible(rows > 0)
        status = getattr(self, f"_site_status_{kind}", None)
        if status is not None:
            status.setText(f"POZYCJI: {rows}")
        self._update_site_selection_ui()

    def _toggle_site(self, host: str, checked: bool) -> None:
        host = str(host)
        if checked:
            self._selected_sites[host] = {
                "host": host,
                "name": self._site_name(host),
                "kind": self._site_kind,
            }
        else:
            self._selected_sites.pop(host, None)
        self._update_site_selection_ui()

    def _unselect_site(self, host: str) -> None:
        host = str(host)
        self._selected_sites.pop(host, None)
        for kind in (STUDY, BLOCKED):
            row = self._site_rows.get(kind, {}).get(host)
            if row is not None:
                row.set_checked(False)
        self._update_site_selection_ui()

    def _select_site(self, host: str, kind: str = STUDY, name: str = "") -> None:
        host = self._normalize_host(host)
        if not host:
            return
        self._selected_sites[host] = {"host": host, "name": name or self._site_name(host), "kind": kind}
        for row_kind in (STUDY, BLOCKED):
            row = self._site_rows.get(row_kind, {}).get(host)
            if row is not None:
                row.set_checked(True)
        self._update_site_selection_ui()

    def _add_manual_host(self, kind: str) -> None:
        field = getattr(self, f"_manual_host_{kind}", None)
        host = self._normalize_host(field.text() if field else "")
        if not host:
            self.show_toast("Wpisz host, np. docs.python.org.")
            return
        self._select_site(host, kind)
        if field is not None:
            field.clear()
        self.show_toast(f"Dodano host: {host}")

    def _on_subject_changed(self, text: str) -> None:
        self._subject_query = str(text or "").strip()
        if not hasattr(self, "_subject_timer"):
            self._subject_timer = QTimer(self)
            self._subject_timer.setSingleShot(True)
            self._subject_timer.setInterval(400)
            self._subject_timer.timeout.connect(self._request_suggestions)
        self._subject_timer.start()

    def _request_suggestions(self) -> None:
        subject = str(getattr(self, "_subject_query", "") or "")
        if not subject:
            self._suggestions = []
            self._render_suggestions()
            return
        self.request_action.emit("subject_suggestions", {"subject": subject})

    def _render_suggestions(self) -> None:
        caption = getattr(self, "_suggestion_caption", None)
        chips = getattr(self, "_suggestion_chips", None)
        if chips is None:
            return
        items = [(str(as_dict(s).get("host") or ""), str(as_dict(s).get("name") or as_dict(s).get("host") or "")) for s in self._suggestions]
        items = [(key, label) for key, label in items if key]
        chips.set_items(items)
        if caption is not None:
            subject = str(getattr(self, "_subject_query", "") or "")
            caption.setText(f"PODPOWIEDZI DLA: {subject.upper()}" if items else "PODPOWIEDZI DLA PRZEDMIOTU")
            caption.setVisible(bool(items))

    def _add_suggestion(self, host: str) -> None:
        self._select_site(host, STUDY)

    def _update_site_selection_ui(self) -> None:
        total = len(self._selected_sites)
        study = sum(1 for meta in self._selected_sites.values() if meta.get("kind") == STUDY)
        blocked = total - study
        self._site_selected_caption.setText(f"WYBRANO: {total}  ·  NAUKA: {study}  ·  BLOKOWANE: {blocked}")
        self._site_chips.set_items(
            [(host, str(meta.get("name") or host)) for host, meta in self._selected_sites.items()]
        )
        on_study = self._site_kind == STUDY
        active = study if on_study else blocked
        self._site_save.setEnabled(active > 0)
        if active:
            self._site_save_note.setText("")
        elif on_study:
            self._site_save_note.setText("Zaznacz strony nauki, żeby zapisać je na stałe.")
        else:
            self._site_save_note.setText("Zaznacz rozpraszacze, żeby dopisać je do blokowanych.")

    def _save_sites(self) -> None:
        on_study = self._site_kind == STUDY
        hosts = [
            host
            for host, meta in self._selected_sites.items()
            if bool(meta.get("kind") == STUDY) == on_study
        ]
        if not hosts:
            self.show_toast("Najpierw zaznacz strony nauki." if on_study else "Najpierw zaznacz rozpraszacze.")
            return
        self.request_action.emit(
            "save_site_selection",
            {"sites": hosts, "category": STUDY if on_study else BLOCKED},
        )

    def _restore_blocklist(self) -> None:
        """Przywraca domyslna liste rozpraszaczy (akcja `clear_blocklist`)."""
        self.request_action.emit("clear_blocklist", {})
        if hasattr(self, "_blocklist_defaults"):
            self._blocklist_defaults.setEnabled(False)
        if hasattr(self, "_blocklist_status"):
            self._blocklist_status.setText("PRZYWRACAM…")

    # ------------------------------------------------------------------ dane
    def payload(self) -> dict:
        study_sites = [host for host, meta in self._selected_sites.items() if meta.get("kind") == STUDY]
        block_sites = [host for host, meta in self._selected_sites.items() if meta.get("kind") == BLOCKED]
        return plan_payload(
            {
                "mode": self._mode.value(),
                "study_minutes": self._study_minutes.value(),
                "break_minutes": self._break_minutes.value(),
                "long_break_minutes": self._long_break_minutes.value(),
                "long_break_every": self._long_break_every.value(),
                "free_minutes": self._free_minutes.value(),
                "tag": self._tag.text().strip(),
                "goal_note": self._goal.toPlainText().strip(),
                "hardcore": self._hardcore.isChecked(),
                "study_apps": list(self._selected_apps.keys()),
                "study_sites": study_sites,
                "block_sites": block_sites,
                "preset": self._preset_name.text().strip(),
            }
        )

    def apply_plan(self, source: dict | None) -> None:
        src = as_dict(source)
        if "study_apps" in src or "apps" in src:
            self._selected_apps.clear()
            for item in as_list(src.get("study_apps") or src.get("apps")):
                if isinstance(item, dict):
                    data = as_dict(item)
                    key = self._app_key(data) or str(data.get("name") or "")
                    if key:
                        self._selected_apps[key] = {"name": str(data.get("name") or key), "exe": key}
                else:
                    key = str(item).strip()
                    if key:
                        self._selected_apps[key] = {"name": key, "exe": key}
            for key, tile in self._app_tiles.items():
                tile.set_selected(key in self._selected_apps)
            self._update_app_selection_ui()
        if "study_sites" in src or "sites" in src or "break_sites" in src or "block_sites" in src:
            self._selected_sites.clear()
            for host in as_list(src.get("study_sites") or src.get("sites")):
                self._selected_sites[self._normalize_host(str(host))] = {
                    "host": self._normalize_host(str(host)),
                    "name": self._normalize_host(str(host)),
                    "kind": STUDY,
                }
            for host in as_list(src.get("break_sites")) + as_list(src.get("block_sites")):
                normalized = self._normalize_host(str(host))
                self._selected_sites[normalized] = {"host": normalized, "name": normalized, "kind": BLOCKED}
            for kind in (STUDY, BLOCKED):
                for host, row in self._site_rows.get(kind, {}).items():
                    row.set_checked(host in self._selected_sites)
            self._update_site_selection_ui()
        if src.get("study_minutes"):
            self._study_minutes.setValue(as_int(src["study_minutes"], 25))
        if src.get("break_minutes"):
            self._break_minutes.setValue(as_int(src["break_minutes"], 5))
        if src.get("long_break_minutes"):
            self._long_break_minutes.setValue(as_int(src["long_break_minutes"], 15))
        if src.get("long_break_every"):
            self._long_break_every.setValue(as_int(src["long_break_every"], 4))
        if src.get("free_minutes"):
            self._free_minutes.setValue(as_int(src["free_minutes"], 10))
        if src.get("mode"):
            self._mode.set_value(str(src["mode"]).upper())
        self._tag.setText(str(src.get("tag") or ""))
        self._goal.setPlainText(str(src.get("goal_note") or ""))
        self._hardcore.setChecked(bool(src.get("hardcore") or False))
        if src.get("name"):
            self._preset_name.setText(str(src["name"]))

    def _seed_selection(self) -> None:
        """Startowy wybor: profile zapisane w bazie (jesli kontroler je przekazal)."""
        if self._seeded:
            return
        self._seeded = True
        store = self.store
        if store is None:
            return
        try:
            for profile in as_list(store.list_app_profiles("STUDY")):
                data = as_dict(profile)
                value = str(data.get("match_value") or "").strip().lower()
                if value:
                    self._selected_apps[value] = {"name": str(data.get("label") or value), "exe": value}
            for category in ("STUDY", "BLOCK", "BLOCKED"):
                for profile in as_list(store.list_site_profiles(category)):
                    data = as_dict(profile)
                    host = self._normalize_host(str(data.get("host") or ""))
                    if host:
                        self._selected_sites[host] = {
                            "host": host,
                            "name": str(data.get("label") or host),
                            "kind": STUDY if category == "STUDY" else BLOCKED,
                        }
        except Exception:  # pragma: no cover - baza bywa niedostepna
            return

    def showEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().showEvent(event)
        if not self._app_tiles:
            self._request_apps()
            self._request_sites(STUDY)

    def render(self, data: dict) -> None:
        settings = as_dict(data.get("settings"))
        if settings:
            self._apply_icon_settings(settings)
        if data.get("presets") is not None:
            self._presets = [as_dict(item) for item in as_list(data.get("presets"))]
            self._rebuild_presets()
        if data.get("plan"):
            self.apply_plan(as_dict(data["plan"]))
        if data.get("apps") is not None and "plan" not in data:
            self._select_apps_from_profiles(as_list(data.get("apps")))
        if data.get("sites") is not None and "plan" not in data:
            self._select_sites_from_profiles(as_list(data.get("sites")))

    def _select_apps_from_profiles(self, rows: list) -> None:
        for raw in rows:
            data = as_dict(raw)
            value = str(data.get("match_value") or data.get("value") or data.get("exe") or "").strip().lower()
            if not value:
                continue
            self._selected_apps[value] = {"name": str(data.get("label") or data.get("name") or value), "exe": value}
        for key, tile in self._app_tiles.items():
            tile.set_selected(key in self._selected_apps)
        self._update_app_selection_ui()

    def _select_sites_from_profiles(self, rows: list) -> None:
        for raw in rows:
            data = as_dict(raw)
            host = self._normalize_host(str(data.get("host") or data.get("value") or ""))
            if not host:
                continue
            category = str(data.get("category") or "STUDY").upper()
            self._selected_sites[host] = {
                "host": host,
                "name": str(data.get("label") or host),
                "kind": STUDY if category == "STUDY" else BLOCKED,
            }
        for kind in (STUDY, BLOCKED):
            for host, row in self._site_rows.get(kind, {}).items():
                row.set_checked(host in self._selected_sites)
        self._update_site_selection_ui()

    # ------------------------------------------------------------------ wyniki
    def on_action_result(self, action: str, result: dict) -> None:
        data = as_dict(result)
        if action in ("catalog_apps", "refresh_apps", "installed_apps"):
            self._handle_apps(data)
        elif action in ("catalog_sites", "refresh_sites"):
            self._handle_sites(data)
        elif action == "save_app_selection":
            saved = as_int(data.get("saved"))
            self.show_toast(f"Zapisano {saved} aplikacji jako domyślne.")
        elif action == "save_site_selection":
            saved = as_int(data.get("saved"))
            if str(data.get("category") or STUDY).upper() == BLOCKED:
                size = as_int(data.get("blocklist_size"))
                suffix = f" · LISTA BLOKAD: {size}" if size else ""
                self.show_toast(f"ZAPISANO {saved} STRON DO BLOKOWANYCH{suffix}")
            else:
                self.show_toast(f"Zapisano {saved} stron jako domyślne.")
        elif action == "clear_blocklist":
            size = as_int(data.get("blocklist_size"))
            if hasattr(self, "_blocklist_defaults"):
                self._blocklist_defaults.setEnabled(True)
            if hasattr(self, "_blocklist_status"):
                self._blocklist_status.setText("")
            self.show_toast(f"PRZYWRÓCONO DOMYŚLNĄ LISTĘ ({size} STRON)")
        elif action == "subject_suggestions":
            self._handle_suggestions(data)

    def on_action_busy(self, action: str, busy: bool) -> None:
        if action in ("catalog_apps", "refresh_apps", "installed_apps"):
            self._set_apps_busy(bool(busy))

    def _handle_apps(self, data: dict) -> None:
        if data.get("apps") is None:
            return
        query = str(data.get("query") or "")
        if query and query != self._app_query:
            return  # wynik nieaktualnego zapytania
        self._app_catalog = [as_dict(app) for app in as_list(data.get("apps"))]
        self._set_apps_busy(False)
        self._rebuild_app_grid()
        if not data.get("cached") and query == "":
            self._app_status.setText(f"KATALOG ODŚWIEŻONY ({len(self._app_catalog)})")

    def _handle_sites(self, data: dict) -> None:
        kind = str(data.get("kind") or self._site_kind).upper()
        if kind not in (STUDY, BLOCKED):
            kind = self._site_kind
        query = str(data.get("query") or "")
        if query != self._site_query.get(kind, ""):
            return  # wynik nieaktualnego zapytania
        self._site_catalog[kind] = [as_dict(category) for category in as_list(data.get("categories"))]
        self._rebuild_site_list(kind)

    def _handle_suggestions(self, data: dict) -> None:
        self._suggestions = [as_dict(site) for site in as_list(data.get("sites"))]
        hosts = [str(host) for host in as_list(data.get("hosts")) if str(host)]
        for host in hosts:
            if not any(str(as_dict(site).get("host")) == host for site in self._suggestions):
                self._suggestions.append({"host": host, "name": host})
        self._render_suggestions()

    # ------------------------------------------------------------------ presety
    def _rebuild_presets(self) -> None:
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("— wczytaj preset —", None)
        for preset in self._presets:
            self._preset_combo.addItem(str(preset.get("name") or "?"), preset)
        self._preset_combo.blockSignals(False)

    def _load_preset(self, index: int) -> None:
        preset = self._preset_combo.itemData(index)
        if not isinstance(preset, dict):
            return
        payload = dict(preset.get("payload") or {})
        payload.setdefault("name", preset.get("name"))
        self.apply_plan(payload)
        self.show_toast(f"Wczytano preset: {preset.get('name')}")

    def _emit_preset(self) -> None:
        name = self._preset_name.text().strip()
        if not name:
            self.show_toast("Podaj nazwę presetu.")
            return
        self.request_preset.emit({"name": name, "payload": self.payload(), "delete": False})

    # ------------------------------------------------------------------ resize
    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        if not hasattr(self, "_app_grid") or not self._app_catalog:
            return
        columns = self._app_columns_for_width()
        if columns != self._app_columns or self._app_tile_width_for(columns) != self._app_tile_width:
            self._rebuild_app_grid()
