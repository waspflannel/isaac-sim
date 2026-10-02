"""Pass one item through the factory's linked machines."""

import json

from factory_intelligence.item import Item
from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.calibration_machine import CalibrationMachine
from factory_intelligence.machines.kit_machine import KitMachine
from factory_intelligence.machines.packing_machine import PackingMachine
from factory_intelligence.machines.test_machine import TestMachine


def run_factory(item):
    kit = KitMachine()
    assembly = AssemblyMachine()
    calibration = CalibrationMachine()
    testing = TestMachine()
    packing = PackingMachine()

    kit.connect(assembly)
    assembly.connect(calibration)
    calibration.connect(testing)
    testing.connect(packing)

    return kit.input_item(item)


def main():
    item = run_factory(Item("item-001"))
    print(json.dumps({"id": item.id, "completed_steps": item.completed_steps}))


if __name__ == "__main__":
    main()
