import json
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from test_edge_agent import append, observation

from factory_intelligence.edge.agent import Agent
from factory_intelligence.edge.contracts import AgentConfig, Connector
from factory_intelligence.edge.transport import request_json


def test_http_retained_pages_preview_auth_and_cursor_restart(tmp_path, monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            page = {
                "events": [] if "cursor=next" in self.path else [observation(1)],
                "next_cursor": "next",
            }
            body = json.dumps(page).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SOURCE_TOKEN", "source-secret")
    config = AgentConfig(
        agent_id="http-edge",
        spool=str(tmp_path / "http.sqlite3"),
        connectors=[
            Connector(
                connector_id="tester",
                kind="http_events",
                location=f"http://127.0.0.1:{server.server_port}/events",
                token_env="SOURCE_TOKEN",
            )
        ],
    ).model_dump()
    try:
        with closing(Agent(config)) as agent:
            assert agent.collect() == 1
        with closing(Agent(config)) as agent:
            assert agent.collect() == 0
            assert len(agent.store.pending(100)) == 1
        assert "cursor=next" in requests[-1][0]
        assert all(token == "Bearer source-secret" for _, token in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_insecure_remote_transport_is_refused_without_connecting():
    with pytest.raises(ValueError, match="HTTPS"):
        request_json("http://example.invalid/events", "secret")


def test_scoped_collection_and_future_operation_capture_survive_restart(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    config = AgentConfig(
        agent_id="line-edge",
        spool=str(tmp_path / "edge.sqlite3"),
        connectors=[
            Connector(connector_id="journal", location=str(source), station_prefixes=["Line01"])
        ],
    ).model_dump()
    path = source / "run.ndjson"
    command = {"command_id": "next-test", "kind": "capture_operations", "station_id": "Line01/Test"}
    with closing(Agent(config)) as agent:
        assert agent.execute(command) is None
        append(
            path,
            observation(1, "operation.started"),
            {**observation(1), "event_id": "other", "station_id": "Line02/Test"},
        )
        assert agent.collect() == 1
    with closing(Agent(config)) as agent:
        append(
            path,
            observation(2, "measurement.recorded", name="voltage", value=24, unit="V"),
            observation(3, "operation.completed"),
        )
        assert agent.collect() == 2
        result = agent.execute(command)
        assert result["status"] == "completed"
        assert len(result["result"]["event_ids"]) == 3
        assert result == agent.execute(command)
        with pytest.raises(RuntimeError, match="already owns"):
            Agent(config)


def test_unknown_equipment_data_is_quarantined_without_stopping_collection(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    config = AgentConfig(
        agent_id="line-edge",
        spool=str(tmp_path / "edge.sqlite3"),
        connectors=[Connector(connector_id="journal", location=str(source))],
    ).model_dump()
    append(
        source / "run.ndjson",
        observation(1, queue=None),
        observation(2, "measurement.recorded", value="bad"),
        observation(3, state="idle"),
    )
    with closing(Agent(config)) as agent:
        assert agent.collect() == 1
        assert agent.store.health()["quarantined"] == 2
        assert any(e["data"].get("rule") == "sequence_gap" for e in agent.store.pending(10))
