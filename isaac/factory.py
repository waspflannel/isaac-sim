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
    parser.add_argument("--layout", choices=("robotic", "large", "demo"), default="robotic")
    parser.add_argument("--minimal", action="store_true", help="Skip rendering for physics checks")
    parser.add_argument(
        "--record", action="store_true", help="Capture robot motion frames at 10 fps"
    )
    parser.add_argument("--duration", type=float, default=240)
    parser.add_argument(
        "--continuous", action="store_true", help="Feed until drained from the panel"
    )
    parser.add_argument(
        "--realtime", action="store_true", help="Pace headless runs for live control"
    )
    parser.add_argument("--dashboard-port", type=int, default=8766)
    parser.add_argument("--processing-time-scale", type=float, default=0.05)
    parser.add_argument("--transport-speed", type=float, default=4.0)
    parser.add_argument("--max-seconds", type=float, default=1500)
    parser.add_argument("--output", type=Path, default=ROOT / ".data" / "isaac" / "factory")
    parser.add_argument("--journal", type=Path, default=ROOT / ".data" / "edge" / "source")
    args = parser.parse_args()
    if not 1 <= args.dashboard_port <= 65535:
        parser.error("Dashboard port must be between 1 and 65535")
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
    if args.record:
        for frame in (args.output / "motion").glob("[0-9][0-9][0-9][0-9].png"):
            frame.unlink()
    for name in (
        "summary.json",
        "live.json",
        "Camera.png",
        "FloorCamera.png",
        "RepairCamera.png",
        "RobotCamera.png",
        "ShippingCamera.png",
    ):
        (args.output / name).unlink(missing_ok=True)

    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": args.headless,
            "width": 1440,
            "height": 900,
            "multi_gpu": False,
            "anti_aliasing": 0,
            "extra_args": ["--enable", "isaacsim.core.api"],
            **(
                {"renderer": "MinimalRendering", "disable_viewport_updates": True}
                if args.minimal
                else {}
            ),
        }
    )
    exit_code = 1
    try:
        if args.layout == "robotic":
            import omni.kit.app

            omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
                "isaacsim.robot_motion.examples", True
            )
            from robot_run import main
        elif args.layout == "large":
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
