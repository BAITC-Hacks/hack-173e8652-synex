from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status

from moneygraph.api.dependencies import AppServices, ServicesDependency
from moneygraph.api.errors import APIError
from moneygraph.api.schemas import (
    InvestigationCreate,
    InvestigationNodesAdd,
    InvestigationNoteCreate,
    InvestigationStatusUpdate,
    OpaqueID,
)
from moneygraph.repository.repositories import (
    InvestigationNotFoundError,
    RunNotFoundError,
)

router = APIRouter(prefix="/api/v1/investigations", tags=["investigations"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_investigation(
    payload: InvestigationCreate,
    services: ServicesDependency,
) -> dict[str, Any]:
    if payload.run_id is not None:
        try:
            services.repository.get_run(payload.run_id)
        except RunNotFoundError as exc:
            raise APIError(404, "run_not_found", f"Unknown run_id: {payload.run_id}") from exc
    for gid in payload.gids:
        _require_node(services, gid)
    result = services.repository.create_investigation(
        title=payload.title,
        description=payload.description,
        run_id=payload.run_id,
        model_version=payload.model_version,
        gids=payload.gids,
        actor=services.settings.analyst_name,
    )
    return {"data": result}


@router.get("")
def list_investigations(
    services: ServicesDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    result = services.repository.list_investigations(limit=limit, offset=offset)
    return {
        "data": result["items"],
        "meta": {key: result[key] for key in ("total", "limit", "offset")},
    }


@router.get("/{investigation_id}")
def get_investigation(investigation_id: str, services: ServicesDependency) -> dict[str, Any]:
    return {"data": _get_or_404(services, investigation_id)}


@router.post("/{investigation_id}/nodes")
def add_nodes(
    investigation_id: str,
    payload: InvestigationNodesAdd,
    services: ServicesDependency,
) -> dict[str, Any]:
    _get_or_404(services, investigation_id)
    for gid in payload.gids:
        _require_node(services, gid)
    return {
        "data": services.repository.add_investigation_nodes(
            investigation_id, payload.gids, actor=services.settings.analyst_name
        )
    }


@router.delete("/{investigation_id}/nodes/{gid}")
def remove_node(
    investigation_id: str,
    gid: OpaqueID,
    services: ServicesDependency,
) -> dict[str, Any]:
    _get_or_404(services, investigation_id)
    return {
        "data": services.repository.remove_investigation_node(
            investigation_id, gid, actor=services.settings.analyst_name
        )
    }


@router.post("/{investigation_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(
    investigation_id: str,
    payload: InvestigationNoteCreate,
    services: ServicesDependency,
) -> dict[str, Any]:
    _get_or_404(services, investigation_id)
    return {
        "data": services.repository.add_note(
            investigation_id, payload.body, actor=services.settings.analyst_name
        )
    }


@router.patch("/{investigation_id}/status")
def update_status(
    investigation_id: str,
    payload: InvestigationStatusUpdate,
    services: ServicesDependency,
) -> dict[str, Any]:
    _get_or_404(services, investigation_id)
    return {
        "data": services.repository.update_investigation_status(
            investigation_id, payload.status, actor=services.settings.analyst_name
        )
    }


@router.get("/{investigation_id}/export", response_model=None)
def export_investigation(
    investigation_id: str,
    services: ServicesDependency,
    format: Literal["json", "csv"] = "json",
) -> dict[str, Any] | Response:
    _get_or_404(services, investigation_id)
    exported = services.repository.export_investigation(investigation_id, format=format)
    if format == "csv":
        return Response(
            content=str(exported),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="investigation-{investigation_id}.csv"'
                )
            },
        )
    return {"data": exported}


def _get_or_404(services: AppServices, investigation_id: str) -> dict[str, Any]:
    try:
        return services.repository.get_investigation(investigation_id)
    except InvestigationNotFoundError as exc:
        raise APIError(
            404,
            "investigation_not_found",
            f"Unknown investigation_id: {investigation_id}",
        ) from exc


def _require_node(services: AppServices, gid: str) -> None:
    try:
        services.graph.get_node(gid)
    except KeyError as exc:
        raise APIError(404, "node_not_found", f"Unknown gid: {gid}") from exc
