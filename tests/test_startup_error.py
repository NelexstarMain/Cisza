"""Testy czytelnego bledu startu (zamiast surowego tracebacku z .exe).

Zamrozona Cisza na cudzym profilu potrafi dostac
``sqlite3.OperationalError: attempt to write a readonly database`` —
zamiast tego uzytkownik ma zobaczyc ktory katalog i co z tym zrobic.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from focuslock import app as app_module  # noqa: E402


def test_data_dir_problem_returns_empty_for_writable_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "dane"))
    assert app_module.data_dir_problem() == ""


def test_data_dir_problem_reports_unwritable_path(monkeypatch, tmp_path):
    blocker = tmp_path / "to-jest-plik"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(app_module.paths, "data_dir", lambda: blocker)
    problem = app_module.data_dir_problem()
    assert "nie moze zapisac" in problem
    assert str(blocker) in problem


def test_data_dir_problem_cleanup_leaves_no_probe_file(monkeypatch, tmp_path):
    target = tmp_path / "dane"
    monkeypatch.setenv("CISZA_DATA_DIR", str(target))
    assert app_module.data_dir_problem() == ""
    assert list(target.glob(".zapis-test")) == []


def test_show_startup_error_returns_code(monkeypatch):
    shown: list[str] = []

    class _FakeMessageBox:
        class Icon:  # noqa: N801 - atrapa API Qt
            Critical = "critical"

        def setWindowTitle(self, _text):  # noqa: N802 - API Qt
            pass

        def setIcon(self, _icon):  # noqa: N802 - API Qt
            pass

        def setText(self, text):  # noqa: N802 - API Qt
            shown.append(text)

        def exec(self):  # noqa: N802 - API Qt
            return 0

    monkeypatch.setattr(app_module, "QMessageBox", _FakeMessageBox)
    assert app_module.show_startup_error("brak zapisu") == 2
    assert shown == ["brak zapisu"]
