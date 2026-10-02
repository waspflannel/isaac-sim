import io
import json

import pytest
import simpy

from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.base_machine import Machine
from factory_intelligence.machines.test_machine import TestMachine as EolMachine
from factory_intelligence.main import Factory


def test_machine_capacity_and_repeat_attempts():
    env = simpy.Environment()
    events = []
    machine = EolMachine(
        env,
        lambda kind, station, product, **data: events.append(
            (env.now, kind, product["unit_id"], data)
        ),
    )
    products = [
        {"unit_id": str(i), "calibration_revision": "1", "test_voltage_drop": 0.1} for i in range(3)
    ]
    for product in products:
        env.process(machine.process(product))
    env.run()
    starts = [e for e in events if e[1] == "operation.started"]
    assert [e[0] for e in starts] == [0, 0, 90]
    assert starts[0][3]["fixture_id"] != starts[1][3]["fixture_id"]
    assert env.now == 180
    assert all(p["test_result"] == "fail" for p in products)
    env.process(machine.process(products[0]))
    env.run()
    assert events[-1][3]["attempt"] == 2
    assert products[0]["test_voltage_drop"] == 0.1
    assert machine.active == 0 and len(machine.slots.items) == 2


def test_failed_operation_aborts_and_releases_capacity():
    env = simpy.Environment()
    events = []
    machine = AssemblyMachine(env, lambda kind, *args, **kwargs: events.append(kind))
    product = {"unit_id": "bad"}
    env.process(machine.process(product))
    with pytest.raises(ValueError, match="kitted"):
        env.run()
    assert events == ["operation.started", "operation.aborted"]
    assert product["status"] == "faulted"
    assert machine.active == 0 and len(machine.slots.items) == 1
    with pytest.raises(ValueError, match="positive integer"):
        AssemblyMachine(env, lambda *args: None, duration_seconds=0)
    with pytest.raises(TypeError):
        Machine(env, lambda *args: None)


def test_repair_route_preserves_failure_and_does_not_invent_recovery():
    stream = io.StringIO()
    factory = Factory("test-run", stream)
    product = {
        "unit_id": "test-run:unit-1",
        "component_lots": {
            part: part + "-original" for part in ("pcb", "motor", "connector", "housing")
        },
        "test_voltage_drop": 0.1,
    }
    factory.env.process(factory.process_product(product))
    factory.env.run()
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    tests = [
        e
        for e in events
        if e["station_id"] == "s04-eol" and e["event_type"] == "operation.completed"
    ]
    assert [e["attempt"] for e in tests] == [1, 2]
    assert [e["result"] for e in tests] == ["fail", "fail"]
    assert tests[0]["operation_run_id"] != tests[1]["operation_run_id"]
    repair = next(
        e
        for e in events
        if e["station_id"] == "repair" and e["event_type"] == "operation.completed"
    )
    assert repair["previous_lot"] == "connector-original"
    assert product["component_lots"]["connector"] == repair["replacement_lot"]
    assert events[-1]["disposition"] == product["status"] == "scrap"
    assert factory.env.now == 525
