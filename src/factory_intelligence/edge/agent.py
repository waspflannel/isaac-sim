"""Local gateway: observe first, then exchange batches and bounded requests with the brain."""

import json
import os
import time

from pydantic import ValidationError

from .connectors import CAPABILITIES, batches
from .contracts import Command, Event, Settings, permitted
from .detection import Detector
from .ownership import acquire
from .store import Store, utc_now
from .transport import request_json


class Agent:
    def __init__(self, config):
        self.config = config
        self.ownership = acquire(config["spool"])
        self.store = Store(config["spool"])
        self.detector = Detector(self.store, config["agent_id"], config["site_id"])
        with self.store.transaction():
            if self.store.get("settings") is None:
                self.store.set("settings", config["settings"])
        self.next_exchange = 0
        self.retry_seconds = 1
        self.brain_status = "not_connected"
        self.received_ticks = {}

    def age(self, key, received_at, *, refresh=False):
        now = time.monotonic()
        if refresh:
            self.received_ticks[key] = now
        self.received_ticks.setdefault(key, now - max(0, time.time() - received_at))
        return max(0, now - self.received_ticks[key])

    def normalize(self, raw, connector):
        if len(raw.encode()) > 1024 * 1024:
            raise ValueError("Equipment event exceeded 1 MiB")
        source = json.loads(raw)
        if not isinstance(source, dict):
            raise ValueError("Equipment record must be an object")
        mapped = {connector["fields"].get(k, k): v for k, v in source.items()}
        mapped["event_type"] = connector["event_types"].get(
            mapped.get("event_type"), mapped.get("event_type")
        )
        data = dict(mapped.get("data", {}))
        if "state" in data:
            data["state"] = connector["states"].get(str(data["state"]), data["state"])
        conversion = connector["conversions"].get(data.get("name"))
        if mapped["event_type"] == "measurement.recorded" and conversion:
            if data.get("unit") != conversion["raw_unit"]:
                raise ValueError("Measurement unit does not match configured conversion")
            data.update(raw_value=data["value"], raw_unit=data["unit"])
            for key in ("value", "lower_limit", "upper_limit"):
                if data.get(key) is not None:
                    data[key] = float(data[key]) * conversion["factor"] + conversion["offset"]
            data["unit"] = conversion["unit"]
            data["conversion_revision"] = connector["mapping_revision"]
        mapped["data"] = data
        event = Event.model_validate(mapped).model_dump()
        if event["site_id"] != self.config["site_id"]:
            raise ValueError("Source site does not match agent assignment")
        event["data"] = {
            **event["data"],
            "connection": {
                "connector_id": connector["connector_id"],
                "mapping_revision": connector["mapping_revision"],
            },
        }
        if any(connector[k] for k in ("fields", "event_types", "states", "conversions")):
            event["data"]["source_record"] = source
        return event

    def track_sequence(self, event, settings):
        key = f"sequence:{event['station_id']}:{event['producer_session']}"
        state = self.store.get(key, {"highest": 0, "gaps": []})
        sequence = event["sequence"]
        if sequence > state["highest"] + 1:
            state["gaps"].append([state["highest"] + 1, sequence - 1])
        gaps = []
        for start, end in state["gaps"]:
            if start <= sequence <= end:
                if start < sequence:
                    gaps.append([start, sequence - 1])
                if sequence < end:
                    gaps.append([sequence + 1, end])
            else:
                gaps.append([start, end])
        state = {"highest": max(sequence, state["highest"]), "gaps": gaps}
        self.store.set(key, state)
        self.detector.incident(
            "sequence_gap:" + key,
            bool(gaps),
            gaps,
            "continuous source sequence",
            [event["event_id"]],
            settings,
            event,
        )

    def collect(self):
        settings = self.store.get("settings")
        if self.store.health()["live_bytes"] > self.config["max_spool_mb"] * 1024 * 1024:
            with self.store.transaction():
                self.store.set("storage_pressure", True)
            return 0  # Leave retained source checkpoints untouched; never evict pending records.
        captured = 0
        with self.store.transaction():
            self.store.set("storage_pressure", False)
        for connector in self.config["connectors"]:
            name = connector["connector_id"]
            if name in settings["disabled_connectors"]:
                with self.store.transaction():
                    self.store.set("connection:" + name, {"status": "disabled"})
                continue
            try:
                observed = False
                for key, cursor, records in batches(connector, self.store):
                    with self.store.transaction():
                        for raw in records:
                            try:
                                event = self.normalize(raw, connector)
                                prefixes = connector["station_prefixes"]
                                if prefixes and not permitted(event["station_id"], prefixes):
                                    continue
                                inserted = self.store.capture(event)
                            except (ValueError, ValidationError, TypeError, KeyError) as error:
                                self.store.quarantine(name, raw, str(error))
                                continue
                            if inserted:
                                observed = True
                                self.track_sequence(event, settings)
                                self.detector.observe(event, settings)
                                self.capture_requested(event)
                                self.store.set(
                                    "last_observation:" + event["station_id"],
                                    {
                                        "connector_id": name,
                                        "last_received_unix": time.time(),
                                        "event_id": event["event_id"],
                                        "run_id": event["run_id"],
                                        "epoch": event["epoch"],
                                        "station_id": event["station_id"],
                                    },
                                )
                                self.age(
                                    "station:" + event["station_id"], time.time(), refresh=True
                                )
                                captured += 1
                        self.store.set(key, cursor)
                previous = self.store.get("connection:" + name, {})
                now = time.time()
                last_event = now if observed else previous.get("last_event_unix", now)
                stale = (
                    self.age("connector:" + name, last_event, refresh=observed)
                    >= settings["rules"]["stale_seconds"]
                )
                with self.store.transaction():
                    self.store.set(
                        "connection:" + name,
                        {
                            "status": "stale" if stale else "connected",
                            "last_poll_utc": utc_now(),
                            "last_event_unix": last_event,
                            "capabilities": CAPABILITIES[connector["kind"]],
                        },
                    )
                    self.connection_incident(name, stale, settings, "source_silent")
            except (OSError, ValueError) as error:
                with self.store.transaction():
                    previous = self.store.get("connection:" + name, {})
                    self.store.set(
                        "connection:" + name,
                        {
                            **previous,
                            "status": "unavailable",
                            "error": type(error).__name__,
                            "last_poll_utc": utc_now(),
                        },
                    )
                    self.connection_incident(name, True, settings, "source_unavailable")
        self.check_freshness(settings)
        return captured

    def check_freshness(self, settings):
        now = time.time()
        with self.store.transaction():
            rows = self.store.db.execute(
                "SELECT value FROM state WHERE key LIKE 'last_observation:%'"
            ).fetchall()
            for row in rows:
                observed = json.loads(row[0])
                if observed["connector_id"] in settings["disabled_connectors"]:
                    continue
                age = self.age("station:" + observed["station_id"], observed["last_received_unix"])
                self.detector.incident(
                    "telemetry_missing:" + observed["station_id"],
                    age >= settings["rules"]["stale_seconds"],
                    age,
                    settings["rules"]["stale_seconds"],
                    [observed["event_id"]],
                    settings,
                    {**observed, "clock_domain": "unix", "time_seconds": now},
                )

    def connection_incident(self, name, active, settings, reason):
        self.detector.incident(
            "connection:" + name,
            active,
            reason,
            settings["rules"]["stale_seconds"],
            [],
            settings,
            {
                "station_id": "@agent/" + self.config["agent_id"],
                "clock_domain": "unix",
                "time_seconds": time.time(),
                "run_id": "agent-health",
                "epoch": 1,
            },
        )

    def health(self):
        return {
            **self.store.health(),
            "agent_id": self.config["agent_id"],
            "configuration_revision": self.store.get("settings")["revision"],
            "brain": self.brain_status,
            "storage_pressure": self.store.get("storage_pressure", False),
            "connections": {
                c["connector_id"]: self.store.get("connection:" + c["connector_id"], {})
                for c in self.config["connectors"]
            },
            "capabilities": [
                "local_rules",
                "durable_events",
                "diagnostics",
                "evidence",
                "capture_operations",
            ],
        }

    def execute(self, command):
        command = Command.model_validate(command).model_dump()
        key = "command:" + command["command_id"]
        result = self.store.get(key)
        if result is None and command["kind"] == "capture_operations":
            job_key = "capture:" + command["command_id"]
            job = self.store.get(job_key)
            if job is None:
                job = {
                    "station_id": command["station_id"],
                    "target": command["operations"],
                    "operations": [],
                    "finished": [],
                    "event_ids": [],
                    "expires": time.time() + command["timeout_seconds"],
                }
                with self.store.transaction():
                    self.store.set(job_key, job)
            complete = len(job["finished"]) >= job["target"]
            limited = len(job["event_ids"]) >= 1000
            expired = time.time() >= job["expires"]
            if not (complete or limited or expired):
                return None
            result = {
                "command_id": command["command_id"],
                "status": "completed" if complete else "partial",
                "result": {
                    "station_id": job["station_id"],
                    "operation_ids": job["operations"],
                    "completed_operations": job["finished"],
                    "event_ids": job["event_ids"],
                    "reason": "complete"
                    if complete
                    else ("record_limit" if limited else "timeout"),
                    "detail": "Retained source observations; source sampling is unchanged",
                },
            }
            with self.store.transaction():
                self.store.set(key, result)
                self.store.db.execute("DELETE FROM state WHERE key=?", (job_key,))
            return result
        if result is None:
            body = (
                self.health()
                if command["kind"] == "diagnostics"
                else self.store.evidence(command["event_ids"])
            )
            result = {"command_id": command["command_id"], "status": "completed", "result": body}
            with self.store.transaction():
                self.store.set(key, result)
        return result

    def capture_requested(self, event):
        rows = self.store.db.execute(
            "SELECT key,value FROM state WHERE key LIKE 'capture:%'"
        ).fetchall()
        for row in rows:
            job = json.loads(row["value"])
            if (
                event["station_id"] != job["station_id"]
                or time.time() >= job["expires"]
                or len(job["event_ids"]) >= 1000
            ):
                continue
            operation = event["operation_id"]
            if (
                event["event_type"] == "operation.started"
                and len(job["operations"]) < job["target"]
            ):
                if operation not in job["operations"]:
                    job["operations"].append(operation)
            if operation in job["operations"]:
                job["event_ids"].append(event["event_id"])
                if event["event_type"] in ("operation.completed", "operation.aborted"):
                    if operation not in job["finished"]:
                        job["finished"].append(operation)
            self.store.set(row["key"], job)

    def exchange(self, request=request_json):
        token = os.environ[self.config["token_env"]]
        base = self.config["brain_url"].rstrip("/") + "/api/edge/" + self.config["agent_id"]
        pending = self.store.pending(self.config["batch_size"])
        if pending:
            receipt = request(base + "/events", token, method="POST", body={"events": pending})
            with self.store.transaction():
                self.store.acknowledge(receipt, {event["event_id"] for event in pending})
        reply = request(base + "/heartbeat", token, method="POST", body=self.health())
        settings = Settings.model_validate(reply["settings"]).model_dump()
        known = {c["connector_id"] for c in self.config["connectors"]}
        if set(settings["disabled_connectors"]) - known:
            raise ValueError("Brain configuration references an unknown connector")
        with self.store.transaction():
            current = self.store.get("settings")
            if settings["revision"] < current["revision"] or (
                settings["revision"] == current["revision"] and settings != current
            ):
                raise ValueError("Brain and agent configuration histories disagree")
            if settings["revision"] > current["revision"]:
                self.store.set("settings", settings)
                self.store.set("settings_history:" + str(settings["revision"]), settings)
        for command in reply["commands"]:
            result = self.execute(command)
            if result is not None:
                request(base + "/results", token, method="POST", body=result)
        self.brain_status = "connected"

    def step(self):
        with self.store.transaction():
            self.store.prune(self.config["acknowledged_retention_days"])
        captured = self.collect()
        if time.monotonic() >= self.next_exchange:
            try:
                self.exchange()
                self.retry_seconds = 1
            except (OSError, ValueError, KeyError) as error:
                self.brain_status = "unavailable:" + type(error).__name__
                self.next_exchange = time.monotonic() + self.retry_seconds
                self.retry_seconds = min(60, self.retry_seconds * 2)
        return captured

    def close(self):
        self.store.close()
        self.ownership.close()
