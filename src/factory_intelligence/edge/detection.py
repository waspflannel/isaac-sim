"""Deterministic local findings. Production timers use source time, never wall time."""

from uuid import uuid4


class Detector:
    def __init__(self, store, agent_id, site_id):
        self.store, self.agent_id, self.site_id = store, agent_id, site_id

    def incident(self, key, active, observed, threshold, evidence, settings, clock):
        old = self.store.get("incident:" + key)
        if not active and (old is None or old["status"] == "resolved"):
            return
        if active and old and old["status"] == "open":
            return
        incident_id = old["incident_id"] if not active else str(uuid4())
        data = {
            "incident_id": incident_id,
            "rule": key.split(":", 1)[0],
            "status": "open" if active else "resolved",
            "observed": observed,
            "threshold": threshold,
            "evidence_ids": evidence,
            "configuration_revision": settings["revision"],
            "rule_version": 1,
            "interpretation": "local observation; cause not established",
        }
        self.store.set("incident:" + key, data)
        sequence = self.store.get("local_sequence", 0) + 1
        self.store.set("local_sequence", sequence)
        self.store.capture(
            {
                "schema_version": 1,
                "event_id": f"{self.agent_id}:incident:{uuid4()}",
                "event_type": "incident.changed",
                "site_id": self.site_id,
                "station_id": clock["station_id"],
                "producer_session": self.agent_id,
                "sequence": sequence,
                "origin": "edge",
                "clock_domain": clock["clock_domain"],
                "time_seconds": clock["time_seconds"],
                "run_id": clock["run_id"],
                "epoch": clock["epoch"],
                "unit_id": clock.get("unit_id"),
                "operation_id": clock.get("operation_id"),
                "data": data,
            }
        )

    def observe(self, event, settings):
        rules = settings["rules"]
        station = event["station_id"]
        domain = f"{event['run_id']}:{event['epoch']}:{station}"
        state = self.store.get("equipment:" + domain, {"results": [], "last_time": -1})
        now, kind, data = event["time_seconds"], event["event_type"], event["data"]
        # Late observations remain evidence but must not rewind the live detector.
        if now < state["last_time"] or (
            now == state["last_time"]
            and event["producer_session"] == state.get("producer_session")
            and event["sequence"] <= state.get("sequence", 0)
        ):
            return
        state["last_time"] = now
        state["producer_session"] = event["producer_session"]
        state["sequence"] = event["sequence"]
        evidence = [event["event_id"]]

        def report(rule, active, observed, limit, refs=evidence):
            self.incident(rule + ":" + domain, active, observed, limit, refs, settings, event)

        if kind == "operation.started":
            state["operation"] = {"at": now, "id": event["operation_id"], "evidence": evidence}
        operation = state.get("operation")
        if operation:
            elapsed = now - operation["at"]
            report(
                "operation_overdue",
                elapsed > rules["operation_seconds"],
                elapsed,
                rules["operation_seconds"],
                operation["evidence"] + evidence,
            )
        if kind in ("operation.completed", "operation.aborted"):
            if operation and operation["id"] == event["operation_id"]:
                report(
                    "operation_overdue",
                    False,
                    now - operation["at"],
                    rules["operation_seconds"],
                    operation["evidence"] + evidence,
                )
                state.pop("operation")
        if "state" in data:
            if state.get("state") != data["state"]:
                state["state"], state["since"] = data["state"], now
            for name, rule in (("blocked", "blocked_seconds"), ("starved", "starvation_seconds")):
                elapsed = now - state["since"]
                report(
                    name, state["state"] == name and elapsed >= rules[rule], elapsed, rules[rule]
                )
        if "queue" in data:
            if data["queue"] >= rules["queue_limit"]:
                state.setdefault("queue_since", now)
            else:
                state.pop("queue_since", None)
            elapsed = now - state.get("queue_since", now)
            report(
                "queue_pressure",
                "queue_since" in state and elapsed >= rules["queue_seconds"],
                {"queue": data["queue"], "seconds": elapsed},
                {"queue": rules["queue_limit"], "seconds": rules["queue_seconds"]},
            )
        if kind == "quality.result":
            state["results"] = (
                state["results"]
                + [{"failed": data["result"] == "fail", "event_id": event["event_id"]}]
            )[-rules["failure_window"] :]
            results = state["results"]
            rate = sum(r["failed"] for r in results) / len(results)
            report(
                "failure_rate",
                len(results) == rules["failure_window"] and rate >= rules["failure_rate"],
                {"rate": rate, "attempts": len(results)},
                rules["failure_rate"],
                [r["event_id"] for r in results],
            )
        if kind == "measurement.recorded":
            value = data["value"]
            outside = (data.get("lower_limit") is not None and value < data["lower_limit"]) or (
                data.get("upper_limit") is not None and value > data["upper_limit"]
            )
            report(
                "measurement_limit/" + data["name"],
                outside,
                value,
                {"lower": data.get("lower_limit"), "upper": data.get("upper_limit")},
            )
        if kind in ("handling.failed", "handling.completed"):
            report("handling_failure", kind == "handling.failed", data, "successful placement")
        self.store.set("equipment:" + domain, state)
