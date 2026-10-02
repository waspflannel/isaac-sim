"""Pass a batch of items through the factory's linked machines."""

import json

import simpy

from factory_intelligence.item import Item
from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.calibration_machine import CalibrationMachine
from factory_intelligence.machines.kit_machine import KitMachine
from factory_intelligence.machines.packing_machine import PackingMachine
from factory_intelligence.machines.test_machine import TestMachine


def run_factory(items):
    env = simpy.Environment()
    kit = KitMachine(env, item_processing_time=35)
    assembly = AssemblyMachine(env, item_processing_time=45)
    calibration = CalibrationMachine(env, item_processing_time=60)
    testing = TestMachine(env, item_processing_time=90)
    packing = PackingMachine(env, item_processing_time=25)

    kit.connect(assembly)
    assembly.connect(calibration)
    calibration.connect(testing)
    testing.connect(packing)

    for item in items:
        kit.input_item(item)
    env.run()
    return items


def main():
    items = [Item(f"item-{index:03d}") for index in range(1, 4)]
    for item in run_factory(items):
        print(json.dumps({"id": item.id, "completed_steps": item.completed_steps}))


if __name__ == "__main__":
    main()
