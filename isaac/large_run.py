"""Run continuous production, then drain the factory and save evidence."""

import asyncio
import json
from contextlib import closing
from time import perf_counter, sleep

import carb
import omni.usd
import simpy
from isaacsim.core.api import World
from large_scene import LargeScene
from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport

from factory_intelligence.control_panel import ControlPanel
from factory_intelligence.production import Production

STEP = 1 / 30


def capture_views(app, world, output):
    world.pause()
    viewport = get_active_viewport()
    for camera in ("Camera", "FloorCamera", "RepairCamera"):
        viewport.set_active_camera("/World/" + camera)
        for _ in range(12):
            app.update()
        capture = capture_viewport_to_file(viewport, str(output / f"{camera}.png"))
        saved = asyncio.ensure_future(capture.wait_for_result())
        for _ in range(300):
            app.update()
            if saved.done():
                saved.result()
                break
        else:
            raise RuntimeError("Scene capture did not complete")
    viewport.set_active_camera("/World/Camera")
    world.play()


def main(app, args):
    carb.settings.get_settings().set_int("/persistent/app/viewport/displayOptions", 0)
    world = World(physics_dt=STEP, rendering_dt=STEP, stage_units_in_meters=1)
    env = simpy.Environment()
    scene = LargeScene(omni.usd.get_context().get_stage(), env, args.transport_speed)
    factory = Production(env, transfer=scene.transfer, on_event=scene.handle_event, ship=scene.ship)
    viewport = get_active_viewport()
    viewport.set_texture_resolution((1440, 900))
    viewport.set_active_camera("/World/Camera")
    world.reset()
    with closing(ControlPanel(args.output, args.dashboard_port)) as panel:
        print(f"Control panel: http://127.0.0.1:{args.dashboard_port}", flush=True)
        run_factory(app, args, world, env, scene, factory, panel)


def run_factory(app, args, world, env, scene, factory, panel):
    started = perf_counter()
    peak_wip = 0
    captured = False
    frames = 0
    drain_started = None
    while factory.running or factory.active:
        panel.apply(factory, scene)
        if not app.is_running():
            raise RuntimeError("Factory closed before the run completed")
        if not args.continuous and env.now >= args.duration:
            factory.running = False
        if not factory.running:
            if drain_started is None:
                drain_started = env.now
            if env.now - drain_started >= args.max_seconds:
                raise RuntimeError("Factory exceeded its drain time limit")
        frame_start = perf_counter()
        env.run(until=env.now + STEP)
        scene.step(STEP)
        world.step(render=False)
        if not args.headless or frames % 10 == 0:
            world.render()
        while env.peek() <= env.now:
            env.step()
        frames += 1
        peak_wip = max(peak_wip, len(factory.active))
        if frames % 30 == 0:
            panel.publish(factory, scene)
            snapshot = panel.snapshot
            (args.output / "live.json").write_text(json.dumps(snapshot), encoding="utf-8")
        if not captured and env.now >= min(120, args.duration / 2):
            capture_views(app, world, args.output)
            captured = True
        if frames % 300 == 0:
            print(
                f"t={env.now:.0f}s wip={len(factory.active)} packed={factory.counts['packed']}",
                flush=True,
            )
        if not args.headless or args.realtime:
            sleep(max(0, STEP - (perf_counter() - frame_start)))
    panel.publish(factory, scene)
    summary = {
        **factory.snapshot(),
        "wall_seconds": perf_counter() - started,
        "peak_wip": peak_wip,
        "peak_moving": scene.peak_moving,
        "transport_speed": scene.speed,
        "transport": "guided lane movement",
    }
    assert summary["introduced"] == summary["packed"] + summary["scrapped"]
    assert not scene.positions
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "machines"}), flush=True)
