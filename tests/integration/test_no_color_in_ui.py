"""Test integracyjny monochromatycznosci UI + kontraktu CLI ``tools/mono_lint.py``.

Dwie rzeczy naraz:

* ``tools/mono_lint.py`` (ladowany przez importlib, bez uruchamiania procesu)
  musi uznac ``focuslock/`` za czyste i zwrocic kod 1 dla pliku z czerwienia;
* kazdy plik UI (``.py``, ``.qss``, ``.html`` itd.) w ``focuslock/`` przechodzi
  ten sam skan - lapie tez kolory ukryte w generowanym HTML strony blokady.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mono_lint.py"


def _load_lint():
    if "cisza_mono_lint" in sys.modules:
        return sys.modules["cisza_mono_lint"]
    assert TOOL.exists(), f"brak {TOOL}"
    spec = importlib.util.spec_from_file_location("cisza_mono_lint", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # rejestracja w sys.modules jest wymagana, bo ``dataclasses`` rozwiazuje
    # adnotacje przez ``sys.modules[cls.__module__]``
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_focuslock_is_fully_grayscale():
    """Cala aplikacja (nie tylko theme.py) nie zawiera nie-szarych kolorow."""
    lint = _load_lint()
    findings = lint.lint(["focuslock"])
    report = "\n".join(finding.format(str(ROOT)) for finding in findings)
    assert findings == [], f"znaleziono nie-szare kolory:\n{report}"


def test_theme_tokens_are_gray():
    """Tokeny motywu i QSS sa wylacznie w skali szarosci."""
    from focuslock.ui import theme

    assert theme.COLORS, "theme.COLORS nie moze byc puste"
    for name, color in theme.COLORS.items():
        assert theme.is_gray(color), f"token {name}={color} nie jest szary"
    assert theme.non_gray_colors(theme.qss()) == []


def test_cli_returns_zero_for_clean_tree(capsys):
    lint = _load_lint()
    code = lint.main(["focuslock"])
    assert code == 0
    assert "OK" in capsys.readouterr().out


def test_cli_returns_one_for_violation(tmp_path, capsys):
    """Kontrakt: kod wyjscia 1 i czytelny raport ``plik:linia:kolor``."""
    bad = tmp_path / "ui_bad.py"
    bad.write_text('COLOR = "#ff0000"\n', encoding="utf-8")
    lint = _load_lint()
    code = lint.main([str(bad)])
    assert code == 1
    out = capsys.readouterr().out
    assert "ui_bad.py:1:" in out
    assert "#ff0000" in out


def test_report_format_and_scanner_cases(tmp_path):
    """Skaner: hex 3/6/8, rgb(), nazwy CSS; szare warianty nie sa naruszeniami."""
    lint = _load_lint()
    bad = tmp_path / "probe.py"
    bad.write_text(
        "\n".join(
            [
                'a = "#ff0000"',
                'b = "#00ff00ff"',
                'c = "rgb(255, 0, 0)"',
                'd = "color: red;"',
                'ok1 = "#0b0b0b"',
                'ok2 = "#ccc"',
                'ok3 = "rgb(10, 10, 10)"',
                'ok4 = "hsl(0, 0%, 50%)"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    findings = lint.scan_file(bad)
    lines = sorted(finding.line for finding in findings)
    assert lines == [1, 2, 3, 4]
    assert all(finding.kind in {"hex", "func", "name", "qt"} for finding in findings)


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("'#ff00'", 1),      # #RGBA: R=ff, G=ff, B=00 -> nie-szary
        ("'#'", 0),          # sam znak # to nie kolor
        ("'#WHITE'", 0),     # to nie hex
        ("'rgb(1,2)'", 0),   # za malo skladowych, nie da sie ocenic
        ("'rgb(1,1,1)'", 0), # szary
    ],
)
def test_scanner_edge_cases(snippet, expected):
    lint = _load_lint()
    assert len(lint.scan_text(f"x = {snippet}")) == expected
