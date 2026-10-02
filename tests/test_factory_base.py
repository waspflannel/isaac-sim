from factory_intelligence.item import Item
from factory_intelligence.main import run_factory


def test_three_items_pass_through_all_machines_in_order():
    items = [Item(str(index)) for index in range(3)]
    originals = list(items)
    assert run_factory(items) is items
    for item, original in zip(items, originals, strict=True):
        assert item is original
        assert item.completed_steps == ["kit", "assembly", "calibration", "test", "packing"]
    assert items[0].completed_steps is not items[1].completed_steps
