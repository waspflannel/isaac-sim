from .base_machine import Machine


class RepairMachine(Machine):
    station_id = "repair"
    duration_seconds = 180

    def run(self, product):
        if product.get("test_result") != "fail":
            raise ValueError("repair requires a failed test")
        yield self.env.timeout(self.duration_seconds)
        old_lot = product["component_lots"]["connector"]
        product["component_lots"]["connector"] = "connector-replacement-001"
        # No automatic healthy result: a new test must observe the repaired unit.
        product.pop("test_result")
        return {
            "result": "pass",
            "action": "connector_replaced",
            "previous_lot": old_lot,
            "replacement_lot": product["component_lots"]["connector"],
        }
