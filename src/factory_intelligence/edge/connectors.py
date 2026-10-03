"""Read-only connectors. HTTP sources expose retained {events, next_cursor} pages."""

import json
import os
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode

from .transport import request_json

CAPABILITIES = {
    "journal": ["retained_events", "replay", "field_mapping", "preview"],
    "http_events": ["retained_events", "cursor_replay", "field_mapping", "preview"],
}


def batches(config, store):
    connector_id = config["connector_id"]
    if config["kind"] == "http_events":
        key = "cursor:" + connector_id
        cursor = store.get(key, "")
        separator = "&" if "?" in config["location"] else "?"
        url = config["location"] + separator + urlencode({"cursor": cursor, "limit": 200})
        token = os.environ[config["token_env"]] if config.get("token_env") else None
        page = request_json(url, token)
        if not isinstance(page, dict) or not isinstance(page.get("events"), list):
            raise ValueError("Source must return events and next_cursor")
        if "next_cursor" not in page or len(page["events"]) > 200:
            raise ValueError("Source page must contain next_cursor and at most 200 events")
        if page["events"] and page["next_cursor"] == cursor:
            raise ValueError("Nonempty source page did not advance its cursor")
        yield key, page["next_cursor"], [json.dumps(e) for e in page["events"]]
        return

    folder = Path(config["location"])
    if not folder.is_dir():
        raise FileNotFoundError("Journal directory is unavailable")
    remaining = 200
    for path in sorted(folder.glob("*.ndjson")):
        key = "cursor:" + connector_id + ":" + path.name
        checkpoint = store.get(key, {"offset": 0, "identity": None})
        with path.open("rb") as stream:
            first = stream.readline(1024 * 1024 + 1)
            if not first.endswith(b"\n"):
                continue
            identity = sha256(first).hexdigest()
            if (
                checkpoint["identity"] not in (None, identity)
                or path.stat().st_size < checkpoint["offset"]
            ):
                raise ValueError(
                    f"Journal replaced or truncated: {path.name}; restore retained source"
                )
            stream.seek(checkpoint["offset"])
            records = []
            while remaining and (raw := stream.readline(1024 * 1024 + 1)):
                if len(raw) > 1024 * 1024:
                    raise ValueError("Journal record exceeded 1 MiB")
                if not raw.endswith(b"\n"):
                    break  # Writer has not committed a complete record yet.
                records.append(raw.decode("utf-8", errors="replace"))
                checkpoint = {"offset": stream.tell(), "identity": identity}
                remaining -= 1
        if records:
            yield key, checkpoint, records
        if not remaining:
            break


def preview(config):
    class Beginning:
        def get(self, key, default=None):
            return default

    samples = []
    for _, _, records in batches(config, Beginning()):
        samples.extend(json.loads(raw) for raw in records[: 3 - len(samples)])
        if len(samples) == 3:
            break
    return {
        "capabilities": CAPABILITIES[config["kind"]],
        "samples": samples,
        "fields": sorted({key for sample in samples for key in sample}),
    }
