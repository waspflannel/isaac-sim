"""One SQLite transaction covers the observation, detections, and source checkpoint."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path


def utc_now():
    return datetime.now(UTC).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                captured_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                acknowledged_at TEXT, rejection TEXT
            );
            CREATE INDEX IF NOT EXISTS pending_events ON events(status);
            CREATE TABLE IF NOT EXISTS seen (event_id TEXT PRIMARY KEY, checksum TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS positions (
                station TEXT, session TEXT, sequence INTEGER, event_id TEXT,
                PRIMARY KEY(station,session,sequence)
            );
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quarantine (
                id INTEGER PRIMARY KEY, source TEXT, raw TEXT, reason TEXT,
                captured_at TEXT NOT NULL
            );
        """)

    @contextmanager
    def transaction(self):
        with self.db:
            yield self

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute(
            "INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, encode(value)),
        )

    def capture(self, event):
        payload = encode(event)
        checksum = sha256(payload.encode()).hexdigest()
        old = self.db.execute(
            "SELECT checksum FROM seen WHERE event_id=?", (event["event_id"],)
        ).fetchone()
        if old:
            if old[0] != checksum:
                raise ValueError("event_id reused with different content")
            return False
        if event["origin"] != "edge":
            position = (event["station_id"], event["producer_session"], event["sequence"])
            old_position = self.db.execute(
                "SELECT event_id FROM positions WHERE station=? AND session=? AND sequence=?",
                position,
            ).fetchone()
            if old_position:
                raise ValueError("Source sequence reused by a different event_id")
            self.db.execute(
                "INSERT INTO positions VALUES (?,?,?,?)", (*position, event["event_id"])
            )
        self.db.execute("INSERT INTO seen VALUES (?,?)", (event["event_id"], checksum))
        self.db.execute(
            "INSERT INTO events(event_id,payload,captured_at) VALUES (?,?,?)",
            (event["event_id"], payload, utc_now()),
        )
        return True

    def quarantine(self, source, raw, reason):
        self.db.execute(
            "INSERT INTO quarantine(source,raw,reason,captured_at) VALUES (?,?,?,?)",
            (source, raw, reason, utc_now()),
        )

    def pending(self, limit):
        records, size = [], 0
        for row in self.db.execute(
            "SELECT payload FROM events WHERE status='pending' ORDER BY rowid LIMIT ?", (limit,)
        ):
            size += len(row[0].encode())
            if records and size > 4 * 1024 * 1024:
                break
            records.append(json.loads(row[0]))
        return records

    def acknowledge(self, response, submitted):
        # Only explicit per-record receipts retire events; lost or partial replies are retryable.
        for event_id in response.get("accepted", []):
            if event_id in submitted:
                self.db.execute(
                    "UPDATE events SET status='accepted',acknowledged_at=? WHERE event_id=?",
                    (utc_now(), event_id),
                )
        for event_id, reason in response.get("rejected", {}).items():
            if event_id in submitted:
                self.db.execute(
                    "UPDATE events SET status='rejected',rejection=? WHERE event_id=?",
                    (reason, event_id),
                )

    def evidence(self, ids):
        records = []
        omitted, size = [], 0
        for event_id in ids:
            row = self.db.execute(
                "SELECT payload FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if row:
                size += len(row[0].encode())
                if size > 4 * 1024 * 1024:
                    omitted.append(event_id)
                else:
                    records.append(json.loads(row[0]))
        return {
            "events": records,
            "missing": sorted(set(ids) - {e["event_id"] for e in records} - set(omitted)),
            "omitted_for_response_limit": omitted,
        }

    def health(self):
        counts = dict(self.db.execute("SELECT status,count(*) FROM events GROUP BY status"))
        return {
            "events": counts,
            "quarantined": self.db.execute("SELECT count(*) FROM quarantine").fetchone()[0],
            "oldest_pending": self.db.execute(
                "SELECT min(captured_at) FROM events WHERE status='pending'"
            ).fetchone()[0],
            "bytes": sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*")),
            "live_bytes": (
                self.db.execute("PRAGMA page_count").fetchone()[0]
                - self.db.execute("PRAGMA freelist_count").fetchone()[0]
            )
            * self.db.execute("PRAGMA page_size").fetchone()[0],
        }

    def prune(self, days):
        self.db.execute(
            "DELETE FROM events WHERE status='accepted' AND julianday(acknowledged_at) "
            "< julianday('now') - ?",
            (days,),
        )

    def close(self):
        self.db.close()
