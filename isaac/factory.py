"""Start Isaac before importing the factory's simulator APIs."""

import argparse
import math
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--layout", choices=("large", "demo"), default="large")
    parser.add_argument("--duration", type=float, default=240)
    parser.add_argument("--processing-time-scale", type=float, default=0.05)
    parser.add_argument("--transport-speed", type=float, default=4.0)
    parser.add_argument("--max-seconds", type=float, default=1500)
    parser.add_argument("--output", type=Path, default=ROOT / ".data" / "isaac" / "factory")
    args = parser.parse_args()
    for value in (
        args.processing_time_scale,
        args.transport_speed,
        args.max_seconds,
        args.duration,
    ):
        if not math.isfinite(value) or value <= 0:
            parser.error("Durations and speed must be finite and positive")
    return args


def start_app():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").unlink(missing_ok=True)

    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": args.headless,
            "width": 1440,
            "height": 900,
            "multi_gpu": False,
            "anti_aliasing": 0,
            "extra_args": ["--enable", "isaacsim.core.api"],
        }
    )
    exit_code = 1
    try:
        if args.layout == "large":
            from large_run import main
        else:
            from factory_run import main

        main(app, args)
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        app.close(exit_code=exit_code)


if __name__ == "__main__":
    start_app()
