import math

from .base_machine import Machine


class TestMachine(Machine):
    station_id = "s04-eol"
    duration_seconds = 90
    capacity = 2

    def run(self, product):
        if not product.get("calibration_revision"):
            raise ValueError("testing requires a calibrated product")
        # Scripted input for station checks; the scenario quality model comes later.
        value = product.get("test_voltage_drop", 0.04)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("test_voltage_drop must be a finite number")
        yield self.env.timeout(self.duration_seconds)
        product["test_result"] = "pass" if 0 <= value <= 0.08 else "fail"
        return {
            "result": product["test_result"],
            "spec_revision": "1",
            "measurements": [
                {
                    "name": "loaded_voltage_drop",
                    "raw_value": value,
                    "raw_unit": "V",
                    "value": value,
                    "unit": "V",
                    "lower_limit": 0,
                    "upper_limit": 0.08,
                    "conversion_revision": "identity-1",
                    "quality": "good",
                }
            ],
        }
