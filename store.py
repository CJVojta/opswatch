"""Persistent monitor configuration, check history, and incident transitions."""

from contextlib import closing, contextmanager
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL,
    interval INTEGER NOT NULL, timeout INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'unknown',
    failures INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
    next_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY, monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    checked_at INTEGER NOT NULL, ok INTEGER NOT NULL,
    status_code INTEGER, latency_ms INTEGER NOT NULL, detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS checks_history ON checks(monitor_id, checked_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY, monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    opened_at INTEGER NOT NULL, resolved_at INTEGER, reason TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS incidents_active ON incidents(monitor_id) WHERE resolved_at IS NULL;
"""


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def create(self, name, url, interval, timeout):
        with self.connect() as db:
            row = db.execute(
                "INSERT INTO monitors (name, url, interval, timeout) VALUES (?, ?, ?, ?)",
                (name, url, interval, timeout),
            )
            return row.lastrowid

    def delete(self, monitor_id):
        with self.connect() as db:
            return db.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,)).rowcount == 1

    def due(self, now):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM monitors WHERE next_at <= ? ORDER BY next_at, id", (now,))
            return [dict(row) for row in rows]

    def record(self, monitor_id, checked_at, ok, code, latency, detail):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            monitor = db.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,)).fetchone()
            if monitor is None:
                return
            db.execute(
                "INSERT INTO checks (monitor_id, checked_at, ok, status_code, latency_ms, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (monitor_id, checked_at, ok, code, latency, detail),
            )
            failures = 0 if ok else monitor["failures"] + 1
            successes = monitor["successes"] + 1 if ok else 0
            state = monitor["state"]
            if state != "down" and failures == 2:
                state = "down"
                db.execute(
                    "INSERT INTO incidents (monitor_id, opened_at, reason) VALUES (?, ?, ?)",
                    (monitor_id, checked_at, detail),
                )
            elif ok and (state == "unknown" or state == "down" and successes == 2):
                state = "up"
                db.execute(
                    "UPDATE incidents SET resolved_at = ? WHERE monitor_id = ? AND resolved_at IS NULL",
                    (checked_at, monitor_id),
                )
            db.execute(
                "UPDATE monitors SET state = ?, failures = ?, successes = ?, next_at = ? WHERE id = ?",
                (state, failures, successes, checked_at + monitor["interval"], monitor_id),
            )

    def prune(self, now):
        with self.connect() as db:
            db.execute("DELETE FROM checks WHERE checked_at < ?", (now - 30 * 86400,))

    def overview(self, now=None):
        cutoff = (int(time.time()) if now is None else now) - 86400
        with self.connect() as db:
            rows = db.execute("""
                WITH daily AS (
                    SELECT monitor_id, COUNT(*) samples, SUM(ok) successful,
                           ROUND(AVG(CASE WHEN ok THEN latency_ms END)) avg_ms
                    FROM checks WHERE checked_at >= ? GROUP BY monitor_id
                )
                SELECT m.id, m.name, m.url, m.interval, m.state, d.samples, d.avg_ms,
                       ROUND(100.0 * d.successful / d.samples, 1) uptime
                FROM monitors m LEFT JOIN daily d ON d.monitor_id = m.id
                ORDER BY m.id
            """, (cutoff,))
            monitors = [dict(row) for row in rows]
            for monitor in monitors:
                monitor["recent"] = self.recent(db, monitor["id"], 24)
            incidents = [dict(row) for row in db.execute("""
                SELECT i.*, m.name FROM incidents i JOIN monitors m ON m.id = i.monitor_id
                ORDER BY i.resolved_at IS NOT NULL, i.opened_at DESC, i.id DESC LIMIT 30
            """)]
        return {"monitors": monitors, "incidents": incidents}

    def history(self, monitor_id, limit):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM monitors WHERE id = ?", (monitor_id,)).fetchone() is None:
                return None
            return self.recent(db, monitor_id, limit)

    @staticmethod
    def recent(db, monitor_id, limit):
        rows = db.execute("""
            SELECT checked_at, ok, status_code, latency_ms, detail
            FROM checks WHERE monitor_id = ? ORDER BY checked_at DESC, id DESC LIMIT ?
        """, (monitor_id, limit))
        return [dict(row) for row in rows]
