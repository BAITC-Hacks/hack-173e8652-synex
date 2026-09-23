from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter

from moneygraph.api.dependencies import ServicesDependency
from moneygraph.api.errors import APIError
from moneygraph.api.schemas import AssistantQuery

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


@router.post("/query")
async def assistant_query(
    payload: AssistantQuery,
    services: ServicesDependency,
) -> dict[str, Any]:
    profiles = []
    for gid in payload.gids:
        try:
            profiles.append(services.graph.get_node(gid))
        except KeyError as exc:
            raise APIError(404, "node_not_found", f"Unknown gid: {gid}") from exc
    cluster = None
    if payload.cluster_id is not None:
        try:
            cluster = services.graph.cluster(payload.cluster_id)
        except KeyError as exc:
            raise APIError(
                404,
                "cluster_not_found",
                f"Unknown cluster_id: {payload.cluster_id}",
            ) from exc

    context = {
        "node": profiles[0] if len(profiles) == 1 else None,
        "nodes": profiles,
        "gids": payload.gids,
        "cluster": cluster,
        "tools_used": _context_tools(profiles=profiles, cluster=cluster),
        "limitations": [
            "Граф отражает только наблюдаемые исходящие внутрибанковские переводы.",
            "Граница depth=4 может скрывать последующие исходящие связи.",
            "Приоритет означает очередь аналитической проверки, а не виновность.",
        ],
    }
    provider_result = await _optional_provider_answer(payload.query, context)
    if provider_result is None:
        provider_result = _deterministic_answer(payload.query, context)
    return {"data": provider_result}


def _context_tools(*, profiles: list[dict[str, Any]], cluster: dict[str, Any] | None) -> list[str]:
    if profiles:
        return ["get_node_profile"]
    if cluster is not None:
        return ["get_cluster"]
    return ["get_data_limitations"]


async def _optional_provider_answer(query: str, context: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from moneygraph.ai.factory import build_provider
    except (ImportError, AttributeError):
        return None
    try:
        provider = build_provider()
        raw: Any = provider.answer(query, context)
        if inspect.isawaitable(raw):
            raw = await raw
        if not isinstance(raw, Mapping):
            return None
        result = dict(raw)
        if not result.get("answer"):
            return None
        if result.get("fallback") is True:
            result["provider"] = "deterministic"
        result.setdefault("provider", provider.__class__.__name__)
        result.setdefault("fallback", False)
        result.setdefault("tools_used", [])
        result.setdefault("limitations", context["limitations"])
        return result
    except Exception:
        # An optional network/provider failure must never break the deterministic product.
        return None


def _deterministic_answer(query: str, context: dict[str, Any]) -> dict[str, Any]:
    nodes = context["nodes"]
    tools_used: list[str] = []
    statements = ["AI отключён или недоступен; показано детерминированное объяснение."]
    if nodes:
        tools_used.append("get_node_profile")
        for node in nodes:
            statements.append(
                "gid {gid}: роль {role}, priority {priority:.3f}; {evidence}".format(
                    gid=node["gid"],
                    role=node.get("role", "не определена"),
                    priority=float(node.get("priority_score") or 0.0),
                    evidence=node.get("evidence") or "наблюдаемое объяснение отсутствует.",
                )
            )
    elif context.get("cluster") is not None:
        tools_used.append("get_cluster")
        cluster = context["cluster"]
        statements.append(
            f"Кластер {cluster.get('cluster_id')}: {cluster.get('hypothesis', 'гипотеза не сформирована')}"
        )
    else:
        tools_used.append("get_data_limitations")
        statements.append("Укажите gid или cluster_id, чтобы ответ опирался на конкретные метрики.")
    statements.append("Запрос аналитика: " + query.strip())
    return {
        "answer": " ".join(statements),
        "provider": "deterministic",
        "fallback": True,
        "tools_used": tools_used,
        "limitations": context["limitations"],
    }
