"""Model output can explain a safe proposal, never change its executable capability."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from moneygraph.ai.case_assessment import validate_assessment

SAFE_MODEL = re.compile(r"^[A-Za-z0-9_.:/-]{1,100}$")
SAFE_STATUSES = frozenset({
    "success", "cache_hit", "budget_exhausted", "in_flight", "cooldown",
    "state_unavailable", "provider_unavailable",
})


def assess_case(provider: Any, alert: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed to factual rules when a provider or output contract fails."""
    status = "provider_unavailable"
    try:
        result = provider.assess_alert(alert)
        status = result.get("ai_status", "success")
        status = status if status in SAFE_STATUSES else "provider_unavailable"
        if not result.get("fallback"):
            assessment = validate_assessment(result.get("assessment"), alert)
            model = str(result.get("model", ""))
            return {**alert, "facts": {**alert["facts"],
                "ai_assessment": assessment,
                "ai_narrative": assessment["summary"],
                "ai_provider": "openai",
                "ai_model": model if SAFE_MODEL.fullmatch(model) else "configured-model",
                "ai_status": "cache_hit" if result.get("cached") else "success",
                "ai_cached": bool(result.get("cached", False)),
                "ai_usage": result.get("usage", {}),
            }}
    except Exception:
        status = "provider_unavailable"
    return {**alert, "facts": {**alert["facts"],
        "ai_status": status, "ai_retry_after": time.time() + 300,
    }}


def explain_proposals(
    proposals: list[dict[str, Any]], facts: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Attach validated model rationale and exact server-side evidence values."""
    assessment = facts.get("ai_assessment")
    if not isinstance(assessment, Mapping):
        return proposals
    indexed = {item["action_key"]: (rank, item)
               for rank, item in enumerate(assessment.get("actions", []), 1)}
    result = []
    for proposal in proposals:
        ranked = indexed.get(proposal["action_key"])
        if ranked is None:
            result.append(proposal)
            continue
        rank, item = ranked
        evidence = "; ".join(f"{key}={facts[key]}" for key in item["evidence_keys"])
        result.append({**proposal, "rationale": (
            f"OpenAI · вариант {rank}. {item['rationale']} Основание: {evidence}."
        )})
    return result
