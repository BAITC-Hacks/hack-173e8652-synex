from __future__ import annotations

from fastapi import APIRouter

from moneygraph.api.dependencies import ServicesDependency

router = APIRouter(tags=["health"])


@router.get("/health")
def health(services: ServicesDependency) -> dict[str, object]:
    """Liveness endpoint that deliberately does not depend on AI or pipeline output."""

    try:
        services.artifacts.refresh()
        artifacts_status = "available"
    except Exception:
        artifacts_status = "pending"
    try:
        ai_runtime = services.agentic.ai_status
    except Exception:
        ai_runtime = {
            "configured": services.agentic.narrative_enabled,
            "provider": services.agentic.narrative_provider_name,
            "ai_status": "state_unavailable",
            "last_error": "AI usage accounting is temporarily unavailable.",
        }
    return {
        "data": {
            "status": "ok",
            "database": "available",
            "artifacts": artifacts_status,
            "ai_required": False,
            "agentic_narrative_enabled": services.agentic.narrative_enabled,
            "agentic_narrative_provider": services.agentic.narrative_provider_name,
            "ai_runtime": ai_runtime,
        }
    }
