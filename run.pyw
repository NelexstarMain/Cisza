"""Launcher Ciszy bez okna konsoli (pythonw run.pyw)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from focuslock.__main__ import main  # noqa: E402

if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except KeyboardInterrupt:
        code = 130
    # Kod wyjscia zawsze przekazujemy dalej - tryby CLI (--restore, --watchdog)
    # musza byc uzywalne w skryptach; pythonw nie ma konsoli, wiec nic to nie psuje.
    raise SystemExit(code)
