from .base_machine import Machine


class CalibrationMachine(Machine):
    station_id = "s03-calibration"
    duration_seconds = 60

    def run(self, product):
        if not product.get("assembled"):
            raise ValueError("calibration requires an assembled product")
        yield self.env.timeout(self.duration_seconds)
        product["firmware_revision"] = "1"
        product["calibration_revision"] = "1"
        return {"result": "pass", "firmware_revision": "1", "calibration_revision": "1"}
