"""A single-station smoke source, not the five-station production model."""

import argparse
import json
import math
from uuid import uuid4

import simpy


def simulate(units=3, cycle_seconds=60.0, run_id="smoke"):
    if not 1 <= units <= 10_000:
        raise ValueError("units must be between 1 and 10000")
    if not math.isfinite(cycle_seconds) or cycle_seconds <= 0:
        raise ValueError("cycle_seconds must be finite and positive")
    if not run_id.strip():
        raise ValueError("run_id must not be empty")

    env = simpy.Environment()
    events = []

    def station():
        for index in range(1, units + 1):
            yield env.timeout(cycle_seconds)
            events.append(
                {
                    "schema_version": 1,
                    "event_type": "operation.completed",
                    "event_id": f"{run_id}:s01:boot-01:{index}",
                    "origin": "simulation",
                    "tenant_id": "local-lab",
                    "site_id": "virtual-factory",
                    "line_id": "smoke-line",
                    "station_id": "s01-smoke",
                    "producer_session": "s01:boot-01",
                    "sequence": index,
                    "source_clock_domain": "simulation",
                    "simulation": {
                        "run_id": run_id,
                        "epoch": 1,
                        "time_ns": round(env.now * 1_000_000_000),
                    },
                    "unit_id": f"{run_id}:unit-{index}",
                    "operation_run_id": f"{run_id}:unit-{index}:smoke:1",
                    "attempt": 1,
                    "recipe_revision": "smoke-1",
                    "spec_revision": "smoke-1",
                    "result": "pass",
                }
            )

    env.process(station())
    env.run()
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=int, default=3)
    parser.add_argument("--cycle-seconds", type=float, default=60.0)
    parser.add_argument("--run-id", default=str(uuid4()))
    args = parser.parse_args()
    try:
        events = simulate(args.units, args.cycle_seconds, args.run_id)
    except ValueError as exc:
        parser.error(str(exc))
    for event in events:
        print(json.dumps(event, allow_nan=False))
