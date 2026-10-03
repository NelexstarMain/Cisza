"""Awaryjne przywracanie powloki po crashu Ciszy.

Uzycie:
    python tools/restore.py            # przywroc powloke i siec
    python tools/restore.py --dry-run  # pokaz, co zostanie zrobione
    python tools/restore.py --panic    # to samo + zamknij procesy Ciszy
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def kill_cisza_processes(dry_run: bool = False) -> list[str]:
    actions: list[str] = []
    try:
        import psutil
    except ImportError:
        return ["psutil niedostepny - pominieto zamykanie procesow"]
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "focuslock" in cmdline and proc.pid != 0:
                actions.append(f"zamykam pid={proc.pid} ({proc.info.get('name')})")
                if not dry_run:
                    proc.kill()
        except Exception:  # noqa: BLE001
            continue
    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Awaryjne przywracanie powloki Ciszy")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--panic", action="store_true", help="dodatkowo zamknij procesy Ciszy")
    args = parser.parse_args(argv or [])

    from focuslock.recovery import restore_everything

    report = restore_everything(dry_run=args.dry_run, reason="panic" if args.panic else "cli")
    print(f"ok={report['ok']} reason={report['reason']}")
    for key in ("applied", "warnings", "errors"):
        for item in report.get(key, []):
            print(f"  [{key}] {item}")

    if args.panic:
        for line in kill_cisza_processes(dry_run=args.dry_run):
            print(f"  [proces] {line}")
        _ = subprocess  # rezerwa na przyszle uzycie (np. restart explorera)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
