import simpy

from factory_intelligence.production import Production


def test_large_factory_backpressure_controls_and_drain():
    env = simpy.Environment()
    factory = Production(env, max_wip=64)
    factory.feed_interval = 0.1
    # Deliberately slow a downstream station to force blocking through its line.
    factory.lines[1][-1].item_processing_time = 25
    env.run(until=200)
    snapshot = factory.snapshot()
    assert len(snapshot["machines"]) == 44
    assert 30 <= snapshot["wip"] <= 64
    assert snapshot["packed"] > 0
    assert snapshot["scrapped"] > 0
    assert snapshot["repaired"] > 0
    assert all(s["max_queue"] <= s["capacity"] for s in snapshot["machines"].values())
    assert any(s["blocked_seconds"] > 10 for s in snapshot["machines"].values())
    factory.enabled_lines.clear()
    before = factory.counts["introduced"]
    env.run(until=250)
    assert factory.counts["introduced"] == before
    factory.running = False
    env.run()
    final = factory.snapshot()
    assert final["wip"] == 0
    assert final["introduced"] == final["packed"] + final["scrapped"]
    assert all(not m.item_wait_queue and not m.is_processing for m in factory.machines.values())
