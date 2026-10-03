"""Factory setup and execution; imported after the Isaac app starts."""

import json
from time import perf_counter, sleep

import omni.usd
import simpy
from factory_scene import FactoryScene
from isaacsim.core.api import World
from omni.kit.viewport.utility import get_active_viewport

from factory_intelligence.item import Item
from factory_intelligence.main import setup_factory

STEP_SECONDS = 1 / 30


def create_factory(args):
    world = World(physics_dt=STEP_SECONDS, rendering_dt=STEP_SECONDS, stage_units_in_meters=1)
    env = simpy.Environment()
    items = [Item("item-001", 70), Item("item-002", 50), Item("item-003", 0)]
    scene = FactoryScene(omni.usd.get_context().get_stage(), env, items, args.transport_speed)
    get_active_viewport().set_active_camera("/World/Camera")
    world.reset()
    setup_factory(
        env,
        items,
        transfer=scene.transfer,
        on_event=scene.handle_event,
        processing_time_scale=args.processing_time_scale,
    )
    return world, env, scene, items


def run_factory(app, world, env, scene, args):
    while env.peek() != float("inf") or scene.moves:
        if not app.is_running():
            raise RuntimeError("Factory window closed before the batch completed")
        if env.now >= args.max_seconds:
            raise RuntimeError("Factory exceeded the simulation time limit")
        started = perf_counter()
        env.run(until=env.now + STEP_SECONDS)
        scene.step(STEP_SECONDS)
        world.step(render=False)
        world.render()
        # Resolve arrivals and SimPy's stop-event cleanup before checking completion.
        while env.peek() <= env.now:
            env.step()
        if not args.headless:
            sleep(max(0, STEP_SECONDS - (perf_counter() - started)))


def save_result(env, items, output):
    summary = {"time_seconds": env.now, "items": [vars(item) for item in items]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary), flush=True)


def main(app, args):
    world, env, scene, items = create_factory(args)
    run_factory(app, world, env, scene, args)
    save_result(env, items, args.output)
