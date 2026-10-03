"""Testy dziedziczenia uprawnien po przodkach (allow_parents) i odpornosci na psutil.

Wiekszosc testow jest hermetyczna (atrapa `focuslock.block.processes.psutil`),
ale sa tez dwa testy NA REALNYM psutil - to one lapia blad, ktorego atrapy nie
widza: `process_iter(attrs=["ppid"])` wola wewnetrznie ppid_map() per proces
(O(n^2)) i zawiesza watek guardu, przez co stats() zwraca same zera.

Test na realnych procesach dziala w dry_run, wiec nic nie jest usypiane/zabijane.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

import psutil as real_psutil

from focuslock.block import processes as P

A = 1000  # dozwolony przodek (np. launcher.exe)
B = 1001  # dziecko A (helper.exe)
C = 1002  # wnuk A (child.exe)
OTHER = 2000  # proces spoza drzewa


class FakeProc:
    def __init__(self, pid, ppid, name, exe="", create_time=0.0):
        self.pid = int(pid)
        self._ppid = int(ppid)
        self._name = name
        self._exe = exe
        self._create = create_time

    @property
    def info(self):
        return {
            "pid": self.pid,
            "ppid": self._ppid,
            "name": self._name,
            "exe": self._exe,
            "create_time": self._create,
        }

    def name(self):
        return self._name

    def ppid(self):
        return self._ppid

    def exe(self):
        return self._exe

    def create_time(self):
        return self._create

    def children(self, recursive=False):
        return []

    def kill(self):
        raise AssertionError("dry_run nie moze zabijac procesow")

    def suspend(self):
        raise AssertionError("dry_run nie moze usypiac procesow")

    def resume(self):
        return None


class FakeNoSuchProcess(Exception):
    pass


class FakePlatform:
    """Atrapa psutil._psplatform z jednym, zbiorczym ppid_map()."""

    def __init__(self, owner):
        self._owner = owner

    def ppid_map(self):
        self._owner.ppid_map_calls += 1
        return {pid: proc._ppid for pid, proc in self._owner.procs.items()}


class FakePsutil:
    NoSuchProcess = FakeNoSuchProcess
    AccessDenied = FakeNoSuchProcess
    ZombieProcess = FakeNoSuchProcess

    def __init__(self, procs, hidden=()):
        self.procs = {proc.pid: proc for proc in procs}
        self.hidden = {int(pid) for pid in hidden}
        self.scans = 0
        self.ppid_map_calls = 0
        self._psplatform = FakePlatform(self)

    def process_iter(self, attrs=None):
        self.scans += 1
        if "ppid" in list(attrs or []):
            # Dokladnie ten anty-wzorzec powodowal zawieszenie watku guardu.
            raise AssertionError("process_iter(attrs z 'ppid') jest zabronione (O(n^2))")
        return [proc for pid, proc in self.procs.items() if pid not in self.hidden]

    def Process(self, pid):
        try:
            return self.procs[int(pid)]
        except (KeyError, TypeError, ValueError):
            raise FakeNoSuchProcess(f"pid={pid}")


def chain_psutil() -> FakePsutil:
    return FakePsutil(
        [
            FakeProc(1, 0, "explorer.exe"),  # przodek korzenia; systemowy = pomijany
            FakeProc(A, 1, "launcher.exe"),
            FakeProc(B, A, "helper.exe"),
            FakeProc(C, B, "child.exe"),
            FakeProc(OTHER, 1, "other.exe"),
        ]
    )


def make_info(pid, name):
    return P.ProcessInfo(pid=pid, name=name, exe=f"C:\\apps\\{name}", create_time=0.0)


def make_guard(monkeypatch, procs=None, **kwargs):
    fake = procs if procs is not None else chain_psutil()
    monkeypatch.setattr(P, "psutil", fake)
    params = {"allow": [], "block": [], "interval": 0.05, "dry_run": True, "grace": 0.0}
    params.update(kwargs)
    guard = P.ProcessGuard(**params)
    return guard, fake


# --------------------------------------------------------------- lancuch A -> B -> C


def test_dziecko_i_wnuk_dozwolone_przez_przodka(monkeypatch):
    events = []
    guard, _ = make_guard(
        monkeypatch,
        allow_parents=["launcher.exe"],
        on_event=lambda event, payload: events.append((event, payload)),
    )

    guard.check_process(make_info(B, "helper.exe"))
    guard.check_process(make_info(C, "child.exe"))

    stats = guard.stats()
    assert stats["blocked"] == 0
    assert stats["suspended"] == 0
    assert stats["allowed_by_parent"] == 2
    assert stats["deferred"] == 0
    assert [event for event, _ in events] == ["allowed_child", "allowed_child"]
    # zwracamy przodka, ktory faktycznie pasuje do wzorca
    assert events[0][1] == {"pid": B, "name": "helper.exe", "parent": "launcher.exe"}
    assert events[1][1] == {"pid": C, "name": "child.exe", "parent": "launcher.exe"}


def test_proces_spoza_drzewa_nadal_blokowany(monkeypatch):
    events = []
    guard, _ = make_guard(
        monkeypatch,
        allow_parents=["launcher.exe"],
        on_event=lambda event, payload: events.append((event, payload)),
    )

    report = guard.check_process(make_info(OTHER, "other.exe"))

    assert report["applied"] == ["blocked:other.exe:suspend"]
    stats = guard.stats()
    assert stats["blocked"] == 1
    assert stats["allowed_by_parent"] == 0
    assert [event for event, _ in events] == ["blocked_app"]


def test_przodek_bez_wpisu_w_skanie_ale_zywy(monkeypatch):
    """Rodzic B nie trafil do skanu (wyscig), ale zyje - nazwe doczytujemy z psutil."""
    procs = [
        FakeProc(A, 1, "launcher.exe"),
        FakeProc(B, A, "helper.exe"),
        FakeProc(C, B, "child.exe"),
    ]
    fake = FakePsutil(procs, hidden=[B])  # B istnieje, ale nie ma go w process_iter
    guard, fake = make_guard(monkeypatch, fake, allow_parents=["launcher.exe"])

    report = guard.check_process(make_info(C, "child.exe"))

    assert report["applied"] == ["skipped:allowed-parent"]
    assert guard.stats()["allowed_by_parent"] == 1
    assert guard.stats()["blocked"] == 0


# ----------------------------------------------------------------- limit poziomow


def test_limit_pieciu_poziomow(monkeypatch):
    procs = [FakeProc(A, 1, "launcher.exe")]
    for depth in range(1, 8):
        procs.append(FakeProc(A + depth, A + depth - 1, f"lvl{depth}.exe"))
    guard, _ = make_guard(monkeypatch, FakePsutil(procs), allow_parents=["launcher.exe"])

    guard.check_process(make_info(A + 5, "lvl5.exe"))
    assert guard.stats()["allowed_by_parent"] == 1

    guard.check_process(make_info(A + 6, "lvl6.exe"))
    assert guard.stats()["allowed_by_parent"] == 1
    assert guard.stats()["blocked"] == 1

    guard.check_process(make_info(A + 7, "lvl7.exe"))
    assert guard.stats()["blocked"] == 2


# --------------------------------------------------------------------- wildcardy


@pytest.mark.parametrize(
    "pattern, pid, expected_parent",
    [
        ("launcher.exe", B, "launcher.exe"),
        ("LAUNCHER.EXE", B, "launcher.exe"),
        ("launch*", B, "launcher.exe"),
        ("launch??.exe", B, "launcher.exe"),
        ("*her.exe", B, "launcher.exe"),
        ("helper.exe", C, "helper.exe"),
    ],
)
def test_wildcardy_i_wielkosc_liter(monkeypatch, pattern, pid, expected_parent):
    guard, _ = make_guard(monkeypatch, allow_parents=[pattern])
    guard.check_process(make_info(pid, "x.exe"))
    stats = guard.stats()
    assert stats["allowed_by_parent"] == 1
    assert stats["blocked"] == 0


def test_allow_parents_pusty_nie_zmienia_zachowania(monkeypatch):
    guard, _ = make_guard(monkeypatch, allow_parents=[])
    guard.check_process(make_info(B, "helper.exe"))
    guard.check_process(make_info(C, "child.exe"))
    stats = guard.stats()
    assert stats["allowed_by_parent"] == 0
    assert stats["blocked"] == 2


def test_allow_parents_nie_dotyczy_systemowych(monkeypatch):
    guard, _ = make_guard(monkeypatch, allow_parents=["explorer.exe"])
    guard.check_process(make_info(os.getpid(), "chrome.exe"))
    guard.check_process(make_info(3000, "svchost.exe"))
    stats = guard.stats()
    assert stats["blocked"] == 0
    assert stats["allowed_by_parent"] == 0


# ------------------------------------------------------------------- przypadki brzegowe


def test_brak_ppid_i_martwy_przodek_nie_wywalaja(monkeypatch):
    procs = FakePsutil(
        [
            FakeProc(3100, 0, "root.exe"),  # brak rodzica (ppid=0)
            FakeProc(3101, 999_999_999, "orphan.exe"),  # rodzic nie istnieje
            FakeProc(3102, 3102, "selfloop.exe"),  # ppid wskazuje na siebie
            FakeProc(3103, 3100, "child-of-root.exe"),
        ]
    )
    guard, _ = make_guard(monkeypatch, procs, allow_parents=["launcher.exe"])

    for pid, name in ((3100, "root.exe"), (3101, "orphan.exe"), (3102, "selfloop.exe")):
        report = guard.check_process(make_info(pid, name))
        assert report["ok"] is True
        assert report["errors"] == []

    stats = guard.stats()
    assert stats["allowed_by_parent"] == 0
    assert stats["blocked"] == 3
    assert stats["deferred"] == 0


def test_dry_run_nie_wykonuje_zadnej_akcji(monkeypatch):
    guard, _ = make_guard(monkeypatch, allow_parents=["launcher.exe"])
    guard.check_process(make_info(B, "helper.exe"))
    guard.check_process(make_info(OTHER, "other.exe"))
    stats = guard.stats()
    assert stats["blocked"] == 1
    assert stats["suspended"] == 0
    assert stats["killed"] == 0


# ------------------------------------------------------------------------- update


def test_update_wlacza_i_wylacza_allow_parents(monkeypatch):
    guard, _ = make_guard(monkeypatch)

    disabled = guard.check_process(make_info(B, "helper.exe"))
    assert disabled["applied"] == ["blocked:helper.exe:suspend"]
    assert guard.stats()["blocked"] == 1

    report = guard.update(allow_parents=["launcher.exe"])
    assert report["ok"] is True
    assert "allow_parents:1" in report["applied"]

    enabled = guard.check_process(make_info(C, "child.exe"))
    assert enabled["applied"] == ["skipped:allowed-parent"]
    assert guard.stats()["allowed_by_parent"] == 1

    guard.update(allow_parents=[])
    after = guard.check_process(make_info(3001, "child2.exe"))
    assert after["applied"] == ["blocked:child2.exe:suspend"]
    assert guard.stats()["allowed_by_parent"] == 1


def test_update_allow_parents_przyjmuje_rozne_typy(monkeypatch):
    guard, _ = make_guard(monkeypatch)
    assert guard.update(allow_parents="launcher.exe")["ok"] is True
    assert guard.update(allow_parents={"value": "launcher.exe"})["applied"] == ["allow_parents:1"]
    assert guard.update(allow_parents=[P.MatchRule("name", "launcher.exe")])["applied"] == ["allow_parents:1"]
    assert guard.update(allow_parents=["", None, 42])["applied"] == ["allow_parents:0"]


def test_coerce_parents_pomija_smieci():
    assert P.coerce_parents(None) == []
    assert P.coerce_parents("x.exe") == ["x.exe"]
    assert P.coerce_parents([{"name": "y.exe"}, "", None]) == ["y.exe"]
    assert P.coerce_parents(P.MatchRule("name", "z.exe")) == ["z.exe"]
    assert P.coerce_parents(42) == []


# ------------------------------------- regresja P1: ppid NIE moze isc przez process_iter


def test_skan_nie_prosi_psutil_o_ppid(monkeypatch):
    """Gdyby _scan_processes znow zadalo 'ppid', atrapa rzuca AssertionError."""
    guard, fake = make_guard(monkeypatch, allow_parents=["launcher.exe"])
    guard._tick(1000.0)  # noqa: SLF001
    assert fake.scans == 1
    assert fake.ppid_map_calls == 1  # jedno zbiorcze ppid_map na runde


def test_ppid_z_cache_miedzy_iteracjami(monkeypatch):
    guard, fake = make_guard(monkeypatch, allow_parents=["launcher.exe"])
    guard._tick(1000.0)  # noqa: SLF001
    assert fake.ppid_map_calls == 1

    fake.ppid_map_calls = 0
    fake.scans = 0
    guard._tick(1001.0)  # noqa: SLF001
    assert fake.scans == 1
    assert fake.ppid_map_calls == 0  # ppid procesu sie nie zmienia - cache wystarcza


def test_brak_allow_parents_nie_pyta_o_ppid(monkeypatch):
    guard, fake = make_guard(monkeypatch)
    guard._tick(1000.0)  # noqa: SLF001
    assert fake.scans == 1
    assert fake.ppid_map_calls == 0
    assert guard.stats()["allowed_by_parent"] == 0


def test_300_procesow_jedno_ppid_map_i_bez_zawieszenia(monkeypatch):
    procs = [FakeProc(1, 0, "explorer.exe"), FakeProc(5000, 1, "launcher.exe")]
    for i in range(1, 300):
        procs.append(FakeProc(5000 + i, 5000 + i - 1, f"p{i}.exe"))
    guard, fake = make_guard(monkeypatch, FakePsutil(procs), allow_parents=["launcher.exe"])

    started = time.perf_counter()
    guard._tick(1000.0)  # noqa: SLF001
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"iteracja 300 procesow trwala {elapsed:.2f} s"
    assert fake.ppid_map_calls == 1
    stats = guard.stats()
    # dozwolone sa tylko procesy w zasiegu PARENT_DEPTH od launcher.exe
    assert stats["allowed_by_parent"] == P.PARENT_DEPTH
    assert stats["blocked"] == 300 - P.PARENT_DEPTH
    assert stats["overruns"] == 0


def test_twardy_limit_iteracji_przerywa_prace(monkeypatch):
    guard, fake = make_guard(monkeypatch, allow_parents=["launcher.exe"])
    monkeypatch.setattr(P, "_TICK_HARD_LIMIT", 0.0)

    guard._tick(1000.0)  # noqa: SLF001

    stats = guard.stats()
    assert stats["overruns"] >= 1
    assert stats["blocked"] == 0
    assert "Iteracja przerwana" in stats["last_error"]


def test_budzet_przodkow_odklada_decyzje_zamiast_blokowac(monkeypatch):
    guard, _ = make_guard(monkeypatch, allow_parents=["launcher.exe"])
    guard._begin_parent_round([])  # noqa: SLF001 - czyscimy mape na runde
    past = time.monotonic() - 1.0

    status = guard._consider(  # noqa: SLF001
        make_info(B, "helper.exe"),
        now=1000.0,
        own_pids=set(),
        allow=(),
        block=(),
        action="suspend",
        dry=True,
        allow_parents=["launcher.exe"],
        deadline=past,
    )

    stats = guard.stats()
    assert status == "deferred:parent-budget"
    assert stats["deferred"] == 1
    assert stats["blocked"] == 0
    assert stats["allowed_by_parent"] == 0


# ------------------------------------------------- realny psutil (bez atrap/mockow)


def test_realny_psutil_z_allow_parents_nie_wisi_na_ppid():
    """Dokladnie ten blad: przy allow_parents stats() zwracalo same zera."""
    P.list_processes()  # rozgrzewka psutil (pierwsze wywolanie bywa wolne)
    guard = P.ProcessGuard(
        [P.MatchRule("name", "nie-ma-takiego-procesu-xyz.exe")],
        [],
        action="suspend",
        interval=0.1,
        dry_run=True,
        grace=0.5,
        allow_parents=["tez-nie-ma-takiego.exe"],
    )
    guard.start()
    try:
        deadline = time.time() + 15
        while time.time() < deadline and guard.stats()["blocked"] == 0:
            time.sleep(0.2)
        stats = guard.stats()
    finally:
        guard.stop()

    assert stats["running"] is True
    assert stats["skipped"] > 0, stats  # systemowe procesy sa pomijane
    assert stats["blocked"] > 0, stats  # cos jest wykrywane, a nie same zera
    assert stats["overruns"] == 0
    assert stats["errors"] == 0


def _find_pid(name, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for proc in real_psutil.process_iter(["pid", "name"]):
            try:
                if (proc.info["name"] or "").lower() == name.lower():
                    return proc.info["pid"]
            except Exception:
                continue
        time.sleep(0.1)
    return None


def _kill(pid):
    if not pid:
        return
    try:
        real_psutil.Process(pid).kill()
    except Exception:
        pass


def test_realne_dziecko_dozwolone_przez_przodka(tmp_path):
    """Realny lancuch cmd -> ping (obce nazwy), dry_run: zadnych akcji na systemie."""
    parent = tmp_path / f"probe-par-{os.getpid()}.exe"
    child = tmp_path / f"probe-chi-{os.getpid()}.exe"
    shutil.copy2(r"C:\Windows\System32\cmd.exe", parent)
    shutil.copy2(r"C:\Windows\System32\ping.exe", child)
    devnull = subprocess.DEVNULL
    subprocess.run(
        ["cmd", "/c", "start", "", "/min", str(parent), "/c", str(child), "-n", "300", "127.0.0.1"],
        check=False,
        stdin=devnull,
        stdout=devnull,
        stderr=devnull,
    )

    parent_pid = _find_pid(parent.name)
    child_pid = _find_pid(child.name)
    guard = P.ProcessGuard(
        [P.MatchRule("name", "nie-ma-takiego-procesu-xyz.exe")],
        [],
        action="suspend",
        interval=0.2,
        dry_run=True,
        grace=0.0,
        allow_parents=[parent.name],
    )
    try:
        assert child_pid, "nie udalo sie uruchomic procesu-kopii"
        guard.start()
        deadline = time.time() + 15
        while time.time() < deadline and guard.stats()["allowed_by_parent"] == 0:
            time.sleep(0.2)
        stats = guard.stats()
        assert stats["allowed_by_parent"] >= 1, stats
        assert stats["overruns"] == 0
        assert stats["errors"] == 0
    finally:
        guard.stop()
        _kill(child_pid)
        _kill(parent_pid)


# ----------------------------------------------------- zgodnosc z istniejacym API


def test_allow_nadal_wygrywa_z_brakiem_list(monkeypatch):
    guard, _ = make_guard(monkeypatch, allow=[P.MatchRule("name", "helper.exe")])
    guard.check_process(make_info(B, "helper.exe"))
    stats = guard.stats()
    assert stats["allowed"] == 1
    assert stats["blocked"] == 0


def test_refleksja_helpera_widzi_allow_parents():
    import inspect

    params = inspect.signature(P.ProcessGuard).parameters
    assert "allow_parents" in params
    assert params["allow_parents"].default == ()
