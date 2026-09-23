from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query, status
from sqlalchemy.exc import IntegrityError

from moneygraph.api.dependencies import ServicesDependency
from moneygraph.api.errors import APIError
from moneygraph.api.schemas import AgenticDecisionRequest, AgenticScanCreate
from moneygraph.repository.repositories import (
    AgenticActionNotFoundError,
    AgenticStateConflictError,
    IdempotencyConflictError,
    MonitoringAlertNotFoundError,
    MonitoringScanNotFoundError,
)
from moneygraph.services.agentic_loop import AgenticValidationError

router = APIRouter(prefix="/api/v1/agentic", tags=["agentic loop"])

AgenticID = Annotated[str, Path(min_length=1, max_length=128)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


@router.get("/monitoring")
def get_monitoring_status(services: ServicesDependency) -> dict[str, Any]:
    return {"data": services.auto_monitor.status()}


@router.post("/scans", status_code=status.HTTP_201_CREATED)
def create_scan(payload: AgenticScanCreate, services: ServicesDependency) -> dict[str, Any]:
    try:
        result = services.agentic.run_scan(
            replay_date=payload.replay_date,
            interval_minutes=payload.interval_minutes,
            limit=payload.limit,
        )
    except AgenticValidationError as exc:
        raise APIError(422, "validation_error", str(exc)) from exc
    return {"data": result}


@router.get("/scans/{scan_id}")
def get_scan(scan_id: AgenticID, services: ServicesDependency) -> dict[str, Any]:
    try:
        return {"data": services.agentic.get_scan(scan_id)}
    except MonitoringScanNotFoundError as exc:
        raise APIError(404, "scan_not_found", f"Unknown scan_id: {scan_id}") from exc


@router.post("/alerts/{alert_id}/proposals", status_code=status.HTTP_201_CREATED)
def create_proposals(alert_id: AgenticID, services: ServicesDependency) -> dict[str, Any]:
    try:
        return {"data": services.agentic.propose_actions(alert_id)}
    except MonitoringAlertNotFoundError as exc:
        raise APIError(404, "alert_not_found", f"Unknown alert_id: {alert_id}") from exc
    except AgenticStateConflictError as exc:
        raise APIError(409, "agentic_state_conflict", str(exc)) from exc


@router.post("/actions/{action_id}/decision")
def decide_action(
    action_id: AgenticID,
    payload: AgenticDecisionRequest,
    services: ServicesDependency,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    if payload.decision == "approve" and idempotency_key is None:
        raise APIError(
            422,
            "validation_error",
            "Idempotency-Key header is required when approving an action",
        )
    effective_key = idempotency_key or f"reject:{sha256(action_id.encode()).hexdigest()}"
    try:
        result = services.agentic.decide_and_execute(
            action_id=action_id,
            decision=payload.decision,
            confirmation=payload.confirmation,
            idempotency_key=effective_key,
        )
    except AgenticActionNotFoundError as exc:
        raise APIError(404, "action_not_found", f"Unknown action_id: {action_id}") from exc
    except IdempotencyConflictError as exc:
        raise APIError(409, "idempotency_conflict", str(exc)) from exc
    except IntegrityError as exc:
        raise APIError(
            409,
            "idempotency_conflict",
            "A concurrent request already claimed this idempotency key",
        ) from exc
    except AgenticStateConflictError as exc:
        raise APIError(409, "agentic_state_conflict", str(exc)) from exc
    except AgenticValidationError as exc:
        raise APIError(422, "validation_error", str(exc)) from exc
    return {"data": result}


@router.get("/audit")
def list_audit(
    services: ServicesDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    result = services.agentic.list_audit_events(limit=limit, offset=offset)
    return {
        "data": result["items"],
        "meta": {key: result[key] for key in ("total", "limit", "offset")},
    }
