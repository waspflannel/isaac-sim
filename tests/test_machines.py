import pytest

from factory_intelligence.item import Item
from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.base_machine import Machine
from factory_intelligence.machines.kit_machine import KitMachine
from factory_intelligence.machines.repair_machine import RepairMachine


def test_machine_links_determine_the_route_and_last_machine_returns_item():
    kit = KitMachine()
    assembly = AssemblyMachine()
    repair = RepairMachine()
    kit.connect(assembly)
    assembly.connect(repair)
    item = Item("item-001")
    assert kit.input_item(item) is item
    assert item.completed_steps == ["kit", "assembly", "repair"]
    assert RepairMachine().input_item(Item("single")).completed_steps == ["repair"]


def test_invalid_machine_input_is_rejected():
    with pytest.raises(TypeError, match="Item"):
        KitMachine().input_item({})
    with pytest.raises(TypeError):
        Machine()
