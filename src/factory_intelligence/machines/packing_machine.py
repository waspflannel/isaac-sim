from .base_machine import Machine


class PackingMachine(Machine):
    station_id = "s05-pack"
    duration_seconds = 25

    def run(self, product):
        if product.get("test_result") not in ("pass", "fail"):
            raise ValueError("packing requires a test result")
        yield self.env.timeout(self.duration_seconds)
        product["disposition"] = "good" if product["test_result"] == "pass" else "scrap"
        return {"result": "pass", "disposition": product["disposition"]}
