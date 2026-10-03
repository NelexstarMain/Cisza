"""Testy focuslock.block.firewall, focuslock.block.networklock i focuslock.shell.elevate.

Zadna operacja nie dotyka prawdziwego systemu: netsh, rejestr, hosts i zapora sa
podmieniane na atrapy, a lockstate trafia do katalogu tymczasowego.
"""
from __future__ import annotations

import json
import multiprocessing.connection as mpc
import threading
import uuid
from pathlib import Path

import pytest

from focuslock import lockstate, paths
from focuslock.block import firewall, networklock
from focuslock.shell import elevate

CHROME = r"C:\Program Files\Google\Chrome\chrome.exe"
FIREFOX = r"C:\Program Files\Mozilla Firefox\firefox.exe"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "data"))


# =============================================================== firewall
def test_firewall_dry_run_lists_rules_without_calling_netsh(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("netsh nie moze byc wolany w dry-run")

    monkeypatch.setattr(firewall, "_run_netsh", boom)
    report = firewall.apply_browser_blocks([CHROME, FIREFOX], dry_run=True)
    assert report["ok"] is True
    assert len(report["applied"]) == 3
    assert any("CiszaBlock-1" in item and "chrome.exe" in item for item in report["applied"])
    assert any("CiszaBlock-2" in item and "firefox.exe" in item for item in report["applied"])
    assert all("remoteport=80,443" in item for item in report["applied"][:2])
    assert "dry-run" in report["applied"][-1]


def test_firewall_dry_run_without_programs_returns_warning(monkeypatch):
    monkeypatch.setattr(firewall, "_run_netsh", lambda args: (_ for _ in ()).throw(AssertionError("netsh")))
    report = firewall.apply_browser_blocks([], dry_run=True)
    assert report["ok"] is True
    assert report["warnings"]


def test_firewall_apply_removes_stale_rules_and_adds_new(monkeypatch):
    calls: list = []

    def fake(args):
        calls.append(list(args))
        if "show" in args:
            return 0, "Rule Name: CiszaBlock-1\n"
        return 0, "Ok."

    monkeypatch.setattr(firewall, "_run_netsh", fake)
    report = firewall.apply_browser_blocks([CHROME], dry_run=False)
    assert report["ok"] is True
    assert any("usunieto stara regule CiszaBlock-1" in item for item in report["applied"])
    assert any(item.startswith("netsh advfirewall firewall add rule") for item in report["applied"])
    assert ["advfirewall", "firewall", "delete", "rule", "name=CiszaBlock-1"] in calls


def test_firewall_reports_missing_admin(monkeypatch):
    monkeypatch.setattr(firewall, "_run_netsh", lambda args: (1, "Requested operation requires elevation."))
    report = firewall.apply_browser_blocks([CHROME])
    assert report["ok"] is False
    assert any("administratora" in item for item in report["errors"])


def test_firewall_remove_blocks_parses_names(monkeypatch):
    calls: list = []

    def fake(args):
        calls.append(list(args))
        if "show" in args:
            return 0, "Nazwa reguly:                              CiszaBlock-2\nCos: CiszaBlock-7\n"
        return 0, "Ok."

    monkeypatch.setattr(firewall, "_run_netsh", fake)
    report = firewall.remove_blocks()
    assert report["ok"] is True
    assert "usunieto regule CiszaBlock-2" in report["applied"]
    assert "usunieto regule CiszaBlock-7" in report["applied"]


def test_firewall_remove_blocks_is_idempotent(monkeypatch):
    monkeypatch.setattr(firewall, "_run_netsh", lambda args: (0, ""))
    report = firewall.remove_blocks()
    assert report["ok"] is True
    assert report["applied"] == []
    assert firewall.is_active() is False


def test_firewall_remove_blocks_dry_run_does_not_call_netsh(monkeypatch):
    monkeypatch.setattr(firewall, "_run_netsh", lambda args: (_ for _ in ()).throw(AssertionError("netsh")))
    report = firewall.remove_blocks(dry_run=True)
    assert report["ok"] is True
    assert report["applied"]


def test_firewall_is_active_true_when_rule_exists(monkeypatch):
    monkeypatch.setattr(firewall, "_run_netsh", lambda args: (0, "Rule Name: CiszaBlock-3\n"))
    assert firewall.is_active() is True


def test_firewall_is_active_never_raises(monkeypatch):
    def boom(args):
        raise RuntimeError("nietypowy blad systemu")

    monkeypatch.setattr(firewall, "_run_netsh", boom)
    assert firewall.is_active() is False


# =============================================================== networklock
class FakeProxy:
    def __init__(self, allowlist, blocklist=(), safe_hosts=(), port=8765, mode="allowlist", dry_run=False, on_event=None):
        self.allowlist = tuple(allowlist)
        self.blocklist = tuple(blocklist)
        self.safe_hosts = tuple(safe_hosts)
        self.mode = mode
        self.port = int(port) or 4321
        self.on_event = on_event
        self.started = False
        self.stop_calls = 0
        self.blocked_count = 7
        self._server = None

    def start(self):
        self.started = True
        self._server = object()
        return {
            "ok": True,
            "applied": [f"proxy {self.mode} nasluchuje na 127.0.0.1:{self.port}"],
            "warnings": [],
            "errors": [],
        }

    def stop(self):
        self.started = False
        self._server = None
        self.stop_calls += 1
        return {"ok": True, "applied": ["proxy zatrzymany"], "warnings": [], "errors": []}

    @property
    def running(self):
        return self.started


class BrokenProxy(FakeProxy):
    def start(self):
        return {"ok": False, "applied": [], "warnings": [], "errors": ["nie mozna uruchomic proxy"]}


class FakeHosts:
    def __init__(self, state):
        self.state = state

    def flush_dns(self, dry_run=False):
        self.state["dns"] += 1
        return {"ok": True, "applied": ["ipconfig /flushdns"], "warnings": [], "errors": []}

    def backup(self, hosts_path=None):
        self.state["hosts"].append(("backup",))
        return str(Path(self.state["backup_path"]))

    def block_domains(self, domains, hosts_path=None, dry_run=False):
        self.state["hosts"].append(("block", tuple(domains)))
        return {"ok": True, "applied": [f"dodano sekcje Cisza ({len(domains)} domen)"], "warnings": [], "errors": []}

    def unblock(self, hosts_path=None, dry_run=False):
        self.state["hosts"].append(("unblock",))
        return {"ok": True, "applied": ["usunieto sekcje Cisza"], "warnings": [], "errors": []}

    def restore_backup(self, backup_path, hosts_path=None, dry_run=False):
        self.state["hosts"].append(("restore", str(backup_path)))
        return {"ok": True, "applied": [f"przywrocono hosts z {backup_path}"], "warnings": [], "errors": []}


class FakeFirewall:
    def __init__(self, state):
        self.state = state
        self.fail = False

    def apply_browser_blocks(self, exes, profile="CiszaBlock", dry_run=False):
        self.state["firewall"].append(("apply", tuple(exes), profile))
        if self.fail:
            return {"ok": False, "applied": [], "warnings": [], "errors": ["brak uprawnien administratora"]}
        return {"ok": True, "applied": [f"netsh advfirewall firewall add rule name={profile}-1"], "warnings": [], "errors": []}

    def remove_blocks(self, profile="CiszaBlock", dry_run=False):
        self.state["firewall"].append(("remove", profile))
        return {"ok": True, "applied": [f"usunieto regule {profile}-1"], "warnings": [], "errors": []}


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "data"))
    state = {
        "registry": [],
        "winhttp": [],
        "hosts": [],
        "firewall": [],
        "dns": 0,
        "notify": 0,
        "proxies": [],
        "backup_path": str(tmp_path / "hosts-test.bak"),
    }

    def make_proxy(*args, **kwargs):
        instance = FakeProxy(*args, **kwargs)
        state["proxies"].append(instance)
        return instance

    monkeypatch.setattr(networklock, "AllowlistProxy", make_proxy)
    monkeypatch.setattr(networklock, "_read_proxy_settings", lambda: {})

    def write_settings(values):
        state["registry"].append(dict(values))
        return [f"rejestr: {name}={value}" for name, value in values.items()]

    def winhttp_set(port):
        state["winhttp"].append(("set", port))
        return True, "Ok."

    def winhttp_reset():
        state["winhttp"].append(("reset", 0))
        return True, "Ok."

    monkeypatch.setattr(networklock, "_write_proxy_settings", write_settings)
    monkeypatch.setattr(networklock, "_notify_settings_change", lambda: state.__setitem__("notify", state["notify"] + 1))
    monkeypatch.setattr(networklock, "_winhttp_set", winhttp_set)
    monkeypatch.setattr(networklock, "_winhttp_reset", winhttp_reset)

    hosts_obj = FakeHosts(state)
    firewall_obj = FakeFirewall(state)
    monkeypatch.setattr(networklock, "hosts", hosts_obj)
    monkeypatch.setattr(networklock, "firewall", firewall_obj)
    state["hosts_obj"] = hosts_obj
    state["firewall_obj"] = firewall_obj
    return state


def test_enable_allowlist_wires_everything(system):
    lock = networklock.NetworkLock(port=9999)
    report = lock.enable_allowlist(
        ["example.com"],
        safe_hosts=["safe.example"],
        browser_exes=["chrome.exe"],
        block_browser_direct=True,
    )
    assert report["ok"] is True
    assert lock.proxy is not None
    assert lock.proxy.mode == "allowlist"
    assert lock.proxy.allowlist == ("example.com",)
    assert lock.proxy.safe_hosts == ("safe.example",)

    assert system["registry"][0]["ProxyEnable"] == 1
    assert system["registry"][0]["ProxyServer"] == "127.0.0.1:9999"
    assert system["registry"][0]["ProxyOverride"] == "localhost;127.*;<local>"
    assert system["registry"][0]["AutoConfigURL"] is None
    assert system["notify"] == 1
    assert ("set", 9999) in system["winhttp"]
    assert ("apply", ("chrome.exe",), "CiszaBlock") in system["firewall"]
    assert system["dns"] == 1

    status = lock.status()
    assert status["active"] is True
    assert status["mode"] == "allowlist"
    assert status["blocked_attempts"] == 7
    assert status["port"] == 9999

    state = lockstate.read()
    assert state is not None
    assert state.network.mode == "allowlist"
    assert state.network.proxy_port == 9999
    assert state.network.proxy_enabled_by_us is True
    assert state.network.firewall_profile == "CiszaBlock"


def test_enable_blocklist_patches_hosts_then_disable_restores(system):
    lock = networklock.NetworkLock(port=9999)
    report = lock.enable_blocklist(["youtube.com", "x.com"])
    assert report["ok"] is True
    assert ("backup",) in system["hosts"]
    assert ("block", ("youtube.com", "x.com")) in system["hosts"]

    state = lockstate.read()
    assert state is not None
    assert state.network.mode == "blocklist"
    assert state.network.hosts_patched is True
    assert state.network.hosts_backup.endswith("hosts-test.bak")

    off = lock.disable()
    assert off["ok"] is True
    assert lock.proxy is None
    assert any(item[0] == "restore" for item in system["hosts"])
    assert ("reset", 0) in system["winhttp"]
    assert system["registry"][-1]["ProxyEnable"] == 0
    assert ("remove", "CiszaBlock") not in system["firewall"]

    cleared = lockstate.read()
    assert cleared is not None
    assert cleared.network.mode == ""
    assert cleared.network.proxy_enabled_by_us is False
    assert cleared.network.hosts_patched is False


def test_disable_is_idempotent(system):
    lock = networklock.NetworkLock(port=9999)
    lock.enable_allowlist(["example.com"], browser_exes=[], block_browser_direct=False)
    first = lock.disable()
    assert first["ok"] is True
    second = lock.disable()
    assert second["ok"] is True
    assert any("nie byl zmieniany" in item for item in second["warnings"])


def test_disable_with_nothing_enabled_is_safe(system):
    lock = networklock.NetworkLock(port=9999)
    report = lock.disable()
    assert report["ok"] is True
    assert report["warnings"]


def test_dry_run_touches_nothing(system):
    lock = networklock.NetworkLock(port=9999, dry_run=True)
    report = lock.enable_allowlist(["example.com"], browser_exes=["chrome.exe"])
    assert report["ok"] is True
    assert report["applied"]
    assert lock.proxy is None
    assert system["registry"] == []
    assert system["winhttp"] == []
    assert system["hosts"] == []
    assert system["firewall"] == []
    assert system["dns"] == 0
    assert lockstate.read() is None

    off = lock.disable()
    assert off["ok"] is True
    assert off["applied"]
    assert system["registry"] == []


def test_proxy_failure_aborts_before_touching_system(system, monkeypatch):
    monkeypatch.setattr(networklock, "AllowlistProxy", BrokenProxy)
    lock = networklock.NetworkLock(port=9999)
    report = lock.enable_allowlist(["example.com"])
    assert report["ok"] is False
    assert report["errors"]
    assert system["registry"] == []
    assert system["winhttp"] == []
    assert lock.status()["active"] is False


def test_firewall_failure_keeps_allowlist_active(system):
    system["firewall_obj"].fail = True
    lock = networklock.NetworkLock(port=9999)
    report = lock.enable_allowlist(["example.com"], browser_exes=["chrome.exe"], block_browser_direct=True)
    assert report["ok"] is True
    assert report["errors"]
    assert any("zapora" in item for item in report["errors"])
    assert lock.status()["active"] is True
    assert lockstate.read().network.firewall_profile == ""


def test_status_reports_zero_without_lockstate():
    lock = networklock.NetworkLock(port=8765)
    status = lock.status()
    assert status == {"active": False, "mode": "", "blocked_attempts": 0, "port": 8765}


# =============================================================== elevate
def test_is_admin_returns_bool():
    assert isinstance(elevate.is_admin(), bool)


def test_python_launch_command_uses_module_entrypoint():
    command = elevate.python_launch_command(["--helper", "--token", "abc"])
    assert command[1:3] == ["-m", "focuslock"]
    assert command[3:] == ["--helper", "--token", "abc"]
    assert Path(command[0]).name.lower() in ("pythonw.exe", "python.exe")


def test_load_credentials_creates_shared_json_format(monkeypatch):
    monkeypatch.delenv("CISZA_HELPER_TOKEN", raising=False)
    monkeypatch.delenv("CISZA_HELPER_AUTHKEY", raising=False)
    token, authkey = elevate.load_credentials()
    assert token and authkey
    data = json.loads(paths.rpc_token_path().read_text(encoding="utf-8"))
    assert data == {"token": token, "authkey": authkey}
    assert elevate.load_credentials() == (token, authkey)


def test_helper_token_is_stable():
    first = elevate.helper_token()
    second = elevate.helper_token()
    assert first and first == second


def test_load_credentials_reads_helperclient_file(monkeypatch):
    monkeypatch.delenv("CISZA_HELPER_TOKEN", raising=False)
    monkeypatch.delenv("CISZA_HELPER_AUTHKEY", raising=False)
    authkey_hex = "ab" * 32
    paths.rpc_token_path().write_text(
        json.dumps({"token": "tok", "authkey": authkey_hex}), encoding="utf-8"
    )
    assert elevate.load_credentials() == ("tok", authkey_hex)


def test_load_credentials_prefers_environment(monkeypatch):
    monkeypatch.setenv("CISZA_HELPER_TOKEN", "env-token")
    monkeypatch.setenv("CISZA_HELPER_AUTHKEY", "cd" * 32)
    assert elevate.load_credentials() == ("env-token", "cd" * 32)


def test_load_credentials_accepts_legacy_plain_token(monkeypatch):
    monkeypatch.delenv("CISZA_HELPER_TOKEN", raising=False)
    monkeypatch.delenv("CISZA_HELPER_AUTHKEY", raising=False)
    paths.rpc_token_path().write_text("legacy-token", encoding="utf-8")
    token, authkey = elevate.load_credentials()
    assert token == "legacy-token"
    assert authkey
    data = json.loads(paths.rpc_token_path().read_text(encoding="utf-8"))
    assert data["token"] == "legacy-token"
    assert data["authkey"] == authkey


def test_launch_helper_dry_run_does_not_execute(monkeypatch):
    def boom(params):
        raise AssertionError("ShellExecuteW nie moze byc wolane w dry-run")

    monkeypatch.setattr(elevate, "_shell_execute", boom)
    report = elevate.launch_helper(dry_run=True)
    assert report["ok"] is True
    assert report["pid"] is None
    assert report["applied"]
    assert report["warnings"]


def test_launch_helper_reports_uac_refusal(monkeypatch):
    monkeypatch.setattr(elevate, "_shell_execute", lambda params: elevate.SE_ERR_ACCESSDENIED)
    report = elevate.launch_helper()
    assert report["ok"] is False
    assert report["pid"] is None
    assert any("UAC" in item for item in report["errors"])


def test_launch_helper_reports_other_error_codes(monkeypatch):
    monkeypatch.setattr(elevate, "_shell_execute", lambda params: 2)
    report = elevate.launch_helper()
    assert report["ok"] is False
    assert any("kod 2" in item for item in report["errors"])


def test_launch_helper_success(monkeypatch):
    captured = {}

    def fake(params):
        captured["params"] = params
        return 42

    monkeypatch.setattr(elevate, "_shell_execute", fake)
    report = elevate.launch_helper()
    assert report["ok"] is True
    assert report["applied"]
    assert "-m focuslock" in captured["params"]
    assert "--helper" in captured["params"]
    assert "--token" in captured["params"]
    assert "--authkey" in captured["params"]


def test_launch_helper_handles_shell_execute_exception(monkeypatch):
    def boom(params):
        raise OSError("shell32 niedostepne")

    monkeypatch.setattr(elevate, "_shell_execute", boom)
    report = elevate.launch_helper()
    assert report["ok"] is False
    assert report["errors"]


def test_helper_running_false_for_missing_pipe():
    name = rf"\\.\pipe\cisza-missing-{uuid.uuid4().hex}"
    assert elevate.helper_running(name, b"key", "token") is False


def test_helper_running_detects_live_pipe():
    name = rf"\\.\pipe\cisza-test-{uuid.uuid4().hex}"
    authkey = b"test-authkey"
    try:
        listener = mpc.Listener(name, family="AF_PIPE", authkey=authkey)
    except OSError as exc:  # srodowisko bez nazwanych potokow
        pytest.skip(f"nazwane potoki niedostepne: {exc}")

    def serve():
        try:
            connection = listener.accept()
            message = connection.recv()
            connection.send({"id": message.get("id", 0), "ok": True, "result": "pong"})
            connection.close()
        except Exception:  # noqa: BLE001
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        assert elevate.helper_running(name, authkey, "token") is True
    finally:
        thread.join(timeout=1.0)
        listener.close()


def test_helper_running_against_real_rpc_server():
    """Zgodnosc z ipc.RpcServer (tym, ktorego uzywa focuslock/helper.py)."""
    from focuslock import ipc

    name = rf"\\.\pipe\cisza-ipc-{uuid.uuid4().hex}"
    authkey = b"integration-authkey"
    token = "integration-token"
    try:
        server = ipc.RpcServer(name, authkey, {"ping": lambda params: {"pong": True}}, token=token)
        server.start()
    except OSError as exc:  # srodowisko bez nazwanych potokow
        pytest.skip(f"nazwane potoki niedostepne: {exc}")
    try:
        assert elevate.helper_running(name, authkey, token) is True
    finally:
        server.stop()
