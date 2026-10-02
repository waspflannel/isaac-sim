"""A compact factory with guided pallet transport, built entirely through USD APIs."""

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pxr import Gf, Sdf, UsdGeom, UsdLux, UsdPhysics, UsdShade

STATIONS = {
    "KitMachine": (-5.0, 0.0),
    "AssemblyMachine": (-2.5, 0.0),
    "CalibrationMachine": (0.0, 0.0),
    "TestMachine": (2.5, 0.0),
    "PackingMachine": (5.0, 0.0),
    "RepairMachine": (2.5, 3.0),
}
IDLE = (0.18, 0.38, 0.48)
BUSY = (1.0, 0.65, 0.12)


def box(stage, path, position, scale, color, *, collision=False, moving=False):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1)
    cube.AddTranslateOp().Set(Gf.Vec3d(*position))
    cube.AddScaleOp().Set(Gf.Vec3f(*scale))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    if collision:
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    if moving:
        body = UsdPhysics.RigidBodyAPI.Apply(cube.GetPrim())
        body.CreateKinematicEnabledAttr(True)
    return cube


def station_label(stage, root, text, x, y, directory):
    directory.mkdir(exist_ok=True)
    image = Image.new("RGB", (512, 128), (20, 35, 45))
    ImageDraw.Draw(image).text(
        (256, 64), text.upper(), fill="white", font=ImageFont.load_default(size=40), anchor="mm"
    )
    texture_path = directory / f"{text}.png"
    image.save(texture_path)
    sign = UsdGeom.Mesh.Define(stage, root + "/Label")
    sign.CreatePointsAttr(
        [
            (x - 0.75, y - 0.12, 1.92),
            (x + 0.75, y - 0.12, 1.92),
            (x + 0.75, y - 0.12, 2.24),
            (x - 0.75, y - 0.12, 2.24),
        ]
    )
    sign.CreateFaceVertexCountsAttr([4])
    sign.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    sign.CreateSubdivisionSchemeAttr("none")
    UsdGeom.PrimvarsAPI(sign).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex
    ).Set([(0, 0), (1, 0), (1, 1), (0, 1)])
    material = UsdShade.Material.Define(stage, root + "/LabelMaterial")
    shader = UsdShade.Shader.Define(stage, root + "/LabelMaterial/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    texture = UsdShade.Shader.Define(stage, root + "/LabelMaterial/Texture")
    texture.CreateIdAttr("UsdUVTexture")
    texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(str(texture_path.resolve()))
    uv = UsdShade.Shader.Define(stage, root + "/LabelMaterial/UV")
    uv.CreateIdAttr("UsdPrimvarReader_float2")
    uv.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
        uv.ConnectableAPI(), "result"
    )
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        texture.ConnectableAPI(), "rgb"
    )
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(sign.GetPrim()).Bind(material)


class FactoryScene:
    def __init__(self, stage, env, items, event_file, speed=2.0):
        self.stage = stage
        self.env = env
        self.items = {item.id: item for item in items}
        self.indices = {item.id: index for index, item in enumerate(items)}
        self.event_file = event_file
        self.speed = speed
        self.moves = {}
        self.pallets = {}
        self.lamps = {}
        self.transfers = 0
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        stage.SetDefaultPrim(stage.DefinePrim("/World", "Xform"))
        box(stage, "/World/Floor", (0, 1, -0.1), (16, 10, 0.2), (0.12, 0.15, 0.18), collision=True)
        light = UsdLux.DomeLight.Define(stage, "/World/Light")
        light.CreateIntensityAttr(900)
        for name, (x, y) in STATIONS.items():
            root = f"/World/{name}"
            stage.DefinePrim(root, "Xform").SetDisplayName(name.replace("Machine", ""))
            box(stage, root + "/Table", (x, y, 0.4), (1.6, 1, 0.8), IDLE, collision=True)
            box(stage, root + "/Worktop", (x, y, 0.82), (1.7, 1.1, 0.08), (0.5, 0.56, 0.6))
            # Open frame keeps the product visible while suggesting a processing fixture.
            for side in (-0.65, 0.65):
                box(
                    stage,
                    root + ("/Left" if side < 0 else "/Right"),
                    (x + side, y + 0.32, 1.25),
                    (0.1, 0.15, 0.8),
                    IDLE,
                )
            box(stage, root + "/Beam", (x, y + 0.32, 1.65), (1.4, 0.15, 0.12), IDLE)
            self.lamps[name] = box(
                stage, root + "/Status", (x, y + 0.32, 1.8), (0.22, 0.22, 0.18), IDLE
            )
            box(stage, root + "/Queue", (x, y - 1.2, 0.72), (1.7, 0.5, 0.2), (0.24, 0.27, 0.3))
            label = stage.GetPrimAtPath(root)
            label.CreateAttribute("factory:state", Sdf.ValueTypeNames.String).Set("idle")
            label.CreateAttribute("factory:queueCount", Sdf.ValueTypeNames.Int).Set(0)
            station_label(
                stage,
                root,
                name.replace("Machine", ""),
                x,
                y,
                Path(event_file.name).parent / "labels",
            )
        box(stage, "/World/TransportLane", (0, -2.0, 0.6), (13, 0.5, 0.15), (0.25, 0.3, 0.32))
        for name, x, color in [("Packed", 5, (0.15, 0.5, 0.25)), ("Scrap", 2.5, (0.6, 0.15, 0.12))]:
            box(stage, f"/World/{name}Output", (x, -3, 0.65), (1.7, 0.7, 0.3), color)
        for item in items:
            self.pallets[item.id] = box(
                stage,
                f"/World/Items/Item_{self.indices[item.id]}",
                (-6.5, -1.0 + self.indices[item.id] * 0.5, 0.98),
                (0.35, 0.3, 0.25),
                (0.2, 0.65, 0.95),
                collision=True,
                moving=True,
            )
            self.pallets[item.id].GetPrim().CreateAttribute(
                "factory:itemId", Sdf.ValueTypeNames.String
            ).Set(item.id)
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        matrix = Gf.Matrix4d().SetLookAt(
            Gf.Vec3d(12, -17, 14), Gf.Vec3d(0, 0.5, 0), Gf.Vec3d(0, 0, 1)
        )
        camera.AddTransformOp().Set(matrix.GetInverse())
        camera.CreateClippingRangeAttr(Gf.Vec2f(0.1, 1000))
        camera.CreateFocalLengthAttr(30)

    def position(self, item_id):
        return self.pallets[item_id].GetPrim().GetAttribute("xformOp:translate").Get()

    def move(self, item_id, target):
        # ponytail: guided kinematic pallets, not belt friction or robot gripping.
        # Replace this transport with measured robot/conveyor motion when adding that cell.
        done = self.env.event()
        self.moves[item_id] = (Gf.Vec3d(*target), done)
        return done

    def transfer(self, item, machine):
        def arrive():
            # Finish the queue transfer before loading the processing position.
            if item.id in self.moves:
                yield self.moves[item.id][1]
            x, y = STATIONS[type(machine).__name__]
            yield self.move(item.id, (x, y, 0.98))
            self.transfers += 1

        return self.env.process(arrive())

    def handle_event(self, record):
        self.event_file.write(json.dumps(record) + "\n")
        self.event_file.flush()
        item_id, name, event = record["item_id"], record["machine"], record["event"]
        item = self.items[item_id]
        station = self.stage.GetPrimAtPath(f"/World/{name}")
        count = station.GetAttribute("factory:queueCount")
        if event == "queued":
            count.Set(count.Get() + 1)
            x, y = STATIONS[name]
            self.move(item_id, (x - 0.5 + self.indices[item_id] * 0.5, y - 1.2, 0.98))
        elif event == "started":
            count.Set(count.Get() - 1)
            station.GetAttribute("factory:state").Set("processing")
            self.lamps[name].GetDisplayColorAttr().Set([Gf.Vec3f(*BUSY)])
        elif event == "finished":
            station.GetAttribute("factory:state").Set("idle")
            self.lamps[name].GetDisplayColorAttr().Set([Gf.Vec3f(*IDLE)])
        if event == "scrapped" or (event == "finished" and item.status == "packed"):
            packed = item.status == "packed"
            color = (0.1, 0.85, 0.3) if packed else (0.9, 0.15, 0.1)
            self.pallets[item_id].GetDisplayColorAttr().Set([Gf.Vec3f(*color)])
            self.move(item_id, self.output_position(item))

    def output_position(self, item):
        x = 5 if item.status == "packed" else 2.5
        return (x - 0.5 + self.indices[item.id] * 0.5, -3, 0.98)

    def step(self, dt):
        for item_id, (target, done) in list(self.moves.items()):
            current = self.position(item_id)
            distance = (target - current).GetLength()
            position = (
                target
                if distance <= self.speed * dt
                else current + (target - current) * (self.speed * dt / distance)
            )
            self.pallets[item_id].GetPrim().GetAttribute("xformOp:translate").Set(position)
            if math.dist(self.position(item_id), target) < 1e-6:
                del self.moves[item_id]
                done.succeed()
