"""Check a completed integrated run, including physical handling evidence."""

import argparse
import json
from pathlib import Path


def check(output, minimum_items=96):
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["introduced"] >= minimum_items, "Batch is too short for this acceptance check"
    assert summary["introduced"] == summary["packed"] + summary["scrapped"]
    assert summary["wip"] == 0
    assert summary["peak_active_robots"] >= 12
    robots = summary["robots"]
    assert len(robots) == 18
    for name, robot in robots.items():
        assert robot["phase"] == "IDLE" and robot["queued"] == 0, name
        assert robot["attachments"] == robot["completed"] > 0, name
        for move in summary["handling_evidence"][name]:
            assert move["lift_meters"] >= 0.05, move
            assert move["placement_error_meters"] < 0.06, move
    assembly = sum(r["completed"] for name, r in robots.items() if "Assembly" in name)
    packing = sum(r["completed"] for name, r in robots.items() if "Packing" in name)
    repair = sum(r["completed"] for name, r in robots.items() if name.endswith("RepairMachine"))
    assert assembly == 2 * summary["introduced"]
    assert packing == 2 * summary["packed"]
    assert repair == 2 * summary["repaired"]
    assert all(
        m["state"] == "idle" and m["queue"] == 0 and m["max_queue"] <= m["capacity"]
        for m in summary["machines"].values()
    )
    print(
        f"PASS: {summary['introduced']} items, "
        f"{assembly + packing + repair} verified robot placements, all 18 cells exercised"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--minimum-items", type=int, default=96)
    args = parser.parse_args()
    check(args.output, args.minimum_items)
