"""Run the project's production machines in a code-built Isaac factory scene."""

import argparse
import asyncio
import json
import math
import sys
import traceback
from pathlib import Path
from time import perf_counter, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--processing-time-scale",
        type=float,
        default=0.05,
        help="Demo processing durations as a fraction of CPU factory durations",
    )
    parser.add_argument("--transport-speed", type=float, default=2.0, help="Pallet speed in m/s")
    parser.add_argument("--max-seconds", type=float, default=300)
    parser.add_argument("--output", type=Path, default=ROOT / ".data" / "isaac" / "factory")
    args = parser.parse_args()
    for value in (args.processing_time_scale, args.transport_speed, args.max_seconds):
        if not math.isfinite(value) or value <= 0:
            parser.error("Durations and speed must be finite and positive")
    args.output.mkdir(parents=True, exist_ok=True)
    summary_path = args.output / "summary.json"
    summary_path.unlink(missing_ok=True)

    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": args.headless,
            "width": 960,
            "height": 540,
            "multi_gpu": False,
            "anti_aliasing": 0,
        }
    )
    exit_code = 1
    try:
        import carb
        import omni.kit.app
        import omni.usd
        import simpy
        from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport

        # The pinned 6.1 runtime includes this compatibility API for explicit stepping.
        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
            "isaacsim.core.api", True
        )
        from factory_scene import FactoryScene
        from isaacsim.core.api import World

        from factory_intelligence.item import Item
        from factory_intelligence.main import setup_factory

        carb.settings.get_settings().set_int("/persistent/app/viewport/displayOptions", 0)

        dt = 1 / 30
        world = World(physics_dt=dt, rendering_dt=dt, stage_units_in_meters=1)
        stage = omni.usd.get_context().get_stage()
        env = simpy.Environment()
        items = [Item("item-001", 70), Item("item-002", 50), Item("item-003", 0)]
        with (args.output / "events.ndjson").open("w", encoding="utf-8") as event_file:
            scene = FactoryScene(stage, env, items, event_file, speed=args.transport_speed)
            viewport = get_active_viewport()
            viewport.set_texture_resolution((960, 540))
            viewport.set_active_camera("/World/Camera")
            stage.GetRootLayer().Export(str(args.output / "factory.usda"))
            world.reset()
            physics_start = world.current_time
            machines = setup_factory(
                env,
                items,
                transfer=scene.transfer,
                on_event=scene.handle_event,
                processing_time_scale=args.processing_time_scale,
            )
            started = perf_counter()
            steps = 0
            while env.peek() != float("inf") or scene.moves:
                if not app.is_running():
                    raise RuntimeError("Factory window closed before the batch completed")
                if env.now >= args.max_seconds:
                    raise RuntimeError("Factory exceeded the simulation time limit")
                env.run(until=(steps + 1) * dt)
                scene.step(dt)
                world.step(render=False)
                if not args.headless or steps % 10 == 0:
                    world.render()
                # Finish this frame's arrival callbacks and SimPy's stop-event cleanup.
                while env.peek() <= env.now:
                    env.step()
                steps += 1
                if steps % 300 == 0:
                    print(f"Factory time={env.now:.1f}s, moving={list(scene.moves)}", flush=True)
                # Windowed mode is paced so the batch is watchable; headless runs freely.
                if not args.headless:
                    sleep(max(0, steps * dt - (perf_counter() - started)))
            assert [item.status for item in items] == ["packed", "packed", "scrapped"]
            assert [item.repair_attempts for item in items] == [0, 1, 2]
            assert all(
                not machine.item_wait_queue and not machine.is_processing for machine in machines
            )
            assert scene.transfers == sum(len(item.completed_steps) for item in items)
            assert all(
                math.dist(scene.position(item.id), scene.output_position(item)) < 1e-5
                for item in items
            )
            physics_seconds = world.current_time - physics_start
            assert math.isclose(physics_seconds, env.now, rel_tol=1e-6, abs_tol=1e-6), (
                physics_seconds,
                env.now,
            )
            world.pause()
            stage.GetRootLayer().Export(str(args.output / "completed.usda"))
            for _ in range(20):
                app.update()
            capture = capture_viewport_to_file(viewport, str(args.output / "factory.png"))
            saved = asyncio.ensure_future(capture.wait_for_result())
            for _ in range(300):
                app.update()
                if saved.done():
                    saved.result()
                    break
            else:
                raise RuntimeError("Factory screenshot did not finish")
            summary = {
                "isaac_version": "6.1.0",
                "headless": args.headless,
                "transport": "guided_kinematic_pallets",
                "processing_time_scale": args.processing_time_scale,
                "transport_speed": args.transport_speed,
                "time_seconds": env.now,
                "physics_time_seconds": physics_seconds,
                "wall_seconds": perf_counter() - started,
                "transfers": scene.transfers,
                "packed": sum(item.status == "packed" for item in items),
                "scrapped": sum(item.status == "scrapped" for item in items),
                "items": [vars(item) for item in items],
            }
            summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary), flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        app.close(exit_code=exit_code)


if __name__ == "__main__":
    main()
