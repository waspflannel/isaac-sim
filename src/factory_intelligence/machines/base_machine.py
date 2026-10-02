from abc import ABC, abstractmethod

from factory_intelligence.item import Item


class Machine(ABC):
    def __init__(self):
        self.next_machine = None

    def connect(self, next_machine):
        self.next_machine = next_machine

    def input_item(self, item):
        if not isinstance(item, Item):
            raise TypeError("machine input must be an Item")
        self.process_item(item)
        return self.output_item(item)

    @abstractmethod
    def process_item(self, item):
        """Apply this machine's change to the item."""

    def output_item(self, item):
        if self.next_machine is not None:
            return self.next_machine.input_item(item)
        return item
