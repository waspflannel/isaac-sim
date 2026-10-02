from .base_machine import Machine


class RepairMachine(Machine):
    def process_item(self, item):
        item.quality += 20
        item.repair_attempts += 1
        item.completed_steps.append("repair")
