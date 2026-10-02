from .base_machine import Machine


class TestMachine(Machine):
    def __init__(self, env, item_processing_time, quality_threshold=80, max_repairs=2):
        super().__init__(env, item_processing_time)
        self.quality_threshold = quality_threshold
        self.max_repairs = max_repairs
        self.repair_machine = None

    def process_item(self, item):
        item.completed_steps.append("test")
        item.test_results.append(
            {
                "time_seconds": self.env.now,
                "quality": item.quality,
                "threshold": self.quality_threshold,
                "passed": item.quality >= self.quality_threshold,
            }
        )

    def output_item(self, item):
        if item.test_results[-1]["passed"]:
            self.log_activity(item, "passed")
            return super().output_item(item)
        self.log_activity(item, "failed")
        if item.repair_attempts < self.max_repairs:
            return self.repair_machine.input_item(item)
        item.status = "scrapped"
        self.log_activity(item, "scrapped")
        return item
