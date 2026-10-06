import json
import os
from contextlib import closing
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_edge_agent import append, observation

from factory_intelligence.api import app
from factory_intelligence.edge.agent import Agent
from factory_intelligence.edge.brain import Brain
from factory_intelligence.edge.contracts import AgentConfig, Connector
from factory_intelligence.edge.routes import brain


@pytest.fixture
def connection(monkeypatch):
    if os.environ.get("EDGE_TEST_POSTGRES") != "1":
        pytest.skip("Set EDGE_TEST_POSTGRES=1 with PG* variables for isolated PostgreSQL tests")
    schema = "edge_test_" + uuid4().hex
    with psycopg.connect() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repository = Brain(make_conninfo(options=f"-c search_path={schema}"))
    repository.initialize()
    app.dependency_overrides[brain] = lambda: repository
    monkeypatch.setenv("FACTORY_BRAIN_ADMIN_TOKEN", "operator-test")
    try:
        with TestClient(app) as client:
            yield repository, client
    finally:
        app.dependency_overrides.clear()
        with psycopg.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


ADMIN = {"Authorization": "Bearer operator-test"}


def enroll(client, agent_id="edge-test"):
    response = client.post(
        "/api/agents",
        headers=ADMIN,
        json={"agent_id": agent_id, "site_id": "virtual-factory", "station_prefixes": ["Line01"]},
    )
    assert response.status_code == 201, response.text
    return response.json(), {"Authorization": "Bearer " + response.json()["token"]}


def test_authenticated_partial_batch_durability_duplicates_and_scope(connection):
    repository, client = connection
    registration, headers = enroll(client)
    outside = {**observation(3), "station_id": "Line02/Test"}
    payload = {"events": [observation(1), {**observation(2), "sequence": -1}, outside]}
    response = client.post("/api/edge/edge-test/events", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == [observation(1)["event_id"]]
    assert len(response.json()["rejected"]) == 2
    assert (
        client.post("/api/edge/edge-test/events", headers=headers, json=payload).json()
        == response.json()
    )
    assert len(repository.events()) == 1
    conflict = client.post(
        "/api/edge/edge-test/events",
        headers=headers,
        json={"events": [observation(1, state="blocked")]},
    )
    assert "different content" in next(iter(conflict.json()["rejected"].values()))
    assert client.get("/api/events", headers=headers).status_code == 403
    _, other = enroll(client, "other")
    assert client.post("/api/edge/edge-test/events", headers=other, json=payload).status_code == 403
    history = client.get("/api/events?unit_id=run:unit-1", headers=ADMIN).json()
    assert len(history["records"]) == 1
    assert history["records"][0]["checksum"]
    assert history["records"][0]["received_at"]
    assert registration["token"] not in json.dumps(client.get("/api/agents", headers=ADMIN).json())


def test_agent_brain_lost_ack_restart_configuration_command_and_revocation(
    connection, tmp_path, monkeypatch
):
    repository, client = connection
    registration, headers = enroll(client)
    monkeypatch.setenv("FACTORY_EDGE_TOKEN", registration["token"])
    source = tmp_path / "source"
    source.mkdir()
    append(source / "run.ndjson", observation(1), observation(2))
    config = AgentConfig(
        agent_id="edge-test",
        spool=str(tmp_path / "spool.sqlite3"),
        connectors=[Connector(connector_id="source", location=str(source))],
    ).model_dump()

    def request(url, token, *, method="GET", body=None):
        path = "/api/" + url.split("/api/", 1)[1]
        response = client.request(
            method, path, headers={"Authorization": "Bearer " + token}, json=body
        )
        response.raise_for_status()
        return response.json()

    def lost_ack(url, token, **kwargs):
        request(url, token, **kwargs)
        raise OSError("Lost response after durable commit")

    with closing(Agent(config)) as agent:
        agent.collect()
        with pytest.raises(OSError):
            agent.exchange(lost_ack)
        assert len(repository.events()) == 2
        assert len(agent.store.pending(10)) == 2
    new_settings = registration["settings"] | {"revision": 2}
    new_settings["rules"]["blocked_seconds"] = 10
    assert (
        client.put("/api/agents/edge-test/settings", headers=ADMIN, json=new_settings).status_code
        == 200
    )
    command = {
        "command_id": "evidence-request",
        "kind": "evidence",
        "event_ids": [observation(1)["event_id"], "missing"],
    }
    client.post("/api/agents/edge-test/commands", headers=ADMIN, json=command).raise_for_status()
    with closing(Agent(config)) as agent:
        assert agent.collect() == 0
        agent.exchange(request)
        assert agent.store.pending(10) == []
        assert agent.store.get("settings")["rules"]["blocked_seconds"] == 10
        assert len(repository.events()) == 2
    result = client.post("/api/agents/edge-test/commands", headers=ADMIN, json=command).json()[
        "result"
    ]
    assert result["result"]["missing"] == ["missing"]
    assert len(result["result"]["events"]) == 1
    assert (
        client.post(
            "/api/agents/edge-test/commands",
            headers=ADMIN,
            json={"command_id": "unsafe", "kind": "machine.stop"},
        ).status_code
        == 422
    )
    client.post("/api/agents/edge-test/revoke", headers=ADMIN).raise_for_status()
    assert client.post("/api/edge/edge-test/heartbeat", headers=headers, json={}).status_code == 403
    rotated = client.post("/api/agents/edge-test/rotate-token", headers=ADMIN).json()
    assert rotated["token"] != registration["token"]
    assert repository.authorize("edge-test", registration["token"]) is None
    assert repository.authorize("edge-test", rotated["token"])


def test_database_failure_does_not_acknowledge_records(connection, monkeypatch):
    repository, client = connection
    _, headers = enroll(client)
    original = repository.connect
    calls = 0

    def unavailable():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise psycopg.OperationalError("private connection details")
        return original()

    monkeypatch.setattr(repository, "connect", unavailable)
    response = client.post(
        "/api/edge/edge-test/events", headers=headers, json={"events": [observation(1)]}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
