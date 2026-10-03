# Cisza — zamrożone kontrakty modułów (Faza A)

Wszystkie moduły są w pakiecie `focuslock`. Nie zmieniaj sygnatur ani nazw zwracanych pól —
inne moduły i `focuslock/helper.py` już z nich korzystają. Jeśli kontrakt jest niewygodny,
zgłoś to Leadowi, nie zmieniaj go po cichu.

Konwencje ogólne:
- Każda funkcja/klasa przyjmuje `dry_run: bool = False` tam, gdzie dotyka systemu. W `dry_run`
  nic nie zmienia w systemie, tylko zwraca raport z `applied` (co by zrobiła) i `warnings`.
- Raport operacji to dict: `{"ok": bool, "applied": [str], "warnings": [str], "errors": [str]}`.
- Wyjątki nie mogą wyciekać z operacji systemowych — łap je i zapisz w `errors`.
- Kod nie może zależeć od PyQt poza modułami `focuslock/ui/**`, `focuslock/shell/overlay.py`
  oraz warstwą GUI `focuslock/app.py` (okno główne, tray, pętla QTimer). Pozostałe moduły
  (`controller`, `session`, `economy`, `block/**`, `helper`, `recovery`) muszą działać bez PyQt.
- Brak kolorów: każde UI korzysta wyłącznie z tokenów `focuslock/ui/theme.py`.

## 1. `focuslock/block/processes.py` (właściciel: block-apps)

```python
@dataclass(frozen=True)
class MatchRule:
    kind: str            # "name" | "path" | "signature"
    value: str
    label: str = ""
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "MatchRule": ...

SYSTEM_SAFE: frozenset[str]      # nazwy procesow nienaruszalnych (male litery)

@dataclass
class ProcessInfo:
    pid: int
    name: str        # male litery, np. "chrome.exe"
    exe: str         # pelna sciezka lub ""
    create_time: float

def list_processes() -> list[ProcessInfo]: ...
def match_process(info: ProcessInfo, rules: Sequence[MatchRule]) -> MatchRule | None: ...
def is_system_safe(name: str) -> bool: ...
def compute_signature(path: str) -> str: ...   # np. "sha1:...:rozmiar", "" gdy blad

class ProcessGuard:
    """Watcher procesow: ubija/usypia wszystko poza lista dozwolonych."""
    def __init__(self, allow: Sequence[MatchRule], block: Sequence[MatchRule] = (),
                 action: str = "suspend",           # "suspend" | "kill"
                 interval: float = 0.75,
                 dry_run: bool = False,
                 on_event: Callable[[str, dict], None] | None = None): ...
    def start(self) -> dict: ...
    def stop(self) -> dict: ...
    def update(self, allow=None, block=None, action: str | None = None) -> dict: ...
    def stats(self) -> dict: ...
    # stats -> {"blocked": int, "suspended": int, "killed": int, "skipped": int,
    #           "recent": [{"name": str, "action": str, "ts": float}]}
```

- Eventy `on_event`: `("blocked_app", {"pid", "name", "action", "rule"})`.
- `action="suspend"` używa `psutil.Process.suspend()`; procesów krytycznych (`SYSTEM_SAFE`)
  ani własnych procesów Ciszy nigdy nie ruszaj.
- Nowo uruchomiony proces ubijaj po `grace_seconds=1.0` (konstruktor: `grace: float = 1.0`).

## 2. `focuslock/block/launchwatch.py` (block-apps)

```python
class LaunchWatcher:
    """Szybkie wykrywanie nowych procesow (WMI Win32_ProcessStartTrace, fallback: odpytywanie)."""
    def __init__(self, guard: ProcessGuard, interval: float = 0.5,
                 on_event: Callable[[str, dict], None] | None = None): ...
    def start(self) -> dict: ...
    def stop(self) -> dict: ...
    @property
    def backend(self) -> str:   # "wmi" | "poll"
```

## 3. `focuslock/block/hosts.py` (block-net)

```python
BLOCK_BEGIN = "# CISZA-BEGIN"
BLOCK_END = "# CISZA-END"

def block_domains(domains: Sequence[str], *, hosts_path: Path | None = None,
                  dry_run: bool = False) -> dict: ...
def unblock(*, hosts_path: Path | None = None, dry_run: bool = False) -> dict: ...
def is_blocked(*, hosts_path: Path | None = None) -> bool: ...
def backup(*, hosts_path: Path | None = None) -> str: ...     # zwraca sciezke backupu
def restore_backup(backup_path: str, *, hosts_path: Path | None = None,
                   dry_run: bool = False) -> dict: ...
def flush_dns(*, dry_run: bool = False) -> dict: ...
```
- Blokada w znacznikach: wpisy `0.0.0.0 domena` między `BLOCK_BEGIN` i `BLOCK_END`.
- Idempotencja: powtórne `block_domains` nadpisuje sekcję, nie duplikuje.
- `unblock` usuwa wyłącznie sekcję Ciszy; nigdy nie rusza wpisów użytkownika.
- Wymaga uprawnień administratora — przy `PermissionError` zwróć `ok=False` z opisem w `errors`.

## 4. `focuslock/block/proxy.py` (block-net)

```python
@dataclass
class Decision:
    allowed: bool
    reason: str          # "allow" | "not-in-allowlist" | "blocked" | "safe" | "ip"

def host_allowed(host: str, allowlist: Sequence[str], blocklist: Sequence[str] = ()) -> Decision: ...
def normalize_host(value: str) -> str: ...      # obniza, obcina port, usuwa kropke koncowa

class AllowlistProxy:
    def __init__(self, allowlist: Sequence[str], blocklist: Sequence[str] = (),
                 safe_hosts: Sequence[str] = (), port: int = 8765, mode: str = "allowlist",
                 dry_run: bool = False, on_event: Callable[[str, dict], None] | None = None): ...
    def start(self) -> dict: ...
    def stop(self) -> dict: ...
    def set_lists(self, allowlist=None, blocklist=None, mode: str | None = None) -> dict: ...
    @property
    def port(self) -> int: ...
    @property
    def blocked_count(self) -> int: ...
```
- Obsługa HTTP i `CONNECT` (bez MITM TLS), tylko `127.0.0.1`.
- Strona blokady: minimalny HTML, czarno-biały, po polsku, z nazwą hosta.
- Event: `("blocked_site", {"host": str, "reason": str})`.
- `host_allowed` jest czystą funkcją — musi być testowalna bez sieci.

## 5. `focuslock/block/firewall.py` (block-net)

```python
def apply_browser_blocks(browser_exes: Sequence[str], *, profile: str = "CiszaBlock",
                         dry_run: bool = False) -> dict: ...
def remove_blocks(*, profile: str = "CiszaBlock", dry_run: bool = False) -> dict: ...
def is_active(*, profile: str = "CiszaBlock") -> bool: ...
```
Reguły: `netsh advfirewall firewall add rule name="CiszaBlock-<n>" dir=out action=block
protocol=TCP remoteport=80,443 program="<exe>"`. Usuwanie po nazwie. Wymaga admina.

## 6. `focuslock/block/networklock.py` (block-net)

```python
class NetworkLock:
    def __init__(self, *, port: int = 8765, dry_run: bool = False,
                 on_event: Callable[[str, dict], None] | None = None): ...
    def enable_allowlist(self, allow_hosts, safe_hosts=(), browser_exes=(),
                         block_browser_direct: bool = True) -> dict: ...
    def enable_blocklist(self, block_hosts, *, browser_exes=(), block_browser_direct: bool = False) -> dict: ...
    def disable(self) -> dict: ...
    def status(self) -> dict:   # {"active": bool, "mode": str, "blocked_attempts": int, "port": int}
```
- `enable_allowlist`: proxy w trybie allowlist + ustawienie proxy systemowego (HKCU Internet Settings:
  `ProxyEnable=1`, `ProxyServer=127.0.0.1:<port>`, `ProxyOverride="localhost;127.*;<local>"`)
  + zapis poprzedniej konfiguracji do `lockstate` + (opcjonalnie) reguły zapory + `flush_dns`.
- `enable_blocklist`: proxy w trybie blocklist (albo sam hosts) + `hosts.block_domains`.
- `disable`: wszystko cofnij (proxy off, PAC usunięty, hosts restore, firewall remove, flush_dns).
  Musi być idempotentne i bezpieczne, gdy nic nie było włączone.

## 7. `focuslock/shell/elevate.py` (block-net)

```python
def is_admin() -> bool: ...
def helper_running(pipe_name: str, authkey: bytes, token: str) -> bool: ...
def launch_helper(*, dry_run: bool = False) -> dict: ...   # uruchamia helper z UAC (ShellExecuteW "runas")
def python_launch_command(args: Sequence[str]) -> list[str]: ...   # pythonw + -m focuslock <args>
```
- `launch_helper` używa `ShellExecuteW(None, "runas", pythonw, "-m focuslock --helper ...", None, SW_HIDE)`.
- Zwraca `{"ok": bool, "pid": int|None, "warnings": [...], "errors": [...]}`. Kod 5 (odmowa UAC)
  → `ok=False`, komunikat "UAC odrzucone".

## 8. `focuslock/shell/desktop.py` (shell-lock)

```python
@dataclass
class DesktopState:
    wallpaper: str = ""
    wallpaper_style: int = 10
    icons_hidden: bool = False

def capture() -> DesktopState: ...
def apply_black(*, dry_run: bool = False, hide_icons: bool = True) -> dict: ...
def restore(state: DesktopState, *, dry_run: bool = False) -> dict: ...
```
- Tapeta: `SystemParametersInfoW(SPI_SETDESKWALLPAPER=0x0014, 0, "", SPIF_UPDATEINIFILE|SPIF_SENDCHANGE)`
  (czarny ekran), zapis poprzedniej wartości z rejestru `HKCU\Control Panel\Desktop\Wallpaper`.
- Ikony: okno `SHELLDLL_DefView` → `SysListView32`, `ShowWindow(SW_HIDE)`.

## 9. `focuslock/shell/taskbar.py` (shell-lock)

```python
def hide(*, dry_run: bool = False) -> dict: ...
def show(*, dry_run: bool = False) -> dict: ...
def is_hidden() -> bool: ...
def list_taskbars() -> list[int]: ...   # HWND: Shell_TrayWnd + Shell_SecondaryTrayWnd
```
- Podstawowa metoda: `ShowWindow(hwnd, SW_HIDE)`; fallback (gdy okno wraca): przesunięcie
  `SetWindowPos` na `-32000` + `WS_EX_TOOLWINDOW`. Sprawdź realny efekt i zwróć metodę w `applied`.

### 9a. `focuslock/shell/taskbarfilter.py` (Lead)

```python
IS_WINDOWS: bool
STATE_NAME = "taskbar_filter.json"

def list_windows(*, exclude_pids=(), titles_required=True) -> list[dict]   # {"hwnd","pid","name","title","class","minimized"}
def allowed(window: dict, *, rules=(), apps=(), parents=()) -> bool
def plan(windows, *, rules=(), apps=(), parents=(), safe=(), own_pids=()) -> dict
def apply(rules=(), *, apps=(), parents=(), dry_run=False, exclude_pids=(),
          hide_fallback=True, force=True, interval=1.0, now=None, windows=None) -> dict
def restore(*, dry_run=False) -> dict
def is_active() -> bool
def filtered_windows() -> list[int]
def status() -> dict          # {"ok","active","filtered","apps","updated_at","windows"}
def reset_throttle() -> None

class TaskbarList:            # COM ITaskbarList: open()/add_tab(hwnd)/delete_tab(hwnd)/close()
    error: str
```
- `plan` jest czystą funkcją: zwraca `{"hide": [hwnd], "keep": [hwnd], "skipped": [...]}`
  i nie dotyka systemu — na tym opierają się testy.
- `apply`/`restore` zwracają standardowy raport + `{"hidden", "restored", "kept", "skipped"}`.
  `dry_run=True` nic nie zmienia w systemie. Pusta allowlista = brak działań (bezpiecznik).
- `apply` dławi się do `interval` sekund (`force=True` omija dławik) — kontroler woła je
  co tick sesji.
- Nie rusza procesów `SYSTEM_SAFE`, własnych procesów Ciszy ani okien paska zadań.
- Gdy `DeleteTab` zawiedzie, a `hide_fallback=True`, okno jest ukrywane (`ShowWindow`), a sposób
  ukrycia i HWND trafiają do pliku stanu, żeby `restore()` działał z innego procesu.

## 10. `focuslock/shell/hotkeys.py` (shell-lock)

```python
BLOCKED_KEYS = ("windows", "left windows", "right windows", "alt+tab", "alt+esc",
                "alt+f4", "ctrl+esc", "ctrl+shift+esc", "windows+d", "windows+e",
                "windows+r", "windows+l", "windows+tab", "ctrl+alt+del")

class HotkeyBlocker:
    def __init__(self, on_emergency: Callable[[str], None] | None = None,
                 hold_seconds: float = 5.0, emergency_enabled: bool = True): ...
    def install(self) -> dict: ...
    def uninstall(self) -> dict: ...
    def set_emergency(self, enabled: bool) -> dict: ...
    @property
    def installed(self) -> bool: ...
```
- Używa biblioteki `keyboard` (`keyboard.block_key`, `keyboard.add_hotkey`). `ctrl+alt+del` jest
  nieblokowalny — pomiń go i zapisz w `warnings`.
- Awaryjne wyjście: przytrzymanie `ctrl+alt+shift+q` przez `hold_seconds` → `on_emergency("hold")`.
- Kombinacja wyjścia zgłaszana jako event `("exit_request", {"how": "hold"|"pin"})` przez GUI.

## 11. `focuslock/shell/overlay.py` (shell-lock, PyQt6)

```python
class OverlayManager:
    def __init__(self, *, dry_run: bool = False, on_event: Callable[[str, dict], None] | None = None): ...
    def lock(self, keep_window=None) -> dict: ...   # czarne okna na kazdym ekranie poza ekranem keep_window
    def unlock(self) -> dict: ...
    def refresh(self) -> None: ...                  # reasekuracja topmost, wolane z QTimer co 1 s
    def locked(self) -> bool: ...
    def message(self, text: str) -> None: ...       # tekst na nakladkach (np. "SESJA TRWA")
```
- Okna: `Qt.WindowType.FramelessWindowHint | WindowStaysOnTopHint | Tool`, kolor `theme.COLORS["overlay"]`,
  każde na innym ekranie (`QGuiApplication.screens()`), zamykane w `unlock`.

## 12. `focuslock/shell/dnd.py` (shell-lock)

```python
def mute_toasts(enabled: bool, *, dry_run: bool = False) -> dict: ...
def mute_sound(enabled: bool, *, dry_run: bool = False) -> dict: ...
def prevent_sleep(enabled: bool, *, dry_run: bool = False) -> dict: ...
```
- Toasty: `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Notifications\Settings\NOC_GLOBAL_SETTING_TOASTS_ENABLED`.
- Dźwięk: `HKCU\AppEvents\...` / wyciszenie przez `SetVolume` nie jest dostępne bez zależności —
  użyj `keyboard`? NIE: użyj `ctypes` + `IAudioEndpointVolume` (COM przez `comtypes` niedostępny)
  → jeśli się nie da, zwróć `ok=False` z ostrzeżeniem (opcja jest best-effort).
- Sleep: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`.

## 13. `focuslock/shell/watchdog.py` (shell-lock)

```python
def run_watchdog(*, interval: float = 2.0, timeout: float = 10.0, dry_run: bool = False) -> int: ...
def heartbeat_touch() -> None: ...    # GUI wola przy kazdym ticku (zapisuje paths.heartbeat_path())
def is_heartbeat_fresh(timeout: float = 10.0) -> bool: ...
```
- Proces `pythonw -m focuslock --watchdog`: dopóki heartbeat świeży — śpi; gdy przestarzały
  (albo `lockstate.active` i brak procesu GUI) → `focuslock.recovery.restore_everything(reason="watchdog")`
  i zakończ.

## 14. `focuslock/recovery.py` + `tools/restore.py` (Lead)

```python
def restore_everything(*, dry_run: bool = False, reason: str = "") -> dict: ...
```
Wywołuje po kolei (każde w try/except, zbierając raport): `shell.taskbarfilter.restore`,
`shell.taskbar.show`, `shell.desktop.restore`, `shell.hotkeys.uninstall`, `shell.dnd.*(False)`,
`block.firewall.remove_blocks`, `block.networklock.NetworkLock().disable`, `lockstate.clear`,
usunięcie flagi crash.

## 15. `focuslock/economy.py` (core-engine)

```python
class Economy:
    def __init__(self, store, settings) -> None: ...
    def credit_pomodoro(self, study_seconds: int, session_id: int, completed: bool,
                        now: float | None = None) -> dict: ...
        # -> {"earned": int, "capped": int, "penalty": int, "balance": int, "reason": str}
    def spend_free(self, seconds: int, session_id: int) -> int: ...
    def refund(self, seconds: int, session_id: int) -> int: ...
    def balance(self) -> int: ...
    def earned_today(self) -> int: ...
    def daily_cap_left(self) -> int: ...
    def sweep_expired(self) -> int: ...
    def register_study_day(self, studied_seconds: int, day: str | None = None) -> dict: ...
        # -> {"current": int, "best": int, "changed": bool}
    def focus_score(self, days: int = 30) -> float: ...   # 0..100
    def summary(self) -> dict: ...
        # {"balance", "earned_today", "cap_left", "cap_minutes", "streak", "best_streak",
        #  "focus_score", "ratio", "ttl_days"}
```
Reguły twarde:
- `earned = floor(study_seconds * ratio / round_seconds) * round_seconds`, ale nie więcej niż
  `daily_cap - earned_today`; nadwyżka w `capped`.
- `completed=False` → `earned = 0`, `reason="przerwane"`, opcjonalna kara `abort_penalty_minutes`
  (zabierana z banku FIFO, nigdy poniżej zera).
- Seria: nowy dzień z nauką ≥ `streak_min_study_minutes` → `current += 1` (jeśli wczoraj był dzień nauki)
  albo reset do 1; pominięty dzień → reset do 0.

## 16. `focuslock/session.py` + `focuslock/timer.py` (core-engine)

```python
class Phase(str, Enum):
    IDLE = "IDLE"; ARMING = "ARMING"; STUDY = "STUDY"; BREAK = "BREAK"
    LONG_BREAK = "LONG_BREAK"; DONE = "DONE"

@dataclass
class Plan:
    mode: str = "STUDY"                 # "STUDY" | "FREE"
    study_seconds: int = 1500
    break_seconds: int = 300
    long_break_seconds: int = 900
    long_break_every: int = 4
    arm_seconds: int = 3
    free_seconds: int = 0
    tag: str = ""
    goal_note: str = ""
    allowlist: dict = field(default_factory=dict)

class SessionEngine:
    def __init__(self, *, on_tick=None, on_phase=None, on_pomodoro_end=None, on_finish=None): ...
    def start(self, plan: Plan, session_id: int, now: float | None = None) -> dict: ...
    def tick(self, now: float | None = None) -> dict: ...      # wolane co 1 s
    def start_break(self, long: bool | None = None) -> dict: ...
    def skip_break(self) -> dict: ...
    def finish(self, reason: str = "user", now: float | None = None) -> dict: ...
    def abort_pomodoro(self, reason: str = "abort", now: float | None = None) -> dict: ...
    def state(self) -> dict: ...
        # {"phase", "mode", "remaining", "total", "elapsed", "pomodoro_index",
        #  "pomodoros_done", "pomodoros_aborted", "study_seconds_done", "free_seconds_left",
        #  "paused", "session_id", "tag"}
    def pause(self, now=None) -> dict: ...
    def resume(self, now=None) -> dict: ...
    def serialize(self) -> dict: ...     # zapis do bazy po crashu
    @classmethod
    def deserialize(cls, data: dict) -> dict: ...
```
- Licznik na `time.monotonic()`, ale `now` przekazywane z zewnątrz dla testów.
- W trybie STUDY po każdym pomodoro `on_pomodoro_end({"completed": True, "study_seconds": 1500})`.
- `abort_pomodoro` → `on_pomodoro_end({"completed": False, ...})`.

## 17. `focuslock/ui/**` (ui-screens)

Widgety: `RingProgress`, `DitheredBar`, `Hairline`, `Card`, `GhostButton`, `PrimaryButton`,
`StatTile`, `MonochromeChart`, `Heatmap`, `Toast`, `NavRail`, `Toggle`.

Ekrany (każdy jako `QWidget` z sygnałami, bez logiki systemowej):
`HomeScreen`, `ComposerScreen`, `RunningScreen`, `BreakScreen`, `FreeScreen`, `BankScreen`,
`StatsScreen`, `SettingsScreen`, `OnboardingScreen`, `PinDialog`, `SummaryScreen`, `JournalScreen`.

Kontrakt komunikacji z kontrolerem (`focuslock/controller.py`, Lead):
```python
class Screen(QWidget):
    request_start = pyqtSignal(dict)      # payload: {"mode","study_minutes","break_minutes",...}
    request_free = pyqtSignal(int)        # sekundy
    request_end = pyqtSignal(str)         # powod
    request_settings = pyqtSignal(dict)   # czastkowa aktualizacja ustawien
    request_pin_check = pyqtSignal(str)   # GUI odpowiada sygnalem pin_result(bool)
```
`ui/tray.py`: `class Tray(QSystemTrayIcon)` z akcjami Start/Zakoncz/Statystyki/Wyjdz (ikona rysowana
w kodzie, monochromatyczna).

## 18. `focuslock/helper.py` (Lead)

Dispatch RPC (metody i parametry — patrz nagłówek pliku). Helper uruchamiany jako
`pythonw -m focuslock --helper --token <token>`; nasłuchuje na `paths.rpc_pipe_name()`.

Eventy wysyłane do GUI (`RpcServer.broadcast`): `blocked_app`, `blocked_site`, `exit_request`,
`guard_stats`, `helper_log`, `lock_changed`.

Tryb paska zadań: `shell_lock` czyta `settings["taskbar_mode"]` (`filtered` | `hide` | `keep`;
brak pola = zgodność ze starym `hide_taskbar`) i dla `filtered` tylko pilnuje, żeby pasek był
widoczny — przyciski nieużywanych aplikacji usuwa GUI (`shell.taskbarfilter`).
`shell_status` zwraca dodatkowo `taskbar_filter` (raport `taskbarfilter.status()`).

## 19. Katalog aplikacji i stron (Lead) — wybór z listy zamiast wpisywania

### 19.1 `focuslock/appcatalog.py`

```python
@dataclass(frozen=True)
class CatalogApp:
    name: str; exe: str; path: str; source: str; icon_path: str
    def to_dict(self) -> dict   # + "label" i gotowy "icon_path" (icon_path albo path)
    @property
    def icon_source(self) -> str

def get_catalog(*, force: bool = False, include_start_menu=True, include_registry=True,
                include_running=True, timeout: float = 120.0) -> list[CatalogApp]
def search(query: str, apps: Sequence[CatalogApp] | None = None) -> list[CatalogApp]
def grouped(apps=None) -> dict[str, list[CatalogApp]]
def describe(apps) -> dict      # {"count", "sources", "cache"}
def prettify_process_name(exe: str) -> str
def pids_with_windows() -> set[int]     # PID-y z widocznym oknem
def load_cache(max_age=86400) / save_cache(apps) / cache_path()
```

Źródła i priorytet scalania: `startmenu` (0) → `registry` (1) → `store` (2) → `running` (3).
Skan: skróty Menu Start (.lnk → `TargetPath` przez `WScript.Shell`), aplikacje ze Sklepu
(`Get-StartApps` + manifesty AppX → `Executable`), klucze `Uninstall` rejestru, procesy
z widocznym oknem. Cache: `%LOCALAPPDATA%\Cisza\apps_cache.json`, TTL 24 h.

### 19.2 `focuslock/sitecatalog.py`

```python
STUDY = "STUDY"; BLOCKED = "BLOCKED"
CATEGORIES: dict[str, str]
SITE_CATALOG: tuple[CatalogSite, ...]
def catalog_payload(kind: str | None = None) -> dict
def by_category(kind=None) -> dict[str, list[CatalogSite]]
def search(query, kind=None) / hosts(kind=None) / find(value) / normalize(host)
def hosts_from_names(names) -> list[str]      # nazwy z UI albo surowe hosty
def default_study_hosts() / default_block_hosts() / suggest_for(subject) / describe()
```

`catalog_payload` zwraca gotowe do wyświetlenia kategorie z etykietami PL oraz `defaults`.

### 19.3 Akcje kontrolera (sygnał `request_action` → wynik przez `on_action_result`)

| akcja | payload | wynik |
|---|---|---|
| `catalog_apps` | `{"query": str, "force": bool, "limit": int}` | `{"ok","apps","count","cached","summary"}` |
| `refresh_apps` / `installed_apps` | — | jak wyżej, zawsze `force=True` |
| `catalog_sites` | `{"kind": "STUDY"\|"BLOCKED", "query": str}` | `catalog_payload` |
| `save_app_selection` | `{"apps": [label \| exe]}` | `{"ok","saved","apps"}` |
| `save_site_selection` | `{"sites": [nazwa \| host]}` | `{"ok","saved","hosts"}` |
| `subject_suggestions` | `{"subject": "matematyka"}` | `{"ok","hosts","sites"}` |

`Controller.normalize_apps()` zamienia wybór UI na nazwy procesów (obsługuje `Nazwa (proces.exe)`,
ścieżkę do `.exe` i nazwę z katalogu, np. `Kalkulator` → `calculatorapp.exe`).
`Controller.build_plan()` przyjmuje klucze `apps`/`sites` oraz `study_apps`/`study_sites`.

### 19.4 Skany w tle (`focuslock/app.py`)

Ciężkie akcje (`refresh_apps`, `installed_apps`, `catalog_apps` z `force=True`) lecą przez
`_ActionWorker` + `QThreadPool`; ekrany dostają `on_action_busy(action, busy)` (stan „SKANUJĘ…")
i asynchronicznie `on_action_result(action, result)`. Z cache odczyt jest natychmiastowy.

### 19.5 Ikony (`focuslock/ui/icons.py`, ui-screens)

```python
class IconCache:
    def pixmap(self, icon_path: str, size: int, gray: bool) -> QPixmap
def monogram(name: str, size: int) -> QPixmap
def clear_cache() -> None
```
Ekstrakcja przez `QFileIconProvider` (`.lnk`, `.exe`, `.ico`), odbarwianie z zachowaniem alfa;
przełącznik `ui.color_app_icons` (domyślnie `False` — monochrom) i `ui.app_icon_size`.
