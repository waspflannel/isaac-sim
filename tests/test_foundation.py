import json
import sqlite3
from unittest.mock import MagicMock

import psycopg
import pytest
from fastapi.testclient import TestClient

from factory_intelligence.api import app
from factory_intelligence.collector import capture
from factory_intelligence.simulator import simulate


def test_simulated_events_are_durable_and_replay_is_idempotent(tmp_path):
    path = tmp_path / "spool.sqlite3"
    events = simulate(3, 60, "test-run")
    assert [event["simulation"]["time_ns"] for event in events] == [
        60_000_000_000,
        120_000_000_000,
        180_000_000_000,
    ]
    assert len({event["unit_id"] for event in events}) == 3
    assert all(capture(path, event) for event in events)
    assert not any(capture(path, event) for event in events)
    with pytest.raises(ValueError, match="different content"):
        capture(path, {**events[0], "result": "fail"})
    with sqlite3.connect(path) as conn:
        saved = conn.execute("SELECT payload FROM spool ORDER BY event_id").fetchall()
    assert [json.loads(row[0]) for row in saved] == events


@pytest.mark.parametrize("event", [[], {}, {"event_id": " "}, {"event_id": "x", "x": float("nan")}])
def test_invalid_capture_is_rejected(tmp_path, event):
    with pytest.raises(ValueError):
        capture(tmp_path / "spool.sqlite3", event)


@pytest.mark.parametrize("units,seconds", [(0, 60), (10_001, 60), (1, -1), (1, float("nan"))])
def test_invalid_simulation_settings(units, seconds):
    with pytest.raises(ValueError):
        simulate(units, seconds)


def test_readiness_reports_database_outage_without_leaking_details(monkeypatch):
    client = TestClient(app)
    assert client.get("/api/health").json()["status"] == "ok"
    connect = MagicMock()
    monkeypatch.setattr(psycopg, "connect", connect)
    assert client.get("/api/ready").json()["database"] == "connected"
    connect.side_effect = psycopg.OperationalError("private connection details")
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unavailable"}
