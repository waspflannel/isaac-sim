"""Validation at file, equipment, and HTTP boundaries; internal code uses plain dicts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ObservationData(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    state: Literal[
        "idle",
        "processing",
        "blocked",
        "starved",
        "faulted",
        "planned_stop",
        "unknown",
        "transporting",
    ] = "unknown"
    queue: int = Field(default=0, ge=0)


class MeasurementData(ObservationData):
    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    lower_limit: float | None = None
    upper_limit: float | None = None


class QualityData(ObservationData):
    result: Literal["pass", "fail", "abort"]


class Event(Contract):
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=256)
    event_type: str = Field(min_length=1, max_length=80)
    site_id: str = Field(min_length=1, max_length=80)
    station_id: str = Field(min_length=1, max_length=160)
    producer_session: str = Field(min_length=1, max_length=160)
    sequence: int = Field(ge=1)
    origin: Literal["simulation", "equipment", "edge"]
    clock_domain: Literal["simulation", "unix"]
    time_seconds: float = Field(ge=0)
    run_id: str = Field(min_length=1, max_length=160)
    epoch: int = Field(default=1, ge=1)
    unit_id: str | None = None
    operation_id: str | None = None
    data: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def observation_shape(self):
        shape = {"measurement.recorded": MeasurementData, "quality.result": QualityData}
        self.data = (
            shape.get(self.event_type, ObservationData)
            .model_validate(self.data)
            .model_dump(exclude_unset=True)
        )
        if self.event_type.startswith("operation.") and not self.operation_id:
            raise ValueError("Operation events require operation_id")
        return self


class Conversion(Contract):
    raw_unit: str
    unit: str
    factor: float = Field(default=1, gt=0)
    offset: float = 0


class Rules(Contract):
    operation_seconds: float = Field(default=60, gt=0)
    blocked_seconds: float = Field(default=20, gt=0)
    starvation_seconds: float = Field(default=30, gt=0)
    queue_limit: int = Field(default=3, ge=1)
    queue_seconds: float = Field(default=20, gt=0)
    failure_window: int = Field(default=20, ge=2, le=1000)
    failure_rate: float = Field(default=0.25, gt=0, le=1)
    stale_seconds: float = Field(default=30, gt=0)


class Settings(Contract):
    revision: int = Field(default=1, ge=1)
    rules: Rules = Field(default_factory=Rules)
    disabled_connectors: list[str] = Field(default_factory=list)


class Connector(Contract):
    connector_id: str = Field(min_length=1)
    kind: Literal["journal", "http_events"] = "journal"
    location: str
    station_prefixes: list[str] = Field(default_factory=list)
    token_env: str | None = None
    # Top-level source field -> canonical field, preserving the source as evidence.
    fields: dict[str, str] = Field(default_factory=dict)
    mapping_revision: str = "1"
    event_types: dict[str, str] = Field(default_factory=dict)
    states: dict[str, str] = Field(default_factory=dict)
    conversions: dict[str, Conversion] = Field(default_factory=dict)


class AgentConfig(Contract):
    agent_id: str = Field(min_length=1, max_length=80)
    site_id: str = "virtual-factory"
    brain_url: str = "http://127.0.0.1:8000"
    token_env: str = "FACTORY_EDGE_TOKEN"
    spool: str = ".data/edge/agent.sqlite3"
    poll_seconds: float = Field(default=1, gt=0)
    batch_size: int = Field(default=100, ge=1, le=500)
    max_spool_mb: int = Field(default=1024, ge=1)
    acknowledged_retention_days: int = Field(default=7, ge=1)
    connectors: list[Connector] = Field(min_length=1)
    settings: Settings = Field(default_factory=Settings)

    @model_validator(mode="after")
    def unique_connectors(self):
        ids = [connector.connector_id for connector in self.connectors]
        if len(ids) != len(set(ids)):
            raise ValueError("connector_id must be unique")
        return self


class Registration(Contract):
    agent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    site_id: str = Field(min_length=1, max_length=80)
    station_prefixes: list[str] = Field(min_length=1)
    settings: Settings = Field(default_factory=Settings)

    @model_validator(mode="after")
    def initial_revision(self):
        if self.settings.revision != 1:
            raise ValueError("Enrollment starts with configuration revision 1")
        return self


class Command(Contract):
    command_id: str = Field(min_length=1, max_length=160)
    kind: Literal["diagnostics", "evidence", "capture_operations"]
    event_ids: list[str] = Field(default_factory=list, max_length=100)
    station_id: str | None = None
    operations: int = Field(default=1, ge=1, le=50)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)

    @model_validator(mode="after")
    def capture_target(self):
        if self.kind == "capture_operations" and not self.station_id:
            raise ValueError("capture_operations requires station_id")
        return self


class Batch(Contract):
    events: list[dict] = Field(max_length=500)


def permitted(station, prefixes):
    return any(station == prefix or station.startswith(prefix + "/") for prefix in prefixes)
