"""Testy focuslock.block.hosts.

Wszystkie testy pracuja wylacznie na pliku tymczasowym (tmp_path) - prawdziwy
C:\\Windows\\System32\\drivers\\etc\\hosts nie jest nigdy dotykany.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from focuslock.block import hosts

USER_LINES = [
    "127.0.0.1 localhost",
    "::1 localhost",
    "10.0.0.5 serwer.local",
]


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Kopie i lockstate trafiaja do katalogu tymczasowego, nie do LOCALAPPDATA."""
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "data"))


def _make_hosts(tmp_path: Path, lines=None, newline: str = "\r\n", encoding: str = "utf-8") -> Path:
    path = tmp_path / "hosts"
    text = newline.join(USER_LINES if lines is None else lines) + newline
    path.write_bytes(text.encode(encoding))
    return path


def test_default_hosts_path_points_to_system_dir():
    path = str(hosts.default_hosts_path())
    assert "System32" in path and "drivers" in path and path.endswith("hosts")


def test_block_adds_section_and_preserves_user_entries(tmp_path):
    path = _make_hosts(tmp_path)
    report = hosts.block_domains(["youtube.com", "*.facebook.com"], hosts_path=path)
    assert report["ok"] is True
    content = path.read_bytes()
    assert b"# CISZA-BEGIN" in content and b"# CISZA-END" in content
    assert b"0.0.0.0 youtube.com" in content
    assert b"0.0.0.0 *.facebook.com" in content
    assert content.count(b"0.0.0.0 ") == 2
    for line in USER_LINES:
        assert line.encode() in content


def test_block_is_idempotent(tmp_path):
    path = _make_hosts(tmp_path)
    hosts.block_domains(["youtube.com", "x.com"], hosts_path=path)
    first = path.read_bytes()
    report = hosts.block_domains(["youtube.com", "x.com"], hosts_path=path)
    second = path.read_bytes()
    assert report["ok"] is True
    assert first == second
    assert second.count(b"# CISZA-BEGIN") == 1
    assert second.count(b"# CISZA-END") == 1
    assert second.count(b"0.0.0.0 youtube.com") == 1


def test_block_replaces_previous_section(tmp_path):
    path = _make_hosts(tmp_path)
    hosts.block_domains(["youtube.com"], hosts_path=path)
    hosts.block_domains(["tiktok.com"], hosts_path=path)
    content = path.read_bytes()
    assert b"0.0.0.0 tiktok.com" in content
    assert b"youtube.com" not in content
    assert content.count(b"# CISZA-BEGIN") == 1


@pytest.mark.parametrize("newline", ["\r\n", "\n"])
def test_block_preserves_line_endings(tmp_path, newline):
    path = _make_hosts(tmp_path, newline=newline)
    hosts.block_domains(["youtube.com"], hosts_path=path)
    content = path.read_bytes()
    if newline == "\r\n":
        assert content.replace(b"\r\n", b"").count(b"\n") == 0
    else:
        assert b"\r" not in content
    assert content.endswith(newline.encode())


def test_block_preserves_non_utf8_bytes(tmp_path):
    path = tmp_path / "hosts"
    ansi_tail = b"\xb3\xf3\xe6"  # cp1250: "loc" z ogonkami - niepoprawne UTF-8
    path.write_bytes(b"127.0.0.1 localhost\r\n# komentarz: " + ansi_tail + b"\r\n")
    report = hosts.block_domains(["youtube.com"], hosts_path=path)
    assert report["ok"] is True
    content = path.read_bytes()
    assert not content.startswith(b"\xef\xbb\xbf")
    assert ansi_tail in content
    assert b"0.0.0.0 youtube.com" in content


def test_block_skips_critical_and_invalid_domains(tmp_path):
    path = _make_hosts(tmp_path)
    report = hosts.block_domains(["localhost", "127.0.0.1", "zly wpis", "youtube.com"], hosts_path=path)
    assert report["ok"] is True
    assert report["warnings"]
    content = path.read_bytes()
    assert b"0.0.0.0 youtube.com" in content
    assert b"0.0.0.0 localhost" not in content
    assert b"0.0.0.0 127.0.0.1" not in content


def test_block_without_domains_returns_warning(tmp_path):
    path = _make_hosts(tmp_path)
    before = path.read_bytes()
    report = hosts.block_domains([], hosts_path=path)
    assert report["ok"] is True
    assert report["warnings"]
    assert path.read_bytes() == before


def test_block_creates_missing_file(tmp_path):
    path = tmp_path / "hosts-missing"
    report = hosts.block_domains(["youtube.com"], hosts_path=path)
    assert report["ok"] is True
    assert hosts.is_blocked(hosts_path=path) is True


def test_unblock_removes_only_section_and_is_idempotent(tmp_path):
    path = _make_hosts(tmp_path)
    original = path.read_bytes()
    hosts.block_domains(["youtube.com"], hosts_path=path)
    assert hosts.is_blocked(hosts_path=path) is True

    report = hosts.unblock(hosts_path=path)
    assert report["ok"] is True
    assert path.read_bytes() == original
    assert hosts.is_blocked(hosts_path=path) is False

    after = path.read_bytes()
    second = hosts.unblock(hosts_path=path)
    assert second["ok"] is True
    assert second["warnings"]
    assert path.read_bytes() == after


def test_unblock_without_section_keeps_file(tmp_path):
    path = _make_hosts(tmp_path)
    before = path.read_bytes()
    report = hosts.unblock(hosts_path=path)
    assert report["ok"] is True
    assert report["warnings"]
    assert path.read_bytes() == before


def test_is_blocked_false_for_partial_section(tmp_path):
    path = tmp_path / "hosts"
    path.write_bytes(b"# CISZA-BEGIN\r\n0.0.0.0 youtube.com\r\n")
    assert hosts.is_blocked(hosts_path=path) is False


def test_is_blocked_never_raises_on_missing_file(tmp_path):
    assert hosts.is_blocked(hosts_path=tmp_path / "nie-ma-takiego") is False


def test_dry_run_does_not_touch_file(tmp_path):
    path = _make_hosts(tmp_path)
    before = path.read_bytes()
    report = hosts.block_domains(["youtube.com"], hosts_path=path, dry_run=True)
    assert report["ok"] is True
    assert report["applied"]
    assert any("dry-run" in item for item in report["applied"])
    assert path.read_bytes() == before
    assert hosts.is_blocked(hosts_path=path) is False


def test_unblock_dry_run_does_not_touch_file(tmp_path):
    path = _make_hosts(tmp_path)
    hosts.block_domains(["youtube.com"], hosts_path=path)
    before = path.read_bytes()
    report = hosts.unblock(hosts_path=path, dry_run=True)
    assert report["ok"] is True
    assert path.read_bytes() == before


def test_permission_error_is_reported_without_exception(tmp_path, monkeypatch):
    path = _make_hosts(tmp_path)
    before = path.read_bytes()

    def deny(*args, **kwargs):
        raise PermissionError("odmowa dostepu")

    monkeypatch.setattr(hosts, "_atomic_write_bytes", deny)
    report = hosts.block_domains(["youtube.com"], hosts_path=path)
    assert report["ok"] is False
    assert report["errors"]
    assert "administratora" in report["errors"][0]
    assert path.read_bytes() == before


def test_permission_error_on_unblock_is_reported(tmp_path, monkeypatch):
    path = _make_hosts(tmp_path)
    hosts.block_domains(["youtube.com"], hosts_path=path)

    def deny(*args, **kwargs):
        raise PermissionError("odmowa dostepu")

    monkeypatch.setattr(hosts, "_atomic_write_bytes", deny)
    report = hosts.unblock(hosts_path=path)
    assert report["ok"] is False
    assert report["errors"]


def test_backup_and_restore_roundtrip(tmp_path):
    path = _make_hosts(tmp_path)
    original = path.read_bytes()
    backup_path = hosts.backup(hosts_path=path)
    assert backup_path
    assert Path(backup_path).exists()
    assert Path(backup_path).read_bytes() == original

    hosts.block_domains(["youtube.com"], hosts_path=path)
    assert hosts.is_blocked(hosts_path=path) is True

    report = hosts.restore_backup(backup_path, hosts_path=path)
    assert report["ok"] is True
    assert path.read_bytes() == original


def test_restore_backup_dry_run(tmp_path):
    path = _make_hosts(tmp_path)
    backup_path = hosts.backup(hosts_path=path)
    hosts.block_domains(["youtube.com"], hosts_path=path)
    after_block = path.read_bytes()
    report = hosts.restore_backup(backup_path, hosts_path=path, dry_run=True)
    assert report["ok"] is True
    assert path.read_bytes() == after_block


def test_restore_missing_backup_reports_error(tmp_path):
    path = _make_hosts(tmp_path)
    report = hosts.restore_backup(str(tmp_path / "nie-ma.bak"), hosts_path=path)
    assert report["ok"] is False
    assert report["errors"]


def test_backup_missing_hosts_returns_empty_string(tmp_path):
    assert hosts.backup(hosts_path=tmp_path / "nie-ma") == ""


def test_flush_dns_dry_run_does_not_call_ipconfig(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("ipconfig nie moze byc wolany w dry-run")

    monkeypatch.setattr(hosts.subprocess, "run", boom)
    report = hosts.flush_dns(dry_run=True)
    assert report["ok"] is True
    assert report["applied"]


def test_flush_dns_reports_failure_without_exception(monkeypatch):
    class Result:
        returncode = 1
        stdout = b"blad"
        stderr = b"odmowa"

    monkeypatch.setattr(hosts.subprocess, "run", lambda *args, **kwargs: Result())
    report = hosts.flush_dns()
    assert report["ok"] is False
    assert report["errors"]


def test_reports_have_frozen_shape(tmp_path):
    path = _make_hosts(tmp_path)
    for report in (
        hosts.block_domains(["youtube.com"], hosts_path=path, dry_run=True),
        hosts.unblock(hosts_path=path, dry_run=True),
        hosts.flush_dns(dry_run=True),
    ):
        assert set(report) == {"ok", "applied", "warnings", "errors"}
        assert isinstance(report["applied"], list)
