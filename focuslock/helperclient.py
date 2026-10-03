"""Most GUI <-> helper (RPC + podniesienie uprawnien przez UAC).

Zamrozony kontrakt: call() NIGDY nie rzuca wyjatkiem - zwraca dict z "ok" i "errors".
"""
from __future__ import annotations

import json
import os
import secrets
import time
from typing import Callable, Optional

from . import ipc, paths


class HelperBridge:
    def __init__(
        self,
        *,
        on_event: Optional[Callable[[str, dict], None]] = None,
        dry_run: bool = False,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.on_event = on_event or (lambda ev, data: None)
        self.dry_run = dry_run
        self._log = logger or (lambda msg: None)
        self.token, self.authkey_hex = self._load_or_create_credentials()
        self.client: Optional[ipc.RpcClient] = None
        self._events: list[dict] = []
        self.last_error: str = ""

    # ------------------------------------------------------------------ poswiadczenia
    @staticmethod
    def _load_or_create_credentials() -> tuple[str, str]:
        file = paths.rpc_token_path()
        data: dict = {}
        if file.exists():
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        if not data.get("token") or not data.get("authkey"):
            data = {"token": secrets.token_urlsafe(32), "authkey": secrets.token_hex(32)}
            try:
                file.write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                pass
        return str(data["token"]), str(data["authkey"])

    @property
    def authkey(self) -> bytes:
        return bytes.fromhex(self.authkey_hex)

    # --------------------------------------------------------------------- polaczenie
    @property
    def online(self) -> bool:
        return self.client is not None and self.client.connected

    def connect(self, timeout: float = 3.0) -> bool:
        if self.online:
            return True
        try:
            self.client = ipc.RpcClient(
                paths.rpc_pipe_name(),
                self.authkey,
                self.token,
                on_event=self._handle_event,
            )
            self.client.call("ping", timeout=timeout)
            self._log("polaczono z helperem")
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.client = None
            return False

    def ensure(self, *, request_uac: bool = True, wait_seconds: float = 12.0) -> bool:
        """Laczy sie z helperem; jesli go nie ma i wolno - uruchamia go z UAC."""
        if self.connect():
            return True
        if not request_uac:
            return False
        if self.dry_run:
            self._log("dry-run: pomijam podniesienie helpera")
            return False
        try:
            from .shell import elevate

            os.environ["CISZA_HELPER_TOKEN"] = self.token
            os.environ["CISZA_HELPER_AUTHKEY"] = self.authkey_hex
            result = elevate.launch_helper(dry_run=self.dry_run)
            if not result.get("ok"):
                self.last_error = "; ".join(result.get("errors") or ["UAC odrzucone"]) 
                self._log(f"helper nie wystartowal: {self.last_error}")
                return False
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self._log(f"elevate niedostepny: {exc}")
            return False
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self.connect():
                return True
            time.sleep(0.5)
        return False

    def call(self, method: str, timeout: float = 30.0, **params) -> dict:
        """Bezpieczne wywolanie metody helpera."""
        if not self.online and not self.connect():
            return {"ok": False, "errors": [f"helper niedostepny: {self.last_error or 'brak polaczenia'}"]}
        try:
            assert self.client is not None
            result = self.client.call(method, timeout=timeout, **params)
            if isinstance(result, dict):
                return result
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self._log(f"RPC {method}: {exc}")
            return {"ok": False, "errors": [f"{method}: {exc}"]}

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    # ------------------------------------------------------------------------ eventy
    def _handle_event(self, event: str, data: dict) -> None:
        self._events.append({"event": event, "data": data, "ts": time.time()})
        self._events = self._events[-200:]
        try:
            self.on_event(event, data)
        except Exception:
            pass

    def drain_events(self) -> list[dict]:
        events, self._events = self._events, []
        return events
