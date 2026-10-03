"""Lekki RPC po nazwanym potoku (multiprocessing.connection).

Zamrozony kontrakt (patrz docs/INTERFACES.md):
  - server: RpcServer(address, authkey, handlers).start() / .stop() / .broadcast(event, data)
  - client: RpcClient(address, authkey, on_event=...).call("method", **params)

Format komunikatow (JSON-owalne dict):
  request : {"id": int, "method": str, "params": dict, "token": str}
  response: {"id": int, "ok": bool, "result": Any} | {"id": int, "ok": False, "error": str}
  event   : {"event": str, "data": dict}
"""
from __future__ import annotations

import itertools
import secrets
import threading
import time
from multiprocessing.connection import Client, Listener
from typing import Any, Callable, Mapping, Optional

DEFAULT_TIMEOUT = 30.0


class RpcError(RuntimeError):
    """Blad komunikacji lub blad zwrocony przez zdalna metode."""


class RpcServer:
    def __init__(
        self,
        address: str,
        authkey: bytes,
        handlers: Mapping[str, Callable[[dict], Any]],
        *,
        token: Optional[str] = None,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.address = address
        self.authkey = authkey
        self.handlers = dict(handlers)
        self.token = token or secrets.token_urlsafe(32)
        self._log = logger or (lambda msg: None)
        self._listener: Optional[Listener] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._conns: list[Any] = []
        self._conns_lock = threading.Lock()

    # ------------------------------------------------------------------- cykl zycia
    def start(self) -> None:
        self._listener = Listener(self.address, family="AF_PIPE", authkey=self.authkey)
        self._thread = threading.Thread(target=self._accept_loop, name="cisza-rpc-accept", daemon=True)
        self._thread.start()
        self._log(f"RPC nasluchuje: {self.address}")

    def stop(self) -> None:
        self._stop.set()
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        if self._listener is not None:
            try:
                self._listener.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def broadcast(self, event: str, data: Optional[dict] = None) -> None:
        message = {"event": event, "data": data or {}}
        with self._conns_lock:
            conns = list(self._conns)
        dead = []
        for conn in conns:
            try:
                conn.send(message)
            except Exception:
                dead.append(conn)
        if dead:
            with self._conns_lock:
                for conn in dead:
                    if conn in self._conns:
                        self._conns.remove(conn)

    # ------------------------------------------------------------------ wewnetrzne
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                assert self._listener is not None
                conn = self._listener.accept()
            except (OSError, EOFError):
                if self._stop.is_set():
                    return
                time.sleep(0.2)
                continue
            with self._conns_lock:
                self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), name="cisza-rpc-conn", daemon=True).start()

    def _serve(self, conn) -> None:
        try:
            while not self._stop.is_set():
                if not conn.poll(0.5):
                    continue
                try:
                    message = conn.recv()
                except (EOFError, OSError):
                    return
                if not isinstance(message, dict) or "method" not in message:
                    continue
                if message.get("token") != self.token:
                    conn.send({"id": message.get("id"), "ok": False, "error": "brak autoryzacji"})
                    continue
                method = message["method"]
                params = message.get("params") or {}
                handler = self.handlers.get(method)
                if handler is None:
                    conn.send({"id": message.get("id"), "ok": False, "error": f"nieznana metoda: {method}"})
                    continue
                try:
                    result = handler(params)
                    conn.send({"id": message.get("id"), "ok": True, "result": result})
                except Exception as exc:  # noqa: BLE001 - blad przekazujemy do klienta
                    self._log(f"RPC {method} blad: {exc!r}")
                    conn.send({"id": message.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            with self._conns_lock:
                if conn in self._conns:
                    self._conns.remove(conn)
            try:
                conn.close()
            except Exception:
                pass


class RpcClient:
    def __init__(
        self,
        address: str,
        authkey: bytes,
        token: str,
        *,
        on_event: Optional[Callable[[str, dict], None]] = None,
        connect_timeout: float = 5.0,
    ) -> None:
        self.address = address
        self.authkey = authkey
        self.token = token
        self.on_event = on_event
        self._conn = Client(self.address, family="AF_PIPE", authkey=self.authkey)
        self._ids = itertools.count(1)
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, name="cisza-rpc-reader", daemon=True)
        self._reader.start()
        self.last_call: float = 0.0

    # ------------------------------------------------------------------------ API
    @property
    def connected(self) -> bool:
        return not self._closed.is_set()

    def call(self, method: str, timeout: float = DEFAULT_TIMEOUT, **params: Any) -> Any:
        if self._closed.is_set():
            raise RpcError("polaczenie z helperem zamkniete")
        request_id = next(self._ids)
        slot = {"event": threading.Event(), "response": None}
        with self._lock:
            self._pending[request_id] = slot
        message = {"id": request_id, "method": method, "params": params, "token": self.token}
        try:
            with self._send_lock:
                self._conn.send(message)
        except (OSError, BrokenPipeError, EOFError) as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            self._closed.set()
            raise RpcError(f"wysylka nieudana: {exc}") from exc
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise RpcError(f"przekroczono czas oczekiwania na {method}")
        self.last_call = time.time()
        response = slot["response"] or {}
        if not response.get("ok"):
            raise RpcError(response.get("error") or "nieznany blad")
        return response.get("result")

    def close(self) -> None:
        self._closed.set()
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "RpcClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------ wewnetrzne
    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                if not self._conn.poll(0.5):
                    continue
                message = self._conn.recv()
            except (EOFError, OSError):
                self._closed.set()
                return
            if not isinstance(message, dict):
                continue
            if "event" in message:
                if self.on_event is not None:
                    try:
                        self.on_event(str(message["event"]), dict(message.get("data") or {}))
                    except Exception:
                        pass
                continue
            request_id = message.get("id")
            with self._lock:
                slot = self._pending.pop(request_id, None)
            if slot is not None:
                slot["response"] = message
                slot["event"].set()
        self._closed.set()


def make_token_file_token() -> str:
    return secrets.token_urlsafe(32)
