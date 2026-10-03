"""Agent credentials are scoped to one identity; operator credentials manage enrollment."""

import os
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from .brain import Brain
from .contracts import Batch, Command, Contract, Registration, Settings

router = APIRouter(prefix="/api")
Credentials = Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())]


def brain():
    return Brain()


Repository = Annotated[Brain, Depends(brain)]


def operator(credentials: Credentials):
    expected = os.environ.get("FACTORY_BRAIN_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(503, "Operator authentication is not configured")
    if not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(403, "Invalid operator credential")


def agent_identity(agent_id: str, credentials: Credentials, repository: Repository):
    agent = repository.authorize(agent_id, credentials.credentials)
    if not agent:
        raise HTTPException(403, "Invalid agent credential")
    return agent


Identity = Annotated[dict, Depends(agent_identity)]
Operator = [Depends(operator)]


@router.post("/agents", dependencies=Operator, status_code=201)
def enroll(registration: Registration, repository: Repository):
    return repository.enroll(registration.model_dump())


@router.get("/agents", dependencies=Operator)
def agents(repository: Repository):
    return repository.agents()


@router.post("/agents/{agent_id}/rotate-token", dependencies=Operator)
def rotate(agent_id: str, repository: Repository):
    return repository.rotate(agent_id)


@router.post("/agents/{agent_id}/revoke", dependencies=Operator)
def revoke(agent_id: str, repository: Repository):
    return repository.revoke(agent_id)


@router.put("/agents/{agent_id}/settings", dependencies=Operator)
def configure(agent_id: str, settings: Settings, repository: Repository):
    return repository.configure(agent_id, settings.model_dump())


@router.post("/agents/{agent_id}/commands", dependencies=Operator)
def command(agent_id: str, command: Command, repository: Repository):
    return repository.command(agent_id, command.model_dump())


@router.post("/edge/{agent_id}/events")
def ingest(batch: Batch, agent: Identity, repository: Repository):
    return repository.ingest(agent, batch.events)


@router.post("/edge/{agent_id}/heartbeat")
def heartbeat(health: dict, agent: Identity, repository: Repository):
    return repository.heartbeat(agent["agent_id"], health)


class Result(Contract):
    command_id: str = Field(min_length=1, max_length=160)
    status: Literal["completed", "partial"]
    result: dict


@router.post("/edge/{agent_id}/results")
def result(result: Result, agent: Identity, repository: Repository):
    return repository.result(agent["agent_id"], result.model_dump())


@router.get("/events", dependencies=Operator)
def events(
    repository: Repository,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    unit_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
):
    records = repository.events(
        after=after, limit=limit, unit_id=unit_id, run_id=run_id, event_type=event_type
    )
    return {"records": records, "next_cursor": records[-1]["receipt_id"] if records else after}
