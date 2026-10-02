"""Pass a batch of items through the factory's linked machines."""

import json

import simpy

from factory_intelligence.item import Item
from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.calibration_machine import CalibrationMachine
from factory_intelligence.machines.kit_machine import KitMachine
from factory_intelligence.machines.packing_machine import PackingMachine
from factory_intelligence.machines.repair_machine import RepairMachine
from factory_intelligence.machines.test_machine import TestMachine


def run_factory(items):
    env = simpy.Environment()
    kit = KitMachine(env, item_processing_time=35)
    assembly = AssemblyMachine(env, item_processing_time=45)
    calibration = CalibrationMachine(env, item_processing_time=60)
    testing = TestMachine(env, item_processing_time=90)
    packing = PackingMachine(env, item_processing_time=25)
    repair = RepairMachine(env, item_processing_time=180)

    kit.connect(assembly)
    assembly.connect(calibration)
    calibration.connect(testing)
    testing.connect(packing)
    testing.repair_machine = repair
    repair.connect(testing)

    for item in items:
        kit.input_item(item)
    env.run()
    print(
        json.dumps(
            {
                "event": "batch_summary",
                "packed": sum(item.status == "packed" for item in items),
                "repaired": sum(item.repair_attempts > 0 for item in items),
                "scrapped": sum(item.status == "scrapped" for item in items),
                "time_seconds": env.now,
            }
        )
    )
    return items


def main():
    # Fictional quality scores: pass, pass after repair, fail after both repairs.
    items = [
        Item("item-001", quality=70),
        Item("item-002", quality=50),
        Item("item-003", quality=0),
    ]
    for item in run_factory(items):
        print(json.dumps(vars(item)))


if __name__ == "__main__":
    main()
