"""Bind continuous production to the authored factory and its route markers."""

from pathlib import Path

from factory_scene import FactoryScene
from pxr import Gf, UsdGeom

ASSETS = Path(__file__).parent / "scenes"


class LargeScene(FactoryScene):
    def __init__(self, stage, env, speed=4):
        stage.DefinePrim("/World").GetReferences().AddReference(str(ASSETS / "large_factory.usda"))
        self.stage, self.env, self.speed = stage, env, speed
        self.positions, self.moves, self.pending, self.locations, self.slots = {}, {}, {}, {}, {}
        self.peak_moving = 0

    def handle_event(self, event):
        item_id, kind = event["item_id"], event["event"]
        if kind == "introduced":
            prim = self.stage.DefinePrim("/World/Items/" + item_id.replace("-", "_"))
            prim.GetReferences().AddReference(str(ASSETS / "factory.usda"), "/World/Items/Item_0")
            self.positions[item_id] = prim.GetAttribute("xformOp:translate")
            self.locations[item_id] = f"Incoming/Line{event['line']:02}"
            self.positions[item_id].Set(self.target(self.locations[item_id]))
        elif kind == "queued":
            station = event["machine"]
            used = {slot for name, slot in self.slots.values() if name == station}
            slot = next(index for index in range(4) if index not in used)
            self.slots[item_id] = station, slot
            destination = self.target(station + "/QueuePoint", slot * 0.45 - 0.675)
            self.pending[item_id] = self.env.process(self.travel(item_id, destination))
        elif kind == "started":
            self.slots.pop(item_id)
            self.pending.pop(item_id)
            self.locations[item_id] = event["machine"]
        elif kind == "shipped":
            self.stage.RemovePrim(self.positions.pop(item_id).GetPrim().GetPath())
            self.locations.pop(item_id)
        if kind in ("started", "finished", "blocked", "released"):
            color = {
                "started": (0.15, 0.9, 0.5),
                "finished": (0.18, 0.38, 0.48),
                "blocked": (1, 0.28, 0.05),
                "released": (0.18, 0.38, 0.48),
            }[kind]
            lamp = self.stage.GetPrimAtPath("/World/" + event["machine"] + "/Status")
            UsdGeom.Gprim(lamp).GetDisplayColorAttr().Set([Gf.Vec3f(*color)])

    def travel(self, item_id, destination):
        source = self.locations[item_id]
        exit_point = self.target(
            source if source.startswith("Incoming/") else source + "/QueuePoint"
        )
        yield self.move(item_id, exit_point)
        # ponytail: guided lane travel; traffic reservations belong with future vehicle control.
        if abs(exit_point[1] - destination[1]) > 0.01:
            aisle = self.target("Routes/ServiceAisle")[0]
            yield self.move(item_id, Gf.Vec3d(aisle, exit_point[1], exit_point[2]))
            yield self.move(item_id, Gf.Vec3d(aisle, destination[1], destination[2]))
        yield self.move(item_id, destination)

    def transfer(self, item, machine):
        def arrive():
            yield self.pending[item.id]
            yield self.move(item.id, self.target(machine.station_id + "/ProcessPoint"))

        return self.env.process(arrive())

    def ship(self, item):
        path = f"Shipping/Line{item.line:02}" if item.status == "packed" else "Scrap/DropPoint"
        return self.env.process(self.travel(item.id, self.target(path)))

    def step(self, dt):
        self.peak_moving = max(self.peak_moving, len(self.moves))
        super().step(dt)
