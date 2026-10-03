"""PostgreSQL evidence ledger, agent enrollment, and durable request mailbox."""

import hashlib
import json
import secrets

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .contracts import Event, Settings, permitted
from .store import encode

SCHEMA = """
CREATE TABLE IF NOT EXISTS edge_agents (
    agent_id TEXT PRIMARY KEY, site_id TEXT NOT NULL, station_prefixes JSONB NOT NULL,
    token_hash TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT true,
    settings JSONB NOT NULL, heartbeat JSONB, last_seen TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS edge_events (
    receipt_id BIGSERIAL PRIMARY KEY, site_id TEXT NOT NULL, event_id TEXT NOT NULL,
    agent_id TEXT NOT NULL REFERENCES edge_agents, payload JSONB NOT NULL,
    checksum TEXT NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(site_id,event_id)
);
CREATE INDEX IF NOT EXISTS edge_unit_history ON edge_events ((payload->>'unit_id'));
CREATE INDEX IF NOT EXISTS edge_run_history ON edge_events ((payload->>'run_id'));
CREATE UNIQUE INDEX IF NOT EXISTS edge_source_position ON edge_events (
    site_id, (payload->>'station_id'), (payload->>'producer_session'), (payload->>'sequence')
) WHERE payload->>'origin' != 'edge';
CREATE TABLE IF NOT EXISTS edge_rejections (
    agent_id TEXT NOT NULL REFERENCES edge_agents, record_hash TEXT NOT NULL,
    payload JSONB NOT NULL, reason TEXT NOT NULL, received_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY(agent_id,record_hash)
);
CREATE TABLE IF NOT EXISTS edge_configurations (
    agent_id TEXT NOT NULL REFERENCES edge_agents, revision INTEGER NOT NULL,
    settings JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY(agent_id,revision)
);
CREATE TABLE IF NOT EXISTS edge_commands (
    agent_id TEXT NOT NULL REFERENCES edge_agents, command_id TEXT NOT NULL,
    payload JSONB NOT NULL, result JSONB, created_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ, PRIMARY KEY(agent_id,command_id)
);
"""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Brain:
    def __init__(self, dsn=""):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3)

    def initialize(self):
        with self.connect() as conn:
            conn.execute(SCHEMA)

    def enroll(self, registration):
        token = secrets.token_urlsafe(32)
        settings = registration.get("settings", Settings().model_dump())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO edge_agents(agent_id,site_id,station_prefixes,token_hash,settings) "
                "VALUES (%s,%s,%s,%s,%s)",
                (
                    registration["agent_id"],
                    registration["site_id"],
                    Jsonb(registration["station_prefixes"]),
                    digest(token),
                    Jsonb(settings),
                ),
            )
            conn.execute(
                "INSERT INTO edge_configurations VALUES (%s,1,%s,now())",
                (registration["agent_id"], Jsonb(settings)),
            )
        return {"agent_id": registration["agent_id"], "token": token, "settings": settings}

    def authorize(self, agent_id, token):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM edge_agents WHERE agent_id=%s", (agent_id,)
            ).fetchone()
        if row and row["enabled"] and secrets.compare_digest(row["token_hash"], digest(token)):
            return row
        return None

    def agents(self):
        with self.connect() as conn:
            return conn.execute(
                "SELECT agent_id,site_id,station_prefixes,enabled,settings,heartbeat,last_seen,"
                "(last_seen IS NULL OR last_seen < now()-interval '30 seconds') AS stale "
                "FROM edge_agents ORDER BY agent_id"
            ).fetchall()

    def rotate(self, agent_id):
        token = secrets.token_urlsafe(32)
        with self.connect() as conn:
            row = conn.execute(
                "UPDATE edge_agents SET token_hash=%s,enabled=true "
                "WHERE agent_id=%s RETURNING agent_id",
                (digest(token), agent_id),
            ).fetchone()
        if not row:
            raise KeyError(agent_id)
        return {"agent_id": agent_id, "token": token}

    def revoke(self, agent_id):
        with self.connect() as conn:
            row = conn.execute(
                "UPDATE edge_agents SET enabled=false WHERE agent_id=%s RETURNING agent_id",
                (agent_id,),
            ).fetchone()
        if not row:
            raise KeyError(agent_id)
        return {"agent_id": agent_id, "enabled": False}

    def ingest(self, agent, events):
        accepted, rejected = [], {}
        with self.connect() as conn:
            # Serialize ledger writers so receipt cursors also follow commit order.
            # Otherwise a slow earlier transaction could appear behind a reader's cursor.
            conn.execute("LOCK TABLE edge_events IN SHARE ROW EXCLUSIVE MODE")
            for raw in events:
                try:
                    event = Event.model_validate(raw).model_dump()
                    own_health = (
                        event["origin"] == "edge"
                        and event["station_id"] == "@agent/" + agent["agent_id"]
                    )
                    if event["site_id"] != agent["site_id"] or not (
                        own_health or permitted(event["station_id"], agent["station_prefixes"])
                    ):
                        raise ValueError("Event outside agent equipment assignment")
                    checksum = digest(encode(event))
                    conn.execute(
                        "INSERT INTO edge_events(site_id,event_id,agent_id,payload,checksum) "
                        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (
                            event["site_id"],
                            event["event_id"],
                            agent["agent_id"],
                            Jsonb(event),
                            checksum,
                        ),
                    )
                    saved = conn.execute(
                        "SELECT checksum FROM edge_events WHERE site_id=%s AND event_id=%s",
                        (event["site_id"], event["event_id"]),
                    ).fetchone()
                    if saved is None:
                        raise ValueError("Source sequence reused by a different event_id")
                    if saved["checksum"] != checksum:
                        raise ValueError("event_id reused with different content")
                    accepted.append(event["event_id"])
                except ValueError as error:
                    reason = str(error)
                    raw_text = json.dumps(raw, sort_keys=True)
                    record_hash = digest(raw_text)
                    conn.execute(
                        "INSERT INTO edge_rejections(agent_id,record_hash,payload,reason) "
                        "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (agent["agent_id"], record_hash, Jsonb({"raw": raw_text}), reason),
                    )
                    event_id = raw.get("event_id")
                    rejected[
                        event_id if isinstance(event_id, str) else "invalid:" + record_hash
                    ] = reason
        return {"accepted": accepted, "rejected": rejected}

    def heartbeat(self, agent_id, health):
        with self.connect() as conn:
            row = conn.execute(
                "UPDATE edge_agents SET heartbeat=%s,last_seen=now() WHERE agent_id=%s "
                "RETURNING settings",
                (Jsonb(health), agent_id),
            ).fetchone()
            commands = conn.execute(
                "SELECT payload FROM edge_commands WHERE agent_id=%s AND result IS NULL "
                "ORDER BY created_at LIMIT 20",
                (agent_id,),
            ).fetchall()
        return {"settings": row["settings"], "commands": [r["payload"] for r in commands]}

    def configure(self, agent_id, settings):
        with self.connect() as conn:
            old = conn.execute(
                "SELECT settings FROM edge_agents WHERE agent_id=%s FOR UPDATE", (agent_id,)
            ).fetchone()
            if not old:
                raise KeyError(agent_id)
            if settings["revision"] != old["settings"]["revision"] + 1:
                raise ValueError("Configuration revision must increment by one")
            conn.execute(
                "UPDATE edge_agents SET settings=%s WHERE agent_id=%s", (Jsonb(settings), agent_id)
            )
            conn.execute(
                "INSERT INTO edge_configurations VALUES (%s,%s,%s,now())",
                (agent_id, settings["revision"], Jsonb(settings)),
            )
        return settings

    def command(self, agent_id, command):
        with self.connect() as conn:
            agent = conn.execute(
                "SELECT station_prefixes FROM edge_agents WHERE agent_id=%s", (agent_id,)
            ).fetchone()
            if agent is None:
                raise KeyError(agent_id)
            if command["kind"] == "capture_operations" and not permitted(
                command["station_id"], agent["station_prefixes"]
            ):
                raise ValueError("Capture target outside agent assignment")
            conn.execute(
                "INSERT INTO edge_commands(agent_id,command_id,payload) VALUES (%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (agent_id, command["command_id"], Jsonb(command)),
            )
            old = conn.execute(
                "SELECT payload,result FROM edge_commands WHERE agent_id=%s AND command_id=%s",
                (agent_id, command["command_id"]),
            ).fetchone()
            if old["payload"] != command:
                raise ValueError("Command ID reused with different content")
        return old

    def result(self, agent_id, result):
        with self.connect() as conn:
            old = conn.execute(
                "SELECT result FROM edge_commands WHERE agent_id=%s AND command_id=%s FOR UPDATE",
                (agent_id, result["command_id"]),
            ).fetchone()
            if not old:
                raise KeyError(result["command_id"])
            if old["result"] is not None and old["result"] != result:
                raise ValueError("Command result conflicts with previously accepted result")
            conn.execute(
                "UPDATE edge_commands SET result=%s,completed_at=COALESCE(completed_at,now()) "
                "WHERE agent_id=%s AND command_id=%s",
                (Jsonb(result), agent_id, result["command_id"]),
            )
        return {"accepted": result["command_id"]}

    def events(self, *, after=0, limit=100, unit_id=None, run_id=None, event_type=None):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM edge_events WHERE receipt_id > %s "
                "AND (%s::text IS NULL OR payload->>'unit_id'=%s) "
                "AND (%s::text IS NULL OR payload->>'run_id'=%s) "
                "AND (%s::text IS NULL OR payload->>'event_type'=%s) "
                "ORDER BY receipt_id LIMIT %s",
                (after, unit_id, unit_id, run_id, run_id, event_type, event_type, limit),
            ).fetchall()
