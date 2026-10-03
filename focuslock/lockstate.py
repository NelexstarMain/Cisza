"""Stan lockdownu zapisywany na dysk (odtwarzanie po crashu).

Zamrozony kontrakt: read()/write()/update()/clear() + struktury ShellState,
NetworkState, LockState. Plik: %LOCALAPPDATA%\\Cisza\\lockstate.json
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import paths


@dataclass
class ShellState:
    """Co dokladnie zmienilismy w powloce (zeby umiec to cofnac)."""

    wallpaper: str = ""
    wallpaper_style: int = 10
    icons_hidden: bool = False
    taskbar_hidden: bool = False
    # Tryb paska zadan z sesji ("filtered" | "hide" | "keep") i informacja, czy
    # zabralismy przyciski niektorym oknom (focuslock/shell/taskbarfilter.py).
    taskbar_mode: str = ""
    taskbar_filtered: bool = False
    hotkeys_blocked: bool = False
    toasts_muted: bool = False
    toasts_previous: int = -1
    sound_muted: bool = False
    taskmgr_disabled: bool = False
    sleep_prevented: bool = False
    capture_error: str = ""


@dataclass
class NetworkState:
    mode: str = ""
    proxy_port: int = 0
    proxy_enabled_by_us: bool = False
    proxy_previous: dict = field(default_factory=dict)
    firewall_profile: str = ""
    hosts_patched: bool = False
    hosts_backup: str = ""
    browsers: list = field(default_factory=list)


@dataclass
class LockState:
    active: bool = False
    started_at: float = 0.0
    hardcore: bool = False
    session_id: Optional[int] = None
    shell: ShellState = field(default_factory=ShellState)
    network: NetworkState = field(default_factory=NetworkState)
    guard_active: bool = False
    reason: str = ""

    def to_json(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_json(cls, data: dict) -> "LockState":
        shell = ShellState(**{k: v for k, v in (data.get("shell") or {}).items() if k in ShellState.__annotations__})
        network = NetworkState(
            **{k: v for k, v in (data.get("network") or {}).items() if k in NetworkState.__annotations__}
        )
        return cls(
            active=bool(data.get("active", False)),
            started_at=float(data.get("started_at", 0.0)),
            hardcore=bool(data.get("hardcore", False)),
            session_id=data.get("session_id"),
            shell=shell,
            network=network,
            guard_active=bool(data.get("guard_active", False)),
            reason=str(data.get("reason", "")),
        )


def read(path: Optional[Path] = None) -> Optional[LockState]:
    file = Path(path) if path is not None else paths.lockstate_path()
    if not file.exists():
        return None
    try:
        return LockState.from_json(json.loads(file.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def write(state: LockState, path: Optional[Path] = None) -> LockState:
    file = Path(path) if path is not None else paths.lockstate_path()
    if not state.started_at:
        state.started_at = time.time()
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(file)
    return state


def update(path: Optional[Path] = None, **changes: Any) -> LockState:
    state = read(path) or LockState()
    for key, value in changes.items():
        if key in ("shell", "network") and isinstance(value, dict):
            current = getattr(state, key)
            for sub_key, sub_value in value.items():
                if hasattr(current, sub_key):
                    setattr(current, sub_key, sub_value)
        elif hasattr(state, key):
            setattr(state, key, value)
    return write(state, path)


def clear(path: Optional[Path] = None) -> None:
    file = Path(path) if path is not None else paths.lockstate_path()
    try:
        if file.exists():
            file.unlink()
    except OSError:
        pass
