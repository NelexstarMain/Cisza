#!/usr/bin/env python3
"""mono_lint - pilnuje, zeby UI Ciszy bylo wylacznie w skali szarosci.

Skanuje pliki zrodlowe (domyslnie ``focuslock/``) i raportuje kazde uzycie
koloru, ktory nie ma postaci ``R == G == B``:

* hex: ``#abc``, ``#abcd``, ``#aabbcc``, ``#aabbccdd``,
* funkcyjne: ``rgb(...)``, ``rgba(...)``, ``hsl(...)``, ``hsla(...)``,
* nazwane kolory CSS/Qt uzywane jako wartosc wlasciwosci stylu lub w ``QColor("...")``.

Uzycie:
    python tools/mono_lint.py                 # skanuje focuslock/
    python tools/mono_lint.py focuslock/ui    # skanuje wskazany katalog
    python tools/mono_lint.py --json          # raport maszynowy
    python tools/mono_lint.py --quiet         # tylko podsumowanie

Kody wyjscia:
    0 - brak naruszen,
    1 - znaleziono nie-szare kolory,
    2 - blad uzycia / brak sciezki.

Wyjatek od reguly mozna zaznaczyc w tej samej linii komentarzem
``mono-lint: allow`` (albo ``mono_lint: ignore``) - wtedy linia jest pomijana.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

DEFAULT_ROOTS: tuple[str, ...] = ("focuslock",)

# Rozszerzenia traktowane jako pliki UI (poza .py skanujemy tez style i HTML).
SCANNED_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".pyw", ".qss", ".css", ".ui", ".html", ".htm", ".svg", ".qrc", ".theme"}
)

SKIPPED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "build",
        "dist",
        ".idea",
        ".vscode",
    }
)

ALLOW_PRAGMA_RE = re.compile(r"mono[-_]lint\s*:\s*(?:allow|ignore)", re.IGNORECASE)

_HEX = r"[0-9a-fA-F]"
HEX_RE = re.compile(
    rf"#(?P<hex>{_HEX}{{8}}|{_HEX}{{6}}|{_HEX}{{4}}|{_HEX}{{3}})(?![0-9a-fA-F])"
)
FUNC_COLOR_RE = re.compile(
    r"\b(?P<fn>rgba|rgb|hsla|hsl)\s*\((?P<body>[^()]*)\)", re.IGNORECASE
)
QCOLOR_RE = re.compile(r"""QColor\s*\(\s*['"](?P<name>[A-Za-z]+)['"]""")
STYLE_PROP_RE = re.compile(
    r"(?:^|[;{\s'\"(])(?:color|background|background-color|border|border-color|"
    r"outline-color|fill|stroke|selection-background-color|gridline-color)\s*[:=]\s*"
    r"['\"]?(?P<name>[A-Za-z]+)\b",
    re.IGNORECASE,
)
QT_COLOR_RE = re.compile(r"\bQt\.(?:GlobalColor\.)?(?P<name>[A-Za-z]+)\b")

# Nazwane kolory CSS (pola, ktore sa szare, wyliczane nizej).
_NAMED_HEX: dict[str, str] = {
    "aliceblue": "#F0F8FF", "antiquewhite": "#FAEBD7", "aqua": "#00FFFF",
    "aquamarine": "#7FFFD4", "azure": "#F0FFFF", "beige": "#F5F5DC",
    "bisque": "#FFE4C4", "black": "#000000", "blanchedalmond": "#FFEBCD",
    "blue": "#0000FF", "blueviolet": "#8A2BE2", "brown": "#A52A2A",
    "burlywood": "#DEB887", "cadetblue": "#5F9EA0", "chartreuse": "#7FFF00",
    "chocolate": "#D2691E", "coral": "#FF7F50", "cornflowerblue": "#6495ED",
    "cornsilk": "#FFF8DC", "crimson": "#DC143C", "cyan": "#00FFFF",
    "darkblue": "#00008B", "darkcyan": "#008B8B", "darkgoldenrod": "#B8860B",
    "darkgray": "#A9A9A9", "darkgrey": "#A9A9A9", "darkgreen": "#006400",
    "darkkhaki": "#BDB76B", "darkmagenta": "#8B008B", "darkolivegreen": "#556B2F",
    "darkorange": "#FF8C00", "darkorchid": "#9932CC", "darkred": "#8B0000",
    "darksalmon": "#E9967A", "darkseagreen": "#8FBC8F", "darkslateblue": "#483D8B",
    "darkslategray": "#2F4F4F", "darkslategrey": "#2F4F4F", "darkturquoise": "#00CED1",
    "darkviolet": "#9400D3", "deeppink": "#FF1493", "deepskyblue": "#00BFFF",
    "dimgray": "#696969", "dimgrey": "#696969", "dodgerblue": "#1E90FF",
    "firebrick": "#B22222", "floralwhite": "#FFFAF0", "forestgreen": "#228B22",
    "fuchsia": "#FF00FF", "gainsboro": "#DCDCDC", "ghostwhite": "#F8F8FF",
    "gold": "#FFD700", "goldenrod": "#DAA520", "gray": "#808080",
    "grey": "#808080", "green": "#008000", "greenyellow": "#ADFF2F",
    "honeydew": "#F0FFF0", "hotpink": "#FF69B4", "indianred": "#CD5C5C",
    "indigo": "#4B0082", "ivory": "#FFFFF0", "khaki": "#F0E68C",
    "lavender": "#E6E6FA", "lavenderblush": "#FFF0F5", "lawngreen": "#7CFC00",
    "lemonchiffon": "#FFFACD", "lightblue": "#ADD8E6", "lightcoral": "#F08080",
    "lightcyan": "#E0FFFF", "lightgoldenrodyellow": "#FAFAD2", "lightgray": "#D3D3D3",
    "lightgrey": "#D3D3D3", "lightgreen": "#90EE90", "lightpink": "#FFB6C1",
    "lightsalmon": "#FFA07A", "lightseagreen": "#20B2AA", "lightskyblue": "#87CEFA",
    "lightslategray": "#778899", "lightslategrey": "#778899", "lightsteelblue": "#B0C4DE",
    "lightyellow": "#FFFFE0", "lime": "#00FF00", "limegreen": "#32CD32",
    "linen": "#FAF0E6", "magenta": "#FF00FF", "maroon": "#800000",
    "mediumaquamarine": "#66CDAA", "mediumblue": "#0000CD", "mediumorchid": "#BA55D3",
    "mediumpurple": "#9370DB", "mediumseagreen": "#3CB371", "mediumslateblue": "#7B68EE",
    "mediumspringgreen": "#00FA9A", "mediumturquoise": "#48D1CC", "mediumvioletred": "#C71585",
    "midnightblue": "#191970", "mintcream": "#F5FFFA", "mistyrose": "#FFE4E1",
    "moccasin": "#FFE4B5", "navajowhite": "#FFDEAD", "navy": "#000080",
    "oldlace": "#FDF5E6", "olive": "#808000", "olivedrab": "#6B8E23",
    "orange": "#FFA500", "orangered": "#FF4500", "orchid": "#DA70D6",
    "palegoldenrod": "#EEE8AA", "palegreen": "#98FB98", "paleturquoise": "#AFEEEE",
    "palevioletred": "#DB7093", "papayawhip": "#FFEFD5", "peachpuff": "#FFDAB9",
    "peru": "#CD853F", "pink": "#FFC0CB", "plum": "#DDA0DD",
    "powderblue": "#B0E0E6", "purple": "#800080", "rebeccapurple": "#663399",
    "red": "#FF0000", "rosybrown": "#BC8F8F", "royalblue": "#4169E1",
    "saddlebrown": "#8B4513", "salmon": "#FA8072", "sandybrown": "#F4A460",
    "seagreen": "#2E8B57", "seashell": "#FFF5EE", "sienna": "#A0522D",
    "silver": "#C0C0C0", "skyblue": "#87CEEB", "slateblue": "#6A5ACD",
    "slategray": "#708090", "slategrey": "#708090", "snow": "#FFFAFA",
    "springgreen": "#00FF7F", "steelblue": "#4682B4", "tan": "#D2B48C",
    "teal": "#008080", "thistle": "#D8BFD8", "tomato": "#FF6347",
    "turquoise": "#40E0D0", "violet": "#EE82EE", "wheat": "#F5DEB3",
    "white": "#FFFFFF", "whitesmoke": "#F5F5F5", "yellow": "#FFFF00",
    "yellowgreen": "#9ACD32",
}


def _hex_is_gray(value: str) -> bool:
    value = value.lstrip("#")
    if len(value) in (3, 4):
        value = "".join(ch * 2 for ch in value[:3])
    if len(value) == 8:
        value = value[:6]
    if len(value) != 6:
        return False
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return r == g == b


GRAY_NAMED: frozenset[str] = frozenset(
    name for name, code in _NAMED_HEX.items() if _hex_is_gray(code)
)
NAMED_COLORS: frozenset[str] = frozenset(_NAMED_HEX)


def _rgb_is_gray(body: str) -> bool | None:
    """None, gdy nie da sie sparsowac; inaczej czy skladowe sa rowne."""
    parts = [p.strip() for p in body.replace("/", " ").split(",")]
    parts = [p for chunk in parts for p in chunk.split()]
    if len(parts) < 3:
        return None
    values: list[float] = []
    for raw in parts[:3]:
        if raw.endswith("%"):
            try:
                values.append(round(float(raw[:-1]) * 255 / 100))
            except ValueError:
                return None
        else:
            try:
                values.append(float(raw))
            except ValueError:
                return None
    return values[0] == values[1] == values[2]


def _hsl_is_gray(body: str) -> bool | None:
    """Kolor HSL jest szary tylko przy nasyceniu 0% (albo 0)."""
    match = re.search(r"([-+]?\d*\.?\d+)\s*%?\s*[, ]\s*([-+]?\d*\.?\d+)\s*%", body)
    if not match:
        return None
    try:
        saturation = float(match.group(2))
    except ValueError:
        return None
    return saturation == 0.0


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    column: int
    color: str
    kind: str
    text: str

    def format(self, root: str | None = None) -> str:
        shown = self.path
        if root:
            try:
                shown = str(Path(self.path).resolve().relative_to(Path(root).resolve()))
            except (ValueError, OSError):
                shown = self.path
        return f"{shown}:{self.line}:{self.column}: {self.color} [{self.kind}]"


def scan_text(text: str, path: str = "<text>") -> list[Finding]:
    """Zwraca naruszenia w podanym tekscie (bez czytania dysku)."""
    findings: list[Finding] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if ALLOW_PRAGMA_RE.search(line):
            continue
        for match in HEX_RE.finditer(line):
            value = match.group("hex")
            if not _hex_is_gray(value):
                findings.append(
                    Finding(path, index, match.start() + 1, f"#{value}", "hex", line.strip())
                )
        for match in FUNC_COLOR_RE.finditer(line):
            fn = match.group("fn").lower()
            body = match.group("body")
            gray = _hsl_is_gray(body) if fn.startswith("hsl") else _rgb_is_gray(body)
            if gray is False:
                findings.append(
                    Finding(path, index, match.start() + 1, match.group(0).strip(), "func", line.strip())
                )
        for regex, kind in ((QCOLOR_RE, "name"), (STYLE_PROP_RE, "name"), (QT_COLOR_RE, "qt")):
            for match in regex.finditer(line):
                name = match.group("name").lower()
                if name in NAMED_COLORS and name not in GRAY_NAMED:
                    findings.append(
                        Finding(path, index, match.start() + 1, name, kind, line.strip())
                    )
    findings.sort(key=lambda f: (f.line, f.column, f.color))
    return findings


def scan_file(path: Path) -> list[Finding]:
    """Skanuje jeden plik; bledy odczytu zwraca jako naruszenie ``io``."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:  # pragma: no cover - sytuacje brzegowe dysku
        return [Finding(str(path), 0, 0, f"<IO: {exc}>", "io", "")]
    return scan_text(text, str(path))


def discover(paths: Sequence[str | Path] = DEFAULT_ROOTS) -> list[Path]:
    """Zwraca liste plikow do przeskanowania (posortowana, bez duplikatow)."""
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        base = Path(raw)
        if base.is_file():
            candidates: Iterable[Path] = [base]
        elif base.is_dir():
            candidates = (
                child
                for child in base.rglob("*")
                if child.is_file()
                and child.suffix.lower() in SCANNED_SUFFIXES
                and not any(part in SKIPPED_DIRS for part in child.parts)
            )
        else:
            continue
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(candidate)
    return sorted(found, key=lambda p: str(p).lower())


def lint(paths: Sequence[str | Path] = DEFAULT_ROOTS) -> list[Finding]:
    findings: list[Finding] = []
    for path in discover(paths):
        findings.extend(scan_file(path))
    return findings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mono_lint",
        description="Wykrywa nie-szare kolory w plikach UI Ciszy (R != G != B).",
    )
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_ROOTS), help="pliki lub katalogi (domyslnie focuslock/)")
    parser.add_argument("--json", action="store_true", help="raport w formacie JSON")
    parser.add_argument("--quiet", action="store_true", help="nie wypisuj listy naruszen")
    parser.add_argument("--max", type=int, default=0, metavar="N", help="ogranicz liczbe wypisanych naruszen (0 = bez limitu)")
    parser.add_argument("--root", default="", help="katalog, wzgledem ktorego skrocic sciezki w raporcie")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - nietypowe stdout
            pass
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    missing = [p for p in args.paths if not Path(p).exists()]
    if missing:
        print(f"mono_lint: nie znaleziono sciezki: {', '.join(missing)}", file=sys.stderr)
        return 2

    files = discover(args.paths)
    findings = lint(args.paths)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not findings,
                    "files_scanned": len(files),
                    "violations": len(findings),
                    "findings": [asdict(f) for f in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if findings else 0

    if not args.quiet:
        shown = findings if not args.max else findings[: args.max]
        for finding in shown:
            print(finding.format(args.root or None))
        if args.max and len(findings) > args.max:
            print(f"... i {len(findings) - args.max} wiecej")

    if findings:
        print(
            f"\nmono_lint: {len(findings)} nie-szarych kolorow w {len(files)} plikach - "
            f"UI musi byc czarno-biale (uzyj tokenow z focuslock/ui/theme.py)."
        )
        return 1

    print(f"mono_lint: OK - {len(files)} plikow, 0 nie-szarych kolorow.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
