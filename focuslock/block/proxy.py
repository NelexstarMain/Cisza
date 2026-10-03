"""Lokalny proxy allowlist/blocklist (HTTP + CONNECT, bez MITM).

Zamrozony kontrakt (docs/INTERFACES.md, sekcja 4):
    Decision, host_allowed(host, allowlist, blocklist=()), normalize_host(value),
    AllowlistProxy(allowlist, blocklist=(), safe_hosts=(), port=8765, mode="allowlist",
                   dry_run=False, on_event=None)

Zasady:
  - Serwer wylacznie na 127.0.0.1 (ThreadingHTTPServer), obsluga wielu polaczen.
  - CONNECT to tunel TCP bez podgladania TLS; zablokowany host nie dostaje tunelu.
  - Zablokowane zadanie -> HTTP 403 + monochromatyczna strona blokady po polsku.
  - host_allowed() jest czysta funkcja (bez sieci) i testowalna jednostkowo.
  - Eventy: ("blocked_site", {"host": str, "reason": str}).
  - Tryb "allowlist": przepuszczamy tylko liste dozwolonych (+ safe_hosts + adresy IP).
  - Tryb "blocklist": przepuszczamy wszystko poza lista blokowanych.

Uwaga o adresach IP: host_allowed() przepuszcza adresy IP (reason "ip"), bo blokowanie
po samym IP jest zawodne i ucieloby m.in. konfiguracje routera. Jawny wpis w blocklist
ma jednak pierwszenstwo nad tym wyjatkiem.
"""
from __future__ import annotations

import html
import http.client
import ipaddress
import re
import select
import socket
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Sequence
from urllib.parse import urlsplit

from .. import config

_HOP_HEADERS = frozenset(
    {
        "connection",
        "proxy-connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

_REASON_TEXT = {
    "not-in-allowlist": "domena nie jest na liscie dozwolonych",
    "blocked": "domena jest na liscie blokowanych",
    "safe": "domena systemowa",
    "ip": "adres IP",
    "allow": "domena dozwolona",
}

BLOCK_PAGE = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strona zablokowana</title>
<style>
  html, body { background: #000000; color: #ffffff; margin: 0; padding: 0; }
  body { font-family: Consolas, "Courier New", monospace; display: flex;
         align-items: center; justify-content: center; min-height: 100vh; }
  main { border: 1px solid #ffffff; padding: 40px 48px; max-width: 640px; }
  h1 { font-size: 20px; letter-spacing: 4px; text-transform: uppercase;
       margin: 0 0 24px 0; font-weight: normal; }
  p { line-height: 1.7; margin: 8px 0; }
  .host { border: 1px solid #ffffff; padding: 2px 8px; }
  .hint { margin-top: 24px; opacity: 0.7; }
</style>
</head>
<body>
<main>
  <h1>Strona zablokowana</h1>
  <p>Domena <span class="host">__HOST__</span> jest zablokowana.</p>
  <p>Powod: __REASON__.</p>
  <p class="hint">Tryb nauki Cisza trwa. Wroc do pracy.</p>
</main>
</body>
</html>
"""


@dataclass
class Decision:
    """Wynik decyzji dla pojedynczego hosta."""

    allowed: bool
    reason: str  # "allow" | "not-in-allowlist" | "blocked" | "safe" | "ip"


# ------------------------------------------------------------------------ funkcje czyste
def normalize_host(value: str) -> str:
    """Obniza, obcina port i kropke koncowa; usuwa scheme, sciezke i userinfo."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            text = text[1:end]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    text = text.strip().strip("\t")
    while text.endswith("."):
        text = text[:-1]
    return text


def _matches(host: str, pattern: str) -> bool:
    """Dopasowanie hosta do wzorca: "*" oraz domena + jej subdomeny."""
    cleaned = normalize_host(pattern)
    if not cleaned:
        return False
    if "*" in cleaned:
        regex = "^" + re.escape(cleaned).replace(r"\*", ".*") + "$"
        if re.match(regex, host):
            return True
        if cleaned.startswith("*.") and host == cleaned[2:]:
            return True
        return False
    return host == cleaned or host.endswith("." + cleaned)


def _host_decision(
    host: str,
    allowlist: Sequence[str],
    blocklist: Sequence[str],
    safe_hosts: Sequence[str],
    mode: str = "allowlist",
) -> Decision:
    """Wlasciwa logika decyzji z jawnym trybem pracy."""
    normalized = normalize_host(host)
    if not normalized:
        return Decision(False, "not-in-allowlist")

    for pattern in safe_hosts or ():
        if _matches(normalized, pattern):
            return Decision(True, "safe")

    for pattern in blocklist or ():
        if _matches(normalized, pattern):
            return Decision(False, "blocked")

    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        return Decision(True, "ip")

    if mode == "blocklist":
        return Decision(True, "allow")

    for pattern in allowlist or ():
        if _matches(normalized, pattern):
            return Decision(True, "allow")
    return Decision(False, "not-in-allowlist")


def host_allowed(host: str, allowlist: Sequence[str], blocklist: Sequence[str] = ()) -> Decision:
    """Czysta decyzja dla hosta; tryb wynika z tego, czy allowlist jest niepusta.

    Domeny z config.SAFE_HOSTS sa zawsze przepuszczane (Windows Update, czas).
    """
    allow = tuple(allowlist or ())
    mode = "allowlist" if allow else "blocklist"
    return _host_decision(host, allow, tuple(blocklist or ()), config.SAFE_HOSTS, mode)


def render_block_page(host: str, reason: str = "") -> str:
    """Monochromatyczny HTML strony blokady (czarne tlo, bialy tekst)."""
    shown = html.escape(normalize_host(host) or "nieznana domena")
    reason_text = _REASON_TEXT.get(reason, reason or "zablokowane")
    return BLOCK_PAGE.replace("__HOST__", shown).replace("__REASON__", html.escape(reason_text))


def _split_host_port(value: str, default_port: int = 80) -> tuple[str, int]:
    text = (value or "").strip()
    if not text:
        return "", default_port
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            host = text[1:end]
            rest = text[end + 1 :]
            if rest.startswith(":") and rest[1:].isdigit():
                return host, int(rest[1:])
            return host, default_port
    if text.count(":") == 1:
        host, _, port_text = text.partition(":")
        if port_text.isdigit():
            return host, int(port_text)
    return text, default_port


# --------------------------------------------------------------------------- serwer
class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CiszaProxy/1.0"
    sys_version = ""

    @property
    def proxy(self) -> "AllowlistProxy":
        return self.server.proxy  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - API bazowe
        """Wycisza domyslny log BaseHTTPRequestHandler (nie zasypujemy konsoli)."""

    # ------------------------------------------------------------------ metody HTTP
    def do_GET(self) -> None:
        self._handle_http()

    def do_HEAD(self) -> None:
        self._handle_http()

    def do_POST(self) -> None:
        self._handle_http()

    def do_PUT(self) -> None:
        self._handle_http()

    def do_DELETE(self) -> None:
        self._handle_http()

    def do_OPTIONS(self) -> None:
        self._handle_http()

    def do_PATCH(self) -> None:
        self._handle_http()

    def _parse_target(self) -> tuple[str, int, str]:
        path = self.path or "/"
        lowered = path.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            parts = urlsplit(path)
            host = parts.hostname or ""
            port = parts.port or (443 if parts.scheme == "https" else 80)
            target = parts.path or "/"
            if parts.query:
                target = f"{target}?{parts.query}"
            return host, int(port), target
        host, port = _split_host_port(self.headers.get("Host") or "", 80)
        return host, port, path

    def _handle_http(self) -> None:
        proxy = self.proxy
        host, port, target = self._parse_target()
        decision = proxy._decide(host)
        if not decision.allowed:
            proxy._note_blocked(host, decision.reason)
            self._send_block_page(host, decision.reason)
            return

        body = self._read_body()
        try:
            connection = http.client.HTTPConnection(host, port, timeout=10)
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in _HOP_HEADERS and key.lower() != "content-length"
            }
            if body is not None:
                headers["Content-Length"] = str(len(body))
            connection.request(self.command, target, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            status = response.status
            response_headers = [
                (key, value)
                for key, value in response.getheaders()
                if key.lower() not in _HOP_HEADERS and key.lower() != "content-length"
            ]
            connection.close()
        except (OSError, http.client.HTTPException) as exc:
            self._send_plain(502, "Bad Gateway", f"nie mozna polaczyc z {host}:{port} ({exc})")
            return

        try:
            self.send_response(status)
            for key, value in response_headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD" and data:
                self.wfile.write(data)
        except OSError:
            pass
        self.close_connection = True

    def _read_body(self) -> Optional[bytes]:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return None
        try:
            length = int(raw_length)
        except ValueError:
            return None
        if length <= 0:
            return None
        try:
            return self.rfile.read(length)
        except OSError:
            return None

    # ------------------------------------------------------------------- CONNECT
    def do_CONNECT(self) -> None:
        proxy = self.proxy
        host, port = _split_host_port(self.path, 443)
        decision = proxy._decide(host)
        if not decision.allowed:
            proxy._note_blocked(host, decision.reason)
            self._send_block_page(host, decision.reason)
            return
        try:
            remote = socket.create_connection((host, port), timeout=15)
        except OSError as exc:
            self._send_plain(502, "Bad Gateway", f"nie mozna otworzyc tunelu do {host}:{port} ({exc})")
            return
        try:
            self.send_response(200, "Connection Established")
            self.end_headers()
        except OSError:
            try:
                remote.close()
            except OSError:
                pass
            return
        self._tunnel(remote)

    def _tunnel(self, remote: socket.socket) -> None:
        """Dwukierunkowe przekazywanie bajtow (bez podgladania TLS)."""
        client = self.connection
        try:
            while True:
                try:
                    readable, _, _ = select.select([client, remote], [], [], 30.0)
                except (OSError, ValueError):
                    return
                if not readable:
                    return
                for source, target in ((client, remote), (remote, client)):
                    if source not in readable:
                        continue
                    try:
                        chunk = source.recv(65536)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError:
                        return
                    if not chunk:
                        return
                    try:
                        target.sendall(chunk)
                    except OSError:
                        return
        finally:
            try:
                remote.close()
            except OSError:
                pass
            self.close_connection = True

    # ------------------------------------------------------------------ odpowiedzi
    def _send_block_page(self, host: str, reason: str) -> None:
        body = render_block_page(host, reason).encode("utf-8")
        try:
            self.send_response(403)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True

    def _send_plain(self, status: int, title: str, message: str) -> None:
        body = f"{title}: {message}\n".encode("utf-8", errors="replace")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler) -> None:
        super().__init__(address, handler)
        self.proxy: Optional["AllowlistProxy"] = None


class AllowlistProxy:
    """Serwer proxy na 127.0.0.1 w trybie allowlist albo blocklist."""

    def __init__(
        self,
        allowlist: Sequence[str],
        blocklist: Sequence[str] = (),
        safe_hosts: Sequence[str] = (),
        port: int = 8765,
        mode: str = "allowlist",
        dry_run: bool = False,
        on_event: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._allowlist = self._as_tuple(allowlist)
        self._blocklist = self._as_tuple(blocklist)
        # Domeny systemowe sa chronione zawsze, nawet gdy caller nic nie poda.
        merged_safe = list(self._as_tuple(safe_hosts)) + list(config.SAFE_HOSTS)
        self._safe_hosts = self._as_tuple(merged_safe)
        self._port = int(port)
        self._configured_port = int(port)
        self._mode = mode if mode in ("allowlist", "blocklist") else "allowlist"
        self._dry_run = bool(dry_run)
        self._on_event = on_event
        self._server: Optional[_ProxyServer] = None
        self._thread: Optional[threading.Thread] = None
        self._blocked = 0
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------- pomoc
    @staticmethod
    def _as_tuple(values: Optional[Sequence[str]]) -> tuple[str, ...]:
        if not values:
            return ()
        seen: list[str] = []
        for item in values:
            text = str(item).strip()
            if text and text not in seen:
                seen.append(text)
        return tuple(seen)

    @property
    def port(self) -> int:
        return int(self._port)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def allowlist(self) -> tuple[str, ...]:
        return self._allowlist

    @property
    def blocklist(self) -> tuple[str, ...]:
        return self._blocklist

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def blocked_count(self) -> int:
        with self._lock:
            return int(self._blocked)

    # ------------------------------------------------------------------- decyzje
    def _decide(self, host: str) -> Decision:
        return _host_decision(host, self._allowlist, self._blocklist, self._safe_hosts, self._mode)

    def _note_blocked(self, host: str, reason: str) -> None:
        with self._lock:
            self._blocked += 1
        if self._on_event is not None:
            try:
                self._on_event("blocked_site", {"host": normalize_host(host), "reason": reason})
            except Exception:  # noqa: BLE001 - event nie moze przewrocic proxy
                pass

    # ------------------------------------------------------------------ cykl zycia
    def start(self) -> dict:
        report = {"ok": True, "applied": [], "warnings": [], "errors": []}
        if self._server is not None:
            report["warnings"].append("proxy juz dziala")
            return report
        if self._dry_run:
            report["applied"].append(
                f"proxy {self._mode} na 127.0.0.1:{self._port} (dry-run, gniazdo nie zostalo otwarte)"
            )
            return report

        try:
            server = _ProxyServer(("127.0.0.1", self._port), _ProxyHandler)
        except (OSError, OverflowError, ValueError) as exc:
            report["ok"] = False
            report["errors"].append(f"nie mozna uruchomic proxy na 127.0.0.1:{self._port}: {exc}")
            return report
        server.proxy = self
        self._server = server
        self._port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="cisza-proxy",
            daemon=True,
        )
        self._thread.start()
        report["applied"].append(f"proxy {self._mode} nasluchuje na 127.0.0.1:{self._port}")
        return report

    def stop(self) -> dict:
        report = {"ok": True, "applied": [], "warnings": [], "errors": []}
        server = self._server
        if server is None:
            report["warnings"].append("proxy nie dziala")
            return report
        self._server = None
        try:
            server.shutdown()
            server.server_close()
        except OSError as exc:
            report["warnings"].append(f"zamkniecie proxy: {exc}")
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        report["applied"].append("proxy zatrzymany")
        return report

    def set_lists(
        self,
        allowlist: Optional[Sequence[str]] = None,
        blocklist: Optional[Sequence[str]] = None,
        mode: Optional[str] = None,
    ) -> dict:
        report = {"ok": True, "applied": [], "warnings": [], "errors": []}
        if allowlist is not None:
            self._allowlist = self._as_tuple(allowlist)
            report["applied"].append(f"allowlist: {len(self._allowlist)} wpisow")
        if blocklist is not None:
            self._blocklist = self._as_tuple(blocklist)
            report["applied"].append(f"blocklist: {len(self._blocklist)} wpisow")
        if mode is not None:
            if mode in ("allowlist", "blocklist"):
                self._mode = mode
                report["applied"].append(f"tryb: {mode}")
            else:
                report["warnings"].append(f"nieznany tryb: {mode}")
        if not report["applied"]:
            report["warnings"].append("brak zmian list")
        return report
