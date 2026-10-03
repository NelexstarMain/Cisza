#!/usr/bin/env python3
"""build_exe - buduje ``Cisza.exe`` (PyInstaller, onefile, bez konsoli).

Ikona jest rysowana w kodzie i zapisywana w skali szarosci (zgodnie z zasada
monochromatycznosci). Skrypt nie wymaga uprawnien administratora.

Uzycie:
    python tools/build_exe.py                  # dist/Cisza.exe (onefile, noconsole) + weryfikacja
    python tools/build_exe.py --onedir         # katalog dist/Cisza/
    python tools/build_exe.py --console        # z konsola (diagnostyka)
    python tools/build_exe.py --dry-run        # tylko plan, nic nie buduje
    python tools/build_exe.py --skip-verify    # bez testu .exe po budowie
    python tools/build_exe.py --no-icon        # bez ikony
    python tools/build_exe.py --with-matplotlib  # nie wykluczaj matplotlib

Po zbudowaniu skrypt uruchamia ``Cisza.exe --restore --dry-run`` (z danymi
w ``build/verify-data``) i wymaga kodu wyjscia 0 oraz braku "No module named" -
to lapie brakujace importy dynamiczne (``--collect-submodules``).

Kody wyjscia: 0 = sukces, 1 = blad budowania/weryfikacji, 2 = blad uzycia/srodowiska.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY = PROJECT_ROOT / "run.pyw"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
DEFAULT_NAME = "Cisza"

#: Moduly, ktorych w aplikacji nie ma (albo sa zbedne) - zmniejszaja rozmiar .exe.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "tkinter",
    "unittest",
    "pydoc_data",
    "setuptools",
)

#: Importy ukryte, ktorych PyInstaller czasem nie widzi sam.
HIDDEN_IMPORTS: tuple[str, ...] = (
    "psutil",
    "keyboard",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtNetwork",
)

#: Pakiety, ktorych podmoduly sa importowane dynamicznie (importlib.import_module
#: w recovery.py, helper.py, controller.py, app.py). Bez tego zamrozony .exe nie
#: potrafi np. przywrocic paska zadan ("No module named 'focuslock.shell.taskbar'").
#: Uwaga: nie zbieramy podmodulow psutil/keyboard - ich hooki i statyczne importy
#: wystarczaja, a ``--collect-submodules`` wciagnalby testy (i pytest) do .exe.
COLLECT_SUBMODULES: tuple[str, ...] = ("focuslock",)


def _pillow_available() -> bool:
    return importlib.util.find_spec("PIL") is not None


def is_windows() -> bool:
    return os.name == "nt"


def _artifact_path(name: str, onedir: bool) -> Path:
    """Sciezka wynikowego pliku/katalogu (bez sprawdzania, czy istnieje)."""
    if onedir:
        return DIST_DIR / name
    return DIST_DIR / (name + (".exe" if is_windows() else ""))


def _pyinstaller_available() -> bool:
    return importlib.util.find_spec("PyInstaller") is not None


def draw_icon(path: Path, *, size: int = 256) -> Path:
    """Rysuje monochromatyczna ikone (luk + kropka) i zapisuje ja jako .ico.

    Kanaly R, G i B sa identyczne, wiec ikona jest z definicji szara.
    """
    from PIL import Image, ImageDraw  # import lokalny: tylko gdy budujemy

    scale = 4
    big = size * scale
    image = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(image)

    pad = int(big * 0.13)
    stroke = max(2, int(big * 0.085))
    draw.arc([pad, pad, big - pad, big - pad], start=38, end=322, fill=245, width=stroke)

    radius = max(2, int(big * 0.075))
    center = big // 2
    draw.ellipse(
        [center - radius, center - radius, center + radius, center + radius], fill=200
    )

    image = image.resize((size, size), Image.LANCZOS)
    rgba = Image.merge("RGBA", (image, image, image, image))

    path.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return path


def _icon_is_gray(path: Path) -> bool:
    """Kontrola jakosci: kazdy piksel ikony ma R == G == B."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return False
    with Image.open(path) as img:
        pixels = img.convert("RGBA").getdata()
        return all(pixel[0] == pixel[1] == pixel[2] for pixel in pixels)


def verify_artifact(exe: Path, *, timeout: float = 300.0) -> tuple[bool, str]:
    """Sprawdza zamrozony .exe: ``--restore --dry-run`` bez brakujacych modulow.

    To regresja, ktora latwo przegapic: ``recovery.py``/``helper.py`` importuja
    moduly dynamicznie, wiec bez ``--collect-submodules`` .exe dziala, ale nie
    potrafi przywrocic powloki ("No module named 'focuslock.shell.taskbar'").
    Zwraca (ok, szczegoly) i nigdy nie dotyka prawdziwego ``%LOCALAPPDATA%``.
    """
    if not exe.exists():
        return False, f"brak pliku {exe}"

    verify_dir = BUILD_DIR / "verify-data"
    shutil.rmtree(verify_dir, ignore_errors=True)
    verify_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["CISZA_DATA_DIR"] = str(verify_dir)
    env["CISZA_DRY_RUN"] = "1"
    env["CISZA_SAFE"] = "1"
    # Jednoplikowy .exe rozpakowuje sie do %TEMP% - wskazujemy wlasny katalog,
    # zeby test nie zalezal od uprawnien systemowego %TEMP% (np. w sandboxie).
    runtime_temp = verify_dir / "temp"
    runtime_temp.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(runtime_temp)
    env["TMP"] = str(runtime_temp)
    # .exe budujemy bez konsoli, wiec stdout bywa pusty - raport trybow CLI
    # trafia dodatkowo do pliku (patrz focuslock/__main__.py: emit_report).
    report_file = verify_dir / "raport.txt"
    env["CISZA_REPORT_FILE"] = str(report_file)
    try:
        proc = subprocess.run(
            [str(exe), "--restore", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(verify_dir, ignore_errors=True)
        return False, f"nie udalo sie uruchomic .exe: {exc}"

    file_text = ""
    try:
        file_text = report_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        file_text = ""
    shutil.rmtree(verify_dir, ignore_errors=True)

    output = f"{proc.stdout or ''}\n{proc.stderr or ''}\n{file_text}"
    if "No module named" in output:
        culprit = next(
            (line.strip() for line in output.splitlines() if "No module named" in line), "?"
        )
        return False, f"brakujacy modul w .exe -> {culprit}"
    if proc.returncode != 0:
        return False, f"kod wyjscia {proc.returncode}: {output.strip()[:400]}"
    if "Przywracanie zakonczone" not in file_text:
        return False, "brak raportu z .exe (pusty stdout i plik raportu)"
    return True, output.strip()


def build_command(
    *,
    name: str = DEFAULT_NAME,
    onefile: bool = True,
    console: bool = False,
    icon: Path | None = None,
    with_matplotlib: bool = False,
) -> list[str]:
    """Zwraca gotowe polecenie PyInstallera (bez uruchamiania)."""
    cmd: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
        "--console" if console else "--noconsole",
        "--name",
        name,
        "--specpath",
        str(BUILD_DIR),
        "--workpath",
        str(BUILD_DIR / "work"),
        "--distpath",
        str(DIST_DIR),
        "--paths",
        str(PROJECT_ROOT),
    ]
    if icon is not None:
        cmd += ["--icon", str(icon)]
    for module in COLLECT_SUBMODULES:
        cmd += ["--collect-submodules", module]
    for module in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", module]
    excludes = DEFAULT_EXCLUDES if not with_matplotlib else tuple(
        item for item in DEFAULT_EXCLUDES if item != "matplotlib"
    )
    for module in excludes:
        cmd += ["--exclude-module", module]
    cmd.append(str(ENTRY))
    return cmd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_exe", description="Buduje monochromatyczny Cisza.exe przez PyInstaller."
    )
    parser.add_argument("--name", default=DEFAULT_NAME, help="nazwa pliku wyjsciowego")
    parser.add_argument("--onedir", action="store_true", help="katalog zamiast jednego pliku")
    parser.add_argument("--console", action="store_true", help="zachowaj okno konsoli (diagnostyka)")
    parser.add_argument("--no-icon", action="store_true", help="nie generuj ikony")
    parser.add_argument("--icon", default="", help="wlasna ikona .ico")
    parser.add_argument("--with-matplotlib", action="store_true", help="nie wykluczaj matplotlib")
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="nie uruchamiaj po budowie testu .exe (--restore --dry-run)",
    )
    parser.add_argument("--dry-run", action="store_true", help="wypisz plan i zakoncz")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    if not ENTRY.exists():
        print(f"build_exe: brak pliku wejsciowego {ENTRY}", file=sys.stderr)
        return 2
    if not _pyinstaller_available():
        print(
            "build_exe: brak PyInstallera - zainstaluj: python -m pip install pyinstaller",
            file=sys.stderr,
        )
        return 2

    icon: Path | None = None
    if args.icon:
        icon = Path(args.icon)
        if not icon.exists():
            print(f"build_exe: nie ma ikony {icon}", file=sys.stderr)
            return 2
    elif not args.no_icon:
        if not _pillow_available():
            print(
                "build_exe: brak Pillow - pomijam ikone (albo uzyj --no-icon / -m pip install Pillow)",
                file=sys.stderr,
            )
        else:
            icon = BUILD_DIR / "cisza.ico"

    cmd = build_command(
        name=args.name,
        onefile=not args.onedir,
        console=args.console,
        icon=icon,
        with_matplotlib=args.with_matplotlib,
    )

    if args.dry_run:
        print("build_exe: tryb --dry-run, nic nie zostanie zbudowane.")
        print(f"  projekt : {PROJECT_ROOT}")
        print(f"  wejscie : {ENTRY}")
        print(f"  ikona   : {icon if icon else '(brak)'}")
        print(f"  wynik   : {_artifact_path(args.name, args.onedir)}")
        print(f"  weryfik.: {'pominieta (--skip-verify)' if args.skip_verify else '--restore --dry-run bez brakujacych modulow'}")
        print("  polecenie:")
        print("    " + subprocess.list2cmdline(cmd))
        return 0

    if icon is not None:
        try:
            draw_icon(icon)
        except Exception as exc:  # noqa: BLE001 - budowanie musi isc dalej
            print(f"build_exe: nie udalo sie narysowac ikony ({exc}); buduje bez ikony", file=sys.stderr)
            icon = None
            cmd = build_command(
                name=args.name,
                onefile=not args.onedir,
                console=args.console,
                icon=None,
                with_matplotlib=args.with_matplotlib,
            )
        else:
            if not _icon_is_gray(icon):
                print("build_exe: UWAGA - ikona nie jest w skali szarosci", file=sys.stderr)
            else:
                print(f"build_exe: ikona (szara) -> {icon}")

    print("build_exe: uruchamiam PyInstallera...")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print(f"build_exe: PyInstaller zwrocil kod {result.returncode}", file=sys.stderr)
        return 1

    suffix = ".exe" if sys.platform == "win32" and not args.onedir else ""
    artifact = DIST_DIR / (args.name + suffix)
    if args.onedir:
        artifact = DIST_DIR / args.name
    if not artifact.exists():
        print(f"build_exe: PyInstaller zakonczyl sie bez bledu, ale brak {artifact}", file=sys.stderr)
        return 1

    if artifact.is_file():
        print(f"build_exe: OK -> {artifact} ({artifact.stat().st_size / 1_048_576:.1f} MB)")
    else:
        print(f"build_exe: OK -> {artifact}")

    if args.skip_verify:
        print("build_exe: weryfikacja pominieta (--skip-verify).")
        return 0
    if not is_windows():
        print("build_exe: weryfikacja .exe mozliwa tylko na Windows - pomijam.")
        return 0

    exe = artifact if artifact.is_file() else artifact / (args.name + ".exe")
    print(f"build_exe: weryfikuje {exe.name} (--restore --dry-run, CISZA_DATA_DIR w build/)...")
    ok, detail = verify_artifact(exe)
    if not ok:
        print(f"build_exe: WERYFIKACJA NIEUDANA - {detail}", file=sys.stderr)
        print("  Wskazowka: dodaj brakujacy modul do COLLECT_SUBMODULES/HIDDEN_IMPORTS.", file=sys.stderr)
        return 1
    print("build_exe: weryfikacja OK - brak 'No module named', kod wyjscia 0.")
    if detail:
        for line in detail.splitlines()[:6]:
            print(f"    {line}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
