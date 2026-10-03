"""Connect production to authored stations, guided conveyors, and physical robots."""

from collections import deque
from pathlib import Path

import numpy as np
from isaacsim.core.experimental.prims import RigidPrim
from pxr import Gf, UsdGeom, UsdPhysics
from robot_cell import RobotCell


class RobotFactoryScene:
    def __init__(self, stage, env, speed=1.5):
        self.stage, self.env, self.speed = stage, env, speed
        stage.DefinePrim("/World").GetReferences().AddReference(
            str(Path(__file__).parent / "scenes/robot_factory.usda")
        )
        self.pool = deque(
            prim.GetPath().pathString for prim in stage.GetPrimAtPath("/World/Items").GetChildren()
        )
        self.items, self.pending, self.slots, self.moves = {}, {}, {}, {}
        self.peak_moving = 0
        self.cells = {}
        stations = list(stage.GetPrimAtPath("/World/Stations").GetChildren())
        for station in stations:
            path = station.GetPath().pathString
            if stage.GetPrimAtPath(path + "/Robot"):
                # Independent fenced cells have disjoint reach envelopes. Track the
                # local safety proxies; contact supports stay physically collidable.
                exclude = [other.GetPath().pathString for other in stations if other != station]
                exclude += ["/World/Decor", "/World/Items", path + "/Workbench", path + "/Fixtures"]
                self.cells[station.GetName()] = RobotCell(env, path, exclude=exclude)

    def station_path(self, name):
        return "/World/Stations/" + name.replace("/", "_")

    def target(self, path, offset=0):
        return np.array(
            UsdGeom.Xformable(self.stage.GetPrimAtPath(path))
            .ComputeLocalToWorldTransform(0)
            .Transform(Gf.Vec3d(offset, 0, 0))
        )

    def body(self, item_id):
        return self.items[item_id][1]

    def set_guided(self, item_id, guided):
        prim = self.stage.GetPrimAtPath(self.items[item_id][0])
        UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Set(guided)
        UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Set(not guided)

    def handle_event(self, event):
        item_id, kind = event["item_id"], event["event"]
        if kind == "introduced":
            path = self.pool.popleft()
            body = RigidPrim(path)
            self.items[item_id] = path, body
            UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).MakeVisible()
            body.set_world_poses(
                positions=[self.target(f"/World/Incoming/Line{event['line']:02}")],
                orientations=[[1, 0, 0, 0]],
            )
        elif kind == "queued":
            station = event["machine"]
            used = {slot for name, slot in self.slots.values() if name == station}
            slot = next(index for index in range(4) if index not in used)
            self.slots[item_id] = station, slot
            target = self.target(self.station_path(station) + "/QueuePoint", slot * 0.25)
            self.pending[item_id] = self.env.process(self.travel(item_id, target))
        elif kind == "started":
            self.slots.pop(item_id)
            self.pending.pop(item_id)
        elif kind == "shipped":
            self.set_guided(item_id, True)
            path, body = self.items.pop(item_id)
            body.set_world_poses(positions=[[0, 0, -5]])
            UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).MakeInvisible()
            self.pool.append(path)
        if kind in ("started", "finished", "blocked", "released"):
            color = {
                "started": (0.1, 0.9, 0.45),
                "finished": (0.12, 0.3, 0.35),
                "blocked": (1, 0.25, 0.03),
                "released": (0.12, 0.3, 0.35),
            }[kind]
            lamp = self.stage.GetPrimAtPath(self.station_path(event["machine"]) + "/Status")
            UsdGeom.Gprim(lamp).GetDisplayColorAttr().Set([Gf.Vec3f(*color)])

    def move(self, item_id, target):
        arrived = self.env.event()
        self.moves[item_id] = target, arrived
        return arrived

    def travel(self, item_id, target):
        self.set_guided(item_id, True)
        current = self.body(item_id).get_world_poses()[0].numpy()[0]
        # Guided conveyor/overhead routing is explicitly separate from robot physics.
        yield self.move(item_id, np.array([current[0], current[1], 1.21]))
        if abs(target[1] - current[1]) > 1.5:
            yield self.move(item_id, np.array([11, current[1], 1.21]))
            yield self.move(item_id, np.array([11, target[1], 1.21]))
        else:
            yield self.move(item_id, np.array([current[0], target[1], 1.21]))
        yield self.move(item_id, np.array([target[0], target[1], 1.21]))
        yield self.move(item_id, target)

    def transfer(self, item, machine):
        def load():
            yield self.pending[item.id]
            station = self.station_path(machine.station_id)
            cell = self.cells.get(machine.station_id.replace("/", "_"))
            yield self.move(item.id, self.target(station + "/Input"))
            if cell:
                self.set_guided(item.id, False)
                yield self.env.timeout(0.25)
                yield cell.move(self.items[item.id][0], self.target(station + "/Process"), 0.04)
            else:
                yield self.move(item.id, self.target(station + "/Process"))

        return self.env.process(load())

    def unload(self, item, machine):
        def release():
            station = self.station_path(machine.station_id)
            cell = self.cells.get(machine.station_id.replace("/", "_"))
            if cell:
                yield cell.move(self.items[item.id][0], self.target(station + "/Output"), 0.04)
            else:
                yield self.move(item.id, self.target(station + "/Output"))

        return self.env.process(release())

    def ship(self, item):
        target = (
            f"/World/Shipping/Line{item.line:02}" if item.status == "packed" else "/World/Scrap"
        )
        return self.env.process(self.travel(item.id, self.target(target)))

    def step(self, dt):
        self.peak_moving = max(self.peak_moving, len(self.moves))
        for item_id, (target, arrived) in list(self.moves.items()):
            body = self.body(item_id)
            current = body.get_world_poses()[0].numpy()[0]
            distance = float(np.linalg.norm(target - current))
            if distance <= self.speed * dt:
                body.set_world_poses(positions=[target])
                del self.moves[item_id]
                arrived.succeed()
            else:
                body.set_world_poses(
                    positions=[current + (target - current) * (self.speed * dt / distance)]
                )
        for cell in self.cells.values():
            cell.step(dt)

    def snapshot(self):
        return {name: cell.snapshot() for name, cell in self.cells.items()}
