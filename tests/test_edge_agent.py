import json
import time
from contextlib import closing

import pytest
import simpy

from factory_intelligence.edge.agent import Agent
from factory_intelligence.edge.contracts import AgentConfig, Connector
from factory_intelligence.edge.journal import FactoryJournal
from factory_intelligence.edge.store import Store
from factory_intelligence.production import Production


def observation(sequence, kind="station.observed", seconds=None, **data):
    return {
        "schema_version": 1,
        "event_id": f"session:Line01/Test:{sequence}",
        "event_type": kind,
        "site_id": "virtual-factory",
        "station_id": "Line01/Test",
        "producer_session": "session",
        "sequence": sequence,
        "origin": "simulation",
        "clock_domain": "simulation",
        "time_seconds": sequence if seconds is None else seconds,
        "run_id": "run",
        "epoch": 1,
        "unit_id": "run:unit-1",
        "operation_id": "run:unit-1:test:1",
        "data": data,
    }


@pytest.fixture
def edge(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    config = AgentConfig(
        agent_id="edge-test",
        spool=str(tmp_path / "spool.sqlite3"),
        connectors=[Connector(connector_id="source", location=str(source))],
    )
    with closing(Agent(config.model_dump())) as agent:
        yield agent, source / "run.ndjson"


def append(path, *events):
    with path.open("a", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event) + "\n")


def incidents(agent, rule):
    return [
        e
        for e in agent.store.pending(1000)
        if e["event_type"] == "incident.changed" and e["data"]["rule"] == rule
    ]


def test_restart_retains_checkpoint_detector_and_recovers_without_alert_flood(edge):
    agent, path = edge
    append(
        path,
        observation(1, "operation.started", 0),
        observation(2, seconds=65),
        observation(3, seconds=66),
    )
    assert agent.collect() == 3
    assert len(incidents(agent, "operation_overdue")) == 1
    config = agent.config
    agent.close()
    with closing(Agent(config)) as restarted:
        assert restarted.collect() == 0
        append(path, observation(4, "operation.completed", 70))
        assert restarted.collect() == 1
        findings = incidents(restarted, "operation_overdue")
        assert [f["data"]["status"] for f in findings] == ["open", "resolved"]
        assert findings[0]["data"]["incident_id"] == findings[1]["data"]["incident_id"]


def test_partial_lines_conflicts_and_invalid_records_do_not_lose_following_events(edge):
    agent, path = edge
    append(path, observation(1), observation(1, changed=True), {"not": "an event"})
    with path.open("a") as file:
        file.write(json.dumps(observation(2))[:-1])
    assert agent.collect() == 1
    assert agent.store.health()["quarantined"] == 2
    with path.open("a") as file:
        file.write("}\n")
    assert agent.collect() == 1
    assert agent.store.health()["quarantined"] == 2


def test_sequence_gap_resolves_on_late_delivery_without_rewinding_station(edge):
    agent, path = edge
    append(
        path, observation(1, seconds=1, state="idle"), observation(3, seconds=3, state="processing")
    )
    agent.collect()
    append(path, observation(2, seconds=2, state="blocked"))
    agent.collect()
    assert [e["data"]["status"] for e in incidents(agent, "sequence_gap")] == ["open", "resolved"]
    assert agent.store.get("equipment:run:1:Line01/Test")["state"] == "processing"


def test_blockage_quality_measurement_and_handling_have_evidence_and_recovery(edge):
    agent, path = edge
    with agent.store.transaction():
        settings = agent.store.get("settings")
        settings["rules"]["failure_window"] = 2
        settings["rules"]["failure_rate"] = 0.5
        agent.store.set("settings", settings)
    append(
        path,
        observation(1, seconds=0, state="blocked", queue=3),
        observation(2, seconds=25, state="blocked", queue=3),
        observation(3, seconds=26, state="idle", queue=0),
        observation(4, "quality.result", 27, result="fail"),
        observation(5, "quality.result", 28, result="pass"),
        observation(6, "quality.result", 29, result="pass"),
        observation(
            7, "measurement.recorded", 30, name="voltage", value=9, unit="V", upper_limit=5
        ),
        observation(
            8, "measurement.recorded", 31, name="voltage", value=4, unit="V", upper_limit=5
        ),
        observation(9, "handling.failed", 32, reason="missed grip"),
        observation(10, "handling.completed", 33, placement_error=0.001),
    )
    assert agent.collect() == 10
    for rule in (
        "blocked",
        "queue_pressure",
        "failure_rate",
        "measurement_limit/voltage",
        "handling_failure",
    ):
        records = incidents(agent, rule)
        assert [r["data"]["status"] for r in records] == ["open", "resolved"]
        assert all(r["data"]["evidence_ids"] for r in records)


def test_acknowledgement_loss_partial_acceptance_and_command_retries(edge, monkeypatch):
    agent, path = edge
    monkeypatch.setenv("FACTORY_EDGE_TOKEN", "test")
    append(path, observation(1), observation(2))
    agent.collect()
    delivered = []

    def lost(url, token, **kwargs):
        delivered.extend(kwargs["body"]["events"])
        raise OSError("response lost after server commit")

    with pytest.raises(OSError):
        agent.exchange(lost)
    assert len(agent.store.pending(100)) == 2
    request = {"command_id": "diagnose", "kind": "diagnostics"}
    command_results = []

    def response(url, token, **kwargs):
        if url.endswith("/events"):
            assert kwargs["body"]["events"] == delivered
            return {"accepted": [delivered[0]["event_id"]], "rejected": {}}
        if url.endswith("/heartbeat"):
            return {
                "settings": {**agent.store.get("settings"), "revision": 2},
                "commands": [request],
            }
        command_results.append(kwargs["body"])
        return {"accepted": "diagnose"}

    agent.exchange(response)
    assert len(agent.store.pending(100)) == 1
    assert agent.store.get("settings")["revision"] == 2
    assert agent.execute(request) == command_results[0]
    assert "token" not in json.dumps(command_results[0])


def test_retention_keeps_replay_protection_and_never_deletes_pending(edge):
    agent, path = edge
    append(path, observation(1), observation(2))
    agent.collect()
    first = agent.store.pending(100)[0]
    with agent.store.transaction():
        agent.store.db.execute(
            "UPDATE events SET status='accepted',acknowledged_at='2000-01-01' WHERE event_id=?",
            (first["event_id"],),
        )
        agent.store.prune(7)
        assert not agent.store.capture(first)
    assert agent.store.health()["events"] == {"pending": 1}
    assert agent.store.evidence([first["event_id"]])["missing"] == [first["event_id"]]


def test_source_silence_is_wall_time_and_does_not_change_machine_state(edge, monkeypatch):
    agent, path = edge
    append(path, observation(1, seconds=900000, state="idle"))
    agent.collect()
    future = time.time() + 31
    monotonic_future = time.monotonic() + 31
    monkeypatch.setattr("factory_intelligence.edge.agent.time.time", lambda: future)
    monkeypatch.setattr("factory_intelligence.edge.agent.time.monotonic", lambda: monotonic_future)
    agent.collect()
    assert agent.health()["connections"]["source"]["status"] == "stale"
    assert agent.store.get("equipment:run:1:Line01/Test")["state"] == "idle"
    assert incidents(agent, "connection")[0]["clock_domain"] == "unix"


def test_conversion_preserves_raw_evidence_and_limits(edge):
    agent, path = edge
    connector = agent.config["connectors"][0]
    connector["conversions"] = {
        "voltage": {"raw_unit": "mV", "unit": "V", "factor": 0.001, "offset": 0}
    }
    connector["mapping_revision"] = "mv-v-2"
    append(
        path,
        observation(
            1,
            "measurement.recorded",
            name="voltage",
            value=61.2,
            unit="mV",
            lower_limit=0,
            upper_limit=80,
        ),
    )
    agent.collect()
    record = agent.store.pending(10)[0]
    assert record["data"]["value"] == pytest.approx(0.0612)
    assert record["data"]["upper_limit"] == 0.08
    assert record["data"]["raw_value"] == 61.2
    assert record["data"]["source_record"]["data"]["unit"] == "mV"


def test_transaction_rolls_back_source_checkpoint_and_observation_on_detector_error(
    edge, monkeypatch
):
    agent, path = edge
    append(path, observation(1))

    def fail(*args):
        raise RuntimeError("interrupted transaction")

    monkeypatch.setattr(agent.detector, "observe", fail)
    with pytest.raises(RuntimeError):
        agent.collect()
    assert agent.store.pending(10) == []
    assert agent.store.get("cursor:source:run.ndjson") is None


def test_full_production_journal_reconciles_units_and_attempts(tmp_path):
    env = simpy.Environment()
    with closing(FactoryJournal(tmp_path / "source", run_id="production-test")) as journal:
        factory = Production(env, on_event=journal.observe)
        journal.factory = factory
        factory.enabled_lines = {1}
        env.run(until=40)
        factory.running = False
        env.run()
        journal.snapshot()
    config = AgentConfig(
        agent_id="production-edge",
        spool=str(tmp_path / "agent.sqlite3"),
        connectors=[Connector(connector_id="source", location=str(tmp_path / "source"))],
    )
    with closing(Agent(config.model_dump())) as agent:
        while agent.collect():
            pass
        events = agent.store.pending(10000)
        assert agent.store.health()["quarantined"] == 0
        released = {e["unit_id"] for e in events if e["event_type"] == "unit.released"}
        completed = {e["unit_id"] for e in events if e["event_type"] == "unit.disposition"}
        assert released == completed
        assert len(completed) == factory.counts["introduced"] == 10
        starts = {e["operation_id"] for e in events if e["event_type"] == "operation.started"}
        finishes = {e["operation_id"] for e in events if e["event_type"] == "operation.completed"}
        assert starts == finishes
        assert not any("seed" in e["data"] or "true_quality" in e["data"] for e in events)


def test_store_durability_in_new_connection(tmp_path):
    path = tmp_path / "edge.sqlite3"
    with closing(Store(path)) as store, store.transaction():
        store.capture(observation(1))
    with closing(Store(path)) as recovered:
        assert recovered.pending(1) == [observation(1)]
