"""Blokada skrotow systemowych przez biblioteke `keyboard`.

Kontrakt: docs/INTERFACES.md sekcja 10.

Ograniczenia (raportowane jako warnings):
- `ctrl+alt+del` (SAS) jest nieblokowalny - pomijamy go.
- bez uprawnien administratora czesc hookow moze nie dzialac lub byc
  usuwana przez system; instalacja nadal jest podejmowana i raportowana.
- `windows+l` jest obslugiwane przez system (Winlogon) - hook bywa ignorowany.

Awaryjne wyjscie: przytrzymanie `ctrl+alt+shift+q` przez `hold_seconds`
wywoluje `on_emergency("hold")`. Logika przytrzymania (HoldTracker) jest czysta
i testowalna bez symulowania klawiatury.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
import weakref
from typing import Callable

try:
    import keyboard as _keyboard
except Exception as _exc:  # pragma: no cover - zalezy od srodowiska
    _keyboard = None
    KEYBOARD_ERROR = str(_exc)
else:
    KEYBOARD_ERROR = ""

KEYBOARD_OK = _keyboard is not None

BLOCKED_KEYS = (
    "windows",
    "left windows",
    "right windows",
    "alt+tab",
    "alt+esc",
    "alt+f4",
    "ctrl+esc",
    "ctrl+shift+esc",
    "windows+d",
    "windows+e",
    "windows+r",
    "windows+l",
    "windows+tab",
    "ctrl+alt+del",
)

# Klawisze pojedyncze blokujemy przez block_key, kombinacje przez add_hotkey.
SINGLE_KEYS = ("windows", "left windows", "right windows")

UNBLOCKABLE = ("ctrl+alt+del",)

EMERGENCY_COMBO = "ctrl+alt+shift+q"
EMERGENCY_KEYS = ("ctrl", "alt", "shift", "q")

WARN_SAS = "ctrl+alt+del jest nieblokowalny (Secure Attention Sequence) - pominieto"
WARN_ADMIN = "brak uprawnien administratora: czesc hookow moze nie dzialac"
WARN_WINL = "windows+l obsluguje Winlogon - blokada moze nie zadzialac"
WARN_HOOK_PERM = "biblioteka keyboard nie zarejestrowala hooka (brak uprawnien?)"

POLL_INTERVAL = 0.1

# Zywe instancje - potrzebne, by force_uninstall() (sciezka bezinstancyjna,
# wolana przez recovery/helper) posprzetal takze ich stan wewnetrzny.
_INSTANCES: "weakref.WeakSet[HotkeyBlocker]" = weakref.WeakSet()


def _report() -> dict:
    return {"ok": True, "applied": [], "warnings": [], "errors": []}


def _is_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class HoldTracker:
    """Czysta logika: ile czasu kombinacja jest trzymana bez przerwy."""

    def __init__(self, hold_seconds: float = 5.0) -> None:
        self.hold_seconds = float(hold_seconds)
        self._since: float | None = None
        self.fired = False

    def reset(self) -> None:
        self._since = None
        self.fired = False

    def update(self, pressed: bool, now: float) -> bool:
        """True dokladnie raz, gdy kombinacja jest trzymana >= hold_seconds."""
        if not pressed:
            self.reset()
            return False
        if self._since is None:
            self._since = now
            return False
        if not self.fired and (now - self._since) >= self.hold_seconds:
            self.fired = True
            return True
        return False


class HotkeyBlocker:
    """Instaluje/usuwa hooki blokujace skroty systemowe (powtarzalnie)."""

    def __init__(
        self,
        on_emergency: Callable[[str], None] | None = None,
        hold_seconds: float = 5.0,
        emergency_enabled: bool = True,
    ) -> None:
        self.on_emergency = on_emergency
        self.hold_seconds = float(hold_seconds)
        self._emergency_enabled = bool(emergency_enabled)
        self._installed = False
        self._emergency_active = False
        self._handles: list[tuple[str, object]] = []
        self._tracker = HoldTracker(self.hold_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hint_until = 0.0
        _INSTANCES.add(self)

    # ------------------------------------------------------------- wlasciwosci
    @property
    def installed(self) -> bool:
        return self._installed

    @property
    def emergency_enabled(self) -> bool:
        return self._emergency_enabled

    # -------------------------------------------------------------- wewnetrzne
    def _track(self, label: str, handle, report: dict) -> None:
        if handle is None:
            report["errors"].append("hook bez uchwytu: %s" % label)
            return
        self._handles.append((label, handle))
        report["applied"].append(label)

    def _register_blocks(self, report: dict) -> None:
        for key in SINGLE_KEYS:
            try:
                handle = _keyboard.block_key(key)
            except Exception as exc:
                report["errors"].append("block_key(%s): %s" % (key, exc))
                continue
            self._track("hotkeys.block:" + key, handle, report)

        for combo in BLOCKED_KEYS:
            if combo in SINGLE_KEYS or combo in UNBLOCKABLE:
                continue
            # UWAGA: kazda kombinacja musi miec wlasny obiekt callbacku.
            # `keyboard` trzyma callback jako klucz w _hotkeys i przy usuwaniu
            # robi `del _hotkeys[callback]` - wspolna funkcja dla wielu hotkeyow
            # powoduje KeyError przy sprzataniu.
            try:
                handle = _keyboard.add_hotkey(combo, (lambda *a, **k: None), suppress=True)
            except Exception as exc:
                report["errors"].append("add_hotkey(%s): %s" % (combo, exc))
                continue
            self._track("hotkeys.hotkey:" + combo, handle, report)

    def _on_emergency_key(self, *_args) -> None:
        # Autorepeat podbija wskaznik, gdy is_pressed zawiedzie.
        self._hint_until = time.time() + 0.5

    def _poll_pressed(self) -> bool:
        if time.time() < self._hint_until:
            return True
        try:
            return all(bool(_keyboard.is_pressed(key)) for key in EMERGENCY_KEYS)
        except Exception:
            return False

    def _emergency_loop(self) -> None:
        while not self._stop.is_set():
            try:
                pressed = self._poll_pressed()
                if self._tracker.update(pressed, time.time()):
                    self._fire_emergency()
            except Exception:
                pass
            self._stop.wait(POLL_INTERVAL)

    def _fire_emergency(self) -> None:
        if self.on_emergency is None:
            return
        try:
            self.on_emergency("hold")
        except Exception:
            pass

    def _start_emergency(self, report: dict) -> None:
        if self._emergency_active:
            return
        try:
            handle = _keyboard.add_hotkey(EMERGENCY_COMBO, self._on_emergency_key, suppress=False)
            self._handles.append(("hotkeys.emergency:" + EMERGENCY_COMBO, handle))
        except Exception as exc:
            report["errors"].append("add_hotkey(%s): %s" % (EMERGENCY_COMBO, exc))
            return
        self._tracker.reset()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._emergency_loop, name="cisza-emergency", daemon=True)
        self._thread.start()
        self._emergency_active = True
        report["applied"].append("hotkeys.emergency:" + EMERGENCY_COMBO)

    def _stop_emergency(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        self._emergency_active = False
        self._tracker.reset()

    def _remove_handles(self, report: dict) -> None:
        for label, handle in reversed(self._handles):
            removed = False
            for attempt in (
                lambda: handle(),
                lambda: _keyboard.remove_hotkey(handle),
                lambda: _keyboard.unhook(handle),
            ):
                try:
                    attempt()
                    removed = True
                    break
                except KeyError:
                    # `keyboard` usuwa hooki PRZED aktualizacja _hotkeys, wiec
                    # KeyError oznacza, ze rejestracja jest juz sprzatnieta.
                    removed = True
                    break
                except Exception:
                    continue
            if removed:
                report["applied"].append("hotkeys.remove:" + label)
            else:
                report["warnings"].append("nie udalo sie usunac hooka: " + label)
        self._handles = []

    # -------------------------------------------------------------------- API
    def install(self, *, dry_run: bool = False) -> dict:
        report = _report()
        report["warnings"].append(WARN_SAS)
        report["warnings"].append(WARN_WINL)
        if not _is_admin():
            report["warnings"].append(WARN_ADMIN)

        if dry_run:
            for combo in BLOCKED_KEYS:
                if combo in UNBLOCKABLE:
                    continue
                prefix = "hotkeys.block:" if combo in SINGLE_KEYS else "hotkeys.hotkey:"
                report["applied"].append(prefix + combo)
            if self._emergency_enabled:
                report["applied"].append("hotkeys.emergency:" + EMERGENCY_COMBO)
            report["warnings"].append("dry_run: hooki niezarejestrowane")
            return report

        if not KEYBOARD_OK:
            report["ok"] = False
            report["errors"].append("biblioteka keyboard niedostepna: " + KEYBOARD_ERROR)
            return report

        if self._installed:
            report["applied"].append("hotkeys.install:already")
            return report

        self._register_blocks(report)
        if self._emergency_enabled:
            self._start_emergency(report)

        if not self._handles:
            report["ok"] = False
            report["errors"].append(WARN_HOOK_PERM)
            return report

        self._installed = True
        if report["errors"]:
            report["ok"] = False
            report["warnings"].append("czesc hookow nie zostala zarejestrowana")
        return report

    def uninstall(self, *, dry_run: bool = False) -> dict:
        report = _report()
        if dry_run:
            for combo in BLOCKED_KEYS:
                if combo in UNBLOCKABLE:
                    continue
                report["applied"].append("hotkeys.unblock:" + combo)
            report["warnings"].append("dry_run: hooki nietkniete")
            return report

        if not self._installed and not self._handles and not self._emergency_active:
            report["applied"].append("hotkeys.uninstall:noop")
            return report
        if not KEYBOARD_OK:
            report["ok"] = False
            report["errors"].append("biblioteka keyboard niedostepna: " + KEYBOARD_ERROR)
            return report

        self._stop_emergency()
        self._remove_handles(report)
        self._installed = False
        report["applied"].append("hotkeys.uninstall:done")
        report["ok"] = not report["errors"]
        return report

    def set_emergency(self, enabled: bool, *, dry_run: bool = False) -> dict:
        report = _report()
        enabled = bool(enabled)
        if dry_run:
            report["applied"].append("hotkeys.emergency:%s" % ("on" if enabled else "off"))
            report["warnings"].append("dry_run: hooki nietkniete")
            return report
        if not KEYBOARD_OK:
            report["ok"] = False
            report["errors"].append("biblioteka keyboard niedostepna: " + KEYBOARD_ERROR)
            return report

        self._emergency_enabled = enabled
        if not self._installed:
            report["applied"].append("hotkeys.emergency:configured")
            return report
        if enabled and not self._emergency_active:
            self._start_emergency(report)
        elif not enabled and self._emergency_active:
            self._stop_emergency()
            report["applied"].append("hotkeys.emergency:off")
        else:
            report["applied"].append("hotkeys.emergency:noop")
        report["ok"] = not report["errors"]
        return report


def force_uninstall() -> dict:
    """Awaryjne, bezstanowe zdjecie WSZYSTKICH hookow Ciszy.

    Sciezka dla `focuslock/recovery.py` i `focuslock/helper.py`, gdy nie ma
    instancji HotkeyBlocker. Nie tworzy zadnego obiektu: czysci rejestry
    biblioteki `keyboard` (`unhook_all` + ksiegowanie `_hotkeys`/`_hooks`)
    oraz unieruchamia zywe instancje HotkeyBlocker, jesli jakies istnieja.
    Jest idempotentna - powtorne wywolanie nie zglasza bledow.
    """
    report = _report()
    if not KEYBOARD_OK:
        report["ok"] = False
        report["errors"].append("biblioteka keyboard niedostepna: " + KEYBOARD_ERROR)
        return report

    try:
        _keyboard.unhook_all()
        report["applied"].append("hotkeys.force_uninstall:unhook_all")
    except Exception as exc:
        report["errors"].append("unhook_all: %s" % exc)

    try:
        _keyboard.unhook_all_hotkeys()
    except Exception:
        pass

    # `unhook_all()` czysci listy hookow w listenerze, ale zostawia ksiegowanie
    # modulu - czyscimy je, zeby kolejny install() startowal ze stanu zerowego.
    for name in ("_hotkeys", "_hooks"):
        store = getattr(_keyboard, name, None)
        if isinstance(store, dict) and store:
            try:
                store.clear()
                report["applied"].append("hotkeys.force_uninstall:clear" + name)
            except Exception as exc:
                report["warnings"].append("clear %s: %s" % (name, exc))

    for blocker in list(_INSTANCES):
        try:
            blocker._stop_emergency()
            blocker._handles = []
            blocker._installed = False
        except Exception:
            continue
    report["applied"].append("hotkeys.force_uninstall:done")
    report["ok"] = not report["errors"]
    return report
