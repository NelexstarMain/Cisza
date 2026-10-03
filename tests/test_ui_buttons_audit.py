"""Audyt przyciskow i funkcji UI: statyczny test regresyjny (bez uruchamiania GUI).

Powstalo z pelnego przegladu `focuslock/ui/screens/*.py`, `focuslock/ui/widgets/*.py`
i `focuslock/ui/tray.py`. Test pilnuje trzech warstw kontraktu:

1. kazdy sygnal `request_*` emitowany z ekranow jest podpiety w `MainWindow._wire`,
   a kazdy podpiety handler to istniejaca metoda okna,
2. kazda akcja `request_action.emit(...)` z UI ma odbiorce: `NAVIGATION_ACTIONS`,
   galaz w `MainWindow._run_action` albo galaz w `Controller.handle_action`,
3. kazdy przycisk (`PrimaryButton`/`GhostButton`/`DangerButton`), pozycja menu tray
   i sygnal widgetu ma odbiorce (`.clicked.connect` / `.triggered.connect` / `.connect`).

Dodatkowo zestaw testow `xfail` (niestrict) dokumentuje braki, ktore w chwili
oddania audytu pozostaly otwarte (np. wyniki `refresh_bank`/`refresh_journal`/
`diagnostics` nie wracaja do ekranu zrodlowego) - `pytest` pozostaje zielony,
a moment naprawy zglosi sie jako XPASS. Pozycje naprawione w trakcie audytu sa
zwyklymi asercjami, wiec ich regresja od razu czerwieni test.

Wszystko jest analiza statyczna (`inspect`, `re`, `Path.read_text`); PyQt6 jest
importowane tylko po to, by odczytac zrodlo klas. Zaden QApplication nie powstaje.
"""
from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "focuslock" / "ui"
SCREENS_DIR = UI_DIR / "screens"
WIDGETS_DIR = UI_DIR / "widgets"
APP_PY = ROOT / "focuslock" / "app.py"
CONTROLLER_PY = ROOT / "focuslock" / "controller.py"
TRAY_PY = UI_DIR / "tray.py"

SCREEN_FILES = sorted(SCREENS_DIR.glob("*.py"))
UI_FILES = SCREEN_FILES + sorted(WIDGETS_DIR.glob("*.py")) + [TRAY_PY]

#: Akcje obslugiwane przez samo okno, bez galezi w kontrolerze.
UI_ONLY_ACTIONS: frozenset[str] = frozenset({"open_pin_dialog"})

ACTION_RE = re.compile(r"""request_action\.emit\(\s*["']([a-z_]+)["']""")
SIGNAL_RE = re.compile(r"self\.(request_[a-z_]+)\.emit\(")
#: Kazde przypisanie przycisku (takze z dynamiczna etykieta, np. presety na ekranie home).
BUTTON_RE = re.compile(r"(\w+)\s*=\s*(?:PrimaryButton|GhostButton|DangerButton)\(([^)]*)\)")
#: Podzbior z etykieta literalu (do raportu i testu skanera).
BUTTON_LABEL_RE = re.compile(
    r"""(\w+)\s*=\s*(?:PrimaryButton|GhostButton|DangerButton)\(\s*(['"])(.*?)\2"""
)
TRAY_ACTION_RE = re.compile(r"""(\w+)\s*=\s*self\._menu\.addAction\(\s*(['"])(.*?)\2""")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _locations(pattern: re.Pattern[str], files=None) -> dict[str, list[str]]:
    """Mapa: nazwa akcji/sygnalu -> lista miejsc `plik:linia`."""
    found: dict[str, list[str]] = {}
    for path in files if files is not None else UI_FILES:
        text = _read(path)
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.setdefault(match.group(1), []).append(
                f"{path.relative_to(ROOT).as_posix()}:{line}"
            )
    return found


UI_ACTIONS = _locations(ACTION_RE)
UI_SIGNALS = _locations(SIGNAL_RE, SCREEN_FILES)


# --------------------------------------------------------------- 1. sygnaly okna
def test_every_screen_signal_is_wired_in_main_window():
    """Kazdy `self.request_*.emit(...)` z ekranow ma podpiecie w `MainWindow._wire`."""
    app_module = pytest.importorskip("focuslock.app")
    wire_source = inspect.getsource(app_module.MainWindow._wire)
    wired = set(re.findall(r'"(request_[a-z_]+)"', wire_source))
    assert {"request_action", "request_screen"} <= wired

    missing = {name: places for name, places in UI_SIGNALS.items() if name not in wired}
    assert missing == {}, f"sygnaly bez podpiecia w _wire: {missing}"


def test_wire_handlers_are_existing_main_window_methods():
    """Podpiete lambdy musza wolac realne metody okna (nie pusty placeholdery)."""
    app_module = pytest.importorskip("focuslock.app")
    wire_source = inspect.getsource(app_module.MainWindow._wire)
    pairs = re.findall(r'"(request_[a-z_]+)":\s*lambda[^:]*:\s*self\.(_[a-z_]+)', wire_source)

    assert len(pairs) >= 11, f"za malo podpietych sygnalow: {pairs}"
    for signal_name, method_name in pairs:
        assert hasattr(app_module.MainWindow, method_name), (
            f"{signal_name} wskazuje na nieistniejaca metode MainWindow.{method_name}"
        )


# ----------------------------------------------------------------- 2. akcje UI
def test_scan_found_actions_and_buttons():
    """Zabezpieczenie przed cichym zepsuciem regexow skanujacych."""
    assert len(UI_ACTIONS) >= 25, sorted(UI_ACTIONS)
    for expected in ("open_composer", "refresh_bank", "save_site_selection", "wipe_data"):
        assert expected in UI_ACTIONS, expected

    buttons = [(path.name, var, text) for path in UI_FILES for var, _, text in
               BUTTON_LABEL_RE.findall(_read(path))]
    all_buttons = [(path.name, var) for path in UI_FILES for var, _args in
                   BUTTON_RE.findall(_read(path))]
    assert len(all_buttons) >= 45, f"znaleziono tylko {len(all_buttons)} przyciskow"
    assert any(text == "ZAKOŃCZ SESJĘ" for _, _, text in buttons)


def test_every_ui_action_has_a_receiver():
    """Kazda akcja z ekranow ma odbiorce w oknie albo w `Controller.handle_action`."""
    app_module = pytest.importorskip("focuslock.app")
    from focuslock.controller import Controller

    action_source = inspect.getsource(app_module.MainWindow._run_action)
    controller_source = inspect.getsource(Controller.handle_action)
    known = (
        set(app_module.NAVIGATION_ACTIONS)
        | set(UI_ONLY_ACTIONS)
        | set(re.findall(r'"([a-z_]+)"', action_source))
        | set(re.findall(r'"([a-z_]+)"', controller_source))
    )
    missing = {action: places for action, places in UI_ACTIONS.items() if action not in known}
    assert missing == {}, f"akcje bez odbiorcy: {missing}"


def test_navigation_actions_point_to_existing_screens():
    """`NAVIGATION_ACTIONS` z `app.py` musi wskazywac ekrany z `SCREEN_MODULES`."""
    app_module = pytest.importorskip("focuslock.app")
    for action, target in app_module.NAVIGATION_ACTIONS.items():
        assert target in app_module.SCREEN_MODULES, f"{action} -> nieznany ekran {target}"
        assert f"screen_{target}" in {
            f"screen_{name}" for name in app_module.SCREEN_MODULES
        }, f"{action} -> brak ekranu {target}"
    # z ekranow emitowana jest dzis tylko ta jedna akcja nawigacyjna (home -> kreator)
    assert "open_composer" in UI_ACTIONS


# --------------------------------------------------------------- 3. kontrolki
@pytest.mark.parametrize("path", [p for p in UI_FILES if p.name != "tray.py"], ids=lambda p: p.name)
def test_every_button_has_a_click_handler(path: Path):
    """Kazdy przycisk w UI ma `.clicked.connect(...)` w tym samym pliku."""
    text = _read(path)
    orphan = [
        (var, args.strip())
        for var, args in BUTTON_RE.findall(text)
        if f"{var}.clicked.connect(" not in text
    ]
    assert orphan == [], f"{path.relative_to(ROOT)}: przyciski bez odbiorcy: {orphan}"


def test_every_tray_action_has_a_triggered_handler():
    """Kazda pozycja menu tray ma `.triggered.connect(...)`."""
    text = _read(TRAY_PY)
    actions = TRAY_ACTION_RE.findall(text)
    assert len(actions) == 5, actions
    orphan = [label for var, _quote, label in actions if f"{var}.triggered.connect(" not in text]
    assert orphan == [], f"pozycje tray bez odbiorcy: {orphan}"


def test_tray_signals_are_connected_in_app():
    """Sygnaly `Tray` maja odbiorce w `run()` z `app.py`."""
    app_source = _read(APP_PY)
    missing = [
        name
        for name in ("show_requested", "start_requested", "end_requested", "stats_requested", "quit_requested")
        if f"tray.{name}.connect(" not in app_source
    ]
    assert missing == [], f"sygnaly tray bez odbiorcy w app.py: {missing}"


def test_widget_signals_have_receivers():
    """Sygnaly widgetow uzywanych w ekranach maja odbiorce (poza martwym NavRail)."""
    corpus = "\n".join(_read(path) for path in UI_FILES) + _read(APP_PY)
    for signal_name in ("clicked", "removed", "toggled", "changed"):
        assert f".{signal_name}.connect(" in corpus, f"brak odbiorcy dla sygnalu {signal_name}"


# ------------------- 4. braki znalezione w audycie (wymuszone albo oznaczone xfail)
def test_save_preset_button_persists_preset():
    """Przycisk 'ZAPISZ PRESET' musi trafiac w akcje `save_preset` kontrolera."""
    from focuslock.controller import Controller

    app_source = _read(APP_PY)
    match = re.search(r'"request_preset"\s*:\s*(.{0,200})', app_source, re.S)
    assert match is not None, "request_preset nie ma podpietego handlera w _wire"
    handler = match.group(1)
    assert "save_preset" in handler, (
        f"request_preset nie zapisuje presetu, handler: {handler[:80]!r}"
    )
    assert "preset_apply" not in handler, "request_preset laduje preset zamiast go zapisac"
    assert '"save_preset"' in inspect.getsource(Controller.handle_action)


def test_action_results_reach_the_source_screen():
    """Akcje odswiezajace dane musza miec `on_action_result` w swoim ekranie."""
    expected = {
        "refresh_bank": "bank.py",
        "refresh_stats": "stats.py",
        "refresh_journal": "journal.py",
        "diagnostics": "settings.py",
    }
    missing = []
    for action, filename in expected.items():
        source = _read(SCREENS_DIR / filename)
        has_handler = "def on_action_result" in source
        mentions = action in source
        if not (has_handler and mentions):
            missing.append(f"{action} -> {filename}")
    assert missing == [], f"wynik akcji porzucony (brak on_action_result): {missing}"


def test_restore_database_button_can_select_a_backup():
    """Przycisk 'PRZYWRÓĆ Z KOPII' musi przekazac `path` albo otworzyc wybor pliku."""
    source = _read(SCREENS_DIR / "settings.py")
    has_path = re.search(r'"restore_database",\s*\{[^}]*"path"', source) is not None
    has_picker = "getOpenFileName" in source
    assert has_path or has_picker, "restore_database wysyla pusty payload -> zawsze blad 'wskaz plik kopii'"


def test_export_stats_honours_csv_format():
    """`export_stats` z `{"format": "csv"}` musi umiec zapisac CSV."""
    from focuslock.controller import Controller

    source = inspect.getsource(Controller.export_data)
    assert '"csv"' in source or ".csv" in source, "eksport ignoruje format csv (zawsze JSON)"


def test_tray_quit_goes_through_controller_shutdown():
    """'Zamknij' z tray nie moze zostawiac blokad systemowych (musi wolac shutdown())."""
    app_source = _read(APP_PY)
    match = re.search(r"tray\.quit_requested\.connect\(([^)]*)\)", app_source)
    assert match is not None, "quit_requested nie ma odbiorcy"
    target = match.group(1).strip()
    assert target not in {"app.quit", "quit"}, (
        f"quit_requested -> {target}: pomija Controller.shutdown() (blokady moga zostac)"
    )
    assert "shutdown" in target or "close" in target, f"quit_requested -> {target}"


def test_free_mode_end_reason_is_completed():
    """'ZAKOŃCZ TRYB WOLNY' wysyla `free_done`, ktory musi byc statusem COMPLETED."""
    source = _read(CONTROLLER_PY)
    match = re.search(r'"COMPLETED"\s+if\s+reason\s+in\s+\(([^)]*)\)', source)
    assert match is not None, "nie znaleziono mapowania statusu sesji"
    assert "free_done" in match.group(1), (
        f"reason 'free_done' nie ma na liscie COMPLETED {match.group(1)} -> sesja zapisze sie jako ABORTED"
    )


def test_stats_period_values_are_understood_by_controller():
    """Klucze `ChoiceGroup` w statystykach musza byc zrozumiale dla kontrolera."""
    stats_source = _read(SCREENS_DIR / "stats.py")
    block = re.search(r"self\._period = ChoiceGroup\(\s*\[(.*?)\]\s*\)", stats_source, re.S)
    assert block is not None, "nie znaleziono ChoiceGroup okresu"
    ui_keys = set(re.findall(r'\("([a-z_]+)"', block.group(1)))

    controller_source = _read(CONTROLLER_PY)
    days_map = re.search(r'days\s*=\s*\{([^}]*)\}\.get\(period', controller_source)
    assert days_map is not None, "nie znaleziono mapy dni w kontrolerze"
    controller_keys = set(re.findall(r'"([a-z]+)":', days_map.group(1)))

    assert ui_keys & controller_keys, (
        f"UI wysyla {sorted(ui_keys)}, kontroler rozumie {sorted(controller_keys)} "
        "-> wybor okresu zawsze daje 7 dni"
    )


def test_onboarding_skip_is_not_the_same_handler_as_next():
    """'POMIŃ KROK' nie moze byc kopią 'DALEJ' (nie pomija np. kroku PIN-u)."""
    source = _read(SCREENS_DIR / "onboarding.py")
    skip = re.search(r"self\._skip\.clicked\.connect\(([^)]+)\)", source)
    nxt = re.search(r"self\._next\.clicked\.connect\(([^)]+)\)", source)
    assert skip is not None and nxt is not None, "brak podpiecia przyciskow nawigacji"
    assert skip.group(1) != nxt.group(1), "'POMIŃ KROK' i 'DALEJ' wolaja ten sam handler"


def test_refresh_apps_button_emits_single_request():
    """'ODŚWIEŻ LISTĘ' powinno wyslac dokladnie jedno zadanie odswiezenia."""
    source = _read(SCREENS_DIR / "composer.py")
    start = source.index("def _refresh_apps(self)")
    end = source.index("def ", start + 10)
    body = source[start:end]
    emissions = re.findall(r"self\.request_(?:action|refresh_apps)\.emit\(", body)
    assert len(emissions) == 1, f"podwojna emisja odswiezenia katalogu: {len(emissions)}"


def test_summary_screen_receives_session_result():
    """`SummaryScreen` musi miec sciezke danych z `session_finished` (nie tylko summary())."""
    source = _read(SCREENS_DIR / "summary.py")
    assert "def on_app_event" in source or "def on_action_result" in source, (
        "show_screen() przekazuje tylko controller.summary(); wynik sesji (result/plan) nigdy nie dociera"
    )


def test_nav_rail_navigate_has_a_receiver():
    """`NavRail.navigate` musi miec odbiorce, jesli widget jest uzywany."""
    corpus = "\n".join(_read(path) for path in UI_FILES) + _read(APP_PY)
    assert ".navigate.connect(" in corpus, "NavRail nie jest instancjonowany - sygnal navigate jest martwy"
