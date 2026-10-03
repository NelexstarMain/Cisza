"""Przeksztalcanie danych ze `focuslock.store.Store` na serie wykresow.

Warstwa czysto danych: brak Qt i brak matplotlib, dzieki czemu te funkcje sa
testowalne bez uruchamiania GUI. Widgety (`MonochromeChart`, `Heatmap`) przyjmuja
wyniki tych funkcji przez `set_data(...)`.
"""
from __future__ import annotations

import time
from typing import Iterable, Sequence

WEEKDAYS_PL: tuple[str, ...] = ("PON", "WTO", "ŚR", "CZW", "PT", "SOB", "NDZ")

_BLOCKED_LABELS: dict[str, str] = {
    "BLOCKED_APP": "APLIKACJE",
    "BLOCKED_SITE": "STRONY",
    "BLOCKED_PROCESS": "PROCESY",
    "BLOCKED_ATTEMPT": "PRÓBY",
}


# --------------------------------------------------------------------- formaty
def _num(value: object, default: float = 0.0) -> float:
    """Bezpieczne rzutowanie na liczbe (dane z bazy bywaja None/str)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return int(default)


def fmt_hms(seconds: float) -> str:
    """'1:05:09' powyzej godziny, inaczej 'MM:SS'."""
    total = max(0, int(round(_num(seconds))))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def fmt_clock(seconds: float) -> str:
    """Zapis zegarowy mm:ss (bez godzin) — na duzy timer."""
    total = max(0, int(round(_num(seconds))))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def fmt_minutes(seconds: float, short: bool = False) -> str:
    """'25 min', '1 h 15 min' albo skrot '25m' / '1h15'."""
    total_minutes = int(round(max(0.0, _num(seconds)) / 60.0))
    if short:
        hours, minutes = divmod(total_minutes, 60)
        if hours:
            return f"{hours}h{minutes:02d}" if minutes else f"{hours}h"
        return f"{minutes}m"
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min" if minutes else f"{hours} h"
    return f"{minutes} min"


def fmt_date(epoch: float | None) -> str:
    """Data lokalna 'DD.MM.RRRR'; dla 0/None — 'bezterminowo'."""
    if not epoch:
        return "bezterminowo"
    return time.strftime("%d.%m.%Y", time.localtime(_num(epoch)))


def fmt_time_short(epoch: float | None) -> str:
    if not epoch:
        return "--:--"
    return time.strftime("%H:%M", time.localtime(_num(epoch)))


def day_label(day: str) -> str:
    """'2026-09-30' -> '30.09' (odporne na smieci)."""
    text = str(day or "")
    parts = text.split("-")
    if len(parts) >= 3:
        return f"{parts[2][:2]}.{parts[1][:2]}"
    return text[-5:] or "?"


def truncate(text: str, limit: int = 18) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def scores(values: Sequence[float]) -> list[str]:
    """Dyskretne glify jakosci (bez emoji): ditheringowe stopnie."""
    out: list[str] = []
    for value in values:
        amount = max(0.0, min(1.0, _num(value)))
        filled = int(round(amount * 4))
        out.append("█" * filled + "·" * (4 - filled))
    return out


# ----------------------------------------------------------------- normalizacja
def normalize(values: Iterable[float]) -> list[float]:
    """Skalowanie do 0..1 wzgledem maksimum (puste -> puste)."""
    data = [max(0.0, _num(v)) for v in values]
    if not data:
        return []
    maximum = max(data)
    if maximum <= 0:
        return [0.0 for _ in data]
    return [value / maximum for value in data]


def trend(values: Sequence[float]) -> float:
    """Roznica: ostatnia wartosc minus srednia poprzednich (0..1 skala wzgledna)."""
    data = [_num(v) for v in values]
    if len(data) < 2:
        return 0.0
    previous = data[:-1]
    average = sum(previous) / max(1, len(previous))
    if average <= 0:
        return 1.0 if data[-1] > 0 else 0.0
    return max(-1.0, min(1.0, (data[-1] - average) / average))


# ------------------------------------------------------- serie z danych Store
def bars_from_daily(series: Sequence[dict], field: str = "study_seconds", limit: int = 30) -> dict:
    """Slupki z `store.daily_series()` (domyslnie sekundy nauki -> minuty)."""
    rows = list(series)[-limit:]
    values = [_num(row.get(field)) / 60.0 for row in rows]
    return {
        "kind": "bar",
        "values": values,
        "labels": [day_label(row.get("day", "")) for row in rows],
        "unit": "min",
        "field": field,
        "total": sum(values),
        "days": len(rows),
        "points": [
            {
                "day": row.get("day", ""),
                "label": day_label(row.get("day", "")),
                "minutes": _num(row.get(field)) / 60.0,
                "study_minutes": _num(row.get("study_seconds")) / 60.0,
                "free_minutes": _num(row.get("free_seconds")) / 60.0,
                "pomodoros": _int(row.get("pomodoros_done")),
                "aborted": _int(row.get("pomodoros_aborted")),
                "sessions": _int(row.get("sessions")),
                "blocked": _int(row.get("blocked_attempts")),
            }
            for row in rows
        ],
    }


def line_from_daily(series: Sequence[dict], field: str = "study_seconds", limit: int = 30) -> dict:
    data = bars_from_daily(series, field=field, limit=limit)
    data["kind"] = "line"
    return data


def bars_from_totals(totals: dict) -> dict:
    """Dwa slupki: nauka vs wolne (`store.totals()`)."""
    study = _num((totals or {}).get("study_seconds")) / 60.0
    free = _num((totals or {}).get("free_seconds")) / 60.0
    return {
        "kind": "bar",
        "values": [study, free],
        "labels": ["NAUKA", "WOLNE"],
        "unit": "min",
        "total": study + free,
        "pomodoros": _int((totals or {}).get("pomodoros_done")),
        "sessions": _int((totals or {}).get("sessions")),
    }


def bars_from_blocked(rows: Sequence[dict], limit: int = 10) -> dict:
    """Slupki z `store.top_blocked()`: nazwa rodzaju blokady i liczba zdarzen."""
    data = list(rows)[:limit]
    labels: list[str] = []
    values: list[float] = []
    for row in data:
        kind = str(row.get("kind") or "")
        label = _BLOCKED_LABELS.get(kind)
        if label is None:
            label = kind.replace("BLOCKED", "").strip("_").replace("_", " ") or "INNE"
        labels.append(label)
        values.append(_num(row.get("n")))
    return {
        "kind": "bar",
        "values": values,
        "labels": labels,
        "unit": "",
        "total": sum(values),
        "rows": [
            {"kind": str(row.get("kind") or ""), "label": label, "count": _int(row.get("n"))}
            for row, label in zip(data, labels)
        ],
    }


def bars_from_processes(rows: Sequence[dict], limit: int = 10) -> dict:
    """Slupki z `store.top_processes()`: nazwa procesu i czas w minutach."""
    data = list(rows)[:limit]
    values = [_num(row.get("seconds")) / 60.0 for row in data]
    labels = [truncate(str(row.get("process_name") or "?"), 16) for row in data]
    return {
        "kind": "bar",
        "values": values,
        "labels": labels,
        "unit": "min",
        "total": sum(values),
        "rows": [
            {"process_name": str(row.get("process_name") or "?"), "minutes": value, "seconds": _num(row.get("seconds"))}
            for row, value in zip(data, values)
        ],
    }


def heatmap_from_events(events: Sequence[dict], days: int = 7, now: float | None = None) -> dict:
    """Siatka 7 x 24 z `store.events()` — gestosc zdarzen w godzinach lokalnych.

    Zwraca macierz wierszy od poniedzialku; wartosci znormalizowane do 0..1.
    """
    now = now if now is not None else time.time()
    cutoff = now - days * 86400
    matrix = [[0.0 for _ in range(24)] for _ in range(7)]
    counted = 0
    for event in events:
        try:
            stamp = _num(event.get("ts"))
        except (TypeError, ValueError):
            continue
        if stamp < cutoff or stamp > now + 86400:
            continue
        local = time.localtime(stamp)
        weekday = int(local.tm_wday) % 7
        hour = int(local.tm_hour) % 24
        matrix[weekday][hour] += 1.0
        counted += 1
    maximum = max((cell for row in matrix for cell in row), default=0.0)
    if maximum > 0:
        matrix = [[cell / maximum for cell in row] for row in matrix]
    return {
        "matrix": matrix,
        "row_labels": list(WEEKDAYS_PL),
        "col_labels": [f"{hour:02d}" if hour % 3 == 0 else "" for hour in range(24)],
        "max": maximum,
        "events": counted,
        "days": days,
    }


def heatmap_from_daily(series: Sequence[dict], weeks: int = 6) -> dict:
    """Zapasowa heatmapa tygodniowa z `daily_series` (gdy brak zdarzen godzinowych)."""
    rows = list(series)[-weeks * 7 :]
    matrix: list[list[float]] = []
    for start in range(0, len(rows), 7):
        chunk = rows[start : start + 7]
        matrix.append([_num(row.get("study_seconds")) / 60.0 for row in chunk])
    if not matrix:
        return {"matrix": [], "row_labels": [], "col_labels": [], "max": 0.0, "events": 0, "days": 0}
    width = max(len(row) for row in matrix)
    for row in matrix:
        while len(row) < width:
            row.append(0.0)
    maximum = max((cell for row in matrix for cell in row), default=0.0)
    normalized = [[(cell / maximum if maximum else 0.0) for cell in row] for row in matrix]
    return {
        "matrix": normalized,
        "row_labels": [f"T{index + 1}" for index in range(len(normalized))],
        "col_labels": list(WEEKDAYS_PL[:width]),
        "max": maximum,
        "events": 0,
        "days": len(rows),
    }


# -------------------------------------------------------------------- podsumy
def series_summary(series: Sequence[dict], now: float | None = None) -> dict:
    """Spięcie `daily_series()` w dziś / tydzień / miesiąc + rekord."""
    rows = list(series)
    if not rows:
        return {
            "today": 0.0,
            "week": 0.0,
            "month": 0.0,
            "total": 0.0,
            "pomodoros": 0,
            "pomodoros_aborted": 0,
            "sessions": 0,
            "blocked": 0,
            "best_day": "",
            "best_minutes": 0.0,
            "spark": [],
        }

    def minutes(row: dict, field: str = "study_seconds") -> float:
        return _num(row.get(field)) / 60.0

    today = minutes(rows[-1])
    week = sum(minutes(row) for row in rows[-7:])
    month = sum(minutes(row) for row in rows[-30:])
    best = max(rows, key=lambda row: _num(row.get("study_seconds")))
    return {
        "today": today,
        "week": week,
        "month": month,
        "total": sum(minutes(row) for row in rows),
        "pomodoros": sum(_int(row.get("pomodoros_done")) for row in rows),
        "pomodoros_aborted": sum(_int(row.get("pomodoros_aborted")) for row in rows),
        "sessions": sum(_int(row.get("sessions")) for row in rows),
        "blocked": sum(_int(row.get("blocked_attempts")) for row in rows),
        "best_day": str(best.get("day") or ""),
        "best_minutes": minutes(best),
        "spark": [minutes(row) for row in rows[-14:]],
    }


def rating_summary(sessions: Sequence[dict]) -> dict:
    """Oceny sesji 1..5 z `store.recent_sessions()`."""
    ratings = [_int(row.get("self_rating")) for row in sessions if row.get("self_rating")]
    if not ratings:
        return {"average": 0.0, "count": 0, "best": 0, "last": 0, "last_text": "—"}
    average = sum(ratings) / len(ratings)
    last = min(5, max(1, ratings[0]))
    return {
        "average": average,
        "count": len(ratings),
        "best": max(ratings),
        "last": last,
        "last_text": "█" * last + "·" * (5 - last),
    }


def score_band(score: float) -> dict:
    """Etykieta i glif dla focus score 0..100 (bez koloru)."""
    value = max(0.0, min(100.0, _num(score)))
    if value < 25:
        band, glyph = "ROZPROSZENIE", "·"
    elif value < 50:
        band, glyph = "SLABO", "▪"
    elif value < 75:
        band, glyph = "DOBRZE", "▣"
    else:
        band, glyph = "MOCNO", "█"
    return {"score": value, "label": band, "glyph": glyph, "level": value / 100.0}


def blocked_total(rows: Sequence[dict]) -> int:
    return sum(_int(row.get("n")) for row in rows)


def journal_groups(events: Sequence[dict]) -> dict[str, list[dict]]:
    """Grupowanie zdarzen dziennika po rodzaju (filtr w `JournalScreen`)."""
    groups: dict[str, list[dict]] = {}
    for event in events:
        kind = str(event.get("kind") or "INNE")
        key = "BLOKADA" if kind.startswith("BLOCKED") else ("SESJA" if kind.isupper() else "INNE")
        groups.setdefault(key, []).append(dict(event))
    return groups
