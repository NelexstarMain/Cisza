"""Testy trybow CLI: raport ma dzialac takze bez konsoli (build --noconsole).

`focuslock/__main__.py` wypisuje raport przez `emit_report`, ktory:
- nie wywala sie, gdy `sys.stdout` jest `None` (zamrozony .exe bez konsoli),
- zapisuje ten sam tekst do `CISZA_REPORT_FILE` (z tego korzysta weryfikacja .exe).
"""
from __future__ import annotations

import sys

import pytest

from focuslock import __main__ as cli


def test_emit_report_writes_file(monkeypatch, tmp_path):
    target = tmp_path / "raport.txt"
    monkeypatch.setenv("CISZA_REPORT_FILE", str(target))
    cli.emit_report(["linia 1", "linia 2"])
    assert target.read_text(encoding="utf-8") == "linia 1\nlinia 2"


def test_emit_report_survives_missing_stdout(monkeypatch, tmp_path):
    monkeypatch.setenv("CISZA_REPORT_FILE", str(tmp_path / "raport.txt"))
    monkeypatch.setattr(sys, "stdout", None)
    cli.emit_report(["bez konsoli"])
    assert (tmp_path / "raport.txt").read_text(encoding="utf-8") == "bez konsoli"


def test_restore_cli_reports_and_writes_file(monkeypatch, tmp_path):
    data_dir = tmp_path / "dane"
    monkeypatch.setenv("CISZA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CISZA_DRY_RUN", "1")
    target = tmp_path / "raport.txt"
    monkeypatch.setenv("CISZA_REPORT_FILE", str(target))

    code = cli.main(["--restore", "--dry-run"])

    assert code == 0
    text = target.read_text(encoding="utf-8")
    assert "Przywracanie zakonczone: ok=True" in text
    # Nowy krok przywracania (filtr paska zadan) musi byc widoczny w raporcie.
    assert "taskbarfilter" in text


def test_version_cli_writes_file(monkeypatch, tmp_path):
    target = tmp_path / "raport.txt"
    monkeypatch.setenv("CISZA_REPORT_FILE", str(target))
    assert cli.main(["--version"]) == 0
    assert target.read_text(encoding="utf-8").startswith("Cisza ")


@pytest.mark.parametrize("mode", ["filtered", "hide", "keep"])
def test_taskbar_mode_from_helper_payload(mode):
    from focuslock.helper import taskbar_mode_from

    assert taskbar_mode_from({"taskbar_mode": mode}) == mode
    assert taskbar_mode_from({}) == "filtered"


def test_taskbar_mode_from_legacy_flag():
    from focuslock.helper import taskbar_mode_from

    assert taskbar_mode_from({"hide_taskbar": True}) == "hide"
    assert taskbar_mode_from({"hide_taskbar": False}) == "keep"
