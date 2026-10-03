import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import simpy

from factory_intelligence.control_panel import ControlPanel
from factory_intelligence.production import Production


def test_live_panel_validates_and_applies_controls(tmp_path):
    factory = Production(simpy.Environment())
    scene = SimpleNamespace(speed=4)
    panel = ControlPanel(tmp_path, port=0)
    base = f"http://127.0.0.1:{panel.server.server_port}"
    try:
        panel.publish(factory, scene)
        with urlopen(base + "/snapshot") as response:
            assert len(json.load(response)["machines"]) == 44
        with urlopen(base) as response:
            assert b"Station activity" in response.read()
        command = {"feed_interval": 10, "enabled_lines": [1, 3], "transport_speed": 2}
        request = Request(
            base + "/controls", json.dumps(command).encode(), {"Content-Type": "application/json"}
        )
        with urlopen(request) as response:
            assert response.status == 202
        assert factory.feed_interval == 4
        panel.apply(factory, scene)
        assert factory.feed_interval == 10 and factory.enabled_lines == {1, 3}
        assert scene.speed == 2
        for invalid in ({"feed_interval": 0}, {"enabled_lines": [9]}, {"transport_speed": "fast"}):
            request = Request(
                base + "/controls",
                json.dumps(invalid).encode(),
                {"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as error:
                urlopen(request)
            assert error.value.code == 400
        panel.commands.put({"drain": True})
        panel.apply(factory, scene)
        assert not factory.running
    finally:
        panel.close()
