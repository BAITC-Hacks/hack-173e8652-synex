from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from moneygraph.api.dependencies import ServicesDependency
from moneygraph.api.errors import APIError
from moneygraph.api.schemas import RunCreate
from moneygraph.repository.repositories import RunNotFoundError

router = APIRouter(prefix="/api/v1/runs", tags=["pipeline runs"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_run(payload: RunCreate, services: ServicesDependency) -> dict[str, Any]:
    snapshots = services.artifacts.nodes()
    run = services.repository.create_run(
        status=payload.status,
        config_version=payload.config_version,
        input_hash=payload.input_hash,
        manifest=payload.manifest,
        node_snapshots=snapshots,
        actor=services.settings.analyst_name,
    )
    return {"data": run}


@router.get("")
def list_runs(
    services: ServicesDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    result = services.repository.list_runs(limit=limit, offset=offset)
    return {
        "data": result["items"],
        "meta": {key: result[key] for key in ("total", "limit", "offset")},
    }


@router.get("/{run_id}")
def get_run(run_id: str, services: ServicesDependency) -> dict[str, Any]:
    try:
        return {"data": services.repository.get_run(run_id)}
    except RunNotFoundError as exc:
        raise APIError(404, "run_not_found", f"Unknown run_id: {run_id}") from exc
