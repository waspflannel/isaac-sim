import json

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


def test_mixed_batch_preserves_results_and_accounts_for_every_item(capsys):
    items = [Item("healthy", 70), Item("repairable", 50), Item("bad", 0)]
    assert run_factory(items) is items
    assert [item.status for item in items] == ["packed", "packed", "scrapped"]
    assert [item.repair_attempts for item in items] == [0, 1, 2]
    assert [item.quality for item in items] == [85, 85, 55]
    assert [[result["passed"] for result in item.test_results] for item in items] == [
        [True],
        [False, True],
        [False, False, False],
    ]
    assert [[result["quality"] for result in item.test_results] for item in items] == [
        [85],
        [65, 85],
        [15, 35, 55],
    ]
    assert [[result["time_seconds"] for result in item.test_results] for item in items] == [
        [230],
        [320, 590],
        [410, 770, 1040],
    ]
    assert all(result["threshold"] == 80 for item in items for result in item.test_results)
    assert items[1].completed_steps == [
        "kit",
        "assembly",
        "calibration",
        "test",
        "repair",
        "test",
        "packing",
    ]
    assert items[2].completed_steps == [
        "kit",
        "assembly",
        "calibration",
        "test",
        "repair",
        "test",
        "repair",
        "test",
    ]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1] == {
        "event": "batch_summary",
        "packed": 2,
        "repaired": 2,
        "scrapped": 1,
        "time_seconds": 1040,
    }
    assert [event["item_id"] for event in events if event["event"] == "scrapped"] == ["bad"]


def test_item_can_pass_at_threshold_after_last_allowed_repair(capsys):
    item = Item("last-chance", quality=25)
    run_factory([item])
    assert item.status == "packed"
    assert item.quality == 80
    assert item.repair_attempts == 2
    assert [result["passed"] for result in item.test_results] == [False, False, True]
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary == {
        "event": "batch_summary",
        "packed": 1,
        "repaired": 1,
        "scrapped": 0,
        "time_seconds": 795,
    }
