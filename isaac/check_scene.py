r"""Check scene movement with bundled USD libraries, without starting the Isaac app.

Run from the repository root:
    .\isaac-runtime\6.1.0\python.bat .\isaac\check_scene.py
"""

import os
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    usd = next((root / "isaac-runtime/6.1.0/extscache").glob("omni.usd.libs-*"))
    sys.path[:0] = [str(usd), str(root / "src")]
    with os.add_dll_directory(str(usd / "bin")):
        import simpy
        from factory_scene import FactoryScene
        from pxr import Gf, Usd, UsdGeom, UsdUtils

        from factory_intelligence.item import Item
        from factory_intelligence.main import setup_factory

        durations = []
        for speed in (2, 1):
            env = simpy.Environment()
            stage = Usd.Stage.CreateInMemory()
            items = [Item("A", 70), Item("B", 50), Item("C", 0)]
            scene = FactoryScene(stage, env, items, speed)
            # Moving a machine in the scene also moves its queue and processing targets.
            assembly = stage.GetPrimAtPath("/World/AssemblyMachine")
            assembly.GetAttribute("xformOp:translate").Set(Gf.Vec3d(-2.5, 1, 0))
            UsdGeom.Xformable(assembly).AddRotateZOp().Set(30)
            assert scene.target("AssemblyMachine/ProcessPoint") == Gf.Vec3d(-2.5, 1, 0.98)
            visits = []

            def on_event(record, scene=scene, visits=visits):
                scene.handle_event(record)
                if record["event"] == "started":
                    assert scene.positions[record["item_id"]].Get() == scene.target(
                        record["machine"] + "/ProcessPoint"
                    )
                    visits.append(record)

            machines = setup_factory(
                env, items, transfer=scene.transfer, on_event=on_event, processing_time_scale=0.05
            )
            while env.peek() != float("inf") or scene.moves:
                assert env.now < 300, "Batch did not complete"
                env.run(until=env.now + 1 / 30)
                scene.step(1 / 30)
                while env.peek() <= env.now:
                    env.step()
            assert [item.status for item in items] == ["packed", "packed", "scrapped"]
            assert [item.repair_attempts for item in items] == [0, 1, 2]
            assert len(visits) == 20
            assert all(not m.is_processing and not m.item_wait_queue for m in machines)
            for item in items:
                output = "PackedOutput" if item.status == "packed" else "ScrapOutput"
                assert scene.positions[item.id].Get() == scene.target(
                    output + "/DropPoint", scene.offsets[item.id]
                )
            assert all(stage.GetPrimAtPath(f"/World/{type(m).__name__}") for m in machines)
            durations.append(env.now)
        from factory_scene import SCENE_FILE

        _, textures, missing = UsdUtils.ComputeAllDependencies(str(SCENE_FILE))
        assert not missing
        assert len(textures) == 6
        assert durations[1] > durations[0]
        print(f"Scene checks passed at both speeds: {durations}")


if __name__ == "__main__":
    main()
