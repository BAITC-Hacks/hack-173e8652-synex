"""Serializable domain records used at file and HTTP boundaries."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from moneygraph.domain.roles import Role


class ImmutableRecord(BaseModel):
    """Base class that prevents accidental mutation after validation."""

    model_config = ConfigDict(frozen=True, extra="forbid", coerce_numbers_to_str=True)


class NodeRoleRecord(ImmutableRecord):
    gid: str
    role: Role
    role_score: float = Field(ge=0.0, le=1.0)
    cluster_id: int = Field(ge=0)
    priority_score: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(min_length=1, max_length=200)


class ClusterRecord(ImmutableRecord):
    cluster_id: int = Field(ge=0)
    n_nodes: int = Field(ge=1)
    n_seed: int = Field(ge=0)
    sum_kzt_internal: float = Field(ge=0.0)
    top_gids: str = Field(min_length=2)
    hypothesis: str = Field(min_length=1)


class TopNodeRecord(ImmutableRecord):
    rank: int = Field(ge=1)
    gid: str
    role: Role
    priority_score: float = Field(ge=0.0, le=1.0)
    why: str = Field(min_length=1)


class ResilienceRecord(ImmutableRecord):
    scenario: str
    n_removed: int = Field(ge=0)
    largest_component_size: int = Field(ge=0)
    n_components: int = Field(ge=0)
    largest_component_ratio: float = Field(ge=0.0, le=1.0)
    fragmentation_delta: int
