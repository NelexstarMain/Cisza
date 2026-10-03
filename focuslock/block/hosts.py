"""Blokada domen przez plik hosts (sekcja Cisza).

Zamrozony kontrakt (docs/INTERFACES.md, sekcja 3):
    BLOCK_BEGIN, BLOCK_END, block_domains, unblock, is_blocked, backup,
    restore_backup, flush_dns.

Zasady:
  - Sekcja miedzy BLOCK_BEGIN i BLOCK_END zawiera wylacznie wpisy "0.0.0.0 domena".
  - Powtorna blokada nadpisuje sekcje (idempotencja), nigdy nie duplikuje wpisow.
  - Wpisy uzytkownika poza sekcja nie sa nigdy zmieniane ani usuwane.
  - Zapis atomowy (plik tymczasowy + os.replace) z zachowaniem kodowania i koncow linii.
  - Brak uprawnien administratora -> ok=False + czytelny blad; zaden wyjatek nie wycieka.

Raport kazdej operacji: {"ok": bool, "applied": [str], "warnings": [str], "errors": [str]}.
"""
from __future__ import annotations

import codecs
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .. import paths

BLOCK_BEGIN = "# CISZA-BEGIN"
BLOCK_END = "# CISZA-END"

#: Adres, na ktory przekierowujemy zablokowane domeny.
REDIRECT_TARGET = "0.0.0.0"

#: Domeny, ktorych nie wolno wpisac do sekcji (zablokowalyby lokalny system).
NEVER_BLOCK = frozenset({"localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"})

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_BACKUP_KEEP = 10


def _new_report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


def default_hosts_path() -> Path:
    """Sciezka systemowego pliku hosts (nadpisywalna argumentem hosts_path)."""
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return Path(root) / "System32" / "drivers" / "etc" / "hosts"


def _resolve(hosts_path: Optional[Path]) -> Path:
    return Path(hosts_path) if hosts_path is not None else default_hosts_path()


# --------------------------------------------------------------------- odczyt/zapis
def _read_text(path: Path) -> tuple[str, str, str]:
    """Zwraca (tresc, koniec linii, kodowanie) zachowujac kodowanie pliku."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "", "\r\n", "utf-8"
    if raw.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    else:
        try:
            raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            # Plik ANSI (np. cp1250): latin-1 tlumaczy kazdy bajt 1:1, wiec zapis
            # odtworzy oryginalne bajty bez zmiany kodowania.
            encoding = "latin-1"
    text = raw.decode(encoding)
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, newline, encoding


def _split_section(text: str) -> tuple[list[str], bool, int]:
    """Usuwa sekcje Cisza.

    Zwraca (linie bez sekcji, czy sekcja istniala, indeks wstawienia).
    """
    kept: list[str] = []
    had_section = False
    insert_at: Optional[int] = None
    inside = False
    for line in text.splitlines():
        marker = line.strip()
        if not inside and marker == BLOCK_BEGIN:
            inside = True
            had_section = True
            insert_at = len(kept)
            continue
        if inside:
            if marker == BLOCK_END:
                inside = False
            continue
        kept.append(line)
    if insert_at is None:
        insert_at = len(kept)
    return kept, had_section, insert_at


def _render(lines: Sequence[str], newline: str) -> str:
    if not lines:
        return ""
    return newline.join(lines) + newline


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Zapis atomowy: plik tymczasowy w tym samym katalogu + os.replace."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(prefix=".cisza-hosts-", suffix=".tmp", dir=str(directory))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _clean_domains(domains: Iterable[str]) -> tuple[list[str], list[str]]:
    cleaned: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for item in domains or ():
        text = str(item).strip().lower().rstrip(".")
        if not text:
            continue
        if any(ch.isspace() for ch in text) or "/" in text or "\\" in text or "#" in text:
            warnings.append(f"pominieto nieprawidlowa domenu: {item!r}")
            continue
        if text in NEVER_BLOCK:
            warnings.append(f"pominieto domenu krytyczna: {text}")
            continue
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned, warnings


def _write_guarded(report: dict, path: Path, data: bytes) -> bool:
    """Wspolna obsluga zapisu; zwraca True gdy zapisano."""
    try:
        _atomic_write_bytes(path, data)
    except PermissionError as exc:
        report["ok"] = False
        report["errors"].append(f"brak uprawnien administratora do zapisu {path}: {exc}")
        return False
    except OSError as exc:
        report["ok"] = False
        report["errors"].append(f"nie mozna zapisac {path}: {exc}")
        return False
    return True


# ------------------------------------------------------------------------- operacje
def block_domains(
    domains: Sequence[str],
    *,
    hosts_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Wpisuje domeny jako "0.0.0.0 domena" w sekcji Cisza (idempotentnie)."""
    report = _new_report()
    path = _resolve(hosts_path)
    cleaned, warnings = _clean_domains(domains)
    report["warnings"].extend(warnings)
    if not cleaned:
        report["warnings"].append("brak domen do zablokowania")
        return report

    try:
        text, newline, encoding = _read_text(path)
        kept, had_section, insert_at = _split_section(text)
        if not had_section:
            while kept and not kept[-1].strip():
                kept.pop()
            insert_at = len(kept)
        section = [BLOCK_BEGIN, *[f"{REDIRECT_TARGET} {domain}" for domain in cleaned], BLOCK_END]
        merged = kept[:insert_at] + section + kept[insert_at:]
        payload = _render(merged, newline)
    except OSError as exc:
        report["ok"] = False
        report["errors"].append(f"nie mozna odczytac {path}: {exc}")
        return report

    action = "nadpisano" if had_section else "dodano"
    report["applied"].append(f"{action} sekcje Cisza ({len(cleaned)} domen) w {path}")
    if dry_run:
        report["applied"].append("dry-run: plik hosts nie zostal zmieniony")
        return report

    try:
        data = payload.encode(encoding)
    except UnicodeEncodeError:  # nie powinno sie zdarzyc dla domen ASCII
        data = payload.encode("utf-8")
    if not _write_guarded(report, path, data):
        return report
    newline_name = "CRLF" if newline == "\r\n" else "LF"
    report["applied"].append(f"zapisano atomowo ({encoding}, {newline_name})")
    return report


def unblock(*, hosts_path: Optional[Path] = None, dry_run: bool = False) -> dict:
    """Usuwa wylacznie sekcje Cisza; nie rusza wpisow uzytkownika."""
    report = _new_report()
    path = _resolve(hosts_path)
    try:
        text, newline, encoding = _read_text(path)
    except OSError as exc:
        report["ok"] = False
        report["errors"].append(f"nie mozna odczytac {path}: {exc}")
        return report

    kept, had_section, _ = _split_section(text)
    if not had_section:
        report["warnings"].append("sekcja Cisza nie istnieje - nic do usuniecia")
        return report

    report["applied"].append(f"usunieto sekcje Cisza z {path}")
    if dry_run:
        report["applied"].append("dry-run: plik hosts nie zostal zmieniony")
        return report

    payload = _render(kept, newline)
    if not _write_guarded(report, path, payload.encode(encoding, errors="replace")):
        return report
    report["applied"].append("zapisano atomowo")
    return report


def is_blocked(*, hosts_path: Optional[Path] = None) -> bool:
    """True, gdy w pliku istnieje pelna sekcja Cisza (BEGIN przed END)."""
    path = _resolve(hosts_path)
    try:
        text, _, _ = _read_text(path)
    except OSError:
        return False
    begin = -1
    end = -1
    for index, line in enumerate(text.splitlines()):
        marker = line.strip()
        if marker == BLOCK_BEGIN and begin < 0:
            begin = index
        elif marker == BLOCK_END and begin >= 0 and end < 0:
            end = index
    return begin >= 0 and end > begin


def backup(*, hosts_path: Optional[Path] = None) -> str:
    """Kopiuje hosts do katalogu kopii; zwraca sciezke kopii lub "" przy bledzie."""
    path = _resolve(hosts_path)
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    try:
        target_dir = paths.backup_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"hosts-{stamp}.bak"
        counter = 1
        while target.exists():
            target = target_dir / f"hosts-{stamp}-{counter}.bak"
            counter += 1
        target.write_bytes(data)
    except OSError:
        return ""
    _prune_backups(target_dir)
    return str(target)


def _prune_backups(directory: Path, keep: int = _BACKUP_KEEP) -> None:
    """Best-effort: zostaw tylko `keep` najnowszych kopii hosts."""
    try:
        files = sorted(directory.glob("hosts-*.bak"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def restore_backup(backup_path: str, *, hosts_path: Optional[Path] = None, dry_run: bool = False) -> dict:
    """Przywraca plik hosts z kopii (bajt w bajt)."""
    report = _new_report()
    path = _resolve(hosts_path)
    source = Path(backup_path)
    try:
        data = source.read_bytes()
    except FileNotFoundError:
        report["ok"] = False
        report["errors"].append(f"brak pliku kopii: {source}")
        return report
    except OSError as exc:
        report["ok"] = False
        report["errors"].append(f"nie mozna odczytac kopii {source}: {exc}")
        return report

    report["applied"].append(f"przywrocono {path} z kopii {source}")
    if dry_run:
        report["applied"].append("dry-run: plik hosts nie zostal zmieniony")
        return report
    _write_guarded(report, path, data)
    return report


def flush_dns(*, dry_run: bool = False) -> dict:
    """Wywoluje ipconfig /flushdns (bez wyjatkow na zewnatrz)."""
    report = _new_report()
    command = ["ipconfig", "/flushdns"]
    if dry_run:
        report["applied"].append("ipconfig /flushdns (dry-run)")
        return report
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=20,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        report["ok"] = False
        report["errors"].append("nie znaleziono programu ipconfig")
        return report
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["ok"] = False
        report["errors"].append(f"ipconfig /flushdns nie powiodlo sie: {exc}")
        return report

    output = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    error = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        report["ok"] = False
        report["errors"].append(f"ipconfig /flushdns zwrocil kod {proc.returncode}: {error or output}")
    else:
        report["applied"].append("ipconfig /flushdns")
    return report
