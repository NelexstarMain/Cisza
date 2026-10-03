"""Katalog stron do wyboru w kreatorze sesji.

Strony sa pogrupowane w kategorie, a kazda ma tryb:
  - kind="STUDY"   -> mozna ja wybrac jako dozwolona w trakcie nauki,
  - kind="BLOCKED" -> rozpraszacz, domyslnie na liscie blokowanych.

Modul jest czysto tekstowy (bez sieci, bez PyQt), wiec latwo go testowac.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

STUDY = "STUDY"
BLOCKED = "BLOCKED"

CATEGORIES: dict[str, str] = {
    "nauka": "Nauka i encyklopedie",
    "szkola": "Szkoła i dziennik",
    "matura": "Matura i powtórki",
    "jezyki": "Języki obce",
    "kod": "Programowanie",
    "muzyka": "Muzyka i nuty",
    "narzedzia": "Narzędzia i notatki",
    "rozrywka": "Rozrywka (blokowane)",
    "spolecznosc": "Social media (blokowane)",
    "wiadomosci": "Wiadomości i portale (blokowane)",
    "zakupy": "Zakupy (blokowane)",
}


@dataclass(frozen=True)
class CatalogSite:
    name: str
    host: str
    category: str
    kind: str = STUDY
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "category": self.category,
            "kind": self.kind,
            "note": self.note,
            "category_label": CATEGORIES.get(self.category, self.category),
        }

    @property
    def process_hint(self) -> str:
        return self.host


def _s(name: str, host: str, category: str, kind: str = STUDY, note: str = "") -> CatalogSite:
    return CatalogSite(name=name, host=host, category=category, kind=kind, note=note)


SITE_CATALOG: tuple[CatalogSite, ...] = (
    # ------------------------------------------------------------------ nauka
    _s("Wikipedia (PL)", "*.wikipedia.org", "nauka", note="encyklopedia"),
    _s("Wikisłownik", "*.wiktionary.org", "nauka"),
    _s("Khan Academy", "pl.khanacademy.org", "nauka", note="matematyka, fizyka"),
    _s("Britannica", "britannica.com", "nauka"),
    _s("TED", "ted.com", "nauka", note="prelekcje"),
    _s("Coursera", "coursera.org", "nauka", note="kursy"),
    _s("edX", "edx.org", "nauka", note="kursy"),
    _s("MIT OpenCourseWare", "ocw.mit.edu", "nauka"),
    _s("Google Scholar", "scholar.google.com", "nauka", note="prace naukowe"),
    _s("Wolne Lektury", "wolnelektury.pl", "nauka", note="lektury szkolne"),
    _s("Polona (Biblioteka Narodowa)", "polona.pl", "nauka"),
    _s("e-Podręczniki", "epodreczniki.pl", "nauka"),
    _s("OpenStax", "openstax.org", "nauka", note="darmowe podręczniki"),
    _s("PhET Simulations", "phet.colorado.edu", "nauka", note="symulacje fizyki"),
    # ------------------------------------------------------------------ szkola
    _s("Librus (dziennik)", "*.librus.pl", "szkola", note="oceny i frekwencja"),
    _s("Vulcan UONET+", "*.vulcan.net.pl", "szkola", note="dziennik elektroniczny"),
    _s("Google Classroom", "classroom.google.com", "szkola"),
    _s("Microsoft Teams", "teams.microsoft.com", "szkola", note="lekcje online"),
    _s("e-Dziennik / Moodle szkolny", "*.moodle.org", "szkola"),
    # ------------------------------------------------------------------ matura
    _s("CKE (arkusze)", "cke.gov.pl", "matura", note="arkusze i informatory"),
    _s("Arkusze.pl", "arkusze.pl", "matura"),
    _s("Matematyka.pisz.pl", "matematyka.pisz.pl", "matura", note="teoria krok po kroku"),
    _s("Zadania.info", "zadania.info", "matura", note="zadania z rozwiązaniami"),
    _s("Materiały maturalne (epodreczniki)", "zpe.gov.pl", "matura"),
    _s("Matura z matematyki", "matemaks.pl", "matura"),
    _s("Symbolab", "*.symbolab.com", "matura", note="krok po kroku"),
    _s("Desmos", "desmos.com", "matura", note="wykresy funkcji"),
    _s("Wolfram Alpha", "*.wolframalpha.com", "matura", note="obliczenia i wyjaśnienia"),
    # ------------------------------------------------------------------ jezyki
    _s("Duolingo", "duolingo.com", "jezyki"),
    _s("DeepL (tłumacz)", "deepl.com", "jezyki"),
    _s("Cambridge Dictionary", "dictionary.cambridge.org", "jezyki"),
    _s("Diki", "diki.pl", "jezyki"),
    _s("bab.la", "en.bab.la", "jezyki"),
    _s("Linguee", "linguee.pl", "jezyki"),
    _s("Quizlet", "quizlet.com", "jezyki", note="fiszki"),
    _s("AnkiWeb", "ankiweb.net", "jezyki", note="fiszki"),
    # ------------------------------------------------------------------ kod
    _s("GitHub", "github.com", "kod"),
    _s("GitHub Gist", "gist.github.com", "kod"),
    _s("Stack Overflow", "stackoverflow.com", "kod"),
    _s("Dokumentacja Pythona", "docs.python.org", "kod"),
    _s("MDN Web Docs", "developer.mozilla.org", "kod"),
    _s("Microsoft Learn", "learn.microsoft.com", "kod"),
    _s("W3Schools", "w3schools.com", "kod"),
    _s("PyPI", "pypi.org", "kod"),
    _s("regex101", "regex101.com", "kod"),
    _s("LeetCode", "leetcode.com", "kod"),
    _s("Codewars", "codewars.com", "kod"),
    _s("Godot Docs", "docs.godotengine.org", "kod"),
    _s("Unity Learn", "learn.unity.com", "kod"),
    # ------------------------------------------------------------------ muzyka
    _s("MuseScore", "musescore.com", "muzyka", note="nuty"),
    _s("IMSLP", "imslp.org", "muzyka", note="nuty public domain"),
    _s("Teoria muzyki", "teoria-muzyki.pl", "muzyka"),
    # ------------------------------------------------------------------ narzedzia
    _s("Notion", "*.notion.so", "narzedzia", note="notatki"),
    _s("Obsidian", "obsidian.md", "narzedzia", note="notatki lokalne"),
    _s("Google Keep", "keep.google.com", "narzedzia"),
    _s("Google Dysk", "drive.google.com", "narzedzia"),
    _s("Google Calendar", "calendar.google.com", "narzedzia"),
    _s("Overleaf (LaTeX)", "overleaf.com", "narzedzia", note="prace pisemne"),
    _s("Google Translate", "translate.google.com", "narzedzia"),
    _s("Wolfram Cloud", "*.wolframcloud.com", "narzedzia"),
    # ---------------------------------------------------------------- rozrywka
    _s("YouTube", "*.youtube.com", "rozrywka", BLOCKED),
    _s("YouTube (skrót)", "youtu.be", "rozrywka", BLOCKED),
    _s("Netflix", "*.netflix.com", "rozrywka", BLOCKED),
    _s("Twitch", "*.twitch.tv", "rozrywka", BLOCKED),
    _s("Disney+", "disneyplus.com", "rozrywka", BLOCKED),
    _s("HBO Max", "hbomax.com", "rozrywka", BLOCKED),
    _s("Prime Video", "primevideo.com", "rozrywka", BLOCKED),
    _s("CDA", "cda.pl", "rozrywka", BLOCKED),
    _s("Steam (sklep)", "store.steampowered.com", "rozrywka", BLOCKED),
    _s("Epic Games", "store.epicgames.com", "rozrywka", BLOCKED),
    _s("Spotify (web)", "open.spotify.com", "rozrywka", BLOCKED),
    _s("9GAG", "9gag.com", "rozrywka", BLOCKED),
    # ------------------------------------------------------------- spolecznosc
    _s("Facebook", "*.facebook.com", "spolecznosc", BLOCKED),
    _s("Instagram", "*.instagram.com", "spolecznosc", BLOCKED),
    _s("TikTok", "*.tiktok.com", "spolecznosc", BLOCKED),
    _s("X / Twitter", "x.com", "spolecznosc", BLOCKED),
    _s("Twitter", "*.twitter.com", "spolecznosc", BLOCKED),
    _s("Reddit", "*.reddit.com", "spolecznosc", BLOCKED),
    _s("Wykop", "*.wykop.pl", "spolecznosc", BLOCKED),
    _s("Discord (web)", "discord.com", "spolecznosc", BLOCKED),
    _s("Snapchat", "*.snapchat.com", "spolecznosc", BLOCKED),
    _s("Pinterest", "*.pinterest.com", "spolecznosc", BLOCKED),
    _s("Threads", "threads.net", "spolecznosc", BLOCKED),
    # -------------------------------------------------------------- wiadomosci
    _s("Onet", "*.onet.pl", "wiadomosci", BLOCKED),
    _s("Wirtualna Polska", "*.wp.pl", "wiadomosci", BLOCKED),
    _s("Interia", "*.interia.pl", "wiadomosci", BLOCKED),
    _s("Gazeta.pl", "*.gazeta.pl", "wiadomosci", BLOCKED),
    _s("YouTube Shorts", "m.youtube.com", "wiadomosci", BLOCKED),
    # ------------------------------------------------------------------ zakupy
    _s("Allegro", "*.allegro.pl", "zakupy", BLOCKED),
    _s("OLX", "*.olx.pl", "zakupy", BLOCKED),
    _s("Aliexpress", "*.aliexpress.com", "zakupy", BLOCKED),
    _s("Temu", "*.temu.com", "zakupy", BLOCKED),
    _s("Amazon", "*.amazon.pl", "zakupy", BLOCKED),
)

# Sensowny zestaw startowy "uczę się" (bez wpisywania czegokolwiek).
DEFAULT_STUDY_SITES: tuple[str, ...] = (
    "*.wikipedia.org",
    "pl.khanacademy.org",
    "cke.gov.pl",
    "wolnelektury.pl",
    "epodreczniki.pl",
    "scholar.google.com",
    "*.wolframalpha.com",
    "dictionary.cambridge.org",
    "docs.python.org",
    "stackoverflow.com",
    "github.com",
    "teams.microsoft.com",
)

# Sugestie per przedmiot (uzywane przez presety i podpowiedzi w UI).
SUBJECT_SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "matematyka": (
        "pl.khanacademy.org",
        "matematyka.pisz.pl",
        "zadania.info",
        "*.wolframalpha.com",
        "desmos.com",
        "cke.gov.pl",
    ),
    "polski": ("wolnelektury.pl", "polona.pl", "sjp.pwn.pl", "cke.gov.pl"),
    "angielski": ("dictionary.cambridge.org", "diki.pl", "duolingo.com", "deepl.com"),
    "informatyka": (
        "docs.python.org",
        "stackoverflow.com",
        "github.com",
        "developer.mozilla.org",
        "learn.microsoft.com",
    ),
    "historia": ("*.wikipedia.org", "polona.pl", "ipn.gov.pl"),
    "biologia": ("*.wikipedia.org", "pl.khanacademy.org", "phet.colorado.edu"),
    "fizyka": ("pl.khanacademy.org", "phet.colorado.edu", "*.wolframalpha.com"),
    "chemia": ("pl.khanacademy.org", "*.wikipedia.org", "periodni.com"),
    "muzyka": ("musescore.com", "imslp.org", "teoria-muzyki.pl"),
}


def normalize(host: str) -> str:
    """Ujednolica wpis hosta (male litery, bez schematu, bez portu, bez kropki)."""
    value = (host or "").strip().lower()
    for prefix in ("https://", "http://", "//"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = value.split("/")[0].split("?")[0]
    value = value.split(":")[0]
    return value.rstrip(".")


def all_sites() -> tuple[CatalogSite, ...]:
    return SITE_CATALOG


def by_category(kind: Optional[str] = None) -> dict[str, list[CatalogSite]]:
    """Zwraca slownik: kategoria -> lista stron (z etykietami PL)."""
    groups: dict[str, list[CatalogSite]] = {}
    for site in SITE_CATALOG:
        if kind and site.kind != kind:
            continue
        groups.setdefault(site.category, []).append(site)
    return groups


def for_kind(kind: str = STUDY) -> list[CatalogSite]:
    return [site for site in SITE_CATALOG if site.kind == kind]


def search(query: str, kind: Optional[str] = None) -> list[CatalogSite]:
    needle = (query or "").strip().lower()
    sites = [site for site in SITE_CATALOG if not kind or site.kind == kind]
    if not needle:
        return sites
    return [site for site in sites if needle in site.name.lower() or needle in site.host.lower()]


def hosts(kind: Optional[str] = None) -> list[str]:
    return [site.host for site in SITE_CATALOG if not kind or site.kind == kind]


def default_study_hosts() -> list[str]:
    return list(DEFAULT_STUDY_SITES)


def default_block_hosts() -> list[str]:
    from .config import DEFAULT_BLOCKLIST

    return list(DEFAULT_BLOCKLIST)


def suggest_for(subject: str) -> list[str]:
    key = (subject or "").strip().lower()
    return list(SUBJECT_SUGGESTIONS.get(key, ()))


def find(value: str) -> Optional[CatalogSite]:
    """Znajduje strone po hoscie albo po nazwie z katalogu."""
    needle = normalize(value)
    label = (value or "").strip().lower()
    for site in SITE_CATALOG:
        if normalize(site.host) == needle:
            return site
    for site in SITE_CATALOG:
        if site.name.strip().lower() == label:
            return site
    return None


def to_dicts(sites: Optional[Iterable[CatalogSite]] = None) -> list[dict]:
    return [site.to_dict() for site in (sites if sites is not None else SITE_CATALOG)]


def describe() -> dict:
    counts: dict[str, int] = {}
    for site in SITE_CATALOG:
        counts[site.category] = counts.get(site.category, 0) + 1
    return {
        "total": len(SITE_CATALOG),
        "study": len(for_kind(STUDY)),
        "blocked": len(for_kind(BLOCKED)),
        "categories": counts,
        "labels": dict(CATEGORIES),
    }


def catalog_payload(kind: Optional[str] = None) -> dict:
    """Gotowy do wyslania do UI zestaw: kategorie + strony."""
    groups = by_category(kind)
    return {
        "ok": True,
        "categories": [
            {
                "key": key,
                "label": CATEGORIES.get(key, key),
                "sites": [site.to_dict() for site in items],
            }
            for key, items in groups.items()
        ],
        "defaults": {"study": default_study_hosts(), "blocked": default_block_hosts()},
        "summary": describe(),
    }


def hosts_from_names(names: Sequence[str]) -> list[str]:
    """Zamienia nazwy wybrane w UI na hosty (przepuszcza tez wlasne wpisy)."""
    resolved: list[str] = []
    for name in names:
        site = find(name)
        host = normalize(site.host) if site else normalize(name)
        if host and host not in resolved:
            resolved.append(host)
    return resolved
