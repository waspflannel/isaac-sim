from abc import ABC, abstractmethod
from collections import deque


class Machine(ABC):
    def __init__(self, env, item_processing_time):
        self.env = env
        self.item_processing_time = item_processing_time
        self.item_wait_queue = deque()
        self.is_processing = False
        self.next_machine = None

    def connect(self, next_machine):
        self.next_machine = next_machine

    def input_item(self, item):
        self.item_wait_queue.append(item)
        if not self.is_processing:
            self.is_processing = True
            self.env.process(self.run())

    def run(self):
        try:
            while self.item_wait_queue:
                item = self.item_wait_queue.popleft()
                yield self.env.timeout(self.item_processing_time)
                self.process_item(item)
                self.output_item(item)
        finally:
            self.is_processing = False

    @abstractmethod
    def process_item(self, item):
        """Apply this machine's change to the item."""

    def output_item(self, item):
        if self.next_machine is not None:
            return self.next_machine.input_item(item)
        return item
