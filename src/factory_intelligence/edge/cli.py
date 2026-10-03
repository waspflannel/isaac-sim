"""Configure, inspect, and operate the gateway without requiring the dashboard."""

import argparse
import json
import os
import time
from pathlib import Path

from .agent import Agent
from .brain import Brain
from .connectors import preview
from .contracts import AgentConfig, Connector
from .store import Store
from .transport import request_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init-db", help="Create edge tables using standard PG* environment settings")
    create = sub.add_parser("init", help="Write a local agent configuration without credentials")
    create.add_argument("--agent-id", default="factory-edge")
    create.add_argument("--area", action="append", default=[])
    create.add_argument("--journal", default=".data/edge/source")
    create.add_argument("--config", type=Path, default=Path(".data/edge/config.json"))
    for name in ("run", "inspect", "status", "enroll"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, default=Path(".data/edge/config.json"))
        if name == "run":
            command.add_argument("--once", action="store_true")
        if name == "enroll":
            command.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "init-db":
        Brain().initialize()
        print("Edge database ready")
        return
    if args.action == "init":
        config = AgentConfig(
            agent_id=args.agent_id,
            spool=f".data/edge/{args.agent_id}.sqlite3",
            connectors=[
                Connector(
                    connector_id="factory-journal",
                    location=args.journal,
                    station_prefixes=args.area,
                )
            ],
        )
        args.config.parent.mkdir(parents=True, exist_ok=True)
        with args.config.open("x", encoding="utf-8") as file:
            file.write(config.model_dump_json(indent=2) + "\n")
        print(f"Created {args.config}")
        return
    config = AgentConfig.model_validate_json(args.config.read_text(encoding="utf-8")).model_dump()
    if args.action == "enroll":
        prefixes = sorted(
            {prefix for c in config["connectors"] for prefix in c["station_prefixes"]}
        )
        if not prefixes:
            prefixes = [f"Line{i:02}" for i in range(1, 9)] + ["Repair1", "Repair2", "Factory"]
        # The token destination is reserved before enrollment to avoid losing a minted credential.
        with args.token_file.open("x", encoding="utf-8") as file:
            result = request_json(
                config["brain_url"].rstrip("/") + "/api/agents",
                os.environ["FACTORY_BRAIN_ADMIN_TOKEN"],
                method="POST",
                body={
                    "agent_id": config["agent_id"],
                    "site_id": config["site_id"],
                    "station_prefixes": prefixes,
                    "settings": config["settings"],
                },
            )
            file.write(result["token"])
        print(f"Enrolled {config['agent_id']}; load {config['token_env']} from {args.token_file}")
    elif args.action == "inspect":
        print(json.dumps({c["connector_id"]: preview(c) for c in config["connectors"]}, indent=2))
    elif args.action == "status":
        store = Store(config["spool"])
        try:
            print(json.dumps(store.health(), indent=2))
        finally:
            store.close()
    else:
        agent = Agent(config)
        try:
            while True:
                agent.step()
                print(json.dumps(agent.health()), flush=True)
                if args.once:
                    break
                time.sleep(config["poll_seconds"])
        except KeyboardInterrupt:
            pass
        finally:
            agent.close()


if __name__ == "__main__":
    main()
