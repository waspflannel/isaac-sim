"""Check the large saved scene and continuous production without launching Isaac."""

import os
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    usd = next((root / "isaac-runtime/6.1.0/extscache").glob("omni.usd.libs-*"))
    sys.path[:0] = [str(usd), str(root / "src")]
    with os.add_dll_directory(str(usd / "bin")):
        import simpy
        from large_scene import ASSETS, LargeScene
        from pxr import Usd, UsdUtils

        from factory_intelligence.production import Production

        env = simpy.Environment()
        stage = Usd.Stage.CreateInMemory()
        scene = LargeScene(stage, env)
        visits = 0

        def on_event(event):
            nonlocal visits
            scene.handle_event(event)
            if event["event"] == "started":
                assert scene.positions[event["item_id"]].Get() == scene.target(
                    event["machine"] + "/ProcessPoint"
                )
                visits += 1

        factory = Production(env, transfer=scene.transfer, on_event=on_event, ship=scene.ship)
        peak_wip = 0
        while env.now < 240 or factory.active:
            if env.now >= 240:
                factory.running = False
            assert env.now < 1500, "Production failed to drain"
            env.run(until=env.now + 1 / 30)
            scene.step(1 / 30)
            while env.peek() <= env.now:
                env.step()
            peak_wip = max(peak_wip, len(factory.active))
        summary = factory.snapshot()
        assert summary["introduced"] == summary["packed"] + summary["scrapped"]
        assert summary["packed"] > 30 and summary["repaired"] > 0 and summary["scrapped"] > 0
        assert peak_wip >= 40 and scene.peak_moving >= 20
        assert not scene.positions and not scene.moves and not scene.slots and not scene.pending
        assert all(s["max_queue"] <= 3 for s in summary["machines"].values())
        _, _, missing = UsdUtils.ComputeAllDependencies(str(ASSETS / "large_factory.usda"))
        assert not missing
        print(
            {
                "stations": len(factory.machines),
                "visits": visits,
                "peak_wip": peak_wip,
                "peak_moving": scene.peak_moving,
                "packed": summary["packed"],
                "scrapped": summary["scrapped"],
                "drained_at": env.now,
            }
        )


if __name__ == "__main__":
    main()
