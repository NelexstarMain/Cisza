"""Testy focuslock.block.proxy.

host_allowed()/normalize_host() sa testowane czysto (bez sieci). Testy serwera uzywaja
prawdziwego gniazda na 127.0.0.1 z wolnym portem i lokalnego serwera upstream -
zaden test nie wychodzi do internetu.
"""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from focuslock.block import proxy

UPSTREAM_BODY = b"CISZA-UPSTREAM-OK"


# ------------------------------------------------------------------ funkcje czyste
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Example.COM:8080", "example.com"),
        ("  example.com.  ", "example.com"),
        ("https://WWW.YouTube.com/watch?v=1", "www.youtube.com"),
        ("user@host.example:443", "host.example"),
        ("127.0.0.1:8765", "127.0.0.1"),
        ("[::1]:80", "::1"),
        ("*.Example.com.", "*.example.com"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_host(raw, expected):
    assert proxy.normalize_host(raw) == expected


def test_host_allowed_exact_match():
    decision = proxy.host_allowed("example.com", ["example.com"])
    assert decision.allowed is True
    assert decision.reason == "allow"


def test_host_allowed_matches_subdomains():
    assert proxy.host_allowed("pl.wikipedia.org", ["wikipedia.org"]).allowed is True


def test_host_allowed_wildcard():
    decision = proxy.host_allowed("a.b.example.com", ["*.example.com"])
    assert decision.allowed is True
    assert decision.reason == "allow"


def test_host_allowed_wildcard_matches_bare_domain():
    assert proxy.host_allowed("example.com", ["*.example.com"]).allowed is True


def test_host_allowed_not_in_allowlist():
    decision = proxy.host_allowed("example.com", ["allowed.example"])
    assert decision.allowed is False
    assert decision.reason == "not-in-allowlist"


def test_host_allowed_does_not_match_lookalike_suffix():
    assert proxy.host_allowed("badexample.com", ["example.com"]).allowed is False


def test_host_allowed_empty_allowlist_means_blocklist_mode():
    decision = proxy.host_allowed("example.com", [])
    assert decision.allowed is True
    assert decision.reason == "allow"


def test_host_allowed_blocked():
    decision = proxy.host_allowed("youtube.com", [], ["youtube.com"])
    assert decision.allowed is False
    assert decision.reason == "blocked"


def test_host_allowed_wildcard_blocklist_blocks_subdomain():
    assert proxy.host_allowed("www.youtube.com", [], ["*.youtube.com"]).allowed is False


def test_host_allowed_blocklist_wins_over_allowlist():
    decision = proxy.host_allowed("x.com", ["x.com"], ["x.com"])
    assert decision.allowed is False
    assert decision.reason == "blocked"


def test_host_allowed_safe_hosts_always_pass():
    decision = proxy.host_allowed("time.windows.com", [])
    assert decision.allowed is True
    assert decision.reason == "safe"


def test_host_allowed_safe_wildcard_passes():
    assert proxy.host_allowed("windowsupdate.microsoft.com", []).allowed is True


def test_host_allowed_ip_address():
    decision = proxy.host_allowed("192.168.0.1", ["example.com"])
    assert decision.allowed is True
    assert decision.reason == "ip"


def test_host_allowed_explicit_ip_block_wins():
    decision = proxy.host_allowed("10.0.0.1", [], ["10.0.0.1"])
    assert decision.allowed is False
    assert decision.reason == "blocked"


def test_host_allowed_empty_host_denied():
    assert proxy.host_allowed("", ["example.com"]).allowed is False


def test_render_block_page_is_monochrome_and_names_host():
    page = proxy.render_block_page("Blocked.Example", "not-in-allowlist")
    assert "blocked.example" in page
    assert "#000000" in page and "#ffffff" in page
    assert "zablokowana" in page
    assert "Strona zablokowana" in page


# ------------------------------------------------------------------- serwer proxy
class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - API bazowe
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(UPSTREAM_BODY)))
        self.end_headers()
        self.wfile.write(UPSTREAM_BODY)

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        yield ("127.0.0.1", int(server.server_address[1]))
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def make_proxy():
    created: list = []

    def factory(*args, **kwargs):
        instance = proxy.AllowlistProxy(*args, **kwargs)
        created.append(instance)
        return instance

    yield factory
    for instance in created:
        try:
            instance.stop()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def fake_dns(monkeypatch):
    """Mapuje wymyslone nazwy na 127.0.0.1, zebysmy nie dotykali prawdziwego DNS."""
    real = socket.getaddrinfo

    def resolver(host, port, *args, **kwargs):
        if isinstance(host, str) and host.endswith(".test"):
            return real("127.0.0.1", port, *args, **kwargs)
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolver)


def _recv_all(sock: socket.socket, limit: int = 65536) -> bytes:
    chunks: list[bytes] = []
    total = 0
    sock.settimeout(5)
    try:
        while total < limit:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except (socket.timeout, OSError):
        pass
    return b"".join(chunks)


def _http_via_proxy(port: int, raw: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(raw)
        return _recv_all(sock)


def _read_header(sock: socket.socket) -> bytes:
    data = b""
    sock.settimeout(5)
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1)
        if not chunk:
            break
        data += chunk
    return data


def test_proxy_blocks_http_and_emits_event(make_proxy):
    events: list = []
    instance = make_proxy(
        ["allowed.example"],
        port=0,
        mode="allowlist",
        on_event=lambda name, data: events.append((name, data)),
    )
    assert instance.start()["ok"] is True
    assert instance.port > 0

    response = _http_via_proxy(
        instance.port,
        b"GET http://blocked.example/ HTTP/1.1\r\nHost: blocked.example\r\nConnection: close\r\n\r\n",
    )
    text = response.decode("utf-8", errors="replace")
    assert "403" in text.split("\r\n", 1)[0]
    assert "blocked.example" in text
    assert "zablokowana" in text
    assert instance.blocked_count == 1
    assert events == [("blocked_site", {"host": "blocked.example", "reason": "not-in-allowlist"})]


def test_proxy_forwards_allowed_http(make_proxy, upstream, fake_dns):
    host, port = upstream
    instance = make_proxy(["upstream.test"], port=0, mode="allowlist")
    assert instance.start()["ok"] is True

    response = _http_via_proxy(
        instance.port,
        f"GET http://upstream.test:{port}/hello HTTP/1.1\r\nHost: upstream.test:{port}\r\n"
        f"Connection: close\r\n\r\n".encode(),
    )
    assert b"200" in response.split(b"\r\n", 1)[0]
    assert UPSTREAM_BODY in response
    assert instance.blocked_count == 0


def test_proxy_forwards_requests_to_ip_host(make_proxy, upstream):
    host, port = upstream
    instance = make_proxy(["example.com"], port=0, mode="allowlist")
    assert instance.start()["ok"] is True

    response = _http_via_proxy(
        instance.port,
        f"GET http://{host}:{port}/hello HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode(),
    )
    assert b"200" in response.split(b"\r\n", 1)[0]
    assert UPSTREAM_BODY in response


def test_proxy_connect_tunnel_allowed(make_proxy, upstream, fake_dns):
    host, port = upstream
    instance = make_proxy(["tunnel.test"], port=0, mode="allowlist")
    assert instance.start()["ok"] is True

    sock = socket.create_connection(("127.0.0.1", instance.port), timeout=5)
    try:
        sock.sendall(f"CONNECT tunnel.test:{port} HTTP/1.1\r\nHost: tunnel.test:{port}\r\n\r\n".encode())
        head = _read_header(sock)
        assert b"200" in head.split(b"\r\n", 1)[0]
        sock.sendall(b"GET /hello HTTP/1.1\r\nHost: tunnel.test\r\nConnection: close\r\n\r\n")
        data = _recv_all(sock)
        assert UPSTREAM_BODY in data
    finally:
        sock.close()


def test_proxy_connect_blocked_gets_403_without_tunnel(make_proxy):
    instance = make_proxy(["allowed.example"], port=0, mode="allowlist")
    assert instance.start()["ok"] is True

    with socket.create_connection(("127.0.0.1", instance.port), timeout=5) as sock:
        sock.sendall(b"CONNECT blocked.example:443 HTTP/1.1\r\nHost: blocked.example:443\r\n\r\n")
        data = _recv_all(sock)
    status_line = data.split(b"\r\n", 1)[0]
    assert b"403" in status_line
    assert b"200" not in status_line
    assert instance.blocked_count == 1


def test_set_lists_takes_effect_immediately(make_proxy, upstream, fake_dns):
    host, port = upstream
    instance = make_proxy([], ["upstream.test"], port=0, mode="blocklist")
    assert instance.start()["ok"] is True

    blocked = _http_via_proxy(
        instance.port,
        f"GET http://upstream.test:{port}/hello HTTP/1.1\r\nHost: upstream.test:{port}\r\n"
        f"Connection: close\r\n\r\n".encode(),
    )
    assert b"403" in blocked.split(b"\r\n", 1)[0]

    report = instance.set_lists(blocklist=[], mode="blocklist")
    assert report["ok"] is True

    allowed = _http_via_proxy(
        instance.port,
        f"GET http://upstream.test:{port}/hello HTTP/1.1\r\nHost: upstream.test:{port}\r\n"
        f"Connection: close\r\n\r\n".encode(),
    )
    assert b"200" in allowed.split(b"\r\n", 1)[0]
    assert UPSTREAM_BODY in allowed
    assert instance.blocked_count == 1


def test_set_lists_switches_to_allowlist_mode(make_proxy, upstream):
    host, port = upstream
    instance = make_proxy([], [], port=0, mode="blocklist")
    assert instance.start()["ok"] is True

    report = instance.set_lists(allowlist=["only.example"], mode="allowlist")
    assert report["ok"] is True

    response = _http_via_proxy(
        instance.port,
        f"GET http://{host}:{port}/hello HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode(),
    )
    # adresy IP sa przepuszczane, wiec sprawdzamy zablokowanie domeny spoza allowlist
    assert b"200" in response.split(b"\r\n", 1)[0]
    blocked = _http_via_proxy(
        instance.port,
        b"GET http://other.example/ HTTP/1.1\r\nHost: other.example\r\nConnection: close\r\n\r\n",
    )
    assert b"403" in blocked.split(b"\r\n", 1)[0]


def test_proxy_dry_run_does_not_bind(make_proxy):
    instance = make_proxy(["example.com"], port=0, dry_run=True)
    report = instance.start()
    assert report["ok"] is True
    assert report["applied"]
    assert instance.running is False
    stop_report = instance.stop()
    assert stop_report["ok"] is True
    assert stop_report["warnings"]


def test_proxy_start_reports_invalid_port(make_proxy):
    instance = make_proxy(["example.com"], port=70000)
    report = instance.start()
    assert report["ok"] is False
    assert report["errors"]


def test_proxy_stop_is_idempotent(make_proxy):
    instance = make_proxy(["example.com"], port=0)
    assert instance.start()["ok"] is True
    first = instance.stop()
    second = instance.stop()
    assert first["ok"] is True
    assert second["ok"] is True
    assert second["warnings"]


def test_proxy_start_twice_warns(make_proxy):
    instance = make_proxy(["example.com"], port=0)
    assert instance.start()["ok"] is True
    again = instance.start()
    assert again["ok"] is True
    assert again["warnings"]


def test_blocked_count_starts_at_zero(make_proxy):
    instance = make_proxy(["example.com"], port=0)
    assert instance.blocked_count == 0
    assert instance.port == 0
