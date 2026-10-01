"""Durable NDJSON capture foundation; forwarding and canonical validation come in D1."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def capture(path, event):
    if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
        raise ValueError("event must be an object with a string event_id")
    if not event["event_id"].strip():
        raise ValueError("event_id must not be empty")
    payload = json.dumps(event, allow_nan=False, sort_keys=True, separators=(",", ":"))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS spool ("
            "event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
            "captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        cursor = conn.execute(
            "INSERT OR IGNORE INTO spool (event_id, payload) VALUES (?, ?)",
            (event["event_id"], payload),
        )
        inserted = cursor.rowcount == 1
        saved = conn.execute(
            "SELECT payload FROM spool WHERE event_id = ?", (event["event_id"],)
        ).fetchone()[0]
        if saved != payload:
            raise ValueError("event_id was reused with different content")
    # Returning only after the transaction commits is the local durability boundary.
    return inserted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spool", type=Path, default=Path(".data/edge/spool.sqlite3"))
    args = parser.parse_args()
    captured = 0
    for line_number, line in enumerate(sys.stdin, start=1):
        try:
            captured += capture(args.spool, json.loads(line))
        except (ValueError, sqlite3.Error, OSError) as exc:
            parser.exit(1, f"Capture failed at line {line_number}: {exc}\n")
    print(json.dumps({"captured": captured}))
