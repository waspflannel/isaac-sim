from .base_machine import Machine


class KitMachine(Machine):
    station_id = "s01-kit"
    duration_seconds = 35

    def run(self, product):
        if set(product["component_lots"]) != {"pcb", "motor", "connector", "housing"}:
            raise ValueError("product must have all four component lots")
        yield self.env.timeout(self.duration_seconds)
        product["kitted"] = True
        return {"result": "pass", "component_lots": dict(product["component_lots"])}
