"""Run the factory's machines against serialized products on one simulation clock."""

import argparse
import json
from pathlib import Path
from uuid import UUID, uuid4

import simpy

from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.calibration_machine import CalibrationMachine
from factory_intelligence.machines.kit_machine import KitMachine
from factory_intelligence.machines.packing_machine import PackingMachine
from factory_intelligence.machines.repair_machine import RepairMachine
from factory_intelligence.machines.test_machine import TestMachine


class Factory:
    def __init__(self, run_id, event_file):
        self.run_id = run_id
        self.event_file = event_file
        self.env = simpy.Environment()
        self.products = []
        self.sequences = {}
        self.machines = [
            machine(self.env, self.emit)
            for machine in (
                KitMachine,
                AssemblyMachine,
                CalibrationMachine,
                TestMachine,
                PackingMachine,
            )
        ]
        self.repair = RepairMachine(self.env, self.emit)

    def emit(self, event_type, station_id, product, **details):
        sequence = self.sequences.get(station_id, 0) + 1
        self.sequences[station_id] = sequence
        event = {
            "schema_version": 1,
            "event_type": event_type,
            "event_id": f"{self.run_id}:{station_id}:boot-01:{sequence}",
            "origin": "simulation",
            "tenant_id": "local-lab",
            "site_id": "virtual-factory",
            "line_id": "motor-line-01",
            "station_id": station_id,
            "producer_session": f"{station_id}:boot-01",
            "sequence": sequence,
            "source_clock_domain": "simulation",
            "simulation": {
                "run_id": self.run_id,
                "epoch": 1,
                "time_ns": self.env.now * 1_000_000_000,
            },
            "unit_id": product["unit_id"],
            **details,
        }
        self.event_file.write(json.dumps(event, allow_nan=False, sort_keys=True) + "\n")

    def process_product(self, product):
        # ponytail: unbounded waiting queues; bounded buffers/backpressure belong to slice 4.
        for machine in self.machines:
            result = yield self.env.process(machine.process(product))
            if isinstance(machine, TestMachine) and result["result"] == "fail":
                yield self.env.process(self.repair.process(product))
                yield self.env.process(machine.process(product))
        product["status"] = product["disposition"]
        self.emit("unit.disposition", "s05-pack", product, disposition=product["disposition"])

    def release_products(self, units, release_every_seconds):
        for index in range(1, units + 1):
            if index > 1:
                yield self.env.timeout(release_every_seconds)
            product = {
                "unit_id": f"{self.run_id}:unit-{index:05d}",
                "product_revision": "motor-module-1",
                "component_lots": {
                    part: f"{part}-lot-001" for part in ("pcb", "motor", "connector", "housing")
                },
                "location": "factory-entry",
                "status": "released",
                "released_at_seconds": self.env.now,
            }
            self.products.append(product)
            self.emit(
                "unit.released",
                "factory-entry",
                product,
                product_revision=product["product_revision"],
                component_lots=product["component_lots"],
                location=product["location"],
                status=product["status"],
            )
            self.env.process(self.process_product(product))

    def run(self, units, release_every_seconds, advance_seconds):
        self.env.process(self.release_products(units, release_every_seconds))
        self.env.run()  # Drain every machine, not just the product-release process.
        if advance_seconds:
            self.env.run(until=self.env.timeout(advance_seconds))


def run_factory(output_root, *, units=1, release_every_seconds=1, advance_seconds=10, run_id=None):
    """Run the line to completion, then advance idle time; save public and private files."""
    for name, value, minimum, maximum in (
        ("units", units, 1, 10_000),
        ("release_every_seconds", release_every_seconds, 0, 86_400),
        ("advance_seconds", advance_seconds, 0, 86_400),
    ):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    run_id = str(uuid4()) if run_id is None else str(UUID(str(run_id)))
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    public, private = run_dir / "public", run_dir / "private"
    public.mkdir()
    private.mkdir()
    with (public / "events.ndjson").open("w", encoding="utf-8", newline="\n") as event_file:
        factory = Factory(run_id, event_file)
        factory.run(units, release_every_seconds, advance_seconds)
    save_run(factory, public, private, units, release_every_seconds, advance_seconds)
    return run_dir


def save_run(factory, public, private, units, release_every_seconds, advance_seconds):
    (public / "products.json").write_text(
        json.dumps(factory.products, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (private / "truth.json").write_text(
        json.dumps(
            {
                "run_id": factory.run_id,
                "quality_model": "not_implemented",
                "measurement_source": "scripted_nominal",
                "faults": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "run_id": factory.run_id,
        "epoch": 1,
        "status": "complete",
        "time_unit": "seconds",
        "clock_resolution_seconds": 1,
        "final_time_seconds": factory.env.now,
        "settings": {
            "units": units,
            "release_every_seconds": release_every_seconds,
            "advance_seconds": advance_seconds,
        },
        "released_units": len(factory.products),
        "good_units": sum(p["status"] == "good" for p in factory.products),
        "scrapped_units": sum(p["status"] == "scrap" for p in factory.products),
        "unresolved_units": sum(p["status"] not in ("good", "scrap") for p in factory.products),
    }
    # Written last: failed runs retain their evidence without a completed manifest.
    (private / "run.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=int, default=1)
    parser.add_argument("--release-every-seconds", type=int, default=1)
    parser.add_argument(
        "--advance-seconds",
        type=int,
        default=10,
        help="Idle seconds to advance after all machines finish",
    )
    parser.add_argument("--run-id", help="Optional UUID for reproducibility; otherwise generated")
    parser.add_argument("--output-root", type=Path, default=Path(".data/runs"))
    args = parser.parse_args()
    try:
        run_dir = run_factory(**vars(args))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Factory run failed: {exc}\n")
    print(json.dumps({"run_directory": str(run_dir.resolve()), "released_units": args.units}))


if __name__ == "__main__":
    main()
