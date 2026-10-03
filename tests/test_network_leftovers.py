"""Regresje: reguly zapory nie moga zostawiac uzytkownika bez internetu.

Uzytkownik bez uprawnien administratora nie usunie regul `CiszaBlock-*`,
wiec (1) domyslnie ich nie tworzymy, a (2) gdy zostaly, aplikacja ma o tym
wyraznie powiedziec i wskazac gotowy naprawiacz.
"""
from __future__ import annotations

import inspect

import pytest

from focuslock.config import NetworkConfig


def test_browser_firewall_rules_are_off_by_default():
    """Reguly zapory wymagaja admina i potrafia zostac po sesji - domyslnie nie tworzymy ich."""
    assert NetworkConfig().block_browser_direct is False


def test_leftover_firewall_warning_when_rules_exist(monkeypatch):
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module
    from focuslock.block import firewall

    monkeypatch.setattr(firewall, "is_active", lambda *a, **k: True)
    message = app_module.leftover_firewall_warning()
    assert "napraw-internet.cmd" in message
    assert "administrator" in message.lower()


def test_leftover_firewall_warning_is_empty_when_clean(monkeypatch):
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module
    from focuslock.block import firewall

    monkeypatch.setattr(firewall, "is_active", lambda *a, **k: False)
    assert app_module.leftover_firewall_warning() == ""


def test_leftover_firewall_warning_survives_errors(monkeypatch):
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module
    from focuslock.block import firewall

    def _boom(*_args, **_kwargs):
        raise OSError("brak netsh")

    monkeypatch.setattr(firewall, "is_active", _boom)
    assert app_module.leftover_firewall_warning() == ""


def test_run_shows_leftover_warning_once():
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    source = inspect.getsource(app_module.run)
    assert "leftover_firewall_warning()" in source
    assert "QMessageBox.warning" in source


def test_repair_script_exists():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = root / "napraw-internet.cmd"
    assert script.is_file()
    text = script.read_text(encoding="utf-8", errors="replace")
    assert "restore.py --panic" in text
