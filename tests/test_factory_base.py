import json
from uuid import UUID

import pytest

from factory_intelligence.factory_base import run_factory


def test_release_history_clock_and_reproduction(tmp_path):
    run_id = "5a2c35bb-a7ae-433c-98ba-64167c03257e"
    settings = dict(units=3, release_every_seconds=2, advance_seconds=7, run_id=run_id)
    run = run_factory(tmp_path / "first", **settings)
    events = [json.loads(line) for line in (run / "public/events.ndjson").read_text().splitlines()]
    all_events = events
    events = [e for e in events if e["event_type"] == "unit.released"]
    products = json.loads((run / "public/products.json").read_text())
    manifest = json.loads((run / "private/run.json").read_text())
    assert [event["simulation"]["time_ns"] for event in events] == [0, 2_000_000_000, 4_000_000_000]
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert len({event["event_id"] for event in events}) == 3
    assert [event["unit_id"] for event in events] == [product["unit_id"] for product in products]
    assert len({product["unit_id"] for product in products}) == 3
    assert manifest["final_time_seconds"] == 382
    assert manifest["released_units"] == manifest["good_units"] == 3
    assert manifest["unresolved_units"] == 0
    assert len({e["event_id"] for e in all_events}) == len(all_events)
    assert len([e for e in all_events if e["event_type"] == "operation.completed"]) == 15
    for product, event in zip(products, events, strict=True):
        assert product["status"] == "good"
        assert product["location"] == "s05-pack"
        assert set(product["component_lots"]) == {"pcb", "motor", "connector", "housing"}
        assert event["event_type"] == "unit.released"
        assert "operation_run_id" not in event
    for path in (run / "public").iterdir():
        assert "faults" not in path.read_text() and "quality_model" not in path.read_text()
    assert (
        json.loads((run / "private/truth.json").read_text())["quality_model"] == "not_implemented"
    )
    replay = run_factory(tmp_path / "second", **settings)
    for path in run.rglob("*.json*"):
        assert path.read_bytes() == (replay / path.relative_to(run)).read_bytes()
    with pytest.raises(FileExistsError):
        run_factory(tmp_path / "first", **settings)
    assert json.loads((run / "private/run.json").read_text()) == manifest


def test_new_runs_and_simultaneous_release(tmp_path):
    runs = [
        run_factory(tmp_path, units=2, release_every_seconds=0, advance_seconds=0) for _ in range(2)
    ]
    assert runs[0] != runs[1]
    ids = []
    for run in runs:
        UUID(run.name)
        events = [
            json.loads(line) for line in (run / "public/events.ndjson").read_text().splitlines()
        ]
        events = [e for e in events if e["event_type"] == "unit.released"]
        assert [e["simulation"]["time_ns"] for e in events] == [0, 0]
        assert [e["sequence"] for e in events] == [1, 2]
        ids.extend(e["event_id"] for e in events)
    assert len(set(ids)) == 4


@pytest.mark.parametrize(
    "settings",
    [
        {"units": 0},
        {"units": 10_001},
        {"units": True},
        {"units": 1.5},
        {"release_every_seconds": -1},
        {"release_every_seconds": float("nan")},
        {"advance_seconds": -1},
        {"advance_seconds": 86_401},
        {"run_id": "../unsafe"},
        {"run_id": "bad-lot"},
    ],
)
def test_invalid_settings_create_no_run(tmp_path, settings):
    with pytest.raises(ValueError):
        run_factory(tmp_path, **settings)
    assert not list(tmp_path.iterdir())
