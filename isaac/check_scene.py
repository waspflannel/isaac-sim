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
        from factory_scene import STATIONS, FactoryScene
        from pxr import Gf, Usd

        from factory_intelligence.item import Item
        from factory_intelligence.main import setup_factory

        durations = []
        for speed in (2, 1):
            env = simpy.Environment()
            stage = Usd.Stage.CreateInMemory()
            items = [Item("A", 70), Item("B", 50), Item("C", 0)]
            scene = FactoryScene(stage, env, items, speed)
            visits = []

            def on_event(record, scene=scene, visits=visits):
                scene.handle_event(record)
                if record["event"] == "started":
                    x, y = STATIONS[record["machine"]]
                    assert scene.positions[record["item_id"]].Get() == Gf.Vec3d(x, y, 0.98)
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
                x = 5 if item.status == "packed" else 2.5
                assert scene.positions[item.id].Get() == Gf.Vec3d(
                    x + scene.offsets[item.id], -3, 0.98
                )
            assert all(stage.GetPrimAtPath(f"/World/Machines/{name}") for name in STATIONS)
            durations.append(env.now)
        assert durations[1] > durations[0]
        print(f"Scene checks passed at both speeds: {durations}")


if __name__ == "__main__":
    main()
