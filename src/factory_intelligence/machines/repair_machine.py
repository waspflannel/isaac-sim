from .base_machine import Machine


class RepairMachine(Machine):
    def process_item(self, item):
        item.completed_steps.append("repair")
