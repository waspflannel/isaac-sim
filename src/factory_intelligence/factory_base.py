"""C1 slice 1: release serialized products and save a standalone simulation run."""

import argparse
import json
from pathlib import Path
from uuid import UUID, uuid4

import simpy


def run_factory(output_root, *, units=1, release_every_seconds=1, advance_seconds=10, run_id=None):
    """Release at t=0, space later releases, then advance past the final release.

    Time is whole simulated seconds in this slice. A supplied UUID reproduces
    identities in a separate output root; existing run directories are never reused.
    """
    for name, value, minimum, maximum in (
        ("units", units, 1, 10_000),
        ("release_every_seconds", release_every_seconds, 0, 86_400),
        ("advance_seconds", advance_seconds, 0, 86_400),
    ):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    run_id = str(uuid4()) if run_id is None else str(UUID(str(run_id)))
    run_dir = Path(output_root) / run_id
    # Exclusive creation prevents overwrites, including after an interrupted run.
    run_dir.mkdir(parents=True, exist_ok=False)
    public = run_dir / "public"
    private = run_dir / "private"
    public.mkdir()
    private.mkdir()
    env = simpy.Environment()
    products = []

    with (public / "events.ndjson").open("w", encoding="utf-8", newline="\n") as event_file:

        def release_products():
            for index in range(1, units + 1):
                if index > 1:
                    yield env.timeout(release_every_seconds)
                product = {
                    "unit_id": f"{run_id}:unit-{index:05d}",
                    "product_revision": "motor-module-1",
                    "component_lots": {
                        part: f"{part}-lot-001" for part in ("pcb", "motor", "connector", "housing")
                    },
                    "location": "factory-entry",
                    "status": "released",
                    "released_at_seconds": env.now,
                }
                products.append(product)
                event = {
                    "schema_version": 1,
                    "event_type": "unit.released",
                    "event_id": f"{run_id}:release:boot-01:{index}",
                    "origin": "simulation",
                    "tenant_id": "local-lab",
                    "site_id": "virtual-factory",
                    "line_id": "motor-line-01",
                    "station_id": "factory-entry",
                    "producer_session": "release:boot-01",
                    "sequence": index,
                    "source_clock_domain": "simulation",
                    "simulation": {
                        "run_id": run_id,
                        "epoch": 1,
                        "time_ns": env.now * 1_000_000_000,
                    },
                    **product,
                }
                # Release is not a machine attempt; no fictional operation/result fields.
                event_file.write(json.dumps(event, allow_nan=False, sort_keys=True) + "\n")
            yield env.timeout(advance_seconds)

        env.run(until=env.process(release_products()))

    (public / "products.json").write_text(
        json.dumps(products, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # No quality mechanism exists yet. Do not manufacture a hidden healthy/faulty label.
    (private / "truth.json").write_text(
        json.dumps({"run_id": run_id, "quality_model": "not_implemented", "faults": []}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "epoch": 1,
        "status": "complete",
        "time_unit": "seconds",
        "clock_resolution_seconds": 1,
        "final_time_seconds": env.now,
        "settings": {
            "units": units,
            "release_every_seconds": release_every_seconds,
            "advance_seconds": advance_seconds,
        },
        "released_units": len(products),
        "unresolved_units": len(products),
    }
    # Written last: an incomplete directory has no completed manifest and is not replay-ready.
    (private / "run.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return run_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=int, default=1)
    parser.add_argument("--release-every-seconds", type=int, default=1)
    parser.add_argument("--advance-seconds", type=int, default=10)
    parser.add_argument("--run-id", help="Optional UUID for reproducibility; otherwise generated")
    parser.add_argument("--output-root", type=Path, default=Path(".data/runs"))
    args = parser.parse_args()
    try:
        run_dir = run_factory(
            args.output_root,
            units=args.units,
            release_every_seconds=args.release_every_seconds,
            advance_seconds=args.advance_seconds,
            run_id=args.run_id,
        )
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Factory run failed: {exc}\n")
    print(json.dumps({"run_directory": str(run_dir.resolve()), "released_units": args.units}))


if __name__ == "__main__":
    main()
