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
