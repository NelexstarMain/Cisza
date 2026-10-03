"""Testy katalogu aplikacji (bez realnego skanu dysku - uzywamy atrap)."""
from __future__ import annotations

from pathlib import Path

import pytest

from focuslock import appcatalog as ac


# ------------------------------------------------------------------ oczyszczanie
def test_clean_name_usuwa_smiec_i_spacje():
    assert ac.clean_name("  Visual   Studio Code - skrót ") == "Visual Studio Code"
    assert ac.clean_name("Notatnik  ") == "Notatnik"
    assert ac.clean_name("") == ""


@pytest.mark.parametrize(
    "name,exe",
    [
        ("Uninstall Anki", "uninstall-anki.exe"),
        ("7-Zip", "unins000.exe"),
        ("Visual C++ Redistributable", "vc_redist.x64.exe"),
        ("Documentation", "readme.txt"),
        ("Component Services", "mmc.exe"),
        ("Registry Editor", "regedit.exe"),
        ("Python 3.13", "python.exe"),
        ("Spotify Setup", "spotify setup.exe"),
        ("OneDrive", "onedrivesetup.exe"),
        ("Wacom Tablet", "remove.exe"),
    ],
)
def test_is_noise_odrzuca_instalatory_i_narzedzia_systemowe(name, exe):
    assert ac.is_noise(name, exe) is True


@pytest.mark.parametrize(
    "name,exe",
    [
        ("Google Chrome", "chrome.exe"),
        ("Visual Studio Code", "code.exe"),
        ("Anki", "anki.exe"),
        ("Kalkulator", "calculatorapp.exe"),
        ("Discord", "discord.exe"),
    ],
)
def test_is_noise_przepuszcza_zwykle_aplikacje(name, exe):
    assert ac.is_noise(name, exe) is False


def test_is_background_process_odrzuca_uslugi():
    assert ac.is_background_process("svchost.exe")
    assert ac.is_background_process("crashpad_handler.exe")
    assert ac.is_background_process("runtimebroker.exe")
    assert not ac.is_background_process("chrome.exe")


# --------------------------------------------------------------------- scalanie
def test_merge_preferuje_menu_start_i_uzupelnia_ikone(tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"MZ")
    lnk = tmp_path / "chrome.lnk"
    lnk.write_bytes(b"L")
    start = ac.CatalogApp("Google Chrome", "chrome.exe", str(exe), "startmenu", str(lnk))
    store = ac.CatalogApp("Google Chrome", "chrome.exe", "appid", "store", "")
    running = ac.CatalogApp("Chrome", "chrome.exe", str(exe), "running", str(exe))
    merged = ac.merge([store, running, start])
    assert len(merged) == 1
    assert merged[0].source == "startmenu"
    assert merged[0].icon_source == str(lnk)


def test_merge_uzupelnia_ikone_po_nazwie_dla_sklepu():
    store = ac.CatalogApp("Kalkulator", "calculatorapp.exe", "appid", "store", "")
    shortcut = ac.CatalogApp("Kalkulator", "calculatorapp.exe", "C:/calc.exe", "startmenu", "C:/calc.lnk")
    merged = ac.merge([store, shortcut])
    assert len(merged) == 1
    assert merged[0].icon_path == "C:/calc.lnk"


def test_merge_bez_duplikatow_i_sortowanie():
    apps = [
        ac.CatalogApp("Zebra", "zebra.exe", source="startmenu"),
        ac.CatalogApp("Anki", "anki.exe", source="startmenu"),
        ac.CatalogApp("Zebra", "zebra.exe", source="running"),
    ]
    merged = ac.merge(apps)
    assert [a.name for a in merged] == ["Anki", "Zebra"]


def test_merge_limit():
    apps = [ac.CatalogApp(f"App {i:03d}", f"app{i}.exe", source="startmenu") for i in range(50)]
    assert len(ac.merge(apps, limit=10)) == 10


# ----------------------------------------------------------------------- cache
def test_cache_zapis_i_odczyt(tmp_path, monkeypatch):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path))
    apps = [ac.CatalogApp("Anki", "anki.exe", "C:/anki.exe", "startmenu", "C:/anki.lnk")]
    ac.save_cache(apps)
    loaded = ac.load_cache()
    assert loaded is not None
    assert loaded[0].name == "Anki"
    assert loaded[0].icon_path == "C:/anki.lnk"


def test_cache_wygasa(tmp_path, monkeypatch):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path))
    ac.save_cache([ac.CatalogApp("Anki", "anki.exe", source="startmenu")])
    assert ac.load_cache(max_age=0) is not None
    assert ac.load_cache(max_age=-1) is None


def test_get_catalog_uzywa_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CISZA_DATA_DIR", str(tmp_path))
    ac.save_cache([ac.CatalogApp("Z cache", "cache.exe", source="startmenu")])
    calls = {"n": 0}

    def fake_scan(**kwargs):
        calls["n"] += 1
        return []

    monkeypatch.setattr(ac, "from_start_menu", fake_scan)
    monkeypatch.setattr(ac, "from_registry", fake_scan)
    monkeypatch.setattr(ac, "from_running", fake_scan)
    result = ac.get_catalog()
    assert [a.name for a in result] == ["Z cache"]
    assert calls["n"] == 0


# ------------------------------------------------------------------ wyszukiwanie
def test_search_po_nazwie_i_exe():
    apps = [
        ac.CatalogApp("Google Chrome", "chrome.exe", source="startmenu"),
        ac.CatalogApp("Kalkulator", "calculatorapp.exe", source="store"),
    ]
    assert [a.name for a in ac.search("chrom", apps)] == ["Google Chrome"]
    assert [a.name for a in ac.search("CALCULATOR", apps)] == ["Kalkulator"]
    assert ac.search("", apps) == apps
    assert ac.search("nieistnieje", apps) == []


def test_grouped_grupuje_po_literze():
    apps = [
        ac.CatalogApp("Anki", "anki.exe", source="startmenu"),
        ac.CatalogApp("Access", "msaccess.exe", source="startmenu"),
        ac.CatalogApp("Zebra", "zebra.exe", source="startmenu"),
    ]
    groups = ac.grouped(apps)
    assert sorted(groups) == ["A", "Z"]
    assert [a.name for a in groups["A"]] == ["Anki", "Access"]


# -------------------------------------------------------------------- opis/ikona
def test_to_dict_zawiera_ikone_i_etykiete(tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"MZ")
    lnk = tmp_path / "chrome.lnk"
    lnk.write_bytes(b"L")
    app = ac.CatalogApp("Google Chrome", "chrome.exe", str(exe), "startmenu", str(lnk))
    payload = app.to_dict()
    assert payload["icon_path"] == str(lnk)
    assert payload["label"] == "Google Chrome (chrome.exe)"
    assert payload["source"] == "startmenu"


def test_icon_source_spada_na_sciezke_exe(tmp_path):
    exe = tmp_path / "bez.exe"
    exe.write_bytes(b"MZ")
    app = ac.CatalogApp("Bez ikony", "bez.exe", str(exe), "registry", "")
    assert app.icon_source == str(exe)


def test_icon_source_pusty_dla_nieistniejacego_pliku():
    app = ac.CatalogApp("Sklep", "app.exe", "Microsoft.App_8wekyb3d8bbwe!App", "store", "")
    assert app.icon_source == ""


def test_prettify_process_name():
    assert ac.prettify_process_name("code.exe") == "Visual Studio Code"
    assert ac.prettify_process_name("chrome.exe") == "Google Chrome"
    assert ac.prettify_process_name("moja_aplikacja.exe") == "Moja Aplikacja"


def test_describe_liczy_zrodla():
    apps = [
        ac.CatalogApp("A", "a.exe", source="startmenu"),
        ac.CatalogApp("B", "b.exe", source="store"),
        ac.CatalogApp("C", "c.exe", source="store"),
    ]
    info = ac.describe(apps)
    assert info["count"] == 3
    assert info["sources"]["store"] == 2


def test_shortcut_fallback_znajduje_skroty(tmp_path, monkeypatch):
    menu = tmp_path / "Programs"
    menu.mkdir()
    (menu / "Anki.lnk").write_bytes(b"x")
    (menu / "Uninstall Anki.lnk").write_bytes(b"x")
    monkeypatch.setattr(ac, "start_menu_dirs", lambda: [menu])
    apps = ac._shortcuts_dir_scan()
    assert [a.name for a in apps] == ["Anki"]
    assert apps[0].icon_path.endswith("Anki.lnk")


def test_pids_with_windows_nie_rzuca():
    result = ac.pids_with_windows()
    assert isinstance(result, set)


def test_merge_nie_gubi_aplikacji_bez_exe():
    apps = [ac.CatalogApp("Bez procesu", "", source="startmenu", icon_path="C:/x.lnk")]
    merged = ac.merge(apps)
    assert merged[0].name == "Bez procesu"
    assert merged[0].icon_path == "C:/x.lnk"


def test_guess_exe_in_dir_wybiera_najwiekszy(tmp_path):
    (tmp_path / "mala.exe").write_bytes(b"a")
    (tmp_path / "duza.exe").write_bytes(b"a" * 100)
    (tmp_path / "uninstall.exe").write_bytes(b"a" * 500)
    chosen = ac._guess_exe_in_dir(tmp_path)
    assert chosen is not None
    assert Path(chosen).name == "duza.exe"
