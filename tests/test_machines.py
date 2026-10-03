import json

import simpy

from factory_intelligence.item import Item
from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.kit_machine import KitMachine
from factory_intelligence.machines.repair_machine import RepairMachine


def test_activity_log_records_queue_waits_and_transfer_order(capsys):
    env = simpy.Environment()
    kit = KitMachine(env, item_processing_time=2)
    assembly = AssemblyMachine(env, item_processing_time=5)
    kit.connect(assembly)
    kit.input_item(Item("A"))
    kit.input_item(Item("B"))
    env.run()

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events == [
        {"time_seconds": time, "machine": machine, "item_id": item, "event": event}
        for time, machine, item, event in [
            (0, "KitMachine", "A", "queued"),
            (0, "KitMachine", "B", "queued"),
            (0, "KitMachine", "A", "started"),
            (2, "KitMachine", "A", "finished"),
            (2, "AssemblyMachine", "A", "queued"),
            (2, "KitMachine", "B", "started"),
            (2, "AssemblyMachine", "A", "started"),
            (4, "KitMachine", "B", "finished"),
            (4, "AssemblyMachine", "B", "queued"),
            (7, "AssemblyMachine", "A", "finished"),
            (7, "AssemblyMachine", "B", "started"),
            (12, "AssemblyMachine", "B", "finished"),
        ]
    ]


def test_machine_waits_for_scene_arrival_before_processing():
    env = simpy.Environment()
    machine = KitMachine(env, item_processing_time=2)
    arrival = env.event()
    events = []
    machine.transfer = lambda item, station: arrival
    machine.on_event = events.append
    item = Item("in-transit")
    machine.input_item(item)
    env.run(until=10)
    assert item.completed_steps == []
    assert [event["event"] for event in events] == ["queued"]
    arrival.succeed()
    env.run()
    assert item.completed_steps == ["kit"]
    assert [(event["event"], event["time_seconds"]) for event in events] == [
        ("queued", 0),
        ("started", 10),
        ("finished", 12),
    ]


def test_queue_is_fifo_and_linked_machines_work_concurrently():
    env = simpy.Environment()
    kit = KitMachine(env, item_processing_time=2)
    assembly = AssemblyMachine(env, item_processing_time=5)
    repair = RepairMachine(env, item_processing_time=1)
    kit.connect(assembly)
    assembly.connect(repair)
    items = [Item(str(index)) for index in range(3)]
    for item in items:
        kit.input_item(item)
    env.run(until=3)
    assert items[0].completed_steps == ["kit"]
    assert items[1].completed_steps == []
    assert list(kit.item_wait_queue) == [items[2]]
    assert kit.is_processing and assembly.is_processing
    env.run(until=9)
    assert items[0].completed_steps == ["kit", "assembly", "repair"]
    assert items[1].completed_steps == ["kit"]
    assert list(assembly.item_wait_queue) == [items[2]]
    env.run()
    assert env.now == 18
    assert all(item.completed_steps == ["kit", "assembly", "repair"] for item in items)
    assert all(not m.is_processing and not m.item_wait_queue for m in (kit, assembly, repair))
    # An idle line can receive work again.
    extra = Item("extra")
    kit.input_item(extra)
    env.run()
    assert env.now == 26
    assert extra.completed_steps == ["kit", "assembly", "repair"]
