"""Continuous production across eight lines and two shared repair cells."""

from collections import deque
from random import Random

import simpy

from factory_intelligence.item import Item
from factory_intelligence.machines.assembly_machine import AssemblyMachine
from factory_intelligence.machines.calibration_machine import CalibrationMachine
from factory_intelligence.machines.kit_machine import KitMachine
from factory_intelligence.machines.packing_machine import PackingMachine
from factory_intelligence.machines.repair_machine import RepairMachine
from factory_intelligence.machines.test_machine import TestMachine

LINE_MACHINES = [
    (KitMachine, 2),
    (AssemblyMachine, 4),
    (CalibrationMachine, 3),
    (TestMachine, 5),
    (PackingMachine, 2),
]


class Production:
    def __init__(self, env, *, transfer=None, on_event=None, ship=None, max_wip=96):
        self.env = env
        self.on_event = on_event
        self.ship = ship
        self.feed_interval = 4.0
        self.enabled_lines = set(range(1, 9))
        self.running = True
        self.active = {}
        self.completed = deque(maxlen=128)
        self.counts = {"introduced": 0, "packed": 0, "scrapped": 0, "repaired": 0}
        self.space = simpy.Container(env, capacity=max_wip, init=max_wip)
        self.random = Random(7)
        self.machines = {}
        self.statistics = {}
        self.lines = {}
        self.repairs = []
        for cell in range(1, 3):
            repair = self.add_machine(RepairMachine, f"Repair{cell}/RepairMachine", 8, transfer)
            retest = self.add_machine(TestMachine, f"Repair{cell}/TestMachine", 3, transfer)
            retest.max_repairs = 0
            repair.connect(retest)
            retest.output_route = lambda item: self.lines[item.line][-1]
            self.repairs.append(repair)
        for line in range(1, 9):
            machines = [
                self.add_machine(cls, f"Line{line:02}/{cls.__name__}", seconds, transfer)
                for cls, seconds in LINE_MACHINES
            ]
            for current, following in zip(machines, machines[1:], strict=False):
                current.connect(following)
            machines[3].max_repairs = 1
            machines[3].repair_machine = self.repairs[(line - 1) // 4]
            self.lines[line] = machines
        self.feeders = [env.process(self.feed(line)) for line in self.lines]

    def add_machine(self, cls, name, seconds, transfer):
        machine = cls(self.env, seconds)
        machine.station_id = name
        machine.queue_capacity = 3
        machine.transfer = transfer
        machine.on_event = self.handle_event
        self.machines[name] = machine
        self.statistics[name] = {
            "state": "idle",
            "since": 0,
            "processing": 0,
            "blocked": 0,
            "transporting": 0,
            "idle": 0,
            "processed": 0,
            "max_queue": 0,
        }
        return machine

    def feed(self, line):
        while self.running:
            if line in self.enabled_lines:
                yield self.space.get(1)
                if not self.running or line not in self.enabled_lines:
                    self.space.put(1)
                else:
                    self.counts["introduced"] += 1
                    item = Item(
                        f"unit-{self.counts['introduced']:06}",
                        self.random.choices([70, 50, 25, 0], [75, 18, 5, 2])[0],
                    )
                    item.line = line
                    item.entered_at = self.env.now
                    self.active[item.id] = item
                    self.emit({"event": "introduced", "item_id": item.id, "line": line})
                    yield self.lines[line][0].input_item(item)
            yield self.env.timeout(self.feed_interval)

    def emit(self, event):
        if self.on_event:
            self.on_event({"time_seconds": self.env.now, **event})

    def handle_event(self, event):
        name, kind = event["machine"], event["event"]
        machine = self.machines[name]
        stats = self.statistics[name]
        states = {
            "transporting": "transporting",
            "started": "processing",
            "finished": "idle",
            "blocked": "blocked",
            "released": "idle",
        }
        if kind in states:
            stats[stats["state"]] += self.env.now - stats["since"]
            stats["since"], stats["state"] = self.env.now, states[kind]
        stats["max_queue"] = max(stats["max_queue"], len(machine.item_wait_queue))
        if kind == "finished":
            stats["processed"] += 1
        self.emit(event)
        item = self.active[event["item_id"]]
        if kind == "scrapped" or (kind == "finished" and item.status == "packed"):
            self.env.process(self.dispatch(item))

    def dispatch(self, item):
        if self.ship:
            yield self.ship(item)
        self.counts[item.status] += 1
        self.counts["repaired"] += item.repair_attempts > 0
        item.completed_at = self.env.now
        self.completed.append(item)
        del self.active[item.id]
        self.space.put(1)
        self.emit(
            {"event": "shipped", "item_id": item.id, "status": item.status, "line": item.line}
        )

    def snapshot(self):
        stations = {}
        for name, stats in self.statistics.items():
            elapsed = self.env.now - stats["since"]
            processing = stats["processing"] + (elapsed if stats["state"] == "processing" else 0)
            blocked = stats["blocked"] + (elapsed if stats["state"] == "blocked" else 0)
            stations[name] = {
                "state": stats["state"],
                "queue": len(self.machines[name].item_wait_queue),
                "capacity": self.machines[name].queue_capacity,
                "utilization": processing / self.env.now if self.env.now else 0,
                "blocked_seconds": blocked,
                "processed": stats["processed"],
                "max_queue": stats["max_queue"],
            }
        busiest = max(stations, key=lambda name: stations[name]["utilization"])
        return {
            "time_seconds": self.env.now,
            **self.counts,
            "wip": len(self.active),
            "throughput_per_minute": self.counts["packed"] * 60 / self.env.now
            if self.env.now
            else 0,
            "bottleneck": busiest,
            "machines": stations,
            "controls": {
                "feed_interval": self.feed_interval,
                "enabled_lines": sorted(self.enabled_lines),
            },
        }
