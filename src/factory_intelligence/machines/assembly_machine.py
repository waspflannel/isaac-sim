from .base_machine import Machine


class AssemblyMachine(Machine):
    def process_item(self, item):
        item.completed_steps.append("assembly")
