# core logic: readings, alerts, refresh commands and the route
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .config import Settings
from .db import Database
from .logic import (fill_percent, fill_rate, hours_until, priority_key, priority_reason,
                    status_of, was_collected)

log = logging.getLogger("smartbin")


class CooldownError(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class UnknownBin(Exception):
    pass


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: Optional[datetime]) -> str:
    if ts is None:
        ts = _utc()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class BinService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.s = settings
        self._lock = threading.RLock()

    # bins
    def get_bin(self, bin_id: str) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM bins WHERE id=?", (bin_id,))

    def create_bin(self, bin_id: str, name: str, location: str, depth_cm: float) -> Dict[str, Any]:
        with self._lock:
            if self.get_bin(bin_id):
                raise ValueError("Bin already exists")
            self.db.execute("INSERT INTO bins(id, name, location, depth_cm, created_at) VALUES (?,?,?,?,?)",
                            (bin_id, name, location, depth_cm, _iso(None)))
            return self.get_bin(bin_id)

    def update_bin(self, bin_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.get_bin(bin_id):
                return None
            for key in ("name", "location", "depth_cm"):
                if fields.get(key) is not None:
                    self.db.execute(f"UPDATE bins SET {key}=? WHERE id=?", (fields[key], bin_id))
            return self.get_bin(bin_id)

    # alerts
    def _open_alert(self, bin_id: str, kind: str) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM alerts WHERE bin_id=? AND kind=? AND resolved_at IS NULL", (bin_id, kind))

    def _raise(self, bin_id: str, kind: str, when: str, fill: Optional[float], message: str) -> None:
        if self._open_alert(bin_id, kind) is None:
            self.db.execute("INSERT INTO alerts(bin_id, kind, started_at, status, fill_at_alert, message) "
                            "VALUES (?,?,?,?,?,?)", (bin_id, kind, when, "open", fill, message))
            log.warning("ALERT %s on %s: %s", kind, bin_id, message)

    def _resolve(self, bin_id: str, kinds: List[str], resolution: str, when: str) -> None:
        for kind in kinds:
            self.db.execute("UPDATE alerts SET status='resolved', resolved_at=?, resolution=? "
                            "WHERE bin_id=? AND kind=? AND resolved_at IS NULL", (when, resolution, bin_id, kind))

    # ingest
    def ingest(self, bin_id: str, distance_cm: float, trigger: str, lid_opens: int,
               battery_pct: Optional[float], command_id: Optional[int], ts: Optional[datetime]) -> Dict[str, Any]:
        with self._lock:
            b = self.get_bin(bin_id)
            if b is None:  # new device, register it so nothing is lost
                b = self.create_bin(bin_id, bin_id, "", self.s.default_depth_cm)
            when, received = _iso(ts), _iso(None)
            fill = fill_percent(distance_cm, b["depth_cm"], self.s.dead_zone_cm)
            prev = self.db.one("SELECT fill_pct FROM readings WHERE bin_id=? AND fill_pct IS NOT NULL "
                               "ORDER BY id DESC LIMIT 1", (bin_id,))
            prev_fill = prev["fill_pct"] if prev else None
            self.db.execute(
                "INSERT INTO readings(bin_id, ts, received_at, distance_cm, fill_pct, `trigger`, lid_opens, "
                "battery_pct, command_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (bin_id, when, received, distance_cm, fill, trigger, lid_opens, battery_pct, command_id))
            self._prune(bin_id)
            if command_id is not None:
                self.db.execute("UPDATE commands SET status='done', completed_at=?, result_fill=? "
                                "WHERE id=? AND bin_id=? AND status IN ('pending','delivered')",
                                (received, fill, command_id, bin_id))
            self._resolve(bin_id, ["offline"], "auto", received)

            if fill is None:
                self._raise(bin_id, "sensor_fault", when, None,
                            f"Impossible distance reading ({distance_cm} cm): sensor blocked or faulty")
                return {"bin_id": bin_id, "fill_pct": None, "status": "SENSOR_FAULT", "event": "sensor_fault"}
            self._resolve(bin_id, ["sensor_fault"], "auto", received)

            event = None
            if was_collected(prev_fill, fill, self.s.empty_drop_pct):
                self.db.execute("INSERT INTO collections(bin_id, ts, fill_before, fill_after) VALUES (?,?,?,?)",
                                (bin_id, when, prev_fill, fill))
                self._resolve(bin_id, ["nearly_full", "full"], "collected", received)
                event = "collected"
            elif fill >= self.s.full_pct:
                if self._open_alert(bin_id, "full") is None:
                    self._resolve(bin_id, ["nearly_full"], "escalated", received)
                    self._raise(bin_id, "full", when, fill, f"{b['name']} is {fill:.0f}% full")
                    event = "full"
            elif fill >= self.s.alert_pct:
                if self._open_alert(bin_id, "nearly_full") is None and self._open_alert(bin_id, "full") is None:
                    self._raise(bin_id, "nearly_full", when, fill, f"{b['name']} is {fill:.0f}% full")
                    event = "nearly_full"
            elif fill < self.s.alert_pct - self.s.hysteresis_pct:
                self._resolve(bin_id, ["nearly_full", "full"], "auto", received)
            view = self._view(self.get_bin(bin_id), _utc())
            return {"bin_id": bin_id, "fill_pct": fill, "status": view["status"], "event": event}

    def _prune(self, bin_id: str) -> None:
        self.db.execute(
            "DELETE FROM readings WHERE bin_id=? AND id <= "
            "(SELECT id FROM (SELECT id FROM readings WHERE bin_id=? ORDER BY id DESC LIMIT 1 OFFSET ?) AS keep_from)",
            (bin_id, bin_id, self.s.max_readings_per_bin))

    # views
    def _points_since_collection(self, bin_id: str):
        last = self.db.one("SELECT ts FROM collections WHERE bin_id=? ORDER BY id DESC LIMIT 1", (bin_id,))
        sql = "SELECT ts, fill_pct FROM readings WHERE bin_id=? AND fill_pct IS NOT NULL"
        params: list = [bin_id]
        if last:
            sql += " AND ts >= ?"
            params.append(last["ts"])
        rows = self.db.query(sql + " ORDER BY id DESC LIMIT 12", tuple(params))
        rows.reverse()
        if not rows:
            return []
        t0 = _parse(rows[0]["ts"])
        return [((_parse(r["ts"]) - t0).total_seconds() / 3600.0, r["fill_pct"]) for r in rows]

    def _view(self, b: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        latest = self.db.one("SELECT ts, received_at, distance_cm, fill_pct, `trigger`, lid_opens, battery_pct "
                             "FROM readings WHERE bin_id=? ORDER BY id DESC LIMIT 1", (b["id"],))
        valid = latest if (latest and latest["fill_pct"] is not None) else self.db.one(
            "SELECT fill_pct FROM readings WHERE bin_id=? AND fill_pct IS NOT NULL ORDER BY id DESC LIMIT 1",
            (b["id"],))
        fill = valid["fill_pct"] if valid else None
        stale = bool(latest) and (now - _parse(latest["received_at"])) > timedelta(minutes=self.s.stale_after_min)
        fault = self._open_alert(b["id"], "sensor_fault") is not None
        offline = stale or self._open_alert(b["id"], "offline") is not None
        status = status_of(fill, self.s.alert_pct, self.s.full_pct, latest is not None, fault, offline)
        rate = fill_rate(self._points_since_collection(b["id"])) if fill is not None else None
        self._expire_commands(now)
        pending = self.db.one("SELECT id, status, created_at FROM commands WHERE bin_id=? AND "
                              "status IN ('pending','delivered') ORDER BY id DESC LIMIT 1", (b["id"],))
        done = self.db.one("SELECT id, completed_at, result_fill FROM commands WHERE bin_id=? AND status='done' "
                           "ORDER BY id DESC LIMIT 1", (b["id"],))
        collections = self.db.one("SELECT COUNT(*) AS n FROM collections WHERE bin_id=?", (b["id"],))
        return {"id": b["id"], "name": b["name"], "location": b["location"], "depth_cm": b["depth_cm"],
                "status": status, "fill_pct": fill, "latest": latest,
                "rate_pct_per_hour": round(rate, 1) if rate is not None else None,
                "hours_to_alert": hours_until(self.s.alert_pct, fill, rate) if fill is not None else None,
                "hours_to_full": hours_until(self.s.full_pct, fill, rate) if fill is not None else None,
                "pending_command": pending, "last_refresh": done, "collections": collections["n"]}

    def bins(self) -> List[Dict[str, Any]]:
        now = _utc()
        return [self._view(b, now) for b in self.db.query("SELECT * FROM bins ORDER BY id")]

    def priority(self) -> Dict[str, List[Dict[str, Any]]]:
        views = self.bins()
        collect = [v for v in views if v["status"] in ("FULL", "NEARLY_FULL")
                   or (v["hours_to_full"] is not None and v["hours_to_full"] <= 8)]
        collect.sort(key=priority_key)
        route = [{"rank": i + 1, "id": v["id"], "name": v["name"], "location": v["location"],
                  "fill_pct": v["fill_pct"], "status": v["status"], "hours_to_full": v["hours_to_full"],
                  "reason": priority_reason(v)} for i, v in enumerate(collect)]
        check = [{"id": v["id"], "name": v["name"], "status": v["status"]}
                 for v in views if v["status"] in ("OFFLINE", "SENSOR_FAULT")]
        return {"collect": route, "check_device": check}

    def readings(self, bin_id: str, limit: int) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT r.ts, r.distance_cm, r.fill_pct, r.`trigger`, r.lid_opens, r.battery_pct, "
            "EXISTS(SELECT 1 FROM collections c WHERE c.bin_id=r.bin_id AND c.ts=r.ts) AS collected "
            "FROM readings r WHERE r.bin_id=? ORDER BY r.id DESC LIMIT ?", (bin_id, limit))
        rows.reverse()
        return rows

    def alerts(self, active_only: bool, limit: int) -> List[Dict[str, Any]]:
        where = "WHERE a.resolved_at IS NULL" if active_only else ""
        return self.db.query(
            f"SELECT a.*, b.name AS bin_name FROM alerts a LEFT JOIN bins b ON b.id=a.bin_id {where} "
            "ORDER BY a.id DESC LIMIT ?", (limit,))

    def acknowledge(self, alert_id: int) -> Optional[Dict[str, Any]]:
        a = self.db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))
        if a and a["resolved_at"] is None and a["status"] == "open":
            self.db.execute("UPDATE alerts SET status='acknowledged', acknowledged_at=? WHERE id=?",
                            (_iso(None), alert_id))
        return self.db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))

    def resolve(self, alert_id: int) -> Optional[Dict[str, Any]]:
        a = self.db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))
        if a and a["resolved_at"] is None:
            self.db.execute("UPDATE alerts SET status='resolved', resolved_at=?, resolution='operator' WHERE id=?",
                            (_iso(None), alert_id))
        return self.db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))

    # refresh commands
    def _expire_commands(self, now: datetime) -> None:
        cutoff = _iso(now - timedelta(seconds=self.s.command_ttl_sec))
        self.db.execute("UPDATE commands SET status='expired' WHERE status IN ('pending','delivered') "
                        "AND created_at < ?", (cutoff,))

    # user wants a fresh reading right now
    def refresh(self, bin_id: str) -> Dict[str, Any]:
        with self._lock:
            if not self.get_bin(bin_id):
                raise UnknownBin()
            now = _utc()
            self._expire_commands(now)
            pending = self.db.one("SELECT * FROM commands WHERE bin_id=? AND status IN ('pending','delivered') "
                                  "ORDER BY id DESC LIMIT 1", (bin_id,))
            if pending:
                return pending
            last = self.db.one("SELECT created_at FROM commands WHERE bin_id=? ORDER BY id DESC LIMIT 1", (bin_id,))
            if last:
                wait = self.s.refresh_cooldown_sec - (now - _parse(last["created_at"])).total_seconds()
                if wait > 0:
                    raise CooldownError(int(wait) + 1)
            cid = self.db.execute("INSERT INTO commands(bin_id, kind, created_at, status) VALUES (?,?,?,?)",
                                  (bin_id, "refresh", _iso(now), "pending"))
            return self.db.one("SELECT * FROM commands WHERE id=?", (cid,))

    def next_command(self, bin_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.get_bin(bin_id):
                raise UnknownBin()
            self._expire_commands(_utc())
            cmd = self.db.one("SELECT * FROM commands WHERE bin_id=? AND status='pending' ORDER BY id LIMIT 1",
                              (bin_id,))
            if cmd:
                self.db.execute("UPDATE commands SET status='delivered', delivered_at=? WHERE id=?",
                                (_iso(None), cmd["id"]))
                return {"id": cmd["id"], "kind": cmd["kind"]}
            return None

    def command(self, command_id: int) -> Optional[Dict[str, Any]]:
        self._expire_commands(_utc())
        return self.db.one("SELECT * FROM commands WHERE id=?", (command_id,))

    # watchdog and summary
    # raise an alert for bins that stopped reporting
    def check_offline(self, now: Optional[datetime] = None) -> int:
        now = now or _utc()
        raised = 0
        with self._lock:
            for b in self.db.query("SELECT id, name FROM bins"):
                last = self.db.one("SELECT received_at FROM readings WHERE bin_id=? ORDER BY id DESC LIMIT 1", (b["id"],))
                if last and (now - _parse(last["received_at"])) > timedelta(minutes=self.s.stale_after_min):
                    if self._open_alert(b["id"], "offline") is None:
                        self._raise(b["id"], "offline", _iso(now), None,
                                    f"{b['name']} has not reported for over {self.s.stale_after_min:.0f} minutes")
                        raised += 1
        return raised

    def summary(self) -> Dict[str, Any]:
        views = self.bins()
        counts: Dict[str, int] = {}
        for v in views:
            counts[v["status"]] = counts.get(v["status"], 0) + 1
        sent = polling = 0
        for row in self.db.query("SELECT bin_id, COUNT(*) AS n, MIN(ts) AS a, MAX(ts) AS z FROM readings GROUP BY bin_id"):
            span_min = (_parse(row["z"]) - _parse(row["a"])).total_seconds() / 60.0
            sent += row["n"]
            polling += int(span_min / self.s.poll_baseline_min) + 1
        by_trigger = {r["trigger"]: r["n"] for r in self.db.query("SELECT `trigger`, COUNT(*) AS n FROM readings GROUP BY `trigger`")}
        saved = round((1 - sent / polling) * 100, 1) if polling > 0 and sent <= polling else 0.0
        active = self.db.one("SELECT COUNT(*) AS n FROM alerts WHERE resolved_at IS NULL")
        coll = self.db.one("SELECT COUNT(*) AS n FROM collections")
        return {"bins": len(views), "status_counts": counts, "need_collection": len(self.priority()["collect"]),
                "active_alerts": active["n"], "collections_detected": coll["n"],
                "readings_sent": sent, "polling_equivalent": polling, "saved_pct": saved,
                "readings_by_trigger": by_trigger, "poll_baseline_min": self.s.poll_baseline_min}
