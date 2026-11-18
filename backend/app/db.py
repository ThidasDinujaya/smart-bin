# small sqlite wrapper
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS bins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    depth_cm REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bin_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    received_at TEXT NOT NULL,
    distance_cm REAL NOT NULL,
    fill_pct REAL,
    trigger TEXT NOT NULL,              -- lid_close | refresh | heartbeat
    lid_opens INTEGER NOT NULL DEFAULT 0,
    battery_pct REAL,
    command_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_readings_bin ON readings(bin_id, id);
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bin_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    fill_before REAL,
    fill_after REAL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bin_id TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- nearly_full | full | sensor_fault | offline
    started_at TEXT NOT NULL,
    acknowledged_at TEXT,
    resolved_at TEXT,
    status TEXT NOT NULL,               -- open | acknowledged | resolved
    resolution TEXT,                    -- auto | collected | escalated | operator
    fill_at_alert REAL,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_bin ON alerts(bin_id, kind);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bin_id TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- refresh
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL,               -- pending | delivered | done | expired
    result_fill REAL
);
CREATE INDEX IF NOT EXISTS idx_commands_bin ON commands(bin_id, id);
"""


class Database:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None
