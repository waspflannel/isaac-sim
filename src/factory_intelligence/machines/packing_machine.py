from .base_machine import Machine


class PackingMachine(Machine):
    def process_item(self, item):
        item.completed_steps.append("packing")
