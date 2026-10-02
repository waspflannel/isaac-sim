from .base_machine import Machine


class CalibrationMachine(Machine):
    def process_item(self, item):
        item.quality += 5
        item.completed_steps.append("calibration")
