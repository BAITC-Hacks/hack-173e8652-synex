from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

OpaqueID = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunCreate(StrictRequest):
    status: Literal["running", "completed", "failed"] = "completed"
    config_version: str = Field(default="rules-v1", min_length=1, max_length=128)
    input_hash: str | None = Field(default=None, max_length=256)
    manifest: dict[str, Any] = Field(default_factory=dict)


class CommonReceiversRequest(StrictRequest):
    gids: list[OpaqueID] = Field(min_length=2, max_length=20)
    max_depth: int = Field(default=4, ge=1, le=4)
    limit: int = Field(default=25, ge=1, le=100)

    @field_validator("gids")
    @classmethod
    def gids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("gids must be unique")
        return value


class InvestigationCreate(StrictRequest):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_version: str = Field(default="rules-v1", min_length=1, max_length=128)
    gids: list[OpaqueID] = Field(default_factory=list, max_length=100)

    @field_validator("gids")
    @classmethod
    def unique_gids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("gids must be unique")
        return value


class InvestigationNodesAdd(StrictRequest):
    gids: list[OpaqueID] = Field(min_length=1, max_length=100)

    @field_validator("gids")
    @classmethod
    def unique_gids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("gids must be unique")
        return value


class InvestigationNoteCreate(StrictRequest):
    body: str = Field(
        min_length=1,
        max_length=10_000,
        validation_alias=AliasChoices("body", "text"),
    )


class InvestigationStatusUpdate(StrictRequest):
    status: Literal["new", "in_review", "escalated", "closed"]


class AssistantQuery(StrictRequest):
    query: str = Field(min_length=1, max_length=2000)
    gids: list[OpaqueID] = Field(default_factory=list, max_length=20)
    cluster_id: int | str | None = None

    @field_validator("gids")
    @classmethod
    def unique_gids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("gids must be unique")
        return value


class AgenticScanCreate(StrictRequest):
    replay_date: date
    interval_minutes: int = Field(default=15, ge=1, le=60)
    limit: int = Field(default=50, ge=1, le=50)


class AgenticDecisionRequest(StrictRequest):
    decision: Literal["approve", "reject"]
    confirmation: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def approve_requires_exact_confirmation(self) -> AgenticDecisionRequest:
        if self.decision == "approve" and self.confirmation != "APPROVE":
            raise ValueError("approve requires exact confirmation: APPROVE")
        return self
