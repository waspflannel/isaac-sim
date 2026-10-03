import json
from abc import ABC, abstractmethod
from collections import deque

import simpy


class Machine(ABC):
    def __init__(self, env, item_processing_time):
        self.env = env
        self.item_processing_time = item_processing_time
        self.item_wait_queue = deque()
        self.is_processing = False
        self.next_machine = None
        self.transfer = None
        self.on_event = None
        self.station_id = type(self).__name__
        self.queue_capacity = None
        self.queue_space = env.event()
        self.output_route = None

    def connect(self, next_machine):
        self.next_machine = next_machine

    def input_item(self, item):
        if self.queue_capacity is not None:
            return self.env.process(self.wait_for_space(item))
        self.enqueue(item)

    def wait_for_space(self, item):
        while len(self.item_wait_queue) >= self.queue_capacity:
            yield self.queue_space
        self.enqueue(item)

    def enqueue(self, item):
        self.item_wait_queue.append(item)
        self.log_activity(item, "queued")
        if not self.is_processing:
            self.is_processing = True
            self.env.process(self.run())

    def run(self):
        try:
            while self.item_wait_queue:
                item = self.item_wait_queue.popleft()
                self.queue_space.succeed()
                self.queue_space = self.env.event()
                if self.transfer is not None:
                    if self.queue_capacity is not None:
                        self.log_activity(item, "transporting")
                    yield self.transfer(item, self)
                self.log_activity(item, "started")
                yield self.env.timeout(self.item_processing_time)
                self.process_item(item)
                self.log_activity(item, "finished")
                delivery = self.output_item(item)
                if isinstance(delivery, simpy.Event):
                    self.log_activity(item, "blocked")
                    yield delivery
                    self.log_activity(item, "released")
        finally:
            self.is_processing = False

    @abstractmethod
    def process_item(self, item):
        """Apply this machine's change to the item."""

    def log_activity(self, item, event):
        record = {
            "time_seconds": self.env.now,
            "machine": self.station_id,
            "item_id": item.id,
            "event": event,
        }
        if self.on_event is not None:
            self.on_event(record)
        else:
            print(json.dumps(record))

    def output_item(self, item):
        destination = self.output_route(item) if self.output_route else self.next_machine
        if destination is not None:
            return destination.input_item(item)
        return item
