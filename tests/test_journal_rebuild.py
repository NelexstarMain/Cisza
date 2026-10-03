"""Testy przebudowanego ekranu DZIENNIK: os czasu, filtry, szukanie, akcje.

Wszystko dziala na `QT_QPA_PLATFORM=offscreen` (bez ekranu), bez bazy i bez
kontrolera - ekran dostaje gotowe listy zdarzen przez `set_data` / `on_action_result`.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from focuslock.ui.screens.journal import (  # noqa: E402
    PAGE_SIZE,
    JournalScreen,
    event_group,
    event_label,
)

KINDS = ("BLOCKED_APP", "BLOCKED_SITE", "SESSION_START", "RATING")


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture()
def screen(app):
    widget = JournalScreen()
    widget.resize(1000, 700)
    yield widget
    widget.close()
    widget.deleteLater()


def _ts(days_ago: int = 0, hour: int = 12, minute: int = 0) -> float:
    """Znacznik czasu o wybranej godzinie `days_ago` dni temu (strefa lokalna)."""
    now = time.localtime()
    base = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, 0, 0, 0, -1))
    return base - days_ago * 86400


def _events() -> list[dict]:
    return [
        {"id": 4, "session_id": 2, "ts": _ts(0, 14, 5), "kind": "RATING", "detail": {"value": 4}},
        {"id": 3, "session_id": 2, "ts": _ts(0, 12, 11), "kind": "BLOCKED_APP", "detail": {"name": "steam.exe"}},
        {"id": 2, "session_id": 2, "ts": _ts(0, 9, 0), "kind": "BLOCKED_SITE", "detail": {"host": "youtube.com"}},
        {
            "id": 1,
            "session_id": 1,
            "ts": _ts(1, 20, 30),
            "kind": "SESSION_START",
            "detail": {"plan": {"mode": "STUDY", "study_minutes": 25}},
        },
    ]


def _lines(screen: JournalScreen) -> list[str]:
    return [screen._list.item(index).text() for index in range(screen._list.count())]


# ------------------------------------------------------------------ budowa ekranu
def test_builds_without_session_and_shows_empty_state(screen):
    assert screen.TITLE == "DZIENNIK ZDARZEŃ"
    assert hasattr(screen, "on_action_result")

    screen.set_data(None)
    assert screen._shown == 0
    assert screen._list.isHidden()
    assert not screen._empty.isHidden()
    assert screen._empty_filter.isHidden()


def test_render_groups_events_by_day(screen):
    screen.set_data({"events": _events()})

    assert screen._shown == 4
    assert screen._list.count() == 6, "2 naglowki dni + 4 zdarzenia"

    lines = _lines(screen)
    assert lines[0].startswith("DZIŚ"), lines[0]
    assert "3 ZDARZENIA" in lines[0], "dzisiejsza grupa ma trzy wpisy"
    yesterday = next(line for line in lines if line.startswith("WCZORAJ"))
    assert "1 ZDARZENIE" in yesterday, "wczorajsza grupa ma jeden wpis"
    assert any("Zablokowana aplikacja" in line and "steam.exe" in line for line in lines)
    assert any("Zablokowana strona" in line and "youtube.com" in line for line in lines)
    assert any("Ocena sesji" in line and "ocena 4" in line for line in lines)

    events = [line for line in lines if not line.startswith(("DZIŚ", "WCZORAJ"))]
    assert "14:05" in events[0], "najnowsze zdarzenie na gorze"
    assert "12:11" in events[1]
    assert "20:30" in events[-1]


def test_filter_choice_group_narrows_the_timeline(screen):
    screen.set_data({"events": _events()})

    screen._filter.set_value("block", emit=True)
    assert screen._shown == 2
    assert screen._list.count() == 3, "naglowek + dwie blokady"
    assert all("Zablokowana" in line for line in _lines(screen)[1:])

    screen._filter.set_value("rating", emit=True)
    assert screen._shown == 1
    assert "Ocena sesji" in _lines(screen)[-1]

    screen._filter.set_value("session", emit=True)
    assert screen._shown == 1
    assert "Start sesji" in _lines(screen)[-1]

    screen._filter.set_value("other", emit=True)
    assert screen._shown == 0
    assert not screen._empty_filter.isHidden()

    screen._filter.set_value("all", emit=True)
    assert screen._shown == 4


def test_search_matches_name_host_and_kind(screen):
    screen.set_data({"events": _events()})

    screen._search.setText("youtube")
    assert screen._shown == 1
    assert "youtube.com" in _lines(screen)[-1]

    screen._search.setText("steam")
    assert screen._shown == 1
    assert "steam.exe" in _lines(screen)[-1]

    screen._search.setText("blocked_site")
    assert screen._shown == 1

    screen._search.setText("nie-ma-takiego-zdarzenia")
    assert screen._shown == 0
    assert screen._list.isHidden()
    assert not screen._empty_filter.isHidden()

    screen._clear_filters()
    assert screen._shown == 4
    assert screen._search.text() == ""
    assert screen._filter.value() == "all"
    assert screen._clear.isHidden()


def test_summary_tiles_and_counter_follow_the_view(screen):
    screen.set_data({"events": _events()})

    assert screen._counter.text() == "4 ZDARZENIA"
    assert screen._tile_events._value.text() == "4"
    assert screen._tile_blocked._value.text() == "2"
    assert screen._tile_days._value.text() == "2"

    screen._search.setText("steam")
    assert screen._counter.text() == "WYNIKI: 1 Z 4"
    assert screen._tile_events._value.text() == "1"
    assert screen._tile_blocked._value.text() == "1"
    assert screen._tile_days._value.text() == "1"


def test_empty_state_vs_no_results_state(screen):
    screen.set_data({"events": []})
    assert not screen._empty.isHidden()
    assert screen._empty_filter.isHidden()

    screen.set_data({"events": _events()})
    assert screen._empty.isHidden()
    assert screen._empty_filter.isHidden()

    screen._search.setText("zzz")
    assert screen._empty.isHidden()
    assert not screen._empty_filter.isHidden()


def test_live_event_lands_in_the_timeline(screen):
    screen.set_data({"events": _events()})

    screen.on_app_event("blocked_app", {"name": "discord.exe"})

    assert screen._shown == 5
    assert screen._counter.text() == "5 ZDARZEŃ"
    assert any("discord.exe" in line for line in _lines(screen))


def test_action_results_refresh_and_export(screen, tmp_path):
    screen.set_data({"events": []})

    screen.on_action_result("refresh_journal", {"ok": True, "events": _events()})
    assert screen._shown == 4
    assert screen._toast is not None
    assert "ODŚWIEŻONO" in screen._toast.message()

    screen.on_action_result("refresh_journal", {"ok": False, "errors": ["boom"]})
    assert screen._shown == 4, "nieudane odswiezenie nie czysci widoku"

    screen.on_action_result("inna_akcja", {"ok": True})

    target = tmp_path / "dziennik-20261003-120000.json"
    screen.on_action_result("export_journal", {"ok": True, "path": str(target)})
    assert "ZAPISANO DZIENNIK" in screen._toast.message()
    assert screen._toast.message().endswith(target.name)


def test_pagination_keeps_the_item_count_bounded(screen, app):
    events = [
        {
            "ts": _ts(index % 10, 8 + (index % 12), index % 60),
            "kind": KINDS[index % len(KINDS)],
            "detail": {"name": f"proc-{index}.exe"},
        }
        for index in range(400)
    ]
    screen.set_data({"events": events})

    assert len(screen._events) == 400
    assert screen._shown == PAGE_SIZE
    assert screen._list.count() <= PAGE_SIZE + 10, "jedna porcja to nie 400 widgetow"
    assert not screen._more.isHidden()

    screen._more.click()
    assert screen._shown == 2 * PAGE_SIZE
    assert screen._list.count() <= 2 * PAGE_SIZE + 10

    for _ in range(10):
        screen._more.click()
    assert screen._shown == 400
    assert screen._more.isHidden()


def test_garbage_data_never_raises(screen, app):
    screen.set_data("smieci")
    screen.set_data({"events": 42})
    screen.set_data({"events": [1, "x", None, {}, {"ts": "zle", "kind": None, "detail": 7}]})
    screen.set_data({"events": [{"ts": None, "kind": "BLOCKED_APP", "detail": {"name": {"deep": 1}}}]})
    screen.set_data({"events": [{"ts": object(), "kind": ["BLOCKED_APP"], "detail": [1, 2]}], "filter": 7})
    screen.update_state(None)

    screen.on_app_event("blocked_app", {"name": "steam.exe"})
    screen.on_app_event("", None)
    screen.on_action_result("refresh_journal", None)
    screen.on_action_result("export_journal", {"ok": True})

    screen.show()
    app.processEvents()
    assert not screen.grab().isNull()
    assert screen._shown >= 0


def test_kind_mapping_helpers():
    assert event_label("BLOCKED_APP") == "Zablokowana aplikacja"
    assert event_label("blocked_site") == "Zablokowana strona"
    assert event_label("") == "Zdarzenie"
    assert event_label("COS_NOWEGO") == "Cos nowego"

    assert event_group("BLOCKED_APP") == "block"
    assert event_group("RATING") == "rating"
    assert event_group("SESSION_START") == "session"
    assert event_group("FREE_START") == "session"
    assert event_group("COS_NOWEGO") == "other"
