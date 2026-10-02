from .base_machine import Machine


class TestMachine(Machine):
    def process_item(self, item):
        item.completed_steps.append("test")
