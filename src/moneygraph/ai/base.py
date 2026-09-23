"""Contracts and privacy boundaries shared by every AI provider."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable


class AIResult(TypedDict):
    """Stable result consumed by the API and Streamlit application."""

    answer: str
    provider: str
    fallback: bool
    tools_used: list[str]
    limitations: list[str]
    assessment: NotRequired[dict[str, Any]]
    model: NotRequired[str]
    usage: NotRequired[dict[str, int]]
    cached: NotRequired[bool]
    ai_status: NotRequired[str]


@runtime_checkable
class AIProvider(Protocol):
    """Synchronous provider boundary so API code is vendor-independent."""

    @property
    def name(self) -> str:
        """Return a non-secret provider identifier."""

    def answer(self, query: str, context: Mapping[str, Any] | None = None) -> AIResult:
        """Answer from already-computed, anonymized analytical context."""


class AIConfigurationError(RuntimeError):
    """Raised when a requested remote provider is not configured."""


_TOP_LEVEL_FIELDS = frozenset(
    {
        "gid",
        "gids",
        "node",
        "nodes",
        "cluster",
        "clusters",
        "summary",
        "metrics",
        "score_drivers",
        "edges",
        "paths",
        "common_receivers",
        "limitations",
        "tools_used",
        "filters",
        "methodology",
    }
)

_ANALYTICAL_FIELDS = frozenset(
    {
        "gid",
        "gids",
        "src",
        "dst",
        "role",
        "role_score",
        "priority_score",
        "cluster_id",
        "depth",
        "is_seed",
        "in_deg",
        "out_deg",
        "in_kzt",
        "out_kzt",
        "total_kzt",
        "sum_kzt",
        "n_tx",
        "total_tx",
        "in_tx",
        "out_tx",
        "pagerank",
        "authority",
        "hub",
        "betweenness",
        "component_id",
        "component_size",
        "pass_through",
        "pass_through_valid",
        "truncated_by_depth",
        "matched_out_ratio",
        "median_holding_days",
        "rank",
        "score",
        "contribution",
        "distance",
        "coverage_ratio",
        "seed_count",
        "size",
        "internal_turnover_kzt",
        "top_gids",
        "explanation",
        "evidence",
        "why",
        "hypothesis",
        "limitation",
        "limitations",
        "tools_used",
        "period",
        "nodes_count",
        "edges_count",
        "transactions_count",
        "clusters_count",
        "components_count",
    }
)

_MAX_LIST_ITEMS = 50
_MAX_STRING_LENGTH = 500


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    return str(value)[:_MAX_STRING_LENGTH]


def _minimize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _minimize_value(item)
            for key, item in value.items()
            if str(key) in _ANALYTICAL_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_minimize_value(item) for item in value[:_MAX_LIST_ITEMS]]
    return _safe_scalar(value)


def minimize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return only anonymized metrics needed for one answer.

    The allow-list deliberately rejects raw transactions, free-form notes,
    credentials and customer attributes. GIDs remain opaque strings.
    """

    if not context:
        return {}
    return {
        str(key): _minimize_value(value)
        for key, value in context.items()
        if str(key) in _TOP_LEVEL_FIELDS
    }


def tools_from_context(context: Mapping[str, Any]) -> list[str]:
    values = context.get("tools_used", [])
    if not isinstance(values, list):
        return []
    return [str(value)[:80] for value in values[:20]]


def limitations_from_context(context: Mapping[str, Any]) -> list[str]:
    values = context.get("limitations", [])
    if not isinstance(values, list):
        return []
    return [str(value)[:_MAX_STRING_LENGTH] for value in values[:20]]


def build_messages(query: str, context: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build a vendor-neutral, grounded chat request."""

    system = (
        "Ты — AI Copilot для AML-аналитика. Используй только переданный "
        "обезличенный контекст. Разделяй факты, гипотезы и ограничения; "
        "ссылайся на gid и числовые метрики. Не придумывай операции или "
        "атрибуты и никогда не утверждай виновность. Ответь по-русски кратко."
    )
    payload = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    user = f"Запрос аналитика: {query[:2000]}\nСтруктурированный контекст: {payload}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
