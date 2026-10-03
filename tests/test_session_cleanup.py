"""Sprzatanie po sesji: wykrywanie i cofanie pozostalosci po padnietym helperze.

Scenariusz z zycia: helper z UAC nie dziala (konto bez uprawnien administratora),
wiec po sesji zostaja reguly zapory `CiszaBlock-*`, proxy w HKCU, sekcja Cisza
w pliku hosts, ukryty pasek zadan / ikony pulpitu albo hooki klawiatury.

Te testy NIGDY nie zmieniaja systemu: wszystkie moduly systemowe sa zamockowane,
a dane trafiaja wylacznie do `CISZA_DATA_DIR` (tmp_path).
"""
from __future__ import annotations

import json

import pytest

from focuslock import paths, recovery


@pytest.fixture()
def dane(monkeypatch, tmp_path):
    """Izolowany katalog danych (bez wplywu na prawdziwe %LOCALAPPDATA%\\Cisza)."""
    target = tmp_path / "dane"
    monkeypatch.setenv("CISZA_DATA_DIR", str(target))
    return target


@pytest.fixture(autouse=True)
def bez_admina(monkeypatch):
    """Domyslnie udajemy konto BEZ uprawnien administratora (jak u uzytkownika).

    Dzieki temu wynik testu nie zalezy od tego, czy pytest uruchomiono z UAC.
    """
    monkeypatch.setattr(recovery, "_is_admin", lambda: False)


# --------------------------------------------------------------------- atrapy
class _Calls(list):
    """Lista wykonanych operacji (do sprawdzenia, co sprzatanie naprawde zrobilo)."""

    def names(self) -> list[str]:
        return [str(item[0]) for item in self]


def _fake_firewall(monkeypatch, *, rules=3, remove_ok=False, remove_calls=None):
    from focuslock.block import firewall

    state = {"rules": list(range(1, rules + 1))}
    remove_calls = _Calls() if remove_calls is None else remove_calls

    def is_active(*, profile=firewall.DEFAULT_PROFILE):
        return bool(state["rules"])

    def active_rule_names(*, profile=firewall.DEFAULT_PROFILE):
        return [f"CiszaBlock-{index}" for index in state["rules"]]

    def quick_rule_names(*, profile=firewall.DEFAULT_PROFILE):
        return [f"CiszaBlock-{index}" for index in state["rules"]]

    def remove_blocks(*, profile=firewall.DEFAULT_PROFILE, dry_run=False):
        remove_calls.append(("firewall.remove_blocks", dry_run))
        if remove_ok:
            state["rules"] = []
            return {"ok": True, "applied": ["usunieto regule CiszaBlock-1"], "warnings": [], "errors": []}
        return {
            "ok": False,
            "applied": [],
            "warnings": [],
            "errors": ["nie mozna usunac reguly CiszaBlock-1: brak uprawnien administratora (UAC)"],
        }

    monkeypatch.setattr(firewall, "is_active", is_active)
    monkeypatch.setattr(firewall, "active_rule_names", active_rule_names)
    monkeypatch.setattr(firewall, "quick_rule_names", quick_rule_names)
    monkeypatch.setattr(firewall, "remove_blocks", remove_blocks)
    state["remove_calls"] = remove_calls
    return state


def _fake_proxy(monkeypatch, *, enabled=True, calls=None):
    from focuslock.block import networklock

    calls = _Calls() if calls is None else calls
    registry = {
        "ProxyEnable": 1 if enabled else 0,
        "ProxyServer": "127.0.0.1:8765" if enabled else "",
        "ProxyOverride": "localhost;127.*;<local>",
        "AutoConfigURL": None,
    }

    def _read():
        return dict(registry)

    def _write(values):
        for name, value in values.items():
            if value is None:
                registry.pop(name, None)
            else:
                registry[name] = value
        calls.append(("proxy.write", dict(values)))
        return [f"rejestr: {name}={value}" for name, value in values.items()]

    monkeypatch.setattr(networklock, "_read_proxy_settings", _read)
    monkeypatch.setattr(networklock, "_write_proxy_settings", _write)
    monkeypatch.setattr(networklock, "_notify_settings_change", lambda: None)
    return {"registry": registry, "calls": calls}


def _fake_hosts(monkeypatch, *, blocked=True, unblock_ok=True, calls=None):
    from focuslock.block import hosts

    calls = _Calls() if calls is None else calls
    state = {"blocked": bool(blocked)}

    def is_blocked(*, hosts_path=None):
        return state["blocked"]

    def unblock(*, hosts_path=None, dry_run=False):
        calls.append(("hosts.unblock", dry_run))
        if unblock_ok:
            state["blocked"] = False
            return {"ok": True, "applied": ["usunieto sekcje Cisza"], "warnings": [], "errors": []}
        return {
            "ok": False,
            "applied": [],
            "warnings": [],
            "errors": ["brak uprawnien administratora do zapisu hosts"],
        }

    monkeypatch.setattr(hosts, "is_blocked", is_blocked)
    monkeypatch.setattr(hosts, "unblock", unblock)
    return {"state": state, "calls": calls}


def _fake_taskbar(monkeypatch, *, hidden=True, show_ok=True, calls=None):
    from focuslock.shell import taskbar

    calls = _Calls() if calls is None else calls
    state = {"hidden": bool(hidden)}

    def is_hidden():
        return state["hidden"]

    def show(*, dry_run=False):
        calls.append(("taskbar.show", dry_run))
        if show_ok:
            state["hidden"] = False
            return {"ok": True, "applied": ["taskbar.show:0x1"], "warnings": [], "errors": []}
        return {"ok": False, "applied": [], "warnings": [], "errors": ["pasek 0x1 nie wrocil na ekran"]}

    monkeypatch.setattr(taskbar, "is_hidden", is_hidden)
    monkeypatch.setattr(taskbar, "show", show)
    return {"state": state, "calls": calls}


def _fake_desktop(monkeypatch, *, icons_hidden=True, black=True, restore_ok=True, backup=True, calls=None):
    from focuslock.shell import desktop

    calls = _Calls() if calls is None else calls
    current = {"wallpaper": "" if black else r"C:\tapeta.bmp", "icons_hidden": bool(icons_hidden)}

    def capture():
        return desktop.DesktopState(wallpaper=current["wallpaper"], icons_hidden=current["icons_hidden"])

    def load_state():
        if not backup:
            return None
        return desktop.DesktopState(wallpaper=r"C:\tapeta.bmp", icons_hidden=False)

    def restore(state, *, dry_run=False):
        calls.append(("desktop.restore", dry_run))
        if restore_ok:
            current["wallpaper"] = str(getattr(state, "wallpaper", "") or "")
            current["icons_hidden"] = bool(getattr(state, "icons_hidden", False))
            return {"ok": True, "applied": ["desktop.wallpaper.restore"], "warnings": [], "errors": []}
        return {"ok": False, "applied": [], "warnings": [], "errors": ["rejestr tapety: odmowa dostepu"]}

    monkeypatch.setattr(desktop, "capture", capture)
    monkeypatch.setattr(desktop, "load_state", load_state)
    monkeypatch.setattr(desktop, "restore", restore)
    return {"current": current, "calls": calls}


def _fake_hotkeys(monkeypatch, *, ok=True, calls=None):
    from focuslock.shell import hotkeys

    calls = _Calls() if calls is None else calls
    monkeypatch.setattr(hotkeys, "KEYBOARD_OK", True, raising=False)

    def force_uninstall():
        calls.append(("hotkeys.force_uninstall",))
        if ok:
            return {"ok": True, "applied": ["hotkeys.force_uninstall:unhook_all"], "warnings": [], "errors": []}
        return {"ok": False, "applied": [], "warnings": [], "errors": ["unhook_all: blad"]}

    monkeypatch.setattr(hotkeys, "force_uninstall", force_uninstall)
    return {"calls": calls}


def _mock_all(monkeypatch, **kwargs):
    """Podstawia wszystkie moduly systemowe uzywane przez cleanup_leftovers."""
    return {
        "firewall": _fake_firewall(monkeypatch, rules=kwargs.get("rules", 3), remove_ok=kwargs.get("remove_ok", False)),
        "proxy": _fake_proxy(monkeypatch, enabled=kwargs.get("enabled", True)),
        "hosts": _fake_hosts(monkeypatch, blocked=kwargs.get("blocked", True), unblock_ok=kwargs.get("unblock_ok", True)),
        "taskbar": _fake_taskbar(monkeypatch, hidden=kwargs.get("hidden", True), show_ok=kwargs.get("show_ok", True)),
        "desktop": _fake_desktop(
            monkeypatch,
            icons_hidden=kwargs.get("icons_hidden", True),
            black=kwargs.get("black", True),
            restore_ok=kwargs.get("restore_ok", True),
            backup=kwargs.get("backup", True),
        ),
        "hotkeys": _fake_hotkeys(monkeypatch, ok=kwargs.get("hotkey_ok", True)),
    }


# ------------------------------------------------- 1. wykrywanie i cofanie zmian
def test_cleanup_cofa_proxy_hosts_pasek_i_pulpit(monkeypatch, dane):
    mocks = _mock_all(monkeypatch, rules=0, remove_ok=True)

    report = recovery.cleanup_leftovers()

    assert report["leftovers"] == []
    assert report["ok"] is True
    assert mocks["proxy"]["calls"].names() == ["proxy.write"]
    assert mocks["hosts"]["calls"].names() == ["hosts.unblock"]
    assert mocks["taskbar"]["calls"].names() == ["taskbar.show"]
    assert mocks["desktop"]["calls"].names() == ["desktop.restore"]
    assert mocks["hotkeys"]["calls"].names() == ["hotkeys.force_uninstall"]
    assert report["checked"] == {
        "firewall": False,
        "firewall_rules": 0,
        "proxy": True,
        "hosts": True,
        "taskbar": True,
        "desktop": True,
        "hotkeys": True,
    }
    assert mocks["proxy"]["registry"]["ProxyEnable"] == 0
    assert mocks["hosts"]["state"]["blocked"] is False
    assert mocks["taskbar"]["state"]["hidden"] is False
    assert mocks["desktop"]["current"]["icons_hidden"] is False
    assert mocks["desktop"]["current"]["wallpaper"] == r"C:\tapeta.bmp"
    assert not paths.leftovers_path().exists()


def test_cleanup_nie_dotyka_niczego_gdy_system_jest_czysty(monkeypatch, dane):
    mocks = _mock_all(
        monkeypatch,
        rules=0,
        enabled=False,
        blocked=False,
        hidden=False,
        icons_hidden=False,
        black=False,
    )

    report = recovery.cleanup_leftovers()

    assert report["ok"] is True
    assert report["leftovers"] == []
    assert mocks["proxy"]["calls"] == []
    assert mocks["hosts"]["calls"] == []
    assert mocks["taskbar"]["calls"] == []
    assert mocks["desktop"]["calls"] == []
    assert report["checked"]["proxy"] is False
    assert report["checked"]["desktop"] is False
    # Hooki klawiatury zdejmujemy zawsze (idempotentne) - inaczej zostalyby po crashu.
    assert mocks["hotkeys"]["calls"].names() == ["hotkeys.force_uninstall"]


# ------------------------------------------------------- 2. brak uprawnien admina
def test_brak_admina_do_zapory_trafia_do_leftovers(monkeypatch, dane):
    mocks = _mock_all(monkeypatch, rules=14, remove_ok=False)

    report = recovery.cleanup_leftovers()

    assert report["ok"] is False
    assert report["leftovers"] == ["brak uprawnien administratora: reguly zapory CiszaBlock (14)"]
    assert report["checked"]["firewall"] is True
    assert report["checked"]["firewall_rules"] == 14
    # Bez admina nie wysylamy kilkunastu polecen netsh, ktore i tak zawioda.
    assert mocks["firewall"]["remove_calls"] == []
    # Reszta krokow musi sie wykonac mimo bledu zapory.
    assert mocks["proxy"]["calls"].names() == ["proxy.write"]
    assert mocks["hosts"]["calls"].names() == ["hosts.unblock"]
    assert report["warnings"]
    # Reguly zostaly -> znacznik dla nastepnego startu.
    zapis = json.loads(paths.leftovers_path().read_text(encoding="utf-8"))
    assert zapis["leftovers"] == ["brak uprawnien administratora: reguly zapory CiszaBlock (14)"]
    assert zapis["ts"] > 0
    assert "napraw-internet.cmd" in zapis["instructions"]


def test_admin_probuje_usunac_reguly_zapory(monkeypatch, dane):
    monkeypatch.setattr(recovery, "_is_admin", lambda: True)
    mocks = _mock_all(monkeypatch, rules=3, remove_ok=True)

    report = recovery.cleanup_leftovers()

    assert mocks["firewall"]["remove_calls"].names() == ["firewall.remove_blocks"]
    assert report["leftovers"] == []
    assert report["ok"] is True
    assert mocks["firewall"]["rules"] == []


def test_czarna_tapeta_bez_kopii_nie_jest_ruszana(monkeypatch, dane):
    """Brak tapety bez sladu po Ciszy to nie pozostalosc - nie zmieniamy pulpitu."""
    mocks = _mock_all(monkeypatch, rules=0, remove_ok=True, icons_hidden=False, black=True, backup=False)

    report = recovery.cleanup_leftovers()

    assert report["checked"]["desktop"] is False
    assert mocks["desktop"]["calls"] == []
    assert report["leftovers"] == []
    assert report["ok"] is True


def test_brak_admina_do_hosts_trafia_do_leftovers(monkeypatch, dane):
    _mock_all(monkeypatch, rules=0, remove_ok=True, unblock_ok=False)

    report = recovery.cleanup_leftovers()

    assert report["ok"] is False
    assert "brak uprawnien administratora: sekcja CISZA-BEGIN/END w pliku hosts" in report["leftovers"]


def test_pasek_i_pulpit_bez_zmian_trafiaja_do_leftovers(monkeypatch, dane):
    _mock_all(monkeypatch, rules=0, remove_ok=True, show_ok=False, restore_ok=False)

    report = recovery.cleanup_leftovers()

    assert report["ok"] is False
    assert "pasek zadan pozostal ukryty" in report["leftovers"]
    assert "ikony pulpitu pozostaly ukryte" in report["leftovers"]


def test_cleanup_nie_rzuca_gdy_moduly_sie_wysypia(monkeypatch, dane):
    from focuslock.block import firewall, hosts, networklock
    from focuslock.shell import desktop, hotkeys, taskbar

    def _boom(*_args, **_kwargs):
        raise RuntimeError("awaria modulu")

    for module, names in (
        (firewall, ("is_active", "remove_blocks", "active_rule_names", "quick_rule_names")),
        (hosts, ("is_blocked", "unblock")),
        (taskbar, ("is_hidden", "show")),
        (desktop, ("capture", "load_state", "restore")),
        (hotkeys, ("force_uninstall",)),
        (networklock, ("_read_proxy_settings", "_write_proxy_settings", "_notify_settings_change")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, _boom)

    report = recovery.cleanup_leftovers()  # nie moze rzucic

    assert report["ok"] is False
    assert report["errors"]


# ------------------------------------------------------------------ 3. znacznik
def test_znacznik_powstaje_i_znika(monkeypatch, dane):
    _mock_all(monkeypatch, rules=2, remove_ok=False)

    recovery.cleanup_leftovers()
    assert paths.leftovers_path().exists()
    assert recovery.read_leftovers() is not None

    # Nastepny start: admin naprawil system, sprzatanie jest juz czyste.
    _mock_all(monkeypatch, rules=0, remove_ok=True)
    report = recovery.cleanup_leftovers()

    assert report["leftovers"] == []
    assert not paths.leftovers_path().exists()
    assert recovery.read_leftovers() is None


def test_znacznik_jest_czytany_przy_starcie(monkeypatch, dane):
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    recovery.save_leftovers(["brak uprawnien administratora: reguly zapory CiszaBlock (3)"])
    assert recovery.read_leftovers() is not None

    wywolania = []

    def _cleanup(*, dry_run=False):
        wywolania.append(dry_run)
        return {
            "ok": True,
            "checked": {},
            "leftovers": [],
            "applied": [],
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(recovery, "cleanup_leftovers", _cleanup)
    raport = app_module.startup_leftovers_report(dry_run=False)

    assert wywolania == [False]
    assert raport["ok"] is True


def test_startup_nie_ponawia_sprzatania_bez_znacznika(monkeypatch, dane):
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    def _cleanup(*, dry_run=False):
        raise AssertionError("bez znacznika nie ma czego ponawiac")

    monkeypatch.setattr(recovery, "cleanup_leftovers", _cleanup)
    assert app_module.startup_leftovers_report(dry_run=False) == {}


def test_komunikat_o_pozostalosciach_wymienia_skrypt_naprawczy(monkeypatch, dane):
    pytest.importorskip("PyQt6")
    from focuslock import app as app_module

    tekst = app_module.cleanup_incomplete_message(
        ["brak uprawnien administratora: reguly zapory CiszaBlock (3)"], ""
    )

    assert "CiszaBlock (3)" in tekst
    assert "napraw-internet.cmd" in tekst
    assert "administrator" in tekst.lower()


# ------------------------------------------------------------------ 4. dry-run
def test_dry_run_nic_nie_zmienia_i_nie_zapisuje_znacznika(monkeypatch, dane):
    mocks = _mock_all(monkeypatch, rules=5, remove_ok=False)

    report = recovery.cleanup_leftovers(dry_run=True)

    assert report["dry_run"] is True
    assert mocks["proxy"]["calls"] == []
    assert mocks["hosts"]["calls"] == []
    assert mocks["taskbar"]["calls"] == []
    assert mocks["desktop"]["calls"] == []
    assert mocks["hotkeys"]["calls"] == []
    assert report["leftovers"] == []
    assert not paths.leftovers_path().exists()
    assert mocks["proxy"]["registry"]["ProxyEnable"] == 1
    assert report["checked"]["firewall_rules"] == 5


def test_dry_run_nie_kasuje_istniejacego_znacznika(monkeypatch, dane):
    recovery.save_leftovers(["stare pozostalosci"])
    _mock_all(monkeypatch, rules=0, remove_ok=True)

    recovery.cleanup_leftovers(dry_run=True)

    assert paths.leftovers_path().exists()


# ------------------------------------------------------- 5. kontroler (koniec sesji)
class _OfflineBridge:
    """Atrapa mostu bez helpera (konto bez UAC) - kazde RPC byloby nieudane."""

    online = False
    last_error = "brak polaczenia"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, method: str, **_kwargs) -> dict:
        self.calls.append(method)
        return {"ok": False, "errors": ["helper niedostepny: brak polaczenia"]}

    def drain_events(self) -> list:
        return []

    def close(self) -> None:
        pass


def _controller(monkeypatch, tmp_path):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path / "dane"))
    monkeypatch.delenv("CISZA_DRY_RUN", raising=False)

    from focuslock.config import Settings
    from focuslock.controller import Controller
    from focuslock.store import Store

    settings = Settings()
    settings.env_overrides()
    store = Store(memory=True)
    zdarzenia: list[tuple[str, dict]] = []
    controller = Controller(
        store,
        settings,
        bridge=_OfflineBridge(),
        emit=lambda event, data: zdarzenia.append((event, dict(data or {}))),
        log=lambda *a: None,
    )
    return controller, store, zdarzenia


def test_release_emituje_cleanup_incomplete_gdy_zostaly_pozostalosci(monkeypatch, tmp_path):
    controller, store, zdarzenia = _controller(monkeypatch, tmp_path)
    monkeypatch.setattr(
        recovery,
        "cleanup_leftovers",
        lambda *, dry_run=False: {
            "ok": False,
            "checked": {"firewall": True},
            "leftovers": ["brak uprawnien administratora: reguly zapory CiszaBlock (4)"],
            "applied": [],
            "warnings": [],
            "errors": [],
        },
    )

    report = controller._release(7, reason="test")

    krok = next(item for item in report["steps"] if item["step"] == "cleanup")
    assert krok["result"]["leftovers"]
    event = next(data for name, data in zdarzenia if name == "cleanup_incomplete")
    assert event["leftovers"] == ["brak uprawnien administratora: reguly zapory CiszaBlock (4)"]
    assert "napraw-internet.cmd" in event["instructions"]
    zapisane = [row for row in store.events(limit=20) if row["kind"] == "CLEANUP_INCOMPLETE"]
    assert len(zapisane) == 1
    assert zapisane[0]["session_id"] == 7
    assert zapisane[0]["detail"]["leftovers"] == event["leftovers"]


def test_release_bez_pozostalosci_nie_straszy_uzytkownika(monkeypatch, tmp_path):
    controller, store, zdarzenia = _controller(monkeypatch, tmp_path)
    monkeypatch.setattr(
        recovery,
        "cleanup_leftovers",
        lambda *, dry_run=False: {
            "ok": True,
            "checked": {},
            "leftovers": [],
            "applied": ["zapora: reguly CiszaBlock usuniete"],
            "warnings": [],
            "errors": [],
        },
    )

    report = controller._release(7, reason="test")

    assert [name for name, _ in zdarzenia if name == "cleanup_incomplete"] == []
    assert [row for row in store.events(limit=20) if row["kind"] == "CLEANUP_INCOMPLETE"] == []
    krok = next(item for item in report["steps"] if item["step"] == "cleanup")
    assert krok["result"]["ok"] is True


def test_release_w_dry_run_nie_emituje_ostrzezenia(monkeypatch, tmp_path):
    controller, store, zdarzenia = _controller(monkeypatch, tmp_path)
    controller.settings.system.dry_run = True
    monkeypatch.setattr(
        recovery,
        "cleanup_leftovers",
        lambda *, dry_run=False: {
            "ok": False,
            "checked": {},
            "leftovers": ["brak uprawnien administratora: reguly zapory CiszaBlock (4)"],
            "applied": [],
            "warnings": [],
            "errors": [],
        },
    )

    controller._release(7, reason="test")

    assert [name for name, _ in zdarzenia if name == "cleanup_incomplete"] == []


def test_release_wywoluje_sprzatanie_z_dry_run_kontrolera(monkeypatch, tmp_path):
    controller, _store, _zdarzenia = _controller(monkeypatch, tmp_path)
    controller.settings.system.dry_run = True
    widziane: list[bool] = []

    def _cleanup(*, dry_run=False):
        widziane.append(bool(dry_run))
        return {"ok": True, "checked": {}, "leftovers": [], "applied": [], "warnings": [], "errors": []}

    monkeypatch.setattr(recovery, "cleanup_leftovers", _cleanup)
    controller._release(None, reason="test")

    assert widziane == [True]


def test_release_nie_wybucha_gdy_sprzatanie_zawiedzie(monkeypatch, tmp_path):
    controller, _store, _zdarzenia = _controller(monkeypatch, tmp_path)

    def _boom(*, dry_run=False):
        raise OSError("rejestr zablokowany")

    monkeypatch.setattr(recovery, "cleanup_leftovers", _boom)

    report = controller._release(3, reason="test")

    krok = next(item for item in report["steps"] if item["step"] == "cleanup")
    assert krok["result"]["ok"] is False
    assert krok["result"]["errors"]


# ------------------------------------------------------------- 6. sciezka znacznika
def test_leftovers_path_jest_w_katalogu_danych(dane):
    assert paths.leftovers_path() == paths.data_dir() / "leftovers.json"


def test_quick_rule_names_nie_rzuca():
    from focuslock.block import firewall

    names = firewall.quick_rule_names()

    assert names is None or isinstance(names, list)


def test_read_leftovers_ignoruje_uszkodzony_plik(monkeypatch, dane):
    paths.leftovers_path().write_text("{to nie jest json", encoding="utf-8")
    assert recovery.read_leftovers() is None


def test_instructions_wymieniaja_skrypt_i_konto_admina(dane):
    tekst = recovery.leftovers_instructions()

    assert "napraw-internet.cmd" in tekst
    assert "admin" in tekst
