"""Empty-app smoke check. Run with Isaac Sim's Python, not the application venv."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--output", type=Path, help="Save the successful run summary as JSON")
    args = parser.parse_args()
    if args.frames <= 0:
        parser.error("--frames must be positive")
    if args.output:
        args.output.unlink(missing_ok=True)

    from isaacsim import SimulationApp

    launch_started = perf_counter()
    app = SimulationApp({"headless": args.headless, "width": 1280, "height": 720})
    startup_seconds = perf_counter() - launch_started
    started = perf_counter()
    frames = 0
    exit_code = 1
    try:
        for _ in range(args.frames):
            if not app.is_running():
                raise RuntimeError("Isaac Sim closed before the smoke check completed")
            app.update()
            frames += 1
        summary = {
            "completed_at": datetime.now(UTC).isoformat(),
            "frames": frames,
            "headless": args.headless,
            "startup_seconds": startup_seconds,
            "wall_seconds": perf_counter() - started,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary), flush=True)
        exit_code = 0
    finally:
        # Isaac's normal shutdown terminates Python; preserve failures in its exit status.
        app.close(exit_code=exit_code)


if __name__ == "__main__":
    main()
