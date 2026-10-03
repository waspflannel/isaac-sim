"""Simulator equipment interface. Standard library only, including under Isaac's Python."""

import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class FactoryJournal:
    def __init__(self, directory, *, site_id="virtual-factory", run_id=None):
        self.run_id = run_id or str(uuid4())
        self.site_id = site_id
        self.session = str(uuid4())
        self.sequences = defaultdict(int)
        self.attempts = defaultdict(int)
        self.operations = {}
        self.factory = None
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{self.session}.ndjson"
        self.file = self.path.open("x", encoding="utf-8", newline="\n")

    def emit(self, event_type, station, seconds, *, item_id=None, operation_id=None, **data):
        self.sequences[station] += 1
        sequence = self.sequences[station]
        event = {
            "schema_version": 1,
            "event_id": f"{self.session}:{station}:{sequence}",
            "event_type": event_type,
            "site_id": self.site_id,
            "station_id": station,
            "producer_session": self.session,
            "sequence": sequence,
            "origin": "simulation",
            "clock_domain": "simulation",
            "time_seconds": seconds,
            "run_id": self.run_id,
            "epoch": 1,
            "unit_id": f"{self.run_id}:{item_id}" if item_id else None,
            "operation_id": operation_id,
            "data": {"source_utc": datetime.now(UTC).isoformat(), **data},
        }
        self.file.write(json.dumps(event, allow_nan=False) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())
        return event

    def observe(self, record):
        kind, seconds, item_id = record["event"], record["time_seconds"], record["item_id"]
        station = record.get("machine", f"Line{record.get('line', 0):02}/Infeed")
        if kind == "shipped":
            # Production has removed the active item; the disposition keeps its own line identity.
            station = f"Line{record['line']:02}/Shipping"
        key = (station, item_id)
        if kind == "started":
            self.attempts[key] += 1
            self.operations[key] = f"{self.run_id}:{station}:{item_id}:{self.attempts[key]}"
        operation = self.operations.get(key)
        machine = self.factory.machines.get(station)
        details = {"queue": len(machine.item_wait_queue)} if machine else {}
        types = {
            "introduced": "unit.released",
            "queued": "queue.changed",
            "transporting": "station.state",
            "started": "operation.started",
            "finished": "operation.completed",
            "blocked": "station.state",
            "released": "station.state",
            "passed": "quality.result",
            "failed": "quality.result",
            "scrapped": "unit.scrap_decided",
            "shipped": "unit.disposition",
        }
        states = {
            "transporting": "transporting",
            "started": "processing",
            "finished": "idle",
            "blocked": "blocked",
            "released": "idle",
        }
        if kind in states:
            details["state"] = states[kind]
        if operation:
            details["attempt"] = self.attempts[key]
        if kind in ("passed", "failed"):
            details["result"] = "pass" if kind == "passed" else "fail"
            measurement = self.factory.active[item_id].test_results[-1]
            self.emit(
                "measurement.recorded",
                station,
                seconds,
                item_id=item_id,
                operation_id=operation,
                name="simulated_quality_score",
                raw_value=measurement["quality"],
                raw_unit="score",
                value=measurement["quality"],
                unit="score",
                conversion_revision="identity-1",
                lower_limit=measurement["threshold"],
                upper_limit=None,
                quality="good",
                model="fictional station test score; not an electrical measurement",
            )
        if kind == "shipped":
            details["disposition"] = record["status"]
        self.emit(types[kind], station, seconds, item_id=item_id, operation_id=operation, **details)
        if kind == "finished" and station.endswith("RepairMachine"):
            self.emit(
                "repair.completed",
                station,
                seconds,
                item_id=item_id,
                operation_id=operation,
                repair="simulated process adjustment",
                component_replacement=None,
            )

    def snapshot(self):
        for station, machine in self.factory.snapshot()["machines"].items():
            self.emit(
                "station.observed",
                station,
                self.factory.env.now,
                state=machine["state"],
                queue=machine["queue"],
                capacity=machine["capacity"],
            )

    def close(self):
        self.file.close()
