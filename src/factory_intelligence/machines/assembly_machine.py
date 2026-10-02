from .base_machine import Machine


class AssemblyMachine(Machine):
    def process_item(self, item):
        item.quality += 10
        item.completed_steps.append("assembly")
