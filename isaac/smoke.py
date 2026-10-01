"""Empty-app smoke check. Run with Isaac Sim's Python, not the application venv."""

import argparse
import json
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if args.frames <= 0:
        parser.error("--frames must be positive")

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": args.headless})
    started = perf_counter()
    frames = 0
    try:
        for _ in range(args.frames):
            if not app.is_running():
                raise RuntimeError("Isaac Sim closed before the smoke check completed")
            app.update()
            frames += 1
        print(json.dumps({"frames": frames, "wall_seconds": perf_counter() - started}))
    finally:
        app.close()


if __name__ == "__main__":
    main()
