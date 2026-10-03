"""Testy dopasowywania regul procesow i bezpieczenstwa ProcessGuard.

Testy sa czyste: nie zabijaja, nie usypiaja i nie modyfikuja zadnego procesu.
ProcessGuard jest uzywany wylacznie z dry_run=True albo na obiektach
ProcessInfo zbudowanych recznie (wysokie PID-y, ktore nie istnieja).
"""
from __future__ import annotations

import os
import re
import time

import pytest

from focuslock import config
from focuslock.block import launchwatch as LW
from focuslock.block import processes as P

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def make_info(pid: int = 4_242_001, name: str = "chrome.exe", exe: str = CHROME,
              created: float = 0.0) -> P.ProcessInfo:
    return P.ProcessInfo(pid=pid, name=name, exe=exe, create_time=created)


def make_guard(**kwargs) -> P.ProcessGuard:
    params = {"allow": [], "block": [], "interval": 0.05, "dry_run": True}
    params.update(kwargs)
    return P.ProcessGuard(**params)


# ------------------------------------------------------------------ MatchRule


def test_matchrule_roundtrip_i_normalizacja():
    rule = P.MatchRule(kind="NAME", value=" chrome.exe ", label="Chrome")
    assert rule.kind == "name"
    assert rule.value == "chrome.exe"
    assert rule.to_dict() == {"kind": "name", "value": "chrome.exe", "label": "Chrome"}
    assert P.MatchRule.from_dict(rule.to_dict()) == rule
    assert rule.describe() == "name:chrome.exe"


def test_matchrule_odrzuca_nieznany_rodzaj():
    with pytest.raises(ValueError):
        P.MatchRule(kind="cokolwiek", value="x")


def test_matchrule_odporne_na_smieci():
    assert P.MatchRule.from_dict({}).kind == "name"
    assert P.MatchRule.from_dict({}).value == ""
    assert P.MatchRule.from_dict(None).value == ""  # type: ignore[arg-type]
    assert P.MatchRule("path", None).value == ""  # type: ignore[arg-type]
    assert P.MatchRule("name", "x", None).label == ""  # type: ignore[arg-type]


# --------------------------------------------------------------- dopasowanie


@pytest.mark.parametrize(
    "name, pattern, expected",
    [
        ("chrome.exe", "chrome.exe", True),
        ("CHROME.EXE", "chrome.exe", True),
        ("chrome.exe", "CHROME.EXE", True),
        ("chrome.exe", "firefox.exe", False),
        ("chrome.exe", "*chrome*", True),
        ("chrome.exe", "chrome*", True),
        ("chrome.exe", "?hrome.exe", True),
        ("chrome.exe", "chrome.ex?", True),
        ("xchrome.exe", "chrome.exe", False),
        ("msedge.exe", "?sedge.exe", True),
        ("chrome.exe", "", False),
        ("", "*", True),
    ],
)
def test_dopasowanie_nazwy(name, pattern, expected):
    rule = P.MatchRule("name", pattern)
    matched = P.match_process(make_info(name=name), [rule])
    assert (matched is rule) is expected


def test_dopasowanie_sciezki_wielkosc_liter_i_ukosniki():
    rule = P.MatchRule("path", r"*\google\chrome\application\chrome.exe")
    assert P.match_process(make_info(), [rule]) is rule

    slash_rule = P.MatchRule("path", "c:/program files/*/chrome.exe")
    assert P.match_process(make_info(), [slash_rule]) is slash_rule

    wildcard = P.MatchRule("path", "*chrome.exe")
    assert P.match_process(make_info(), [wildcard]) is wildcard

    other = P.MatchRule("path", r"*firefox.exe")
    assert P.match_process(make_info(), [other]) is None


def test_dopasowanie_sciezki_pomija_puste_exe():
    rule = P.MatchRule("path", "*chrome.exe")
    assert P.match_process(make_info(exe=""), [rule]) is None


def test_dopasowanie_sygnatury(tmp_path):
    target = tmp_path / "app.exe"
    payload = b"cisza-test-signature"
    target.write_bytes(payload)

    signature = P.compute_signature(str(target))
    assert re.fullmatch(r"sha1:[0-9a-f]{40}:\d+", signature)
    assert signature.endswith(f":{len(payload)}")

    rule = P.MatchRule("signature", signature)
    assert P.match_process(make_info(exe=str(target)), [rule]) is rule

    wrong = P.MatchRule("signature", "sha1:" + "0" * 40 + f":{len(payload)}")
    assert P.match_process(make_info(exe=str(target)), [wrong]) is None


def test_sygnatura_pusta_przy_bledzie(tmp_path):
    assert P.compute_signature("") == ""
    assert P.compute_signature(str(tmp_path)) == ""  # katalog, nie plik
    assert P.compute_signature(str(tmp_path / "nie-ma-takiego.exe")) == ""
    assert P.compute_signature(None) == ""  # type: ignore[arg-type]


def test_pierwsza_regula_wygrywa():
    first = P.MatchRule("name", "*chrome*")
    second = P.MatchRule("name", "chrome.exe")
    assert P.match_process(make_info(), [first, second]) is first
    assert P.match_process(make_info(), [second, first]) is second


def test_brak_regul_zwraca_none():
    assert P.match_process(make_info(), []) is None
    assert P.match_process(make_info(), None) is None  # type: ignore[arg-type]
    assert P.match_process(None, [P.MatchRule("name", "*")]) is None  # type: ignore[arg-type]


def test_reguly_przyjmuja_dict_i_string():
    dict_rule = P.match_process(make_info(), [{"kind": "name", "value": "chrome.exe"}])
    assert dict_rule is not None and dict_rule.kind == "name"
    str_rule = P.match_process(make_info(), ["chrome.exe"])
    assert str_rule is not None and str_rule.value == "chrome.exe"
    assert P.match_process(make_info(), [42, "firefox.exe"]) is None  # smieci pomijane


# ------------------------------------------------------------- procesy systemowe


def test_is_system_safe():
    assert P.is_system_safe("svchost.exe")
    assert P.is_system_safe("SVCHOST.EXE")
    assert P.is_system_safe(r"C:\Windows\System32\svchost.exe")
    assert P.is_system_safe(" explorer.exe ")
    assert not P.is_system_safe("chrome.exe")
    assert not P.is_system_safe("")
    assert not P.is_system_safe(None)  # type: ignore[arg-type]


def test_system_safe_jest_polaczeniem_listy_z_config():
    assert P.SYSTEM_SAFE
    assert all(name == name.lower() for name in P.SYSTEM_SAFE)
    assert "" not in P.SYSTEM_SAFE
    for name in config.SYSTEM_SAFE_PROCESSES:
        assert name in P.SYSTEM_SAFE
    for name in P.EXTRA_SAFE_PROCESSES:
        assert name in P.SYSTEM_SAFE
    assert "trustedinstaller.exe" in P.SYSTEM_SAFE


def test_is_own_process():
    assert P.is_own_process(make_info(pid=os.getpid(), name="chrome.exe"))
    assert P.is_own_process(make_info(pid=4_242_002, name="cisza-helper.exe"))
    assert P.is_own_process(make_info(pid=4_242_003, name="python.exe"))
    assert P.is_own_process(make_info(pid=4_242_004, name="python3.13.exe"))
    assert P.is_own_process(make_info(pid=4_242_005, name="cisza-watchdog.exe"))
    assert not P.is_own_process(make_info(pid=4_242_006, name="chrome.exe"))
    assert not P.is_own_process(make_info(pid=4_242_007, name=""))
    # sciezka nie decyduje: katalog z "cisza" w nazwie to nie nasz proces
    assert not P.is_own_process(make_info(pid=4_242_008, name="probe.exe", exe=r"C:\Temp\cisza-test\probe.exe"))
    assert not P.is_own_process(make_info(pid=4_242_009, name="probe.exe", exe=r"C:\focuslock\probe.exe"))


def test_list_processes_zwraca_processinfo():
    proc_list = P.list_processes()
    assert isinstance(proc_list, list)
    assert proc_list, "spodziewana niepusta lista procesow"
    pids = {info.pid for info in proc_list}
    assert os.getpid() in pids
    for info in proc_list:
        assert isinstance(info, P.ProcessInfo)
        assert info.pid > 0
        assert info.name == info.name.lower()
        assert isinstance(info.exe, str)
        assert isinstance(info.create_time, float)


# ----------------------------------------------------------------- ProcessGuard


def test_guard_start_stop_stats_nie_rzucaja_dry_run():
    guard = make_guard(allow=[P.MatchRule("name", "chrome.exe")])
    started = guard.start()
    assert set(started) == {"ok", "applied", "warnings", "errors"}
    assert started["ok"] is True
    assert started["applied"]
    assert started["errors"] == []

    again = guard.start()
    assert again["ok"] is True and again["warnings"]

    time.sleep(0.2)
    stats = guard.stats()
    for key in ("blocked", "suspended", "killed", "skipped", "recent"):
        assert key in stats
    assert stats["ok"] is True
    assert isinstance(stats["recent"], list)
    assert stats["dry_run"] is True
    assert stats["running"] is True
    # dry_run: zadna akcja na systemie nie zostala wykonana
    assert stats["suspended"] == 0
    assert stats["killed"] == 0

    stopped = guard.stop()
    assert stopped["ok"] is True
    assert stopped["applied"]
    assert guard.stats()["running"] is False

    stopped_again = guard.stop()
    assert stopped_again["ok"] is True and stopped_again["warnings"]


def test_guard_stats_przed_startem_i_pusty_guard():
    guard = make_guard()
    stats = guard.stats()
    assert stats["blocked"] == 0
    assert stats["recent"] == []
    assert stats["running"] is False
    assert guard.stop()["warnings"]
    assert guard.update()["ok"] is True


def test_guard_pomija_systemowe_i_wlasne():
    guard = make_guard(grace=0.0)
    guard.check_process(make_info(pid=os.getpid(), name="chrome.exe"))
    guard.check_process(make_info(pid=4_242_010, name="svchost.exe"))
    guard.check_process(make_info(pid=4_242_011, name="explorer.exe", exe=r"C:\Windows\explorer.exe"))
    guard.check_process(make_info(pid=4_242_012, name="cisza-helper.exe"))
    stats = guard.stats()
    assert stats["blocked"] == 0
    assert stats["suspended"] == 0
    assert stats["killed"] == 0
    assert stats["skipped"] >= 4


def test_guard_dry_run_bez_akcji_na_systemie():
    guard = make_guard(block=[P.MatchRule("name", "chrome.exe")], grace=0.0)
    report = guard.check_process(make_info(pid=4_242_020, name="chrome.exe"))
    assert report["ok"] is True
    stats = guard.stats()
    assert stats["blocked"] == 1
    assert stats["suspended"] == 0
    assert stats["killed"] == 0
    entry = stats["recent"][-1]
    assert entry["name"] == "chrome.exe"
    assert entry["action"] == "suspend"
    assert entry["dry_run"] is True


def test_guard_zglasza_blocked_app():
    events = []
    guard = make_guard(
        block=[P.MatchRule("name", "chrome.exe")],
        grace=0.0,
        on_event=lambda event, payload: events.append((event, payload)),
    )
    guard.check_process(make_info(pid=4_242_021, name="chrome.exe"))
    assert events, "spodziewane zdarzenie blocked_app"
    event, payload = events[0]
    assert event == "blocked_app"
    assert payload["name"] == "chrome.exe"
    assert payload["pid"] == 4_242_021
    assert payload["action"] == "suspend"
    assert payload["rule"] == "name:chrome.exe"
    assert payload["dry_run"] is True


def test_guard_block_dziala_natychmiast_na_pasujace_procesy():
    """block = lista zawsze blokowanych; proces spoza list tez jest blokowany (po grace)."""
    events = []
    guard = make_guard(
        block=[P.MatchRule("name", "chrome.exe")],
        grace=0.0,
        on_event=lambda event, payload: events.append((event, payload)),
    )
    guard.check_process(make_info(pid=4_242_022, name="chrome.exe"))
    guard.check_process(make_info(pid=4_242_026, name="firefox.exe"))

    assert guard.stats()["blocked"] == 2
    assert len(events) == 2
    assert events[0][1]["rule"] == "name:chrome.exe"
    assert events[1][1]["rule"] == "*"  # spoza list - regula domyslna


def test_guard_block_wygrywa_z_allow():
    guard = make_guard(
        allow=[P.MatchRule("name", "chrome.exe")],
        block=[P.MatchRule("name", "chrome.exe")],
        grace=0.0,
    )
    guard.check_process(make_info(pid=4_242_023, name="chrome.exe"))
    stats = guard.stats()
    assert stats["blocked"] == 1
    assert stats["allowed"] == 0


def test_guard_block_natychmiast_bez_karencji():
    """Pozycja z `block` nie czeka na grace; proces spoza list - czeka."""
    guard = make_guard(block=[P.MatchRule("name", "steam.exe")], grace=99.0)
    first = guard.check_process(make_info(pid=4_242_027, name="steam.exe"), now=1000.0)
    assert first["applied"] == ["blocked:steam.exe:suspend"]
    assert guard.stats()["blocked"] == 1

    pending = guard.check_process(make_info(pid=4_242_028, name="notepad.exe"), now=1000.0)
    assert pending["applied"] == ["pending"]
    assert guard.stats()["blocked"] == 1


def test_guard_allow_i_block_razem():
    """Przyklad z zalozen: allow=[chrome], block=[steam] -> reszta tez blokowana."""
    guard = make_guard(
        allow=[P.MatchRule("name", "chrome.exe")],
        block=[P.MatchRule("name", "steam.exe")],
        grace=0.0,
    )
    guard.check_process(make_info(pid=4_242_033, name="chrome.exe"))
    guard.check_process(make_info(pid=4_242_034, name="steam.exe"))
    guard.check_process(make_info(pid=4_242_035, name="notepad.exe"))

    stats = guard.stats()
    assert stats["allowed"] == 1
    assert stats["blocked"] == 2
    names = [entry["name"] for entry in stats["recent"]]
    assert names == ["steam.exe", "notepad.exe"]


def test_guard_grace_odracza_akcje():
    guard = make_guard(grace=5.0)
    first = guard.check_process(make_info(pid=4_242_024, name="chrome.exe"), now=1000.0)
    assert first["applied"] == ["pending"]
    assert guard.stats()["blocked"] == 0

    second = guard.check_process(make_info(pid=4_242_024, name="chrome.exe"), now=1006.0)
    assert second["applied"]
    assert guard.stats()["blocked"] == 1


def test_guard_update_zmienia_reguly_i_akcje():
    guard = make_guard(grace=0.0)
    report = guard.update(allow=[P.MatchRule("name", "chrome.exe")], block=[], action="KILL")
    assert report["ok"] is True
    assert "action:kill" in report["applied"]
    assert guard.stats()["action"] == "kill"

    bad = guard.update(action="wybuch")
    assert bad["ok"] is False
    assert bad["errors"]
    assert guard.stats()["action"] == "kill"


def test_guard_update_reguly_przyjmuje_stringi():
    guard = make_guard(grace=0.0)
    assert guard.update(block=["chrome.exe"])["ok"] is True
    guard.check_process(make_info(pid=4_242_025, name="chrome.exe"))
    assert guard.stats()["blocked"] == 1


def test_guard_nie_rusza_dwukrotnie_tego_samego_pid():
    guard = make_guard(block=[P.MatchRule("name", "chrome.exe")], grace=0.0)
    guard.check_process(make_info(pid=4_242_026, name="chrome.exe"))
    guard.check_process(make_info(pid=4_242_026, name="chrome.exe"))
    assert guard.stats()["blocked"] == 1


def test_guard_bledny_callback_nie_przerywa():
    def boom(event, payload):
        raise RuntimeError("callback nie moze wywalic guardu")

    guard = make_guard(block=[P.MatchRule("name", "chrome.exe")], grace=0.0, on_event=boom)
    report = guard.check_process(make_info(pid=4_242_027, name="chrome.exe"))
    assert report["ok"] is True
    assert guard.stats()["blocked"] == 1


def test_guard_kill_pomija_systemowe_i_wlasne(monkeypatch):
    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "svchost.exe"

        def kill(self):
            raise AssertionError("kill nie moze dotknac procesu systemowego")

        def suspend(self):
            raise AssertionError("suspend nie moze dotknac procesu systemowego")

    monkeypatch.setattr(P.psutil, "Process", FakeProc)
    guard = make_guard(action="kill", block=[P.MatchRule("name", "*")], grace=0.0, dry_run=False)
    for pid, name in (
        (1, "svchost.exe"),
        (2, "python.exe"),
        (3, "pythonw.exe"),
        (4, "cisza-helper.exe"),
        (5, "explorer.exe"),
        (6, "trustedinstaller.exe"),
    ):
        guard.check_process(make_info(pid=pid, name=name))

    stats = guard.stats()
    assert stats["blocked"] == 0
    assert stats["killed"] == 0
    assert stats["suspended"] == 0
    assert stats["skipped"] >= 6


def test_guard_kill_wykonuje_akcje_na_fake_procesie(monkeypatch):
    calls = []

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "probe-app-xq.exe"

        def kill(self):
            calls.append(("kill", self.pid))

        def suspend(self):
            calls.append(("suspend", self.pid))

    monkeypatch.setattr(P.psutil, "Process", FakeProc)
    guard = make_guard(
        action="kill", block=[P.MatchRule("name", "probe-app-xq.exe")], grace=0.0, dry_run=False
    )
    guard.check_process(make_info(pid=4_242_040, name="probe-app-xq.exe"))

    assert calls == [("kill", 4_242_040)]
    stats = guard.stats()
    assert stats["blocked"] == 1
    assert stats["killed"] == 1


def test_guard_suspend_wykonuje_akcje_na_fake_procesie(monkeypatch):
    calls = []

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "probe-app-xq.exe"

        def kill(self):
            calls.append(("kill", self.pid))

        def suspend(self):
            calls.append(("suspend", self.pid))

    monkeypatch.setattr(P.psutil, "Process", FakeProc)
    guard = make_guard(
        action="suspend", block=[P.MatchRule("name", "probe-app-xq.exe")], grace=0.0, dry_run=False
    )
    guard.check_process(make_info(pid=4_242_041, name="probe-app-xq.exe"))

    assert calls == [("suspend", 4_242_041)]
    stats = guard.stats()
    assert stats["blocked"] == 1
    assert stats["suspended"] == 1
    assert stats["killed"] == 0


def test_guard_nie_rusza_procesu_gdy_pid_zostal_uzyty_powtornie(monkeypatch):
    calls = []

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "inny-proces.exe"  # PID przejety przez inny proces

        def kill(self):
            calls.append("kill")

        def suspend(self):
            calls.append("suspend")

    monkeypatch.setattr(P.psutil, "Process", FakeProc)
    guard = make_guard(
        action="kill", block=[P.MatchRule("name", "probe-app-xq.exe")], grace=0.0, dry_run=False
    )
    report = guard.check_process(make_info(pid=4_242_042, name="probe-app-xq.exe"))

    assert calls == []
    assert report["applied"] == ["skip"]
    assert guard.stats()["killed"] == 0


def test_guard_nie_rzuca_na_uszkodzonym_wejsciu():
    guard = make_guard(grace=0.0)
    empty = guard.check_process(None)  # type: ignore[arg-type]
    assert empty["ok"] is True
    assert empty["warnings"]

    # Bledny typ nie moze wyciec wyjatkiem - laduje w raporcie jako blad.
    bad = guard.check_process("nie-proces")  # type: ignore[arg-type]
    assert bad["ok"] is False
    assert bad["errors"]


# ----------------------------------------------------------------- LaunchWatcher


def test_launchwatcher_start_stop_dry_run():
    guard = make_guard(grace=0.0)
    watcher = LW.LaunchWatcher(guard, interval=0.05)
    assert watcher.backend in ("wmi", "poll")

    started = watcher.start()
    assert started["ok"] is True
    assert any(str(item).startswith("launchwatch:start") for item in started["applied"])
    assert started["backend"] in ("wmi", "poll")
    assert watcher.backend in ("wmi", "poll")

    time.sleep(0.15)
    stopped = watcher.stop()
    assert stopped["ok"] is True
    assert stopped["backend"] in ("wmi", "poll")
    assert isinstance(stopped["warnings"], list)
    assert watcher.stop()["warnings"]


def test_launchwatcher_przekazuje_nowy_proces_do_guardu():
    events = []
    guard = make_guard(block=[P.MatchRule("name", "chrome.exe")], grace=0.0)
    watcher = LW.LaunchWatcher(
        guard, interval=0.05, on_event=lambda event, payload: events.append((event, payload))
    )
    watcher._dispatch(make_info(pid=4_242_030, name="chrome.exe"))  # noqa: SLF001 - brak publicznego API

    assert events and events[0][0] == "process_start"
    assert events[0][1]["pid"] == 4_242_030
    assert events[0][1]["name"] == "chrome.exe"
    assert guard.stats()["blocked"] == 1


def test_launchwatcher_pomija_systemowe_i_wlasne():
    events = []
    guard = make_guard(grace=0.0)
    watcher = LW.LaunchWatcher(guard, interval=0.05, on_event=lambda event, payload: events.append(event))
    watcher._dispatch(make_info(pid=4_242_031, name="svchost.exe"))  # noqa: SLF001
    watcher._dispatch(make_info(pid=os.getpid(), name="chrome.exe"))  # noqa: SLF001

    assert events == []
    assert guard.stats()["blocked"] == 0
