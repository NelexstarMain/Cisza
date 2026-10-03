"""Testy pickerów aplikacji i stron w USTAwieniach (offscreen, bez realnego GUI).

Pilnują nowej ścieżki wyboru z katalogu:
* okno pickera otwiera się i filtruje lokalnie (bez zadań do kontrolera),
* Enter = dodaj zaznaczone, Esc = zamknij,
* wybór wpisu wypełnia pola profilu (aplikacje: nazwa+proces, strony: host+opis+kategoria),
* ręczne wpisanie nadal działa (pola _app_* / _site_* i przyciski DODAJ / ZAPISZ),
* wyniki `catalog_apps` / `catalog_sites` nie wywracają ekranu,
* brak regresji `diagnostics`.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from focuslock.ui.screens.settings import SettingsScreen
from focuslock.ui.widgets.picker import CatalogPicker

APP_CATALOG = [
    {
        "name": "Google Chrome",
        "exe": "chrome.exe",
        "path": r"C:\Program Files\Google\Chrome\chrome.exe",
        "source": "startmenu",
        "label": "Google Chrome (chrome.exe)",
    },
    {"name": "Visual Studio Code", "exe": "code.exe", "path": "", "source": "running", "label": "VS Code"},
    {"name": "Steam", "exe": "steam.exe", "path": "", "source": "running", "label": "Steam"},
]

SITE_PAYLOAD = {
    "ok": True,
    "kind": "STUDY",
    "query": "",
    "categories": [
        {
            "key": "nauka",
            "label": "Nauka i encyklopedie",
            "sites": [{"host": "*.wikipedia.org", "name": "Wikipedia (PL)", "note": "encyklopedia"}],
        },
        {
            "key": "kod",
            "label": "Programowanie",
            "sites": [{"host": "github.com", "name": "GitHub", "note": ""}],
        },
    ],
}

BLOCKED_PAYLOAD = {
    "ok": True,
    "kind": "BLOCKED",
    "query": "",
    "categories": [
        {
            "key": "rozrywka",
            "label": "Rozrywka (blokowane)",
            "sites": [{"host": "youtube.com", "name": "YouTube", "note": "wideo"}],
        }
    ],
}

APP_ENTRIES = [
    {"key": "chrome.exe", "label": "Google Chrome", "detail": "chrome.exe  ·  startmenu"},
    {"key": "code.exe", "label": "Visual Studio Code", "detail": "code.exe  ·  running"},
]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def screen(qapp):
    window = SettingsScreen()
    yield window
    window.close()


class _StubDialog:
    """Zastepuje modalne okno w testach wpiecia (bez blokujacego exec())."""

    def __init__(self, picked=None, manual=""):
        self._picked = list(picked or [])
        self._manual = str(manual)

    def exec(self) -> int:
        return int(QDialog.DialogCode.Accepted)

    def picked_entries(self) -> list[dict]:
        return [dict(row) for row in self._picked]

    def manual_text(self) -> str:
        return self._manual


# ------------------------------------------------------------------ widget
def test_picker_filters_locally_and_counts(qapp):
    dialog = CatalogPicker("WYBIERZ APLIKACJE", "test", multi=True, search_placeholder="SZUKAJ…")
    dialog.set_entries(APP_ENTRIES)
    assert dialog.shown_count() == 2
    assert dialog.keys() == ["chrome.exe", "code.exe"]

    dialog.set_query("code")
    assert dialog.shown_count() == 1
    assert dialog.keys() == ["code.exe"]
    assert "WYNIKI: 1" in dialog._count.text()

    dialog.set_query("nie-ma-takiej")
    assert dialog.shown_count() == 0
    assert dialog.keys() == []

    dialog.set_query("")
    assert dialog.shown_count() == 2
    dialog.close()


def test_picker_enter_adds_selected_and_esc_closes(qapp):
    dialog = CatalogPicker("WYBIERZ APLIKACJE", "test")
    dialog.set_entries(APP_ENTRIES)
    dialog.set_query("code")

    captured: list[list] = []
    dialog.picked.connect(lambda entries: captured.append(list(entries)))
    dialog._search.returnPressed.emit()
    assert captured and captured[-1][0]["key"] == "code.exe"
    assert dialog.result() == QDialog.DialogCode.Accepted

    other = CatalogPicker("WYBIERZ APLIKACJE", "test")
    other.set_entries(APP_ENTRIES)
    other.show()
    qapp.processEvents()
    other.keyPressEvent(_escape_event())
    assert other.result() == QDialog.DialogCode.Rejected
    other.close()

    dialog.close()


def _escape_event():
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent

    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)


def test_picker_groups_and_multiselect(qapp):
    dialog = CatalogPicker("WYBIERZ STRONY", "test")
    dialog.set_entries(
        [
            {"kind": "group", "label": "NAUKA  ·  DOZWOLONE"},
            {"key": "*.wikipedia.org", "label": "Wikipedia (PL)", "detail": "*.wikipedia.org"},
            {"kind": "group", "label": "KOD  ·  DOZWOLONE"},
            {"key": "github.com", "label": "GitHub", "detail": "github.com"},
        ]
    )
    assert dialog.shown_count() == 2
    assert dialog.keys() == ["*.wikipedia.org", "github.com"]

    dialog.set_query("wiki")
    assert dialog.shown_count() == 1
    assert dialog.keys() == ["*.wikipedia.org"]

    dialog.set_query("")
    dialog.list.clearSelection()
    dialog.select_keys(["*.wikipedia.org", "github.com"])
    assert len(dialog.selected_entries()) == 2
    dialog.close()


def test_picker_manual_footer_returns_typed_text(qapp):
    dialog = CatalogPicker("WYBIERZ APLIKACJE", "test")
    dialog.set_entries(APP_ENTRIES)
    dialog.set_query("moja-aplikacja.exe")

    manual: list[str] = []
    dialog.manual.connect(lambda text: manual.append(text))
    dialog.use_manual()
    assert dialog.manual_text() == "moja-aplikacja.exe"
    assert manual == ["moja-aplikacja.exe"]
    assert dialog.picked_entries() == []
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_picker_without_selection_stays_open(qapp):
    dialog = CatalogPicker("WYBIERZ APLIKACJE", "test")
    dialog.set_entries([])
    dialog.list.clearSelection()
    dialog.confirm_selection()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.close()


def test_picker_renders_offscreen(qapp):
    dialog = CatalogPicker("WYBIERZ APLIKACJE", "test")
    dialog.set_entries(APP_ENTRIES)
    dialog.resize(620, 520)
    dialog.show()
    qapp.processEvents()
    assert not dialog.grab().isNull()
    dialog.close()


# --------------------------------------------------- wpiecie w USTAWIENIA
def test_open_app_picker_requests_catalog_once(screen):
    requests: list[tuple] = []
    screen.request_action.connect(lambda name, payload: requests.append((name, dict(payload or {}))))
    screen._open_app_picker()
    assert screen._awaiting_app_catalog is True
    assert requests == [("catalog_apps", {"query": ""})]


def test_app_catalog_result_does_not_crash(screen):
    screen.on_action_result(
        "catalog_apps",
        {"ok": True, "apps": APP_CATALOG, "count": 3, "query": "", "cached": True, "summary": {}},
    )
    assert len(screen._app_catalog) == 3
    # wynik z zapytaniem (np. z kreatora) nie nadpisuje cache pickera
    screen.on_action_result(
        "catalog_apps", {"ok": True, "apps": [APP_CATALOG[0]], "count": 1, "query": "chrome"}
    )
    assert len(screen._app_catalog) == 3
    screen.on_action_result("catalog_apps", {"ok": False, "errors": ["boom"]})
    screen.on_action_result("catalog_apps", "smieci")
    assert len(screen._app_catalog) == 3


def test_empty_app_catalog_shows_building_message(screen):
    screen._awaiting_app_catalog = True
    screen.on_action_result(
        "catalog_apps", {"ok": True, "apps": [], "count": 0, "query": "", "cached": True}
    )
    assert screen._awaiting_app_catalog is False
    assert screen._toast is not None
    assert "Katalog aplikacji buduje się" in screen._toast.message()


def test_catalog_result_defers_picker_open(screen, qapp, monkeypatch):
    """Modalne okno nie moze blokowac rozsylania wyniku do innych ekranow."""
    calls: list[bool] = []
    monkeypatch.setattr(screen, "_open_app_picker", lambda: calls.append(True))
    screen._awaiting_app_catalog = True
    screen.on_action_result(
        "catalog_apps", {"ok": True, "apps": APP_CATALOG, "count": 3, "query": "", "cached": True}
    )
    assert calls == []
    qapp.processEvents()
    assert calls == [True]


def test_app_picker_shows_name_process_and_source(screen):
    screen._app_catalog = list(APP_CATALOG)
    dialog = screen._make_app_picker()
    labels = dialog.visible_labels()
    assert any("Google Chrome" in text and "chrome.exe" in text and "startmenu" in text for text in labels)
    dialog.set_query("code")
    picked = dialog.selected_entries()
    assert picked and picked[0]["key"] == "code.exe"
    dialog.close()


def test_app_picker_choice_fills_fields(screen):
    screen._app_catalog = list(APP_CATALOG)
    dialog = screen._make_app_picker()
    dialog.set_query("code")
    screen._apply_app_picks(dialog.selected_entries())
    assert screen._app_value.text() == "code.exe"
    assert screen._app_label.text() == "Visual Studio Code"
    assert screen._app_kind.currentData() == "name"
    dialog.close()


def test_open_app_picker_applies_choice(screen, monkeypatch):
    screen._app_catalog = list(APP_CATALOG)
    monkeypatch.setattr(screen, "_make_app_picker", lambda: _StubDialog(picked=[APP_CATALOG[1]]))
    screen._open_app_picker()
    assert screen._app_label.text() == "Visual Studio Code"
    assert screen._app_value.text() == "code.exe"


def test_open_app_picker_manual_entry(screen, monkeypatch):
    screen._app_catalog = list(APP_CATALOG)
    monkeypatch.setattr(screen, "_make_app_picker", lambda: _StubDialog(manual="wlasny.exe"))
    screen._open_app_picker()
    assert screen._app_value.text() == "wlasny.exe"
    assert screen._app_label.text() == "wlasny.exe"
    assert screen._app_kind.currentData() == "name"


def test_local_filtering_sends_no_requests(screen):
    screen._app_catalog = list(APP_CATALOG)
    requests: list[tuple] = []
    screen.request_action.connect(lambda name, payload: requests.append((name, dict(payload or {}))))
    dialog = screen._make_app_picker()
    for text in ("c", "ch", "chr", "chro", "chrome"):
        dialog.set_query(text)
    assert requests == []
    dialog.close()


# ------------------------------------------------------------------ strony
def test_site_catalog_results_do_not_crash(screen):
    screen.on_action_result("catalog_sites", SITE_PAYLOAD)
    assert "STUDY" in screen._site_catalog
    screen.on_action_result("catalog_sites", BLOCKED_PAYLOAD)
    assert "BLOCKED" in screen._site_catalog
    screen.on_action_result("catalog_sites", "smieci")
    screen.on_action_result("catalog_sites", {"ok": True, "query": "", "categories": []})
    items = [entry for entry in screen._site_entries() if entry.get("kind") == "item"]
    assert len(items) == 3


def test_site_picker_is_grouped_by_category(screen):
    screen.on_action_result("catalog_sites", SITE_PAYLOAD)
    entries = screen._site_entries()
    groups = [entry["label"] for entry in entries if entry.get("kind") == "group"]
    assert groups == ["NAUKA I ENCYKLOPEDIE  ·  DOZWOLONE", "PROGRAMOWANIE  ·  DOZWOLONE"]
    assert any("Wikipedia (PL)" in text for text in screen._make_site_picker(entries).visible_labels())


def test_site_picker_choice_fills_fields(screen):
    screen.on_action_result("catalog_sites", SITE_PAYLOAD)
    dialog = screen._make_site_picker(screen._site_entries())
    dialog.set_query("github")
    picked = dialog.selected_entries()
    assert picked and picked[0]["host"] == "github.com"
    screen._apply_site_picks(picked)
    assert screen._site_host.text() == "github.com"
    assert screen._site_label.text() == "GitHub"
    assert screen._site_category.currentData() == "kod"
    dialog.close()


def test_site_picker_approximates_missing_category(screen):
    screen._apply_site_picks([{"host": "librus.pl", "name": "Librus", "category": "szkola", "kind_hint": "STUDY"}])
    assert screen._site_host.text() == "librus.pl"
    assert screen._site_category.currentData() == "nauka"

    screen._apply_site_picks([{"host": "onet.pl", "name": "Onet", "category": "wiadomosci", "kind_hint": "BLOCKED"}])
    assert screen._site_category.currentData() == "rozrywka"


def test_site_picker_multi_adds_directly(screen):
    requests: list[tuple] = []
    screen.request_action.connect(lambda name, payload: requests.append((name, dict(payload or {}))))
    screen._apply_site_picks(
        [
            {"host": "github.com", "name": "GitHub", "category": "kod", "kind_hint": "STUDY"},
            {"host": "*.wikipedia.org", "name": "Wikipedia", "category": "nauka", "kind_hint": "STUDY"},
        ]
    )
    assert [name for name, _ in requests] == ["save_site", "save_site"]
    assert requests[0][1]["host"] == "github.com"
    assert requests[1][1]["category"] == "nauka"


def test_subject_suggestions_use_local_catalog(screen, monkeypatch):
    screen._site_subject.setText("matematyka")
    requests: list[tuple] = []
    screen.request_action.connect(lambda name, payload: requests.append((name, dict(payload or {}))))
    seen: list[str] = []

    class _Spy(_StubDialog):
        pass

    def _fake_make(entries, title="WYBIERZ STRONY"):
        seen.append(title)
        assert entries, "podpowiedzi musza dac wpisy"
        return _Spy(picked=[entries[0]])

    monkeypatch.setattr(screen, "_make_site_picker", _fake_make)
    screen._suggest_sites()
    assert requests == []
    assert seen and seen[0].startswith("PODPOWIEDZI")
    assert screen._site_host.text()


def test_subject_suggestions_without_subject_shows_toast(screen):
    screen._site_subject.setText("")
    screen._suggest_sites()
    assert screen._toast is not None


# ----------------------------------------------------------- sciezka reczna
def test_manual_app_save_still_works(screen):
    requests: list[tuple] = []
    screen.request_action.connect(lambda name, payload: requests.append((name, dict(payload or {}))))
    screen._app_label.setText("Chrome")
    screen._app_value.setText("chrome.exe")
    screen._save_app()
    assert requests[-1][0] == "save_app"
    assert requests[-1][1] == {
        "label": "Chrome",
        "match_kind": "name",
        "match_value": "chrome.exe",
        "category": "STUDY",
    }
    assert screen._app_value.text() == ""


def test_manual_site_save_keeps_custom_category(screen):
    requests: list[tuple] = []
    screen.request_action.connect(lambda name, payload: requests.append((name, dict(payload or {}))))
    screen._site_host.setText("example.com")
    screen._site_label.setText("Przykład")
    screen._site_category.setCurrentIndex(screen._site_category.findData("wlasne_blok"))
    screen._save_site()
    assert requests[-1][0] == "save_site"
    assert requests[-1][1]["category"] == "wlasne_blok"
    assert requests[-1][1]["host"] == "example.com"


# ------------------------------------------------------------------ liczniki
def test_profile_counters_and_empty_states(screen):
    assert screen._apps_count.text() == "WPISÓW: 0"
    assert screen._sites_count.text() == "WPISÓW: 0"
    assert not screen._apps_empty.isHidden()

    screen._fill_apps(
        [{"id": 1, "label": "Chrome", "match_kind": "name", "match_value": "chrome.exe", "category": "STUDY"}]
    )
    assert screen._apps_count.text() == "WPISÓW: 1"
    assert screen._apps_empty.isHidden()
    assert "NAUKA" in screen._apps_list.item(0).text()

    screen._fill_sites([{"id": 2, "host": "youtube.com", "category": "rozrywka", "label": "YouTube"}])
    assert screen._sites_count.text() == "WPISÓW: 1"
    assert "BLOK" in screen._sites_list.item(0).text()

    screen._fill_apps([])
    screen._fill_sites([])
    assert screen._apps_count.text() == "WPISÓW: 0"
    assert not screen._sites_empty.isHidden()


# ------------------------------------------------------------------ regresje
def test_diagnostics_result_still_rendered(screen):
    screen.on_action_result(
        "diagnostics",
        {
            "ok": True,
            "wersja": "1.0",
            "python": "3.13",
            "helper_online": True,
            "admin": True,
            "autostart": "tak",
            "bank_min": 30,
            "sesji": 2,
        },
    )
    assert "1.0" in screen._diag_info.text()
    assert screen._status.text() == "DIAGNOSTYKA: OK"

    screen.on_action_result("diagnostics", {"ok": False, "errors": ["x"]})
    assert screen._status.text() == "DIAGNOSTYKA: BŁĄD"


def test_settings_screen_grabs_with_pickers(screen, qapp):
    screen._app_catalog = list(APP_CATALOG)
    screen.on_action_result("catalog_sites", SITE_PAYLOAD)
    screen.resize(1100, 720)
    screen.show()
    qapp.processEvents()
    assert not screen.grab().isNull()
