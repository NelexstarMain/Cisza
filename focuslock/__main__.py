"""Punkt wejscia Ciszy.

Tryby:
  (brak)        - GUI (pythonw -m focuslock)
  --helper      - proces pomocniczy z uprawnieniami administratora (RPC)
  --watchdog    - pilnowanie zycia GUI i przywracanie powloki po crashu
  --restore     - awaryjne przywrocenie powloki i sieci (alias tools/restore.py --panic)
  --dry-run     - nic nie zmienia w systemie, tylko loguje
  --safe        - pomija lockdown powloki i sieci
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _ensure_import_path() -> None:
    package_root = Path(__file__).resolve().parent.parent
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))


def emit_report(lines: list[str]) -> None:
    """Wypisuje raport trybow CLI tak, by dzialal tez w .exe bez konsoli.

    W buildzie ``--noconsole`` ``sys.stdout`` bywa ``None`` - zwykle ``print``
    rzuca wtedy wyjatkiem i caly proces konczy sie kodem bledu (a weryfikacja
    buildu nie ma czego sprawdzic). Dodatkowo, gdy ustawione jest
    ``CISZA_REPORT_FILE``, ten sam tekst trafia do pliku - z niego korzysta
    ``tools/build_exe.py``.
    """
    text = "\n".join(str(line) for line in lines)
    try:
        if sys.stdout is not None:
            print(text)
    except Exception:  # noqa: BLE001 - brak konsoli nie moze wywalic CLI
        pass
    path = os.environ.get("CISZA_REPORT_FILE")
    if not path:
        return
    try:
        Path(path).write_text(text, encoding="utf-8")
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cisza", description="Cisza - monochromatyczna maszyna do nauki")
    parser.add_argument("--helper", action="store_true", help="uruchom proces pomocniczy (admin)")
    parser.add_argument("--watchdog", action="store_true", help="uruchom watchdog powloki")
    parser.add_argument("--restore", action="store_true", help="awaryjne przywrocenie powloki i sieci")
    parser.add_argument("--catalog", action="store_true", help="wypisz wykryte aplikacje (diagnostyka)")
    parser.add_argument("--catalog-force", action="store_true", help="jak --catalog, ale z pominieciem cache")
    parser.add_argument("--sites", action="store_true", help="wypisz katalog stron (diagnostyka)")
    parser.add_argument("--dry-run", action="store_true", help="nie zmieniaj systemu")
    parser.add_argument("--safe", action="store_true", help="bez lockdownu powloki/sieci")
    parser.add_argument("--version", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_import_path()
    argv = list(sys.argv[1:] if argv is None else argv)
    args, unknown = build_parser().parse_known_args(argv)

    if args.dry_run:
        os.environ["CISZA_DRY_RUN"] = "1"
    if args.safe:
        os.environ["CISZA_SAFE"] = "1"

    from focuslock import __version__

    if args.version:
        emit_report([f"Cisza {__version__}"])
        return 0

    if args.catalog or args.catalog_force:
        from focuslock import appcatalog

        apps = appcatalog.get_catalog(force=args.catalog_force)
        info = appcatalog.describe(apps)
        lines = [f"Wykryte aplikacje: {info['count']} {info['sources']}"]
        for app in apps:
            icon = "ikona" if app.icon_source else "monogram"
            lines.append(f"  {app.name:<40} {app.exe:<28} {app.source:<10} {icon}")
        emit_report(lines)
        return 0

    if args.sites:
        from focuslock import sitecatalog

        info = sitecatalog.describe()
        lines = [f"Strony w katalogu: {info['total']} (nauka: {info['study']}, blokowane: {info['blocked']})"]
        for key, items in sitecatalog.by_category().items():
            label = sitecatalog.CATEGORIES.get(key, key)
            lines.append(f"  {label} ({len(items)}):")
            for site in items:
                lines.append(f"    {site.name:<36} {site.host}")
        emit_report(lines)
        return 0

    if args.helper:
        from focuslock.helper import main as helper_main

        filtered = [arg for arg in unknown if arg not in ("-m", "focuslock")]
        return helper_main(filtered)

    if args.watchdog:
        from focuslock.shell.watchdog import run_watchdog

        return run_watchdog(dry_run=args.dry_run)

    if args.restore:
        from focuslock.recovery import restore_everything

        report = restore_everything(dry_run=args.dry_run, reason="cli")
        lines = [f"Przywracanie zakonczone: ok={report['ok']}"]
        for key in ("applied", "warnings", "errors"):
            for item in report.get(key, []):
                lines.append(f"  [{key}] {item}")
        emit_report(lines)
        return 0 if report["ok"] else 1

    from focuslock.app import run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
