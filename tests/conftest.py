"""Wspolna konfiguracja testow: projekt na sys.path + odizolowane dane.

Dzieki temu `python -m pytest` i `pytest` dzialaja identycznie, a testy nigdy nie
dotykaja prawdziwego %LOCALAPPDATA%\\Cisza.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Odizolowany katalog danych dla calego przebiegu testow.
if not os.environ.get("CISZA_DATA_DIR"):
    os.environ["CISZA_DATA_DIR"] = tempfile.mkdtemp(prefix="cisza-tests-")

# Testy nigdy nie zmieniaja systemu.
os.environ.setdefault("CISZA_DRY_RUN", "1")
