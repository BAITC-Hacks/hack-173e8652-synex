from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from moneygraph.api.dependencies import AppServices, ServicesDependency
from moneygraph.api.errors import APIError
from moneygraph.api.schemas import CommonReceiversRequest, OpaqueID

router = APIRouter(prefix="/api/v1", tags=["analytics"])

PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


@router.get("/summary")
def summary(services: ServicesDependency) -> dict[str, Any]:
    return {"data": services.artifacts.summary()}


@router.get("/top-nodes")
def top_nodes(
    services: ServicesDependency,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    role: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    cluster_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> dict[str, Any]:
    records = []
    for item in services.artifacts.top_nodes():
        profile = services.graph.get_node(item["gid"])
        enriched = {**profile, **item}
        if role is not None and enriched.get("role") != role:
            continue
        if cluster_id is not None and str(enriched.get("cluster_id")) != cluster_id:
            continue
        records.append(enriched)
    total = len(records)
    return {
        "data": records[offset : offset + limit],
        "meta": {"total": total, "limit": limit, "offset": offset},
    }


@router.get("/nodes/{gid}")
def node(gid: OpaqueID, services: ServicesDependency) -> dict[str, Any]:
    return {"data": _node_or_404(services, gid)}


@router.get("/nodes/{gid}/ego")
def ego(
    gid: OpaqueID,
    services: ServicesDependency,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
) -> dict[str, Any]:
    _node_or_404(services, gid)
    return {"data": services.graph.ego(gid, depth=depth)}


@router.get("/nodes/{gid}/upstream")
def upstream(
    gid: OpaqueID,
    services: ServicesDependency,
    depth: Annotated[int, Query(ge=1, le=4)] = 4,
    max_paths: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    _node_or_404(services, gid)
    return {
        "data": services.graph.trace(gid, direction="upstream", depth=depth, max_paths=max_paths)
    }


@router.get("/nodes/{gid}/downstream")
def downstream(
    gid: OpaqueID,
    services: ServicesDependency,
    depth: Annotated[int, Query(ge=1, le=4)] = 4,
    max_paths: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    _node_or_404(services, gid)
    return {
        "data": services.graph.trace(gid, direction="downstream", depth=depth, max_paths=max_paths)
    }


@router.post("/common-receivers")
def common_receivers(
    payload: CommonReceiversRequest,
    services: ServicesDependency,
) -> dict[str, Any]:
    for gid in payload.gids:
        _node_or_404(services, gid)
    return {
        "data": services.graph.common_receivers(
            payload.gids, max_depth=payload.max_depth, limit=payload.limit
        )
    }


@router.get("/clusters")
def clusters(
    services: ServicesDependency,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> dict[str, Any]:
    records = services.artifacts.clusters()
    return {
        "data": records[offset : offset + limit],
        "meta": {"total": len(records), "limit": limit, "offset": offset},
    }


@router.get("/resilience")
def resilience(services: ServicesDependency) -> dict[str, Any]:
    return {"data": services.artifacts.resilience()}


@router.get("/clusters/{cluster_id}")
def cluster(cluster_id: str, services: ServicesDependency) -> dict[str, Any]:
    try:
        return {"data": services.graph.cluster(cluster_id)}
    except KeyError as exc:
        raise APIError(404, "cluster_not_found", f"Unknown cluster_id: {cluster_id}") from exc


def _node_or_404(services: AppServices, gid: str) -> dict[str, Any]:
    try:
        return services.graph.get_node(gid)
    except KeyError as exc:
        raise APIError(404, "node_not_found", f"Unknown gid: {gid}") from exc
