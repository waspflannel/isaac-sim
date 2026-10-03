"""Production must wait for physical handling, including after processing."""

import simpy

from factory_intelligence.item import Item
from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.calibration_machine import CalibrationMachine


def test_load_and_unload_gate_downstream_production():
    env = simpy.Environment()
    machine = AssemblyMachine(env, 2)
    downstream = CalibrationMachine(env, 3)
    machine.connect(downstream)
    loaded, unloaded = env.event(), env.event()
    machine.transfer = lambda item, station: loaded
    machine.unload = lambda item, station: unloaded
    events = []
    machine.on_event = downstream.on_event = events.append
    machine.input_item(Item("part-1", 70))
    env.run(until=5)
    assert not any(event["event"] == "started" for event in events)
    loaded.succeed()
    env.run(until=8)
    assert not any(event["event"] == "finished" for event in events)
    assert not downstream.is_processing
    unloaded.succeed()
    env.run(until=9)
    assert downstream.is_processing
    assert any(event["event"] == "finished" for event in events)
