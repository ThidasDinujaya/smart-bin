# database wrapper: MySQL in production (AWS RDS or any MySQL server).
# SQLite is only used when DB_PATH is set, so the tests run without a MySQL server.
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from .config import Settings

log = logging.getLogger("smartbin")

# MySQL tables (ISO timestamps are stored as text so they sort correctly)
MYSQL_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS bins (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        location VARCHAR(255) NOT NULL DEFAULT '',
        depth_cm DOUBLE NOT NULL,
        created_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS readings (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        bin_id VARCHAR(64) NOT NULL,
        ts VARCHAR(40) NOT NULL,
        received_at VARCHAR(40) NOT NULL,
        distance_cm DOUBLE NOT NULL,
        fill_pct DOUBLE NULL,
        `trigger` VARCHAR(20) NOT NULL,
        lid_opens INT NOT NULL DEFAULT 0,
        battery_pct DOUBLE NULL,
        command_id BIGINT NULL,
        INDEX idx_readings_bin (bin_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS collections (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        bin_id VARCHAR(64) NOT NULL,
        ts VARCHAR(40) NOT NULL,
        fill_before DOUBLE NULL,
        fill_after DOUBLE NULL,
        INDEX idx_collections_bin (bin_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alerts (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        bin_id VARCHAR(64) NOT NULL,
        kind VARCHAR(20) NOT NULL,
        started_at VARCHAR(40) NOT NULL,
        acknowledged_at VARCHAR(40) NULL,
        resolved_at VARCHAR(40) NULL,
        status VARCHAR(20) NOT NULL,
        resolution VARCHAR(20) NULL,
        fill_at_alert DOUBLE NULL,
        message TEXT NULL,
        INDEX idx_alerts_bin (bin_id, kind)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS commands (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        bin_id VARCHAR(64) NOT NULL,
        kind VARCHAR(20) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        delivered_at VARCHAR(40) NULL,
        completed_at VARCHAR(40) NULL,
        status VARCHAR(20) NOT NULL,
        result_fill DOUBLE NULL,
        INDEX idx_commands_bin (bin_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]

# same tables for SQLite (tests only)
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bins (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
    depth_cm REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bin_id TEXT NOT NULL, ts TEXT NOT NULL,
    received_at TEXT NOT NULL, distance_cm REAL NOT NULL, fill_pct REAL,
    `trigger` TEXT NOT NULL, lid_opens INTEGER NOT NULL DEFAULT 0, battery_pct REAL, command_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_readings_bin ON readings(bin_id, id);
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bin_id TEXT NOT NULL, ts TEXT NOT NULL,
    fill_before REAL, fill_after REAL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bin_id TEXT NOT NULL, kind TEXT NOT NULL,
    started_at TEXT NOT NULL, acknowledged_at TEXT, resolved_at TEXT, status TEXT NOT NULL,
    resolution TEXT, fill_at_alert REAL, message TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_bin ON alerts(bin_id, kind);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bin_id TEXT NOT NULL, kind TEXT NOT NULL,
    created_at TEXT NOT NULL, delivered_at TEXT, completed_at TEXT, status TEXT NOT NULL, result_fill REAL
);
CREATE INDEX IF NOT EXISTS idx_commands_bin ON commands(bin_id, id);
"""


class Database:
    def __init__(self, cfg: Settings):
        self._lock = threading.RLock()
        self.mysql = bool(cfg.db_host)
        if self.mysql:
            self._open_mysql(cfg)
        elif cfg.db_path:
            os.makedirs(os.path.dirname(os.path.abspath(cfg.db_path)), exist_ok=True)
            self._conn = sqlite3.connect(cfg.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.executescript(SQLITE_SCHEMA)
                self._conn.commit()
        else:
            raise RuntimeError("No database configured: set DB_HOST, DB_USER, DB_PASSWORD and DB_NAME")

    def _open_mysql(self, cfg: Settings) -> None:
        import pymysql
        from pymysql.cursors import DictCursor

        if not re.fullmatch(r"[A-Za-z0-9_]+", cfg.db_name):
            raise RuntimeError("DB_NAME may only contain letters, numbers and underscores")
        self._pymysql = pymysql
        args = dict(host=cfg.db_host, port=cfg.db_port, user=cfg.db_user, password=cfg.db_password,
                    charset="utf8mb4", cursorclass=DictCursor, autocommit=True, connect_timeout=10)
        last = None
        for attempt in range(1, 31):  # the database may still be starting
            try:
                conn = pymysql.connect(**args)
                break
            except pymysql.err.OperationalError as exc:
                last = exc
                log.warning("MySQL not ready (%s), retry %d/30", exc, attempt)
                time.sleep(2)
        else:
            raise RuntimeError(f"Cannot connect to MySQL at {cfg.db_host}:{cfg.db_port}: {last}")
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{cfg.db_name}` CHARACTER SET utf8mb4")
        conn.select_db(cfg.db_name)
        with conn.cursor() as cur:
            for statement in MYSQL_SCHEMA:
                cur.execute(statement)
        self._conn = conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # services write ? placeholders, MySQL wants %s
    def _sql(self, sql: str) -> str:
        return sql.replace("%", "%%").replace("?", "%s") if self.mysql else sql

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            if self.mysql:
                self._conn.ping(reconnect=True)
                with self._conn.cursor() as cur:
                    cur.execute(self._sql(sql), params)
                    return cur.lastrowid
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            if self.mysql:
                self._conn.ping(reconnect=True)
                with self._conn.cursor() as cur:
                    cur.execute(self._sql(sql), params)
                    return list(cur.fetchall())
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None
