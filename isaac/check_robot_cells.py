"""Real headless load/unload check at separate authored cell transforms."""

import json
import traceback
from pathlib import Path


def main(app):
    import omni.kit.app

    omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
        "isaacsim.robot_motion.examples", True
    )
    import isaacsim.core.experimental.utils.app as app_utils
    import omni.usd
    import simpy
    from isaacsim.core.experimental.objects import Cube, GroundPlane
    from isaacsim.core.experimental.prims import GeomPrim, RigidPrim
    from isaacsim.core.simulation_manager import SimulationManager
    from pxr import Gf, UsdGeom
    from robot_cell import RobotCell

    SimulationManager.setup_simulation(dt=1 / 60, device="cpu")
    stage = omni.usd.get_context().get_stage()
    GroundPlane("/World/Ground")
    env = simpy.Environment()
    cells = []
    for index, offset in enumerate(((0, 0, 0), (4, 3, 0))):
        root = f"/World/Cell{index}"
        prim = stage.DefinePrim(root, "Xform")
        prim.GetReferences().AddReference(str(Path(__file__).parent / "scenes/robot_cell.usda"))
        UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(*offset))
        item_path = root + "/Item"
        pick = [offset[0] + 0.45, offset[1], -5]
        Cube(item_path, positions=[pick], sizes=0.08, colors=[[0.95, 0.5, 0.1]])
        RigidPrim(item_path, masses=0.05)
        GeomPrim(item_path, apply_collision_apis=True)
    for index in range(2):
        root = f"/World/Cell{index}"
        cells.append(
            RobotCell(
                env, root, exclude=(root + "/Item", root + "/Workbench", f"/World/Cell{1 - index}")
            )
        )
    app.update()
    app_utils.play()
    app.update()
    for cell in cells:
        cell.scenario.articulation.reset_to_default_state()
    for _ in range(30):
        app.update()
    # Supply the items after parking the arms, as the production feeder does.
    for cell in cells:
        target = (
            UsdGeom.XformCache()
            .GetLocalToWorldTransform(stage.GetPrimAtPath(cell.path + "/Input"))
            .ExtractTranslation()
        )
        body = RigidPrim(cell.path + "/Item")
        body.set_world_poses(positions=[list(target)])
        body.set_velocities([[0, 0, 0]], [[0, 0, 0]])
    for _ in range(15):
        app.update()

    def cycle(cell):
        cache = UsdGeom.XformCache()
        for _ in range(2):
            for name in ("Process", "Output", "Input"):
                target = cache.GetLocalToWorldTransform(
                    stage.GetPrimAtPath(cell.path + "/" + name)
                ).ExtractTranslation()
                yield cell.move(cell.path + "/Item", list(target), 0.04)

    tasks = [env.process(cycle(cell)) for cell in cells]
    for _ in range(18000):
        app.update()
        if not SimulationManager.is_simulating():
            continue
        env.run(until=env.now + 1 / 60)
        for cell in cells:
            cell.step(1 / 60)
        if all(task.triggered for task in tasks):
            break
    else:
        raise TimeoutError("Concurrent robot cycles did not complete")
    report = {cell.path: {**cell.snapshot(), "moves": list(cell.history)} for cell in cells}
    output = Path(__file__).resolve().parents[1] / ".data/isaac/robot-cells.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS: two concurrent robot cells completed twelve physical handoffs", flush=True)


if __name__ == "__main__":
    from isaacsim import SimulationApp

    app = SimulationApp(
        {"headless": True, "renderer": "MinimalRendering", "disable_viewport_updates": True}
    )
    exit_code = 1
    try:
        main(app)
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        app.close(exit_code=exit_code)
