from .base_machine import Machine


class AssemblyMachine(Machine):
    station_id = "s02-assembly"
    duration_seconds = 45

    def run(self, product):
        if not product.get("kitted"):
            raise ValueError("assembly requires a kitted product")
        yield self.env.timeout(self.duration_seconds)
        product["assembled"] = True
        return {"result": "pass", "tool_id": "fastener-01"}
