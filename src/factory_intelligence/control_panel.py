"""Loopback-only factory telemetry and queued controls, using the standard library."""

import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import SimpleQueue
from threading import Thread


def validate_controls(data):
    if (
        not isinstance(data, dict)
        or not data
        or data.keys() - {"feed_interval", "enabled_lines", "transport_speed", "drain"}
    ):
        raise ValueError("Unknown or empty controls")
    for key, low, high in (("feed_interval", 0.1, 60), ("transport_speed", 0.1, 20)):
        if key in data and (
            type(data[key]) not in (int, float)
            or not math.isfinite(data[key])
            or not low <= data[key] <= high
        ):
            raise ValueError(f"{key} must be between {low} and {high}")
    if "enabled_lines" in data and (
        not isinstance(data["enabled_lines"], list)
        or any(type(line) is not int or line not in range(1, 9) for line in data["enabled_lines"])
    ):
        raise ValueError("enabled_lines must contain line numbers 1 through 8")
    if "drain" in data and data["drain"] is not True:
        raise ValueError("drain must be true")
    return data


class ControlPanel:
    def __init__(self, output, port=8766):
        self.output = Path(output)
        self.snapshot = {"status": "starting"}
        self.commands = SimpleQueue()
        panel = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, code, content, content_type="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self):
                if self.path == "/":
                    self.reply(
                        200,
                        Path(__file__).with_name("control_panel.html").read_bytes(),
                        "text/html; charset=utf-8",
                    )
                elif self.path == "/snapshot":
                    self.reply(200, json.dumps(panel.snapshot).encode())
                elif self.path == "/scene.png" and (panel.output / "Camera.png").exists():
                    self.reply(200, (panel.output / "Camera.png").read_bytes(), "image/png")
                else:
                    self.reply(404, b'{"error":"Not found"}')

            def do_POST(self):
                if self.path != "/controls":
                    self.reply(404, b'{"error":"Not found"}')
                    return
                try:
                    # Browser requests must be same-origin JSON; no cross-origin controls.
                    if self.headers.get_content_type() != "application/json":
                        raise ValueError("Send application/json")
                    origin = self.headers.get("Origin")
                    expected = f"http://127.0.0.1:{panel.server.server_port}"
                    if origin and origin != expected:
                        raise ValueError("Controls require the local panel origin")
                    size = int(self.headers.get("Content-Length", 0))
                    if not 0 < size <= 4096:
                        raise ValueError("Invalid control payload size")
                    data = validate_controls(json.loads(self.rfile.read(size)))
                except (ValueError, UnicodeDecodeError) as error:
                    self.reply(400, json.dumps({"error": str(error)}).encode())
                    return
                panel.commands.put(data)
                self.reply(202, b'{"status":"queued"}')

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def apply(self, factory, scene):
        while not self.commands.empty():
            command = self.commands.get()
            if "feed_interval" in command:
                factory.feed_interval = command["feed_interval"]
            if "enabled_lines" in command:
                factory.enabled_lines = set(command["enabled_lines"])
            if "transport_speed" in command:
                scene.speed = command["transport_speed"]
            if command.get("drain"):
                factory.running = False

    def publish(self, factory, scene):
        self.snapshot = {
            **factory.snapshot(),
            "status": "running" if factory.running else "draining",
            "transport_speed": scene.speed,
        }

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
