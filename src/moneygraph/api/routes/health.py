from __future__ import annotations

from fastapi import APIRouter

from moneygraph.api.dependencies import ServicesDependency
from moneygraph.services.artifacts import ArtifactUnavailableError

router = APIRouter(tags=["health"])


@router.get("/health")
def health(services: ServicesDependency) -> dict[str, object]:
    """Liveness endpoint that deliberately does not depend on AI or pipeline output."""

    try:
        services.artifacts.refresh()
        artifacts_status = "available"
    except ArtifactUnavailableError:
        artifacts_status = "pending"
    return {
        "data": {
            "status": "ok",
            "database": "available",
            "artifacts": artifacts_status,
            "ai_required": False,
        }
    }
