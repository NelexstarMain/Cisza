"""Katalog aplikacji zainstalowanych na komputerze.

Zbieramy kandydatow z czterech zrodel (w tej kolejnosci jakosci nazw):
  1. skroty w Menu Start (.lnk -> TargetPath)  - najlepsze, czytelne nazwy,
  2. aplikacje ze Sklepu (Get-StartApps, AppID),
  3. rejestr Uninstall (DisplayName + DisplayIcon/InstallLocation),
  4. aktualnie uruchomione procesy (psutil) - dokladne nazwy procesow.

Wynik jest cache'owany w %LOCALAPPDATA%\\Cisza\\apps_cache.json (TTL 24 h), zeby
otwarcie kreatora sesji bylo natychmiastowe; przycisk "ODSWIEZ" wymusza skan.

Modul jest czysto Windowsowy i nie wymaga uprawnien administratora ani PyQt.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import paths

CACHE_NAME = "apps_cache.json"
CACHE_TTL_SECONDS = 24 * 3600
POWERSHELL_TIMEOUT = 120.0
MAX_APPS = 400

# Nazwy, ktore nie sa aplikacjami uzytkownika (instalatory, aktualizatory, dokumentacja).
NOISE_PATTERNS: tuple[str, ...] = (
    r"uninstall",
    r"deinstal",
    r"odinstaluj",
    r"^setup",
    r"\bsetup\b",
    r"installer",
    r"instalator",
    r"updater",
    r"update\b",
    r"aktualizacj",
    r"readme",
    r"release notes",
    r"changelog",
    r"licen[cs]",
    r"documentation",
    r"dokumentacj",
    r"^help$",
    r"pomoc$",
    r"website",
    r"^strona ",
    r"repair",
    r"napraw",
    r"modify",
    r"modyfikuj",
    r"^remove\b",
    r"^usun\b",
    r"\bremove\b",
    r"crash",
    r"report",
    r"redistributable",
    r"runtime",
    r"\.net ",
    r"visual c\+\+",
    r"directx",
    r"driver",
    r"sterownik",
    r"^python \d",
    r"^java",
    r"^node\.js",
    r"^git for windows",
    r"^windows ",
    r"^microsoft edge webview",
    r"^microsoft visual studio installer",
    r"sample",
    r"example",
)
_NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)

# Nazwy plikow wykonywalnych, ktore sa instalatorami/aktualizatorami, a nie aplikacja.
_INSTALLER_EXE_RE = re.compile(
    r"(unins|deinstal|setup|install|updater|update|remove|repair|napraw)", re.IGNORECASE
)

# Procesy, ktore nigdy nie sa "aplikacja do nauki" (uslugi systemowe i tlo).
BACKGROUND_PATTERNS: tuple[str, ...] = (
    r"svchost",
    r"service",
    r"helper",
    r"crashpad",
    r"conhost",
    r"dllhost",
    r"runtimebroker",
    r"backgroundtaskhost",
    r"^audiodg",
    r"^dwm",
    r"^taskhost",
    r"^sihost",
    r"^searchindexer",
    r"^msmpeng",
    r"^nissrv",
    r"^wmiprvse",
    r"^spoolsv",
    r"^fontdrvhost",
    r"^ctfmon",
    r"^securityhealth",
    r"^widgets",
    r"^startmenuexperiencehost",
    r"^shellexperiencehost",
    r"^textinputhost",
    r"^applicationframehost",
    r"^searchhost",
    r"^phoneexperiencehost",
    r"^lockapp",
    r"^systemsettings",
    r"^sppsvc",
    r"^usoclient",
)
_BACKGROUND_RE = re.compile("|".join(BACKGROUND_PATTERNS), re.IGNORECASE)

# Skroty, ktore wskazuja na narzedzia systemowe, a nie aplikacje uzytkownika.
SYSTEM_TARGETS: frozenset[str] = frozenset(
    {
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "control.exe",
        "mmc.exe",
        "regedit.exe",
        "taskmgr.exe",
        "explorer.exe",
        "wscript.exe",
        "cscript.exe",
        "mshta.exe",
        "rundll32.exe",
        "dxdiag.exe",
        "cleanmgr.exe",
        "eventvwr.exe",
        "perfmon.exe",
        "resmon.exe",
        "winver.exe",
        "charmap.exe",
        "narrator.exe",
        "magnify.exe",
        "osk.exe",
        "utilman.exe",
        "sndvol.exe",
        "wusa.exe",
        "msiexec.exe",
        "setx.exe",
        "python.exe",
        "pythonw.exe",
        "unins000.exe",
        # przystawki i narzedzia systemowe Windows
        "dfrgui.exe",
        "mdsched.exe",
        "msconfig.exe",
        "msinfo32.exe",
        "iscsicpl.exe",
        "odbcad32.exe",
        "mstsc.exe",
        "psr.exe",
        "recoverydrive.exe",
        "vmcreate.exe",
        "livecaptions.exe",
        "voiceaccess.exe",
        "setlang.exe",
        "chkdsk.exe",
        "diskpart.exe",
        "wslg.exe",
    }
)


@dataclass(frozen=True)
class CatalogApp:
    """Pojedyncza pozycja katalogu aplikacji.

    `path`      - sciezka do pliku .exe (o ile znana),
    `icon_path` - plik, z ktorego najlepiej wyciagnac ikone (.lnk, .exe, .ico),
    `source`    - skad wiemy o aplikacji (startmenu/store/registry/running).
    """

    name: str
    exe: str
    path: str = ""
    source: str = ""
    icon_path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "exe": self.exe,
            "path": self.path,
            "source": self.source,
            "icon_path": self.icon_source,
            "label": self.label,
        }

    @property
    def process_name(self) -> str:
        return self.exe

    @property
    def label(self) -> str:
        if self.exe and self.exe.lower() not in self.name.lower():
            return f"{self.name} ({self.exe})"
        return self.name

    @property
    def icon_source(self) -> str:
        """Sciezka do pliku z ikona - tylko istniejące pliki (AppID nie jest plikiem)."""
        for candidate in (self.icon_path, self.path):
            if candidate and os.path.exists(candidate):
                return candidate
        return ""

    @property
    def has_icon(self) -> bool:
        """Czy znamy plik z ikona (bez sprawdzania dysku - do scalania i cache)."""
        return bool(self.icon_path)

    def cache_dict(self) -> dict:
        """Surowy zapis do cache - bez sprawdzania istnienia plikow."""
        return {
            "name": self.name,
            "exe": self.exe,
            "path": self.path,
            "source": self.source,
            "icon_path": self.icon_path,
        }


# Procesy towarzyszace: gdy uzytkownik wybierze glowna aplikacje, jej pomocnicze
# procesy tez musza byc dozwolone - inaczej guard je wstrzyma i aplikacja padnie.
COMPANION_PROCESSES: dict[str, tuple[str, ...]] = {
    "msedge.exe": ("msedge_proxy.exe", "identity_helper.exe", "msedgewebview2.exe"),
    "chrome.exe": ("chrome_proxy.exe",),
    "brave.exe": ("brave_proxy.exe",),
    "steam.exe": ("steamwebhelper.exe",),
    "windowsterminal.exe": ("openconsole.exe", "wt.exe"),
    "wt.exe": ("openconsole.exe", "windowsterminal.exe"),
    "pycharm64.exe": ("fsnotifier.exe",),
    "idea64.exe": ("fsnotifier.exe",),
    "webstorm64.exe": ("fsnotifier.exe",),
    "clion64.exe": ("fsnotifier.exe",),
    "datagrip64.exe": ("fsnotifier.exe",),
    "goland64.exe": ("fsnotifier.exe",),
    "rider64.exe": ("fsnotifier.exe",),
    "phpstorm64.exe": ("fsnotifier.exe",),
    "obsidian.exe": (),
    "code.exe": (),
}


def companions_for(exe: str) -> tuple[str, ...]:
    """Zwraca procesy towarzyszace dla podanego pliku .exe (moze byc pusty)."""
    return COMPANION_PROCESSES.get((exe or "").strip().lower(), ())


def expand_with_companions(names: Iterable[str]) -> list[str]:
    """Rozszerza liste procesow o ich procesy towarzyszace (bez duplikatow)."""
    expanded: list[str] = []
    for name in names:
        key = str(name or "").strip().lower()
        if not key:
            continue
        if key not in expanded:
            expanded.append(key)
        for companion in companions_for(key):
            if companion not in expanded:
                expanded.append(companion)
    return expanded


# --------------------------------------------------------------------- narzedzia
def start_menu_dirs() -> list[Path]:
    dirs: list[Path] = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        candidate = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if candidate.exists():
            dirs.append(candidate)
    return dirs


def clean_name(value: str) -> str:
    name = (value or "").strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"\s*-\s*(skrót|shortcut)$", "", name, flags=re.IGNORECASE)
    return name.strip(" .-")


def is_noise(name: str, exe: str = "") -> bool:
    text = f"{name} {exe}"
    if not text.strip():
        return True
    if _NOISE_RE.search(name) or _NOISE_RE.search(exe):
        return True
    if exe and _INSTALLER_EXE_RE.search(exe):
        return True
    if exe.lower() in SYSTEM_TARGETS:
        return True
    return False


def is_background_process(name: str) -> bool:
    return bool(_BACKGROUND_RE.search(name))


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


# ------------------------------------------------------------------ 1. Menu Start
def _powershell_json(script: str, timeout: float = POWERSHELL_TIMEOUT) -> Optional[dict]:
    """Uruchamia skrypt PowerShell i zwraca sparsowany JSON (albo None).

    Wynik idzie przez plik tymczasowy w UTF-8, a nie przez stdout - konsola
    systemowa (cp1250) inaczej nie odczyta polskich nazw aplikacji.
    """
    import base64
    import tempfile

    out_file = Path(tempfile.gettempdir()) / f"cisza-ps-{os.getpid()}-{int(time.time() * 1000)}.json"
    prepared = script.replace("__OUT__", str(out_file).replace("'", "''"))
    encoded = prepared.encode("utf-16-le")
    payload = base64.b64encode(encoded).decode("ascii")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for binary in ("powershell.exe", "pwsh.exe"):
            try:
                result = subprocess.run(
                    [binary, "-NoProfile", "-NonInteractive", "-EncodedCommand", payload],
                    capture_output=True,
                    timeout=timeout,
                    creationflags=creationflags,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode != 0 or not out_file.exists():
                continue
            try:
                return json.loads(out_file.read_text(encoding="utf-8-sig", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
        return None
    finally:
        try:
            out_file.unlink(missing_ok=True)
        except OSError:
            pass


_START_MENU_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$shell = New-Object -ComObject WScript.Shell
$dirs = @(
  (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'),
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
)
$shortcuts = New-Object System.Collections.ArrayList
foreach ($dir in $dirs) {
  if (-not (Test-Path -LiteralPath $dir)) { continue }
  Get-ChildItem -LiteralPath $dir -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
    $target = ''
    $arguments = ''
    try { $link = $shell.CreateShortcut($_.FullName); $target = $link.TargetPath; $arguments = $link.Arguments } catch {}
    [void]$shortcuts.Add([pscustomobject]@{ name = $_.BaseName; target = $target; args = $arguments; lnk = $_.FullName })
  }
}
# Mapa AppID -> plik wykonywalny dla aplikacji ze Sklepu (tylko te z Menu Start).
$appList = @()
try { $appList = @(Get-StartApps) } catch {}
$families = New-Object 'System.Collections.Generic.HashSet[string]'
foreach ($item in $appList) {
  if ($item.AppID -like '*!*') { [void]$families.Add(($item.AppID -split '!')[0]) }
}
$exeMap = @{}
$logoMap = @{}
try {
  Get-AppxPackage | Where-Object { $families.Contains($_.PackageFamilyName) } | ForEach-Object {
    $pkg = $_
    try {
      $manifest = Get-AppxPackageManifest -Package $pkg.PackageFullName
      foreach ($app in $manifest.Package.Applications.Application) {
        $key = $pkg.PackageFamilyName + '!' + $app.Id
        if ($app.Executable) { $exeMap[$key] = $app.Executable }
        $logo = $app.VisualElements.Square44x44Logo
        if (-not $logo) { $logo = $app.VisualElements.Square150x150Logo }
        if ($logo) {
          $candidate = Join-Path $pkg.InstallLocation $logo
          if (Test-Path -LiteralPath $candidate) {
            $logoMap[$key] = $candidate
          } else {
            # Manifest podaje nazwe bazowa, a plik czesto istnieje z kwalifikatorem skali.
            $dir = Split-Path -Parent $candidate
            $base = [System.IO.Path]::GetFileNameWithoutExtension($candidate)
            $ext = [System.IO.Path]::GetExtension($candidate)
            $found = Get-ChildItem -LiteralPath $dir -Filter ($base + '*') -ErrorAction SilentlyContinue |
              Where-Object { $_.Extension -eq $ext } | Select-Object -First 1
            if ($found) { $logoMap[$key] = $found.FullName }
          }
        }
      }
    } catch {}
  }
} catch {}
# Aplikacje widoczne w Menu Start (bez skrotow systemowych i tla).
$packaged = New-Object System.Collections.ArrayList
foreach ($item in $appList) {
  $exe = $exeMap[$item.AppID]
  if ($exe) {
    [void]$packaged.Add([pscustomobject]@{ name = $item.Name; exe = $exe; appid = $item.AppID; logo = $logoMap[$item.AppID] })
  }
}
[pscustomobject]@{ shortcuts = $shortcuts; startapps = $packaged } | ConvertTo-Json -Depth 4 -Compress | Set-Content -LiteralPath '__OUT__' -Encoding UTF8
"""


def _shortcuts_dir_scan() -> list[CatalogApp]:
    """Zapasowy skan samych nazw skrotow, gdy PowerShell jest niedostepny."""
    apps: list[CatalogApp] = []
    for directory in start_menu_dirs():
        try:
            entries = directory.rglob("*.lnk")
        except OSError:
            continue
        for entry in entries:
            name = clean_name(entry.stem)
            if is_noise(name):
                continue
            apps.append(
                CatalogApp(name=name, exe="", path="", source="startmenu", icon_path=str(entry))
            )
    return apps


def from_start_menu(timeout: float = POWERSHELL_TIMEOUT) -> list[CatalogApp]:
    data = _powershell_json(_START_MENU_SCRIPT, timeout=timeout)
    if not data:
        return _shortcuts_dir_scan()
    apps: list[CatalogApp] = []
    for item in data.get("shortcuts") or []:
        name = clean_name(str(item.get("name", "")))
        target = str(item.get("target", "") or "").strip().strip('"')
        if not name:
            continue
        target_path = Path(target) if target else None
        if target_path is not None and target_path.is_dir():
            # Skrot do folderu - szukamy w nim glownego pliku .exe.
            target = _guess_exe_in_dir(target_path) or ""
            target_path = Path(target) if target else None
        exe = target_path.name.lower() if target_path and target_path.suffix.lower() == ".exe" else ""
        # Skroty bez rozwiazanego .exe (foldery, przystawki MMC, linki sklepowe)
        # pomijamy - i tak nie dalo by sie ich dopasowac po nazwie procesu.
        if not exe or is_noise(name, exe):
            continue
        lnk = str(item.get("lnk", "") or "")
        icon = lnk if lnk and Path(lnk).exists() else str(target_path)
        apps.append(
            CatalogApp(name=name, exe=exe, path=str(target_path), source="startmenu", icon_path=icon)
        )
    for item in data.get("startapps") or []:
        name = clean_name(str(item.get("name", "")))
        exe = str(item.get("exe", "") or "").strip().lower()
        appid = str(item.get("appid", "") or "")
        if not name or not exe or is_noise(name, exe):
            continue
        if not exe.endswith(".exe"):
            exe = f"{exe}.exe"
        logo = str(item.get("logo", "") or "")
        icon = logo if logo and os.path.exists(logo) else ""
        apps.append(
            CatalogApp(name=name, exe=Path(exe).name, path=appid, source="store", icon_path=icon)
        )
    return apps


def _guess_exe_in_dir(directory: Path) -> Optional[str]:
    """Wybiera najbardziej prawdopodobny plik .exe w katalogu (bez rekurencji)."""
    try:
        candidates = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".exe"]
    except OSError:
        return None
    candidates = [p for p in candidates if not is_noise(p.stem, p.name)]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return str(candidates[0])


# -------------------------------------------------------------------- 3. Rejestr
def from_registry() -> list[CatalogApp]:
    """Czyta liste zainstalowanych programow z kluczy Uninstall."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - tylko Windows
        return []

    apps: list[CatalogApp] = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
    ]
    for hive, base, view in roots:
        try:
            with winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view) as key:
                count = winreg.QueryInfoKey(key)[0]
                names = [winreg.EnumKey(key, index) for index in range(count)]
        except OSError:
            continue
        for sub in names:
            try:
                with winreg.OpenKey(hive, f"{base}\\{sub}", 0, winreg.KEY_READ | view) as entry:
                    display = _reg_str(entry, "DisplayName")
                    if not display or is_noise(display):
                        continue
                    if _reg_int(entry, "SystemComponent") == 1:
                        continue
                    icon = _reg_str(entry, "DisplayIcon")
                    location = _reg_str(entry, "InstallLocation")
            except OSError:
                continue
            exe = _exe_from_icon(icon) or _exe_from_location(location, display)
            if not exe:
                continue
            exe_name = Path(exe).name.lower()
            if is_noise(display, exe_name):
                continue
            icon_file = icon.strip().strip('"').split(",")[0].strip().strip('"')
            apps.append(
                CatalogApp(
                    name=clean_name(display),
                    exe=exe_name,
                    path=exe if os.path.isabs(exe) else "",
                    source="registry",
                    icon_path=icon_file if icon_file and os.path.exists(icon_file) else "",
                )
            )
    return apps


def _reg_str(key, name: str) -> str:
    import winreg

    try:
        value, _kind = winreg.QueryValueEx(key, name)
        return str(value or "").strip()
    except OSError:
        return ""


def _reg_int(key, name: str) -> int:
    import winreg

    try:
        value, _kind = winreg.QueryValueEx(key, name)
        return int(value)
    except (OSError, TypeError, ValueError):
        return 0


def _exe_from_icon(icon: str) -> str:
    if not icon:
        return ""
    candidate = icon.strip().strip('"').split(",")[0].strip().strip('"')
    if candidate.lower().endswith(".exe") and os.path.exists(candidate):
        return candidate
    return ""


def _exe_from_location(location: str, display: str) -> str:
    if not location:
        return ""
    directory = Path(location.strip().strip('"'))
    if not directory.is_dir():
        return ""
    try:
        candidates = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".exe"]
    except OSError:
        return ""
    candidates = [p for p in candidates if not is_noise(p.stem, p.name)]
    if not candidates:
        return ""
    wanted = _norm(display)
    ranked = sorted(
        candidates,
        key=lambda p: (_norm(p.stem) in wanted or wanted in _norm(p.stem), p.stat().st_size if p.exists() else 0),
        reverse=True,
    )
    return str(ranked[0])


# ---------------------------------------------------------- 4. Uruchomione procesy
def pids_with_windows() -> set[int]:
    """PID-y procesow, ktore maja widoczne okno (czyli realnie otwarte aplikacje)."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover
        return set()
    try:
        user32 = ctypes.windll.user32
    except AttributeError:  # pragma: no cover - poza Windows
        return set()

    found: set[int] = set()

    def callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                found.add(int(pid.value))
        except Exception:
            pass
        return True

    try:
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(enum_proc(callback), 0)
    except Exception:
        return set()
    return found


def prettify_process_name(process_name: str) -> str:
    """Z 'visualstudiocode' robi 'Visual Studio Code'-podobna czytelna nazwe."""
    stem = Path(process_name).stem
    cleaned = re.sub(r"[_\-.]+", " ", stem).strip()
    parts = [part for part in cleaned.split() if part]
    if not parts:
        return process_name
    known = {
        "chrome": "Google Chrome",
        "msedge": "Microsoft Edge",
        "firefox": "Firefox",
        "code": "Visual Studio Code",
        "devenv": "Visual Studio",
        "explorer": "Eksplorator plików",
        "notepad": "Notatnik",
        "notepad++": "Notepad++",
        "winword": "Microsoft Word",
        "excel": "Microsoft Excel",
        "powerpnt": "Microsoft PowerPoint",
        "outlook": "Microsoft Outlook",
        "teams": "Microsoft Teams",
        "ms-teams": "Microsoft Teams",
        "spotify": "Spotify",
        "vlc": "VLC",
        "discord": "Discord",
        "steam": "Steam",
        "obs64": "OBS Studio",
        "photoshop": "Adobe Photoshop",
        "illustrator": "Adobe Illustrator",
        "resolve": "DaVinci Resolve",
        "musescore4": "MuseScore 4",
        "anki": "Anki",
        "zoom": "Zoom",
        "slack": "Slack",
        "telegram": "Telegram",
        "whatsapp": "WhatsApp",
    }
    lowered = stem.lower()
    if lowered in known:
        return known[lowered]
    return " ".join(part[:1].upper() + part[1:] for part in parts)


def from_running(*, timeout: float = 30.0) -> list[CatalogApp]:
    """Tylko realnie otwarte aplikacje (procesy z widocznym oknem)."""
    try:
        from .block.processes import is_own_process, is_system_safe, list_processes
    except Exception:  # pragma: no cover
        return []
    visible = pids_with_windows()
    apps: list[CatalogApp] = []
    for info in list_processes():
        name = (info.name or "").lower()
        if not name or is_system_safe(name) or is_background_process(name):
            continue
        if visible and info.pid not in visible:
            continue
        try:
            if is_own_process(info):
                continue
        except Exception:
            pass
        apps.append(
            CatalogApp(
                name=clean_name(prettify_process_name(name)),
                exe=name,
                path=info.exe or "",
                source="running",
                icon_path=info.exe or "",
            )
        )
    return apps


# ----------------------------------------------------------------------- scalanie
SOURCE_PRIORITY = {"startmenu": 0, "registry": 1, "store": 2, "running": 3}


def merge(apps: Iterable[CatalogApp], *, limit: int = MAX_APPS) -> list[CatalogApp]:
    """Scala zrodla: klucz = nazwa procesu, a bez niego - nazwa czytelna.

    Po scaleniu uzupelniamy brakujace ikony: jesli mamy skrot z Menu Start
    o tej samej nazwie, to on jest najlepszym zrodlem ikony (dziala tez dla
    aplikacji ze Sklepu).
    """
    best: dict[str, CatalogApp] = {}
    unnamed: dict[str, CatalogApp] = {}
    for app in apps:
        if not app.name:
            continue
        if app.exe:
            key = app.exe.lower()
            current = best.get(key)
            if current is None or SOURCE_PRIORITY.get(app.source, 9) < SOURCE_PRIORITY.get(current.source, 9):
                best[key] = app
        else:
            unnamed.setdefault(_norm(app.name), app)
    merged = list(best.values())
    known_names = {_norm(app.name) for app in merged}
    for norm, app in unnamed.items():
        if norm not in known_names:
            merged.append(app)

    icon_by_name: dict[str, str] = {}
    for app in merged:
        if app.has_icon:
            icon_by_name.setdefault(_norm(app.name), app.icon_path)

    merged = [
        app
        if app.has_icon
        else CatalogApp(
            name=app.name,
            exe=app.exe,
            path=app.path,
            source=app.source,
            icon_path=icon_by_name.get(_norm(app.name), ""),
        )
        for app in merged
    ]
    merged.sort(key=lambda a: (a.name.lower(), a.exe))
    return merged[:limit]


def cache_path() -> Path:
    return paths.data_dir() / CACHE_NAME


#: Cache w pamieci: sciezka pliku -> (mtime pliku, max_age, lista aplikacji).
#: Odczyt z dysku przy kazdym wpisaniu litery w wyszukiwarce byl zauwazalny,
#: a plik i tak zmienia sie tylko po pelnym skanie.
_MEM_CACHE: dict[str, tuple[float, float, list["CatalogApp"]]] = {}


def _remember(file: Path, max_age: float, apps: list["CatalogApp"]) -> None:
    try:
        mtime = file.stat().st_mtime
    except OSError:
        return
    _MEM_CACHE[str(file)] = (float(mtime), float(max_age), list(apps))


def load_cache(max_age: float = CACHE_TTL_SECONDS) -> Optional[list[CatalogApp]]:
    file = cache_path()
    if not file.exists():
        _MEM_CACHE.pop(str(file), None)
        return None
    try:
        mtime = file.stat().st_mtime
    except OSError:
        return None
    memo = _MEM_CACHE.get(str(file))
    if memo is not None and memo[0] == float(mtime) and memo[1] == float(max_age):
        return list(memo[2]) or None
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    created = float(payload.get("created", 0))
    if max_age and time.time() - created > max_age:
        return None
    apps = [
        CatalogApp(
            name=str(item.get("name", "")),
            exe=str(item.get("exe", "")),
            path=str(item.get("path", "")),
            source=str(item.get("source", "")),
            icon_path=str(item.get("icon_path", "") or ""),
        )
        for item in payload.get("apps") or []
        if item.get("name")
    ]
    if apps:
        _remember(file, max_age, apps)
    return apps or None


def save_cache(apps: Sequence[CatalogApp]) -> None:
    payload = {"created": time.time(), "apps": [app.cache_dict() for app in apps]}
    file = cache_path()
    try:
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(file)
    except OSError:
        return
    _remember(file, CACHE_TTL_SECONDS, list(apps))


def get_catalog(
    *,
    force: bool = False,
    include_start_menu: bool = True,
    include_registry: bool = True,
    include_running: bool = True,
    timeout: float = POWERSHELL_TIMEOUT,
) -> list[CatalogApp]:
    """Zwraca katalog aplikacji (z cache, chyba ze force=True)."""
    if not force:
        cached = load_cache()
        if cached:
            return cached
    collected: list[CatalogApp] = []
    if include_start_menu:
        collected.extend(from_start_menu(timeout=timeout))
    if include_registry:
        collected.extend(from_registry())
    if include_running:
        collected.extend(from_running())
    merged = merge(collected)
    save_cache(merged)
    return merged


def search(query: str, apps: Optional[Sequence[CatalogApp]] = None) -> list[CatalogApp]:
    """Filtruje katalog po fragmencie nazwy lub nazwy procesu."""
    source = list(apps) if apps is not None else get_catalog()
    needle = _norm(query)
    if not needle:
        return source
    return [app for app in source if needle in _norm(app.name) or needle in _norm(app.exe)]


def grouped(apps: Optional[Sequence[CatalogApp]] = None) -> dict[str, list[CatalogApp]]:
    """Grupuje katalog po pierwszej literze nazwy (do listy wyboru)."""
    source = list(apps) if apps is not None else get_catalog()
    groups: dict[str, list[CatalogApp]] = {}
    for app in source:
        letter = (app.name[:1] or "#").upper()
        groups.setdefault(letter, []).append(app)
    return dict(sorted(groups.items()))


def describe(apps: Sequence[CatalogApp]) -> dict:
    sources: dict[str, int] = {}
    for app in apps:
        sources[app.source] = sources.get(app.source, 0) + 1
    return {"count": len(apps), "sources": sources, "cache": str(cache_path())}
