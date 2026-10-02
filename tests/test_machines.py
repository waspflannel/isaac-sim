import pytest
import simpy

from factory_intelligence.item import Item
from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.base_machine import Machine
from factory_intelligence.machines.kit_machine import KitMachine
from factory_intelligence.machines.repair_machine import RepairMachine


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


def test_invalid_machine_input_and_time_are_rejected():
    env = simpy.Environment()
    with pytest.raises(TypeError, match="Item"):
        KitMachine(env, 1).input_item({})
    with pytest.raises(TypeError):
        Machine(env, 1)
    for duration in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            KitMachine(env, duration)
