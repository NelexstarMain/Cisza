"""Testy katalogu stron do wyboru w kreatorze sesji."""
from __future__ import annotations

import pytest

from focuslock import sitecatalog as sc


def test_catalog_nie_jest_pusty_i_ma_obie_strony():
    assert len(sc.all_sites()) > 60
    assert len(sc.for_kind(sc.STUDY)) > 30
    assert len(sc.for_kind(sc.BLOCKED)) > 20


def test_kazda_strona_ma_poprawny_host_i_kategorie():
    for site in sc.all_sites():
        assert site.host, f"brak hosta w {site.name}"
        assert " " not in site.host.strip(), f"host ze spacja: {site.host}"
        assert site.host == site.host.lower(), f"host nie jest malymi literami: {site.host}"
        assert site.category in sc.CATEGORIES, f"nieznana kategoria: {site.category}"
        assert site.kind in (sc.STUDY, sc.BLOCKED)


def test_brak_duplikatow_hostow():
    hosts = [sc.normalize(site.host) for site in sc.all_sites()]
    duplicates = {host for host in hosts if hosts.count(host) > 1}
    assert not duplicates, f"duplikaty: {duplicates}"


def test_kategorie_maja_etykiety_pl():
    for key, label in sc.CATEGORIES.items():
        assert label and label[0].isupper()


def test_by_category_z_filtrem_kind():
    study = sc.by_category(sc.STUDY)
    blocked = sc.by_category(sc.BLOCKED)
    assert "rozrywka" not in study
    assert "rozrywka" in blocked
    assert "nauka" in study


def test_normalize_usuwa_schemat_port_i_kropke():
    assert sc.normalize("https://WWW.Wikipedia.ORG/") == "www.wikipedia.org"
    assert sc.normalize("http://example.com:8080/sciezka") == "example.com"
    assert sc.normalize("  En.Wikipedia.org.  ") == "en.wikipedia.org"
    assert sc.normalize("") == ""


def test_find_po_hoscie_i_nazwie():
    assert sc.find("*.wikipedia.org").name.startswith("Wikipedia")
    assert sc.find("Wikipedia (PL)").host == "*.wikipedia.org"
    assert sc.find("nie-ma-takiej-strony.example") is None


def test_search_filtruje():
    results = sc.search("khan")
    assert any("Khan" in site.name for site in results)
    assert sc.search("khan", sc.BLOCKED) == []
    assert sc.search("") == list(sc.all_sites())


def test_hosts_from_names_rozpoznaje_nazwy_i_wlasne_wpisy():
    hosts = sc.hosts_from_names(["Wikipedia (PL)", "cke.gov.pl", "https://example.com/x", "cke.gov.pl"])
    assert hosts == ["*.wikipedia.org", "cke.gov.pl", "example.com"]


def test_hosts_from_names_bez_duplikatow():
    hosts = sc.hosts_from_names(["YouTube", "*.youtube.com", "youtu.be"])
    assert len(hosts) == len(set(hosts))


def test_default_study_hosts_sensowne():
    hosts = sc.default_study_hosts()
    assert "*.wikipedia.org" in hosts
    assert "cke.gov.pl" in hosts
    assert len(hosts) >= 8


def test_default_block_hosts_z_configu():
    from focuslock.config import DEFAULT_BLOCKLIST

    assert sc.default_block_hosts() == list(DEFAULT_BLOCKLIST)


def test_suggest_for_przedmiot():
    assert "pl.khanacademy.org" in sc.suggest_for("matematyka")
    assert "docs.python.org" in sc.suggest_for("informatyka")
    assert sc.suggest_for("nieistniejacy-przedmiot") == []
    assert sc.suggest_for("MATEMATYKA") == sc.suggest_for("matematyka")


def test_catalog_payload_struktura():
    payload = sc.catalog_payload(sc.STUDY)
    assert payload["ok"] is True
    assert payload["categories"]
    assert "summary" in payload and payload["summary"]["total"] > 0
    assert payload["defaults"]["study"]
    first = payload["categories"][0]
    assert {"key", "label", "sites"} <= set(first)
    site = first["sites"][0]
    assert {"name", "host", "category", "kind", "note", "category_label"} <= set(site)


def test_catalog_payload_dla_blokowanych():
    payload = sc.catalog_payload(sc.BLOCKED)
    kinds = {site["kind"] for category in payload["categories"] for site in category["sites"]}
    assert kinds == {sc.BLOCKED}


def test_describe_liczy_kategorie():
    info = sc.describe()
    assert info["total"] == len(sc.all_sites())
    assert info["study"] == len(sc.for_kind(sc.STUDY))
    assert info["blocked"] == len(sc.for_kind(sc.BLOCKED))
    assert sum(info["categories"].values()) == info["total"]


def test_to_dicts():
    dicts = sc.to_dicts(sc.for_kind(sc.STUDY)[:3])
    assert len(dicts) == 3
    assert all(isinstance(item["host"], str) for item in dicts)


@pytest.mark.parametrize(
    "name,expected_host",
    [
        ("Librus (dziennik)", "*.librus.pl"),
        ("CKE (arkusze)", "cke.gov.pl"),
        ("GitHub", "github.com"),
        ("TikTok", "*.tiktok.com"),
    ],
)
def test_wybrane_wpisy_katalogu(name, expected_host):
    site = sc.find(name)
    assert site is not None
    assert site.host == expected_host
