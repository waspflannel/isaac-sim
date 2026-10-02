from .base_machine import Machine


class CalibrationMachine(Machine):
    def process_item(self, item):
        item.completed_steps.append("calibration")
