"""Named machine boxes and items moving through the production line."""

from pxr import Gf, UsdGeom, UsdLux

STATIONS = {
    "KitMachine": (-5.0, 0.0),
    "AssemblyMachine": (-2.5, 0.0),
    "CalibrationMachine": (0.0, 0.0),
    "TestMachine": (2.5, 0.0),
    "PackingMachine": (5.0, 0.0),
    "RepairMachine": (2.5, 3.0),
}


def box(stage, path, position, scale, color):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1)
    translation = cube.AddTranslateOp()
    translation.Set(Gf.Vec3d(*position))
    cube.AddScaleOp().Set(Gf.Vec3f(*scale))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    return translation


class FactoryScene:
    def __init__(self, stage, env, items, speed=2.0):
        self.env = env
        self.speed = speed
        self.items = {item.id: item for item in items}
        self.offsets = {item.id: index * 0.5 - 0.5 for index, item in enumerate(items)}
        self.moves = {}
        self.create_floor_and_camera(stage)
        self.place_machines(stage)
        self.positions = {
            item.id: box(
                stage,
                f"/World/Items/Item_{index}",
                (-6.5, index * 0.5 - 1, 0.98),
                (0.35, 0.3, 0.25),
                (0.2, 0.65, 0.95),
            )
            for index, item in enumerate(items)
        }

    def create_floor_and_camera(self, stage):
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        box(stage, "/World/Floor", (0, 1, -0.1), (16, 10, 0.2), (0.12, 0.15, 0.18))
        UsdLux.DomeLight.Define(stage, "/World/Light").CreateIntensityAttr(900)
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        view = Gf.Matrix4d().SetLookAt(
            Gf.Vec3d(12, -17, 14), Gf.Vec3d(0, 0.5, 0), Gf.Vec3d(0, 0, 1)
        )
        camera.AddTransformOp().Set(view.GetInverse())
        camera.CreateFocalLengthAttr(30)

    def place_machines(self, stage):
        for name, (x, y) in STATIONS.items():
            box(stage, f"/World/Machines/{name}", (x, y, 0.4), (1.6, 1, 0.8), (0.18, 0.38, 0.48))
        box(stage, "/World/Packed", (5, -3, 0.4), (1.7, 0.7, 0.8), (0.15, 0.5, 0.25))
        box(stage, "/World/Scrap", (2.5, -3, 0.4), (1.7, 0.7, 0.8), (0.6, 0.15, 0.12))

    def move(self, item_id, target):
        # ponytail: straight-line visual motion; add robot control when the cell needs it.
        arrived = self.env.event()
        self.moves[item_id] = (Gf.Vec3d(*target), arrived)
        return arrived

    def transfer(self, item, machine):
        def arrive():
            if item.id in self.moves:
                yield self.moves[item.id][1]
            x, y = STATIONS[type(machine).__name__]
            yield self.move(item.id, (x, y, 0.98))

        return self.env.process(arrive())

    def handle_event(self, record):
        item_id, event = record["item_id"], record["event"]
        item = self.items[item_id]
        if event == "queued":
            x, y = STATIONS[record["machine"]]
            self.move(item_id, (x + self.offsets[item_id], y - 1.2, 0.98))
        elif event == "scrapped" or (event == "finished" and item.status == "packed"):
            x = 5 if item.status == "packed" else 2.5
            self.move(item_id, (x + self.offsets[item_id], -3, 0.98))

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
