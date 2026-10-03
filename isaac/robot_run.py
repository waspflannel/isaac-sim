"""Run physical robot cells and the production model on one simulation clock."""

import json
from contextlib import closing
from time import perf_counter, sleep

import isaacsim.core.experimental.utils.app as app_utils
import omni.usd
import simpy
from isaacsim.core.simulation_manager import SimulationManager
from robot_factory_scene import RobotFactoryScene

from factory_intelligence.control_panel import ControlPanel
from factory_intelligence.edge.journal import FactoryJournal
from factory_intelligence.production import Production

STEP = 1 / 60


def main(app, args):
    SimulationManager.setup_simulation(dt=STEP, device="cpu")
    env = simpy.Environment()
    scene = RobotFactoryScene(omni.usd.get_context().get_stage(), env, args.transport_speed)
    journal = FactoryJournal(args.journal)

    def observe(event):
        scene.handle_event(event)
        journal.observe(event)

    factory = Production(env, transfer=scene.transfer, on_event=observe, ship=scene.ship)
    journal.factory = factory
    scene.journal = journal
    factory.feed_interval = 12
    for machine in factory.machines.values():
        machine.unload = scene.unload
    app.update()
    if not args.minimal:
        import carb
        from omni.kit.viewport.utility import get_active_viewport

        carb.settings.get_settings().set_int("/persistent/app/viewport/displayOptions", 0)
        get_active_viewport().set_active_camera("/World/Camera")
    app_utils.play()
    app.update()
    for cell in scene.cells.values():
        cell.scenario.articulation.reset_to_default_state()
    for _ in range(30):
        app.update()
    print(f"Started {len(scene.cells)} articulated production robots", flush=True)
    with closing(journal), closing(ControlPanel(args.output, args.dashboard_port)) as panel:
        journal.emit(
            "run.started",
            "Factory",
            env.now,
            capabilities=["operations", "queues", "quality_score", "physical_handling"],
            unavailable=["component_lots", "electrical_measurements", "process_revisions"],
        )
        try:
            run_factory(app, args, env, scene, factory, panel)
        except Exception as error:
            journal.emit("run.aborted", "Factory", env.now, reason=type(error).__name__)
            raise
        journal.emit("run.completed", "Factory", env.now)


def run_factory(app, args, env, scene, factory, panel):
    started = perf_counter()
    frames = peak_wip = peak_robots = 0
    captured = False
    if args.record:
        (args.output / "motion").mkdir(exist_ok=True)
    film_frame = 0
    drain_started = None
    while factory.running or factory.active:
        frame_started = perf_counter()
        app.update()
        if not app.is_running():
            raise RuntimeError("Isaac stopped before production drained")
        if not app_utils.is_playing() or not SimulationManager.is_simulating():
            continue
        panel.apply(factory, scene)
        if not args.continuous and env.now >= args.duration:
            factory.running = False
        if not factory.running:
            if drain_started is None:
                drain_started = env.now
            if env.now - drain_started >= args.max_seconds:
                raise TimeoutError("Robotic factory did not drain")
        env.run(until=env.now + STEP)
        scene.step(STEP)
        peak_wip = max(peak_wip, len(factory.active))
        peak_robots = max(peak_robots, sum(cell.job is not None for cell in scene.cells.values()))
        frames += 1
        if not args.minimal and not captured and env.now >= 30:
            from robot_capture import capture_views

            capture_views(app, args.output)
            captured = True
        if args.record and not args.minimal and 20 <= env.now < 32 and frames % 6 == 0:
            from robot_capture import capture

            capture(app, args.output / "motion" / f"{film_frame:04}.png", "RobotCamera")
            film_frame += 1
        if frames % 60 == 0:
            panel.publish(factory, scene, robots=scene.snapshot(), rendering=not args.minimal)
            (args.output / "live.json").write_text(json.dumps(panel.snapshot), encoding="utf-8")
        if frames % 300 == 0:
            scene.journal.snapshot()
        if frames % 600 == 0:
            print(
                f"t={env.now:.0f} wip={len(factory.active)} packed={factory.counts['packed']} "
                f"robot_moves={sum(cell.completed for cell in scene.cells.values())}",
                flush=True,
            )
        if args.realtime:
            sleep(max(0, STEP - (perf_counter() - frame_started)))
    summary = {
        **factory.snapshot(),
        "wall_seconds": perf_counter() - started,
        "peak_wip": peak_wip,
        "peak_active_robots": peak_robots,
        "robots": scene.snapshot(),
        "handling_evidence": {name: list(cell.history) for name, cell in scene.cells.items()},
        "transport": "guided conveyors; physical articulated pick/place at robot cells",
    }
    assert summary["introduced"] == summary["packed"] + summary["scrapped"]
    assert len(scene.pool) == 96
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in ("machines", "handling_evidence", "robots")
            }
        ),
        flush=True,
    )
