"""Standalone station behaviors; the shared wrapper owns capacity and operation records."""

import math
from abc import ABC, abstractmethod

import simpy


class Machine(ABC):
    station_id = ""
    duration_seconds = 1
    capacity = 1

    def __init__(self, env, emit, *, duration_seconds=None):
        self.env = env
        self.emit = emit
        self.duration_seconds = (
            self.duration_seconds if duration_seconds is None else duration_seconds
        )
        if type(self.duration_seconds) is not int or self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be a positive integer")
        self.slots = simpy.Store(env, capacity=self.capacity)
        self.slots.items = [f"{self.station_id}-{i + 1}" for i in range(self.capacity)]
        self.active = 0
        self.attempts = {}

    def process(self, product):
        product["location"] = f"{self.station_id}:queue"
        fixture = yield self.slots.get()
        self.active += 1
        unit_id = product["unit_id"]
        attempt = self.attempts.get(unit_id, 0) + 1
        self.attempts[unit_id] = attempt
        operation = {
            "operation_run_id": f"{unit_id}:{self.station_id}:{attempt}",
            "attempt": attempt,
            "fixture_id": fixture,
            "recipe_revision": "1",
        }
        product["location"] = self.station_id
        product["status"] = "processing"
        try:
            self.emit("operation.started", self.station_id, product, **operation)
            result = yield from self.run(product)
            self.emit("operation.completed", self.station_id, product, **operation, **result)
            product["status"] = "waiting"
            return result
        except Exception:
            product["status"] = "faulted"
            self.emit("operation.aborted", self.station_id, product, **operation)
            raise
        finally:
            self.active -= 1
            self.slots.put(fixture)

    @abstractmethod
    def run(self, product):
        """Perform this station's operation; yield simulated time and return observations."""


class KitMachine(Machine):
    station_id = "s01-kit"
    duration_seconds = 35

    def run(self, product):
        if set(product["component_lots"]) != {"pcb", "motor", "connector", "housing"}:
            raise ValueError("product must have all four component lots")
        yield self.env.timeout(self.duration_seconds)
        product["kitted"] = True
        return {"result": "pass", "component_lots": dict(product["component_lots"])}


class AssemblyMachine(Machine):
    station_id = "s02-assembly"
    duration_seconds = 45

    def run(self, product):
        if not product.get("kitted"):
            raise ValueError("assembly requires a kitted product")
        yield self.env.timeout(self.duration_seconds)
        product["assembled"] = True
        return {"result": "pass", "tool_id": "fastener-01"}


class CalibrationMachine(Machine):
    station_id = "s03-calibration"
    duration_seconds = 60

    def run(self, product):
        if not product.get("assembled"):
            raise ValueError("calibration requires an assembled product")
        yield self.env.timeout(self.duration_seconds)
        product["firmware_revision"] = "1"
        product["calibration_revision"] = "1"
        return {"result": "pass", "firmware_revision": "1", "calibration_revision": "1"}


class TestMachine(Machine):
    station_id = "s04-eol"
    duration_seconds = 90
    capacity = 2

    def run(self, product):
        if not product.get("calibration_revision"):
            raise ValueError("testing requires a calibrated product")
        # Scripted input for station checks; the scenario quality model comes later.
        value = product.get("test_voltage_drop", 0.04)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("test_voltage_drop must be a finite number")
        yield self.env.timeout(self.duration_seconds)
        product["test_result"] = "pass" if 0 <= value <= 0.08 else "fail"
        return {
            "result": product["test_result"],
            "spec_revision": "1",
            "measurements": [
                {
                    "name": "loaded_voltage_drop",
                    "raw_value": value,
                    "raw_unit": "V",
                    "value": value,
                    "unit": "V",
                    "lower_limit": 0,
                    "upper_limit": 0.08,
                    "conversion_revision": "identity-1",
                    "quality": "good",
                }
            ],
        }


class RepairMachine(Machine):
    station_id = "repair"
    duration_seconds = 180

    def run(self, product):
        if product.get("test_result") != "fail":
            raise ValueError("repair requires a failed test")
        yield self.env.timeout(self.duration_seconds)
        old_lot = product["component_lots"]["connector"]
        product["component_lots"]["connector"] = "connector-replacement-001"
        # No automatic healthy result: a new test must observe the repaired unit.
        product.pop("test_result")
        return {
            "result": "pass",
            "action": "connector_replaced",
            "previous_lot": old_lot,
            "replacement_lot": product["component_lots"]["connector"],
        }


class PackingMachine(Machine):
    station_id = "s05-pack"
    duration_seconds = 25

    def run(self, product):
        if product.get("test_result") not in ("pass", "fail"):
            raise ValueError("packing requires a test result")
        yield self.env.timeout(self.duration_seconds)
        product["disposition"] = "good" if product["test_result"] == "pass" else "scrap"
        return {"result": "pass", "disposition": product["disposition"]}
