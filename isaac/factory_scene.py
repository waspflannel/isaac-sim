"""Move demo items through the factory layout saved in scenes/factory.usda."""

from pathlib import Path

from pxr import Gf, UsdGeom

SCENE_FILE = Path(__file__).parent / "scenes" / "factory.usda"


class FactoryScene:
    def __init__(self, stage, env, items, speed=2.0):
        stage.DefinePrim("/World").GetReferences().AddReference(str(SCENE_FILE))
        self.stage = stage
        self.env = env
        self.speed = speed
        self.items = {item.id: item for item in items}
        self.offsets = {item.id: index * 0.5 - 0.5 for index, item in enumerate(items)}
        self.moves = {}
        self.positions = {
            item.id: stage.GetPrimAtPath(f"/World/Items/Item_{index}").GetAttribute(
                "xformOp:translate"
            )
            for index, item in enumerate(items)
        }

    def target(self, path, offset=0):
        marker = UsdGeom.Xformable(self.stage.GetPrimAtPath("/World/" + path))
        return marker.ComputeLocalToWorldTransform(0).Transform(Gf.Vec3d(offset, 0, 0))

    def move(self, item_id, target):
        # ponytail: straight-line visual motion; add robot control when the cell needs it.
        arrived = self.env.event()
        self.moves[item_id] = (target, arrived)
        return arrived

    def transfer(self, item, machine):
        def arrive():
            if item.id in self.moves:
                yield self.moves[item.id][1]
            yield self.move(item.id, self.target(type(machine).__name__ + "/ProcessPoint"))

        return self.env.process(arrive())

    def handle_event(self, record):
        item_id, event = record["item_id"], record["event"]
        item = self.items[item_id]
        if event == "queued":
            path = record["machine"] + "/QueuePoint"
            self.move(item_id, self.target(path, self.offsets[item_id]))
        elif event == "scrapped" or (event == "finished" and item.status == "packed"):
            output = "PackedOutput" if item.status == "packed" else "ScrapOutput"
            self.move(item_id, self.target(output + "/DropPoint", self.offsets[item_id]))

    def step(self, dt):
        for item_id, (target, arrived) in list(self.moves.items()):
            position = self.positions[item_id]
            current = position.Get()
            distance = (target - current).GetLength()
            if distance <= self.speed * dt:
                position.Set(target)
                del self.moves[item_id]
                arrived.succeed()
            else:
                position.Set(current + (target - current) * (self.speed * dt / distance))
