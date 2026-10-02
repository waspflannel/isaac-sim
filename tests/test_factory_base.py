import pytest

from factory_intelligence.item import Item
from factory_intelligence.main import run_factory


def test_same_item_passes_through_all_machines_in_order():
    item = Item("item-001")
    assert item.completed_steps == []
    assert run_factory(item) is item
    assert item.id == "item-001"
    assert item.completed_steps == ["kit", "assembly", "calibration", "test", "packing"]
    other = Item("item-002")
    assert other.completed_steps == []
    run_factory(other)
    assert other.completed_steps == item.completed_steps
    assert other.completed_steps is not item.completed_steps


def test_invalid_item_id_is_rejected():
    for item_id in (None, 1, "", " "):
        with pytest.raises(ValueError):
            Item(item_id)
