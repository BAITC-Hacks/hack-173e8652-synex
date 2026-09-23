from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from moneygraph.repository.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    node_snapshots: Mapped[list[RunNodeSnapshot]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunNodeSnapshot.gid"
    )


class RunNodeSnapshot(Base):
    __tablename__ = "run_node_snapshots"
    __table_args__ = (UniqueConstraint("run_id", "gid", name="uq_run_snapshot_gid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    role_score: Mapped[float] = mapped_column(Float, nullable=False)
    cluster_id: Mapped[str] = mapped_column(String(128), nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[PipelineRun] = relationship(back_populates="node_snapshots")


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="new")
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    nodes: Mapped[list[InvestigationNode]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="InvestigationNode.gid",
    )
    notes: Mapped[list[InvestigationNote]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="InvestigationNote.created_at",
    )


class InvestigationNode(Base):
    __tablename__ = "investigation_nodes"
    __table_args__ = (
        UniqueConstraint("investigation_id", "gid", name="uq_investigation_node_gid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    added_by: Mapped[str] = mapped_column(String(128), nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    investigation: Mapped[Investigation] = relationship(back_populates="nodes")


class InvestigationNote(Base):
    __tablename__ = "investigation_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    analyst: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    investigation: Mapped[Investigation] = relationship(back_populates="notes")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MonitoringScan(Base):
    __tablename__ = "monitoring_scans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    replay_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    simulation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    alerts: Mapped[list[MonitoringAlert]] = relationship(
        back_populates="scan",
        cascade="all, delete-orphan",
        order_by="MonitoringAlert.id",
    )


class AutoMonitorClaim(Base):
    """One durable claim per source date across API workers and restarts."""

    __tablename__ = "auto_monitor_claims"

    replay_date: Mapped[date] = mapped_column(Date, primary_key=True)
    scan_id: Mapped[str] = mapped_column(String(36), nullable=False)


class MonitoringAlert(Base):
    __tablename__ = "monitoring_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_scans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    rule_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    cluster_id: Mapped[str] = mapped_column(String(128), nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    scan: Mapped[MonitoringScan] = relationship(back_populates="alerts")
    actions: Mapped[list[AgenticAction]] = relationship(
        back_populates="alert",
        cascade="all, delete-orphan",
        order_by="AgenticAction.action_key",
    )


class AgenticAction(Base):
    __tablename__ = "agentic_actions"
    __table_args__ = (
        UniqueConstraint("alert_id", "action_key", name="uq_agentic_alert_action"),
        UniqueConstraint(
            "idempotency_actor",
            "idempotency_key",
            name="uq_agentic_actor_idempotency_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_alerts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    recommendation_score: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    investigation_id: Mapped[str | None] = mapped_column(
        ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    alert: Mapped[MonitoringAlert] = relationship(back_populates="actions")
