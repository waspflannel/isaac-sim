from .base_machine import Machine


class KitMachine(Machine):
    def process_item(self, item):
        item.completed_steps.append("kit")
