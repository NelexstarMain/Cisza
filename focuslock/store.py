"""Warstwa danych: SQLite + repozytoria.

Zamrozony kontrakt (patrz docs/INTERFACES.md). Wszystkie daty to epoch
(time.time()), daty dzienne liczone w czasie lokalnym.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from . import paths

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_profiles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    match_kind TEXT NOT NULL CHECK(match_kind IN ('name','path','signature')),
    match_value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'STUDY',
    created_at REAL NOT NULL,
    UNIQUE(match_kind, match_value)
);
CREATE TABLE IF NOT EXISTS site_profiles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'STUDY',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS presets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL CHECK(mode IN ('STUDY','FREE')),
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    plan_seconds INTEGER NOT NULL DEFAULT 0,
    actual_seconds INTEGER NOT NULL DEFAULT 0,
    pomodoros_done INTEGER NOT NULL DEFAULT 0,
    pomodoros_aborted INTEGER NOT NULL DEFAULT 0,
    tag TEXT NOT NULL DEFAULT '',
    goal_note TEXT NOT NULL DEFAULT '',
    allowlist_json TEXT NOT NULL DEFAULT '{}',
    self_rating INTEGER,
    hardcore INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS session_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS usage(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    process_name TEXT NOT NULL,
    seconds REAL NOT NULL DEFAULT 0,
    foreground_seconds REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    UNIQUE(session_id, process_name)
);
CREATE TABLE IF NOT EXISTS bank_ledger(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    delta_seconds INTEGER NOT NULL,
    reason TEXT NOT NULL,
    session_id INTEGER,
    note TEXT NOT NULL DEFAULT '',
    expires_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bank_lots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL DEFAULT 0,
    original_seconds INTEGER NOT NULL,
    remaining_seconds INTEGER NOT NULL,
    session_id INTEGER,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS streaks(
    kind TEXT PRIMARY KEY,
    current INTEGER NOT NULL DEFAULT 0,
    best INTEGER NOT NULL DEFAULT 0,
    last_day TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT 'null',
    after_json TEXT NOT NULL DEFAULT 'null'
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id);
CREATE INDEX IF NOT EXISTS idx_ledger_ts ON bank_ledger(ts);
CREATE VIEW IF NOT EXISTS v_daily_stats AS
SELECT date(started_at, 'unixepoch', 'localtime') AS day,
       SUM(CASE WHEN mode = 'STUDY' THEN actual_seconds ELSE 0 END) AS study_seconds,
       SUM(CASE WHEN mode = 'FREE' THEN actual_seconds ELSE 0 END) AS free_seconds,
       SUM(pomodoros_done) AS pomodoros_done,
       SUM(pomodoros_aborted) AS pomodoros_aborted,
       COUNT(*) AS sessions
FROM sessions
WHERE status IN ('COMPLETED', 'ABORTED')
GROUP BY day;
CREATE VIEW IF NOT EXISTS v_bank_balance AS
SELECT COALESCE(SUM(remaining_seconds), 0) AS balance FROM bank_lots;
"""


def _today(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    """Bezpieczny watkowo dostep do bazy (jeden zamek, WAL)."""

    def __init__(self, path: Optional[Path] = None, *, memory: bool = False) -> None:
        self.path = Path(path) if path is not None else paths.db_path()
        self._lock = threading.RLock()
        if memory:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ niskie
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- ustawienia
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value_json FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (key, _dumps(value), time.time()),
        )

    def all_settings(self) -> dict:
        return {row["key"]: json.loads(row["value_json"]) for row in self.query("SELECT * FROM settings")}

    # ------------------------------------------------------------- profile aplikacji
    def list_app_profiles(self, category: Optional[str] = None) -> list[dict]:
        if category:
            return self.query("SELECT * FROM app_profiles WHERE category = ? ORDER BY label", (category,))
        return self.query("SELECT * FROM app_profiles ORDER BY category, label")

    def upsert_app_profile(
        self,
        label: str,
        match_kind: str,
        match_value: str,
        category: str = "STUDY",
        profile_id: Optional[int] = None,
    ) -> int:
        if profile_id:
            self.execute(
                "UPDATE app_profiles SET label=?, match_kind=?, match_value=?, category=? WHERE id=?",
                (label, match_kind, match_value, category, profile_id),
            )
            return int(profile_id)
        cur = self.execute(
            "INSERT INTO app_profiles(label, match_kind, match_value, category, created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(match_kind, match_value) DO UPDATE SET label=excluded.label, category=excluded.category",
            (label, match_kind, match_value, category, time.time()),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self.query_one(
            "SELECT id FROM app_profiles WHERE match_kind=? AND match_value=?", (match_kind, match_value)
        )
        return int(row["id"]) if row else 0

    def delete_app_profile(self, profile_id: int) -> None:
        self.execute("DELETE FROM app_profiles WHERE id=?", (profile_id,))

    # ----------------------------------------------------------------- profile stron
    def list_site_profiles(self, category: Optional[str] = None) -> list[dict]:
        if category:
            return self.query("SELECT * FROM site_profiles WHERE category=? ORDER BY host", (category,))
        return self.query("SELECT * FROM site_profiles ORDER BY category, host")

    def upsert_site_profile(self, host: str, label: str = "", category: str = "STUDY") -> int:
        self.execute(
            "INSERT INTO site_profiles(host, label, category, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(host) DO UPDATE SET label=excluded.label, category=excluded.category",
            (host.strip().lower(), label, category, time.time()),
        )
        row = self.query_one("SELECT id FROM site_profiles WHERE host=?", (host.strip().lower(),))
        return int(row["id"]) if row else 0

    def delete_site_profile(self, profile_id: int) -> None:
        self.execute("DELETE FROM site_profiles WHERE id=?", (profile_id,))

    # --------------------------------------------------------------------- presety
    def list_presets(self) -> list[dict]:
        rows = self.query("SELECT * FROM presets ORDER BY name")
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return rows

    def upsert_preset(self, name: str, payload: dict) -> int:
        self.execute(
            "INSERT INTO presets(name, payload_json, created_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET payload_json=excluded.payload_json",
            (name, _dumps(payload), time.time()),
        )
        row = self.query_one("SELECT id FROM presets WHERE name=?", (name,))
        return int(row["id"]) if row else 0

    def delete_preset(self, preset_id: int) -> None:
        self.execute("DELETE FROM presets WHERE id=?", (preset_id,))

    # -------------------------------------------------------------------- sesje
    def start_session(
        self,
        mode: str,
        plan_seconds: int,
        tag: str = "",
        goal_note: str = "",
        allowlist: Optional[dict] = None,
        hardcore: bool = False,
    ) -> int:
        cur = self.execute(
            "INSERT INTO sessions(mode, status, started_at, plan_seconds, tag, goal_note, allowlist_json, hardcore) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (mode, "RUNNING", time.time(), int(plan_seconds), tag, goal_note, _dumps(allowlist or {}), int(bool(hardcore))),
        )
        return int(cur.lastrowid or 0)

    def update_session(self, session_id: int, **fields: Any) -> None:
        allowed = {
            "status",
            "ended_at",
            "actual_seconds",
            "pomodoros_done",
            "pomodoros_aborted",
            "self_rating",
            "goal_note",
            "tag",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        sql = "UPDATE sessions SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?"
        self.execute(sql, (*sets.values(), session_id))

    def finish_session(
        self,
        session_id: int,
        status: str,
        actual_seconds: int,
        pomodoros_done: int = 0,
        pomodoros_aborted: int = 0,
        self_rating: Optional[int] = None,
    ) -> None:
        self.execute(
            "UPDATE sessions SET status=?, ended_at=?, actual_seconds=?, pomodoros_done=?, pomodoros_aborted=?, "
            "self_rating=COALESCE(?, self_rating) WHERE id=?",
            (status, time.time(), int(actual_seconds), int(pomodoros_done), int(pomodoros_aborted), self_rating, session_id),
        )

    def active_session(self) -> Optional[dict]:
        row = self.query_one("SELECT * FROM sessions WHERE status='RUNNING' ORDER BY id DESC LIMIT 1")
        if row:
            row["allowlist"] = json.loads(row.pop("allowlist_json") or "{}")
        return row

    def get_session(self, session_id: int) -> Optional[dict]:
        row = self.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if row:
            row["allowlist"] = json.loads(row.pop("allowlist_json") or "{}")
        return row

    def recent_sessions(self, limit: int = 50) -> list[dict]:
        rows = self.query("SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["allowlist"] = json.loads(row.pop("allowlist_json") or "{}")
        return rows

    def open_sessions(self) -> list[dict]:
        """Sesje, ktore nie zostaly zamkniete czysto (do wykrycia crashu)."""
        return self.query("SELECT * FROM sessions WHERE status IN ('RUNNING','ARMED') ORDER BY id")

    def add_event(self, session_id: Optional[int], kind: str, detail: Optional[dict] = None) -> int:
        cur = self.execute(
            "INSERT INTO session_events(session_id, ts, kind, detail_json) VALUES(?,?,?,?)",
            (session_id, time.time(), kind, _dumps(detail or {})),
        )
        return int(cur.lastrowid or 0)

    def events(self, session_id: Optional[int] = None, limit: int = 200) -> list[dict]:
        if session_id is None:
            rows = self.query("SELECT * FROM session_events ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self.query(
                "SELECT * FROM session_events WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)
            )
        for row in rows:
            row["detail"] = json.loads(row.pop("detail_json") or "{}")
        return rows

    # --------------------------------------------------------------------- bank
    def add_lot(
        self,
        seconds: int,
        session_id: Optional[int] = None,
        note: str = "",
        ttl_days: int = 0,
        reason: str = "EARNED",
        *,
        created_at: Optional[float] = None,
    ) -> int:
        """Dodaje lot banku. `created_at` pozwala uzyc czasu przekazanego z zewnatrz
        (testy i rozliczenia z jawnym `now`), zamiast zegara sciennego."""
        now = float(created_at) if created_at is not None else time.time()
        expires_at = now + ttl_days * 86400 if ttl_days else 0.0
        cur = self.execute(
            "INSERT INTO bank_lots(created_at, expires_at, original_seconds, remaining_seconds, session_id, note) "
            "VALUES(?,?,?,?,?,?)",
            (now, expires_at, int(seconds), int(seconds), session_id, note),
        )
        self.execute(
            "INSERT INTO bank_ledger(ts, delta_seconds, reason, session_id, note, expires_at) VALUES(?,?,?,?,?,?)",
            (now, int(seconds), reason, session_id, note, expires_at),
        )
        return int(cur.lastrowid or 0)

    def spend(self, seconds: int, session_id: Optional[int] = None, note: str = "") -> int:
        """Konsumuje bank FIFO (najstarsze, niewygasle). Zwraca faktycznie zuzyte sekundy."""
        now = time.time()
        remaining_to_spend = max(0, int(seconds))
        spent = 0
        lots = self.query(
            "SELECT * FROM bank_lots WHERE remaining_seconds > 0 AND (expires_at = 0 OR expires_at > ?) "
            "ORDER BY created_at, id",
            (now,),
        )
        for lot in lots:
            if remaining_to_spend <= 0:
                break
            take = min(int(lot["remaining_seconds"]), remaining_to_spend)
            self.execute("UPDATE bank_lots SET remaining_seconds = remaining_seconds - ? WHERE id=?", (take, lot["id"]))
            remaining_to_spend -= take
            spent += take
        if spent:
            self.execute(
                "INSERT INTO bank_ledger(ts, delta_seconds, reason, session_id, note, expires_at) VALUES(?,?,?,?,?,0)",
                (now, -spent, "SPENT", session_id, note),
            )
        return spent

    def return_unspent(self, seconds: int, session_id: Optional[int] = None, note: str = "") -> int:
        """Zwraca niewykorzystane minuty z trybu wolnego jako nowy lot (bez zmiany TTL oryginalu)."""
        if seconds <= 0:
            return 0
        return self.add_lot(int(seconds), session_id=session_id, note=note or "RETURN", ttl_days=0, reason="ADJUST")

    def expire_lots(self, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        lots = self.query(
            "SELECT * FROM bank_lots WHERE remaining_seconds > 0 AND expires_at > 0 AND expires_at <= ?", (now,)
        )
        total = 0
        for lot in lots:
            total += int(lot["remaining_seconds"])
            self.execute("UPDATE bank_lots SET remaining_seconds = 0 WHERE id=?", (lot["id"],))
            self.execute(
                "INSERT INTO bank_ledger(ts, delta_seconds, reason, session_id, note, expires_at) VALUES(?,?,?,?,?,0)",
                (now, -int(lot["remaining_seconds"]), "EXPIRED", lot["session_id"], "wygaslo"),
            )
        return total

    def bank_balance(self, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        row = self.query_one(
            "SELECT COALESCE(SUM(remaining_seconds),0) AS balance FROM bank_lots "
            "WHERE remaining_seconds > 0 AND (expires_at = 0 OR expires_at > ?)",
            (now,),
        )
        return int(row["balance"]) if row else 0

    def lots(self, include_empty: bool = False) -> list[dict]:
        if include_empty:
            return self.query("SELECT * FROM bank_lots ORDER BY created_at")
        return self.query("SELECT * FROM bank_lots WHERE remaining_seconds > 0 ORDER BY created_at")

    def ledger(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM bank_ledger ORDER BY id DESC LIMIT ?", (limit,))

    def earned_today(self, now: Optional[float] = None) -> int:
        day = _today(now)
        row = self.query_one(
            "SELECT COALESCE(SUM(delta_seconds),0) AS earned FROM bank_ledger "
            "WHERE reason='EARNED' AND date(ts, 'unixepoch', 'localtime') = ?",
            (day,),
        )
        return int(row["earned"]) if row else 0

    def adjust_bank(self, seconds: int, note: str = "", session_id: Optional[int] = None) -> int:
        """Reczna korekta banku. Dodatnia = nowy lot bez wygasania, ujemna = zabranie FIFO."""
        if seconds >= 0:
            return self.add_lot(seconds, session_id=session_id, note=note or "ADJUST", ttl_days=0, reason="ADJUST")
        return self.spend(-seconds, session_id=session_id, note=note or "ADJUST")

    # -------------------------------------------------------------------- uzycie
    def add_usage(self, session_id: int, process_name: str, seconds: float, foreground_seconds: float = 0.0) -> None:
        self.execute(
            "INSERT INTO usage(session_id, process_name, seconds, foreground_seconds, updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(session_id, process_name) DO UPDATE SET "
            "seconds = usage.seconds + excluded.seconds, "
            "foreground_seconds = usage.foreground_seconds + excluded.foreground_seconds, "
            "updated_at = excluded.updated_at",
            (session_id, process_name, float(seconds), float(foreground_seconds), time.time()),
        )

    def usage_for(self, session_id: int) -> list[dict]:
        return self.query("SELECT * FROM usage WHERE session_id=? ORDER BY seconds DESC", (session_id,))

    # -------------------------------------------------------------------- serie
    def get_streak(self, kind: str = "study") -> dict:
        row = self.query_one("SELECT * FROM streaks WHERE kind=?", (kind,))
        return row or {"kind": kind, "current": 0, "best": 0, "last_day": ""}

    def set_streak(self, kind: str, current: int, best: int, last_day: str) -> None:
        self.execute(
            "INSERT INTO streaks(kind, current, best, last_day) VALUES(?,?,?,?) "
            "ON CONFLICT(kind) DO UPDATE SET current=excluded.current, best=excluded.best, last_day=excluded.last_day",
            (kind, int(current), int(best), last_day),
        )

    # -------------------------------------------------------------------- audyt
    def log_audit(self, action: str, before: Any = None, after: Any = None) -> None:
        self.execute(
            "INSERT INTO audit(ts, action, before_json, after_json) VALUES(?,?,?,?)",
            (time.time(), action, _dumps(before) if before is not None else "null",
             _dumps(after) if after is not None else "null"),
        )

    # --------------------------------------------------------------- statystyki
    def daily_stats(self, day: Optional[str] = None) -> dict:
        day = day or _today()
        row = self.query_one("SELECT * FROM v_daily_stats WHERE day=?", (day,))
        base = row or {
            "day": day,
            "study_seconds": 0,
            "free_seconds": 0,
            "pomodoros_done": 0,
            "pomodoros_aborted": 0,
            "sessions": 0,
        }
        base["blocked_attempts"] = self.blocked_count(day=day)
        return base

    def blocked_count(self, day: Optional[str] = None) -> int:
        if day is None:
            row = self.query_one("SELECT COUNT(*) AS n FROM session_events WHERE kind LIKE 'BLOCKED%'")
        else:
            row = self.query_one(
                "SELECT COUNT(*) AS n FROM session_events WHERE kind LIKE 'BLOCKED%' "
                "AND date(ts, 'unixepoch', 'localtime') = ?",
                (day,),
            )
        return int(row["n"]) if row else 0

    def daily_series(self, days: int = 30, now: Optional[float] = None) -> list[dict]:
        now = now if now is not None else time.time()
        out = []
        for offset in range(days - 1, -1, -1):
            ts = now - offset * 86400
            day = _today(ts)
            out.append(self.daily_stats(day))
        return out

    def totals(self) -> dict:
        row = self.query_one(
            "SELECT COALESCE(SUM(CASE WHEN mode='STUDY' THEN actual_seconds ELSE 0 END),0) AS study_seconds, "
            "COALESCE(SUM(CASE WHEN mode='FREE' THEN actual_seconds ELSE 0 END),0) AS free_seconds, "
            "COALESCE(SUM(pomodoros_done),0) AS pomodoros_done, "
            "COALESCE(SUM(pomodoros_aborted),0) AS pomodoros_aborted, COUNT(*) AS sessions "
            "FROM sessions WHERE status IN ('COMPLETED','ABORTED')"
        ) or {}
        row["blocked_attempts"] = self.blocked_count()
        row["bank_balance"] = self.bank_balance()
        return row

    def top_blocked(self, kind_prefix: str = "BLOCKED", limit: int = 10) -> list[dict]:
        rows = self.query(
            "SELECT kind, COUNT(*) AS n FROM session_events WHERE kind LIKE ? GROUP BY kind ORDER BY n DESC LIMIT ?",
            (f"{kind_prefix}%", limit),
        )
        return rows

    def top_processes(self, limit: int = 10) -> list[dict]:
        return self.query(
            "SELECT process_name, SUM(seconds) AS seconds FROM usage GROUP BY process_name ORDER BY seconds DESC LIMIT ?",
            (limit,),
        )
