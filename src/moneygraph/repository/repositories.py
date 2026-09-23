from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal, cast
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, selectinload, sessionmaker

from moneygraph.repository.models import (
    AgenticAction,
    AuditEvent,
    AutoMonitorClaim,
    Investigation,
    InvestigationNode,
    InvestigationNote,
    MonitoringAlert,
    MonitoringScan,
    PipelineRun,
    RunNodeSnapshot,
)

INVESTIGATION_STATUSES = frozenset({"new", "in_review", "escalated", "closed"})
RUN_STATUSES = frozenset({"running", "completed", "failed"})
ALLOWED_AGENTIC_ACTIONS = frozenset(
    {"prepare_aml_review_draft", "build_money_route", "create_local_watchlist"}
)
AGENTIC_ACTION_ORDER = (
    "prepare_aml_review_draft",
    "build_money_route",
    "create_local_watchlist",
)
AGENTIC_ACTION_TITLES = {
    "prepare_aml_review_draft": "Подготовить черновик для внутренней AML-проверки",
    "build_money_route": "Построить наблюдаемый маршрут денег",
    "create_local_watchlist": "Создать локальный список наблюдения",
}
AGENTIC_AUDIT_ACTIONS = frozenset(
    {
        "monitor.scan_completed",
        "monitor.scan_failed",
        "alert.created",
        "actions.proposed",
        "action.approved",
        "action.rejected",
        "action.failed",
        "tool.executed",
        "ai.assessment_completed",
        "ai.assessment_fallback",
    }
)
AI_ASSESSMENT_FIELDS = frozenset(
    {
        "ai_assessment",
        "ai_narrative",
        "ai_provider",
        "ai_model",
        "ai_status",
        "ai_cached",
        "ai_retry_after",
        "ai_usage",
    }
)
AI_ASSESSMENT_STATUSES = frozenset(
    {
        "success",
        "cache_hit",
        "budget_exhausted",
        "in_flight",
        "cooldown",
        "state_unavailable",
        "provider_unavailable",
    }
)


class InvestigationNotFoundError(LookupError):
    """Raised when a case identifier does not exist."""


class RunNotFoundError(LookupError):
    """Raised when a pipeline run identifier does not exist."""


class MonitoringScanNotFoundError(LookupError):
    """Raised when a monitoring scan identifier does not exist."""


class MonitoringAlertNotFoundError(LookupError):
    """Raised when a monitoring alert identifier does not exist."""


class AgenticActionNotFoundError(LookupError):
    """Raised when an agentic action identifier does not exist."""


class AgenticStateConflictError(RuntimeError):
    """Raised when an action cannot transition from its current state."""


class IdempotencyConflictError(RuntimeError):
    """Raised when a key is reused for a different action or decision."""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _json_safe(value: Any) -> Any:
    """Recursively normalize dataframe scalars before writing portable JSON columns."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    item_method = getattr(value, "item", None)
    if callable(item_method):
        return _json_safe(item_method())
    return str(value)


class MoneyGraphRepository:
    """Transactional repository for reproducible runs, cases, notes, and audit events."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_run(
        self,
        *,
        run_id: str | None = None,
        status: str,
        config_version: str,
        input_hash: str | None,
        manifest: Mapping[str, Any] | None = None,
        node_snapshots: Iterable[Mapping[str, Any]] = (),
        actor: str,
    ) -> dict[str, Any]:
        if status not in RUN_STATUSES:
            raise ValueError(f"Unsupported run status: {status}")
        resolved_run_id = run_id or str(uuid4())
        if not resolved_run_id.strip() or len(resolved_run_id) > 128:
            raise ValueError("run_id must contain between 1 and 128 characters")
        now = datetime.now(UTC)
        with self._session_factory.begin() as session:
            run = PipelineRun(
                id=resolved_run_id,
                status=status,
                config_version=config_version,
                input_hash=input_hash,
                manifest=_json_safe(dict(manifest or {})),
                created_at=now,
                completed_at=now if status in {"completed", "failed"} else None,
            )
            session.add(run)
            for snapshot in node_snapshots:
                session.add(self._snapshot_model(resolved_run_id, snapshot))
            self._add_audit(
                session,
                actor=actor,
                action="run.created",
                entity_type="pipeline_run",
                entity_id=resolved_run_id,
                details={"status": status, "config_version": config_version},
            )
        return self.get_run(resolved_run_id)

    @staticmethod
    def _snapshot_model(run_id: str, snapshot: Mapping[str, Any]) -> RunNodeSnapshot:
        required = {
            "gid",
            "role",
            "role_score",
            "cluster_id",
            "priority_score",
            "evidence",
        }
        missing = required.difference(snapshot)
        if missing:
            raise ValueError(f"Node snapshot is missing fields: {sorted(missing)}")
        known = required
        return RunNodeSnapshot(
            run_id=run_id,
            gid=str(snapshot["gid"]),
            role=str(snapshot["role"]),
            role_score=float(snapshot["role_score"]),
            cluster_id=str(snapshot["cluster_id"]),
            priority_score=float(snapshot["priority_score"]),
            evidence=str(snapshot["evidence"]),
            metrics=_json_safe({key: value for key, value in snapshot.items() if key not in known}),
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            run = session.scalar(
                select(PipelineRun)
                .where(PipelineRun.id == run_id)
                .options(selectinload(PipelineRun.node_snapshots))
            )
            if run is None:
                raise RunNotFoundError(run_id)
            return self._run_dict(run, include_snapshots=True)

    def list_runs(self, *, limit: int, offset: int) -> dict[str, Any]:
        with self._session_factory() as session:
            total = int(session.scalar(select(func.count()).select_from(PipelineRun)) or 0)
            runs = session.scalars(
                select(PipelineRun)
                .order_by(PipelineRun.created_at.desc(), PipelineRun.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return {
                "items": [self._run_dict(run, include_snapshots=False) for run in runs],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    @staticmethod
    def _run_dict(run: PipelineRun, *, include_snapshots: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": run.id,
            "status": run.status,
            "config_version": run.config_version,
            "input_hash": run.input_hash,
            "manifest": run.manifest,
            "created_at": _iso(run.created_at),
            "completed_at": _iso(run.completed_at),
        }
        if include_snapshots:
            result["node_snapshots"] = [
                {
                    "gid": snapshot.gid,
                    "role": snapshot.role,
                    "role_score": snapshot.role_score,
                    "cluster_id": snapshot.cluster_id,
                    "priority_score": snapshot.priority_score,
                    "evidence": snapshot.evidence,
                    **snapshot.metrics,
                }
                for snapshot in run.node_snapshots
            ]
        return result

    def create_investigation(
        self,
        *,
        title: str,
        description: str,
        run_id: str | None,
        model_version: str,
        gids: Sequence[str],
        actor: str,
    ) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Investigation title must not be empty")
        investigation_id = str(uuid4())
        with self._session_factory.begin() as session:
            investigation = Investigation(
                id=investigation_id,
                title=clean_title,
                description=description.strip(),
                status="new",
                run_id=run_id,
                model_version=model_version.strip() or "unknown",
                created_by=actor,
            )
            session.add(investigation)
            for gid in dict.fromkeys(gids):
                session.add(
                    InvestigationNode(
                        investigation_id=investigation_id,
                        gid=str(gid),
                        added_by=actor,
                    )
                )
            self._add_audit(
                session,
                actor=actor,
                action="investigation.created",
                entity_type="investigation",
                entity_id=investigation_id,
                details={"gids": list(dict.fromkeys(gids)), "run_id": run_id},
            )
        return self.get_investigation(investigation_id)

    def get_investigation(self, investigation_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            investigation = self._get_investigation_model(session, investigation_id)
            return self._investigation_dict(investigation, include_children=True)

    def list_investigations(self, *, limit: int, offset: int) -> dict[str, Any]:
        with self._session_factory() as session:
            total = int(session.scalar(select(func.count()).select_from(Investigation)) or 0)
            investigations = session.scalars(
                select(Investigation)
                .options(selectinload(Investigation.nodes), selectinload(Investigation.notes))
                .order_by(Investigation.updated_at.desc(), Investigation.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return {
                "items": [
                    self._investigation_dict(item, include_children=True) for item in investigations
                ],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def add_investigation_nodes(
        self, investigation_id: str, gids: Sequence[str], *, actor: str
    ) -> dict[str, Any]:
        requested = list(dict.fromkeys(str(gid) for gid in gids))
        with self._session_factory.begin() as session:
            investigation = self._get_investigation_model(session, investigation_id)
            existing = {node.gid for node in investigation.nodes}
            added = [gid for gid in requested if gid not in existing]
            for gid in added:
                session.add(
                    InvestigationNode(
                        investigation_id=investigation_id,
                        gid=gid,
                        added_by=actor,
                    )
                )
            investigation.updated_at = datetime.now(UTC)
            self._add_audit(
                session,
                actor=actor,
                action="investigation.nodes_added",
                entity_type="investigation",
                entity_id=investigation_id,
                details={"requested_gids": requested, "added_gids": added},
            )
        return self.get_investigation(investigation_id)

    def remove_investigation_node(
        self, investigation_id: str, gid: str, *, actor: str
    ) -> dict[str, Any]:
        with self._session_factory.begin() as session:
            investigation = self._get_investigation_model(session, investigation_id)
            node = next((item for item in investigation.nodes if item.gid == gid), None)
            if node is not None:
                session.delete(node)
            investigation.updated_at = datetime.now(UTC)
            self._add_audit(
                session,
                actor=actor,
                action="investigation.node_removed",
                entity_type="investigation",
                entity_id=investigation_id,
                details={"gid": gid, "was_present": node is not None},
            )
        return self.get_investigation(investigation_id)

    def add_note(self, investigation_id: str, body: str, *, actor: str) -> dict[str, Any]:
        clean_body = body.strip()
        if not clean_body:
            raise ValueError("Note body must not be empty")
        note_id = str(uuid4())
        with self._session_factory.begin() as session:
            investigation = self._get_investigation_model(session, investigation_id)
            note = InvestigationNote(
                id=note_id,
                investigation_id=investigation_id,
                body=clean_body,
                analyst=actor,
            )
            session.add(note)
            investigation.updated_at = datetime.now(UTC)
            self._add_audit(
                session,
                actor=actor,
                action="investigation.note_added",
                entity_type="investigation",
                entity_id=investigation_id,
                details={"note_id": note_id},
            )
        return {
            "id": note.id,
            "body": note.body,
            "analyst": note.analyst,
            "created_at": _iso(note.created_at),
        }

    def update_investigation_status(
        self, investigation_id: str, status: str, *, actor: str
    ) -> dict[str, Any]:
        if status not in INVESTIGATION_STATUSES:
            raise ValueError(f"Unsupported investigation status: {status}")
        with self._session_factory.begin() as session:
            investigation = self._get_investigation_model(session, investigation_id)
            previous_status = investigation.status
            investigation.status = status
            investigation.updated_at = datetime.now(UTC)
            self._add_audit(
                session,
                actor=actor,
                action="investigation.status_changed",
                entity_type="investigation",
                entity_id=investigation_id,
                details={"from": previous_status, "to": status},
            )
        return self.get_investigation(investigation_id)

    def export_investigation(
        self, investigation_id: str, *, format: Literal["json", "csv"]
    ) -> dict[str, Any] | str:
        with self._session_factory() as session:
            investigation = self._get_investigation_model(session, investigation_id)
            investigation_data = self._investigation_dict(investigation, include_children=True)
            audit_events = session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.entity_type == "investigation",
                    AuditEvent.entity_id == investigation_id,
                )
                .order_by(AuditEvent.created_at, AuditEvent.id)
            ).all()
            audit_data = [self._audit_dict(event) for event in audit_events]

        if format == "json":
            return {"investigation": investigation_data, "audit_events": audit_data}
        if format == "csv":
            return self._investigation_csv(investigation_data)
        raise ValueError(f"Unsupported export format: {format}")

    @staticmethod
    def _investigation_csv(investigation: Mapping[str, Any]) -> str:
        columns = [
            "record_type",
            "investigation_id",
            "title",
            "status",
            "gid",
            "note_id",
            "note_body",
            "analyst",
            "created_at",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerow(
            {
                "record_type": "investigation",
                "investigation_id": investigation["id"],
                "title": investigation["title"],
                "status": investigation["status"],
                "analyst": investigation["created_by"],
                "created_at": investigation["created_at"],
            }
        )
        for node in investigation["nodes"]:
            writer.writerow(
                {
                    "record_type": "node",
                    "investigation_id": investigation["id"],
                    "gid": node["gid"],
                    "analyst": node["added_by"],
                    "created_at": node["added_at"],
                }
            )
        for note in investigation["notes"]:
            writer.writerow(
                {
                    "record_type": "note",
                    "investigation_id": investigation["id"],
                    "note_id": note["id"],
                    "note_body": note["body"],
                    "analyst": note["analyst"],
                    "created_at": note["created_at"],
                }
            )
        return output.getvalue()

    @staticmethod
    def _get_investigation_model(session: Session, investigation_id: str) -> Investigation:
        investigation = session.scalar(
            select(Investigation)
            .where(Investigation.id == investigation_id)
            .options(selectinload(Investigation.nodes), selectinload(Investigation.notes))
        )
        if investigation is None:
            raise InvestigationNotFoundError(investigation_id)
        return investigation

    @staticmethod
    def _investigation_dict(
        investigation: Investigation, *, include_children: bool
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": investigation.id,
            "title": investigation.title,
            "description": investigation.description,
            "status": investigation.status,
            "run_id": investigation.run_id,
            "model_version": investigation.model_version,
            "created_by": investigation.created_by,
            "created_at": _iso(investigation.created_at),
            "updated_at": _iso(investigation.updated_at),
        }
        if include_children:
            result["nodes"] = [
                {
                    "gid": node.gid,
                    "added_by": node.added_by,
                    "added_at": _iso(node.added_at),
                }
                for node in investigation.nodes
            ]
            result["notes"] = [
                {
                    "id": note.id,
                    "body": note.body,
                    "analyst": note.analyst,
                    "created_at": _iso(note.created_at),
                }
                for note in investigation.notes
            ]
        return result

    @staticmethod
    def _add_audit(
        session: Session,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        details: Mapping[str, Any],
    ) -> None:
        session.add(
            AuditEvent(
                id=str(uuid4()),
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=_json_safe(dict(details)),
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _audit_dict(event: AuditEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "actor": event.actor,
            "action": event.action,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "details": event.details,
            "created_at": _iso(event.created_at),
        }

    def create_monitoring_scan(
        self,
        *,
        replay_date: date,
        interval_minutes: int,
        summary: Mapping[str, Any],
        alerts: Sequence[Mapping[str, Any]],
        actor: str,
    ) -> dict[str, Any]:
        """Persist one completed replay scan, its alerts, and transition audit atomically."""

        if not 1 <= interval_minutes <= 1_440:
            raise ValueError("interval_minutes must be between 1 and 1440")
        scan_id = str(uuid4())
        with self._session_factory.begin() as session:
            if actor == "auto-monitor":
                session.add(AutoMonitorClaim(replay_date=replay_date, scan_id=scan_id))
                session.flush()
            session.add(
                MonitoringScan(
                    id=scan_id,
                    replay_date=replay_date,
                    interval_minutes=interval_minutes,
                    simulation=True,
                    status="completed",
                    summary=_json_safe(dict(summary)),
                    created_by=actor,
                )
            )
            self._add_audit(
                session,
                actor=actor,
                action="monitor.scan_completed",
                entity_type="monitoring_scan",
                entity_id=scan_id,
                details={
                    "replay_date": replay_date,
                    "simulation": True,
                    "alerts_created": len(alerts),
                },
            )
            for alert_data in alerts:
                alert = self._monitoring_alert_model(scan_id, alert_data)
                session.add(alert)
                self._add_audit(
                    session,
                    actor=actor,
                    action="alert.created",
                    entity_type="monitoring_alert",
                    entity_id=alert.id,
                    details={"scan_id": scan_id, "gid": alert.gid, "rule_keys": alert.rule_keys},
                )
                self._add_assessment_audit(session, alert.id, alert.facts)
        return self.get_monitoring_scan(scan_id)

    @classmethod
    def _add_assessment_audit(
        cls, session: Session, alert_id: str, facts: Mapping[str, Any]
    ) -> None:
        if "ai_status" not in facts:
            return
        status = str(facts["ai_status"])
        provider = str(facts.get("ai_provider", ""))
        model = str(facts.get("ai_model", ""))
        details: dict[str, Any] = {
            "provider": provider
            if provider in {"openai", "nvidia_nim", "deterministic_fallback"}
            else "unknown",
            "model": model
            if re.fullmatch(r"(?:gpt-|o[134](?:-|$)|nvidia/|meta/)[\w./:-]{0,100}", model)
            else "unknown",
            "status": status if status in AI_ASSESSMENT_STATUSES else "unknown",
            "cached": facts.get("ai_cached") is True,
        }
        usage = facts.get("ai_usage", {})
        if isinstance(usage, Mapping):
            details = {
                **details,
                **{
                    key: usage[key]
                    for key in ("input_tokens", "output_tokens")
                    if type(usage.get(key)) is int and 0 <= usage[key] <= 1_000_000_000
                },
            }
        cls._add_audit(
            session,
            actor="ai-orchestrator",
            action="ai.assessment_completed"
            if status in {"success", "cache_hit"}
            else "ai.assessment_fallback",
            entity_type="monitoring_alert",
            entity_id=alert_id,
            details=details,
        )

    @staticmethod
    def _monitoring_alert_model(scan_id: str, alert_data: Mapping[str, Any]) -> MonitoringAlert:
        required = {
            "gid",
            "rule_keys",
            "facts",
            "role",
            "cluster_id",
            "priority_score",
            "explanation",
        }
        if missing := required.difference(alert_data):
            raise ValueError(f"Monitoring alert is missing fields: {sorted(missing)}")
        score = float(alert_data["priority_score"])
        if not 0.0 <= score <= 1.0:
            raise ValueError("priority_score must be between 0 and 1")
        return MonitoringAlert(
            id=str(uuid4()),
            scan_id=scan_id,
            gid=str(alert_data["gid"]),
            status="open",
            rule_keys=_json_safe(list(alert_data["rule_keys"])),
            facts=_json_safe(dict(alert_data["facts"])),
            role=str(alert_data["role"]),
            cluster_id=str(alert_data["cluster_id"]),
            priority_score=score,
            explanation=str(alert_data["explanation"]),
        )

    def get_monitoring_scan(self, scan_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            scan = session.scalar(
                select(MonitoringScan)
                .where(MonitoringScan.id == scan_id)
                .options(selectinload(MonitoringScan.alerts).selectinload(MonitoringAlert.actions))
            )
            if scan is None:
                raise MonitoringScanNotFoundError(scan_id)
            return self._scan_dict(scan)

    def list_auto_monitoring_scans(self) -> list[dict[str, Any]]:
        """Return persisted automatic scans in replay order for restart-safe progress."""

        with self._session_factory() as session:
            scans = session.scalars(
                select(MonitoringScan)
                .where(MonitoringScan.created_by == "auto-monitor")
                .options(selectinload(MonitoringScan.alerts).selectinload(MonitoringAlert.actions))
                .order_by(MonitoringScan.replay_date, MonitoringScan.created_at, MonitoringScan.id)
            ).all()
            return [self._scan_dict(scan) for scan in scans]

    def record_monitoring_failure(self, message: str) -> None:
        """Journal an automatic scan failure without revealing source paths."""

        with self._session_factory.begin() as session:
            self._add_audit(
                session,
                actor="auto-monitor",
                action="monitor.scan_failed",
                entity_type="monitoring_source",
                entity_id="transactions.parquet",
                details={"message": message},
            )

    def get_monitoring_alert(self, alert_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            alert = session.scalar(
                select(MonitoringAlert)
                .where(MonitoringAlert.id == alert_id)
                .options(selectinload(MonitoringAlert.actions), selectinload(MonitoringAlert.scan))
            )
            if alert is None:
                raise MonitoringAlertNotFoundError(alert_id)
            result = self._alert_dict(alert)
            result["replay_date"] = alert.scan.replay_date.isoformat()
            return result

    def update_alert_assessment(
        self,
        alert_id: str,
        facts: Mapping[str, Any],
        proposals: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Enrich an undecided case without changing evidence or a human's decision."""

        if proposals is not None:
            keys = [str(proposal.get("action_key", "")) for proposal in proposals]
            if len(keys) != 3 or set(keys) != ALLOWED_AGENTIC_ACTIONS:
                raise ValueError(
                    "Action proposals must contain exactly the three allowlisted actions"
                )
        enrichment = _json_safe(
            {key: value for key, value in facts.items() if key in AI_ASSESSMENT_FIELDS}
        )
        with self._session_factory.begin() as session:
            # Acquire SQLite's writer lock before reading the actions. A human decision
            # then either precedes this transaction or waits until enrichment commits.
            claim = session.execute(
                update(MonitoringAlert)
                .where(MonitoringAlert.id == alert_id)
                .values(facts=MonitoringAlert.facts)
            )
            if cast(CursorResult[Any], claim).rowcount != 1:
                raise MonitoringAlertNotFoundError(alert_id)
            alert = session.scalar(
                select(MonitoringAlert)
                .where(MonitoringAlert.id == alert_id)
                .options(selectinload(MonitoringAlert.actions))
            )
            assert alert is not None  # The matching row is locked by the update above.
            if not any(action.status != "proposed" for action in alert.actions):
                alert.facts = {**alert.facts, **enrichment}
                rationale_by_key = {
                    str(item["action_key"]): str(item["rationale"])[:2_000]
                    for item in proposals or ()
                }
                for action in alert.actions:
                    if action.action_key in rationale_by_key:
                        action.rationale = rationale_by_key[action.action_key]
                self._add_assessment_audit(session, alert.id, enrichment)
        return self.get_monitoring_alert(alert_id)

    @classmethod
    def _scan_dict(cls, scan: MonitoringScan) -> dict[str, Any]:
        return {
            "id": scan.id,
            "replay_date": scan.replay_date.isoformat(),
            "simulation": scan.simulation,
            "source_time_granularity": "day",
            "interval_minutes": scan.interval_minutes,
            "status": scan.status,
            "summary": scan.summary,
            "alerts": [cls._alert_dict(alert) for alert in scan.alerts],
            "created_by": scan.created_by,
            "created_at": _iso(scan.created_at),
        }

    @classmethod
    def _alert_dict(cls, alert: MonitoringAlert) -> dict[str, Any]:
        ordered_actions = sorted(
            alert.actions,
            key=lambda item: AGENTIC_ACTION_ORDER.index(item.action_key),
        )
        severity = "critical" if len(alert.rule_keys) >= 2 else "high"
        return {
            "id": alert.id,
            "scan_id": alert.scan_id,
            "gid": alert.gid,
            "status": alert.status,
            "simulation": True,
            "source_time_granularity": "day",
            "rule_keys": list(alert.rule_keys),
            "trigger_codes": list(alert.rule_keys),
            "facts": dict(alert.facts),
            "metrics": dict(alert.facts),
            "role": alert.role,
            "cluster_id": alert.cluster_id,
            "priority_score": alert.priority_score,
            "severity": severity,
            "explanation": alert.explanation,
            "limitations": [
                "Источник содержит календарные даты без внутридневного времени; "
                "признак dwell < 2 часов не вычисляется."
            ],
            "actions": [cls._action_dict(action) for action in ordered_actions],
            "created_at": _iso(alert.created_at),
        }

    def create_action_proposals(
        self,
        *,
        alert_id: str,
        proposals: Sequence[Mapping[str, Any]],
        actor: str,
    ) -> dict[str, Any]:
        """Create the complete allowlisted action set once for an alert."""

        requested_keys = [str(proposal.get("action_key", "")) for proposal in proposals]
        unsupported = set(requested_keys).difference(ALLOWED_AGENTIC_ACTIONS)
        if unsupported:
            raise ValueError(f"Unsupported agentic action: {sorted(unsupported)}")
        if len(proposals) != 3 or set(requested_keys) != ALLOWED_AGENTIC_ACTIONS:
            raise ValueError("Action proposals must contain exactly the three allowlisted actions")

        with self._session_factory.begin() as session:
            alert = session.scalar(
                select(MonitoringAlert)
                .where(MonitoringAlert.id == alert_id)
                .options(selectinload(MonitoringAlert.actions))
            )
            if alert is None:
                raise MonitoringAlertNotFoundError(alert_id)
            if alert.actions:
                return {
                    "alert_id": alert_id,
                    "score_meaning": "next_step_suitability_not_violation_probability",
                    "actions": [self._action_dict(action) for action in alert.actions],
                }

            action_ids: list[str] = []
            for proposal in proposals:
                score = float(proposal["recommendation_score"])
                if not 0.0 <= score <= 1.0:
                    raise ValueError("recommendation_score must be between 0 and 1")
                action_id = str(uuid4())
                action_ids.append(action_id)
                session.add(
                    AgenticAction(
                        id=action_id,
                        alert_id=alert_id,
                        action_key=str(proposal["action_key"]),
                        recommendation_score=score,
                        rationale=str(proposal["rationale"]),
                        expected_outcome=str(proposal["expected_outcome"]),
                        status="proposed",
                    )
                )
            alert.status = "actions_proposed"
            self._add_audit(
                session,
                actor=actor,
                action="actions.proposed",
                entity_type="monitoring_alert",
                entity_id=alert_id,
                details={"action_ids": action_ids, "action_keys": requested_keys},
            )
        stored = self.get_monitoring_alert(alert_id)
        return {
            "alert_id": alert_id,
            "score_meaning": "next_step_suitability_not_violation_probability",
            "actions": stored["actions"],
        }

    def get_agentic_action(self, action_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            action = session.get(AgenticAction, action_id)
            if action is None:
                raise AgenticActionNotFoundError(action_id)
            return self._action_dict(action)

    def decide_agentic_action(
        self,
        *,
        action_id: str,
        decision: Literal["approve", "reject"],
        confirmation: str | None,
        idempotency_key: str,
        actor: str,
        effect: Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None,
    ) -> dict[str, Any]:
        """Record a human decision and its local effect in one database transaction."""

        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        clean_key = idempotency_key.strip()
        if not clean_key or len(clean_key) > 128:
            raise ValueError("idempotency_key must contain between 1 and 128 characters")
        if decision == "approve" and confirmation != "APPROVE":
            raise AgenticStateConflictError("Approved actions require exact confirmation APPROVE")
        request_hash = hashlib.sha256(
            json.dumps(
                {"action_id": action_id, "decision": decision},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        with self._session_factory.begin() as session:
            previous = session.scalar(
                select(AgenticAction).where(
                    AgenticAction.idempotency_actor == actor,
                    AgenticAction.idempotency_key == clean_key,
                )
            )
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise IdempotencyConflictError(
                        "Idempotency key was already used for a different decision"
                    )
                return self._action_dict(previous)

            action = session.get(AgenticAction, action_id)
            if action is None:
                raise AgenticActionNotFoundError(action_id)
            transition = cast(
                CursorResult[Any],
                session.execute(
                    update(AgenticAction)
                    .where(AgenticAction.id == action_id, AgenticAction.status == "proposed")
                    .values(status="executing")
                ),
            )
            if transition.rowcount != 1:
                session.expire(action)
                session.refresh(action)
                concurrent_replay = session.scalar(
                    select(AgenticAction).where(
                        AgenticAction.idempotency_actor == actor,
                        AgenticAction.idempotency_key == clean_key,
                    )
                )
                if concurrent_replay is not None:
                    if concurrent_replay.request_hash != request_hash:
                        raise IdempotencyConflictError(
                            "Idempotency key was already used for a different decision"
                        )
                    return self._action_dict(concurrent_replay)
                raise AgenticStateConflictError(
                    f"Action {action_id} cannot be decided from status {action.status}"
                )
            session.flush()
            session.refresh(action)

            now = datetime.now(UTC)
            action.decision = decision
            action.decided_by = actor
            action.decided_at = now
            action.idempotency_actor = actor
            action.idempotency_key = clean_key
            action.request_hash = request_hash
            if decision == "reject":
                action.status = "rejected"
                action.result = {"executed": False, "reason": "rejected_by_analyst"}
                self._add_audit(
                    session,
                    actor=actor,
                    action="action.rejected",
                    entity_type="agentic_action",
                    entity_id=action.id,
                    details={"action_key": action.action_key},
                )
            else:
                self._add_audit(
                    session,
                    actor=actor,
                    action="action.approved",
                    entity_type="agentic_action",
                    entity_id=action.id,
                    details={"action_key": action.action_key, "confirmation": "APPROVE"},
                )
                try:
                    with session.begin_nested():
                        resolved_effect = effect() if callable(effect) else effect
                        if resolved_effect is None or not isinstance(
                            resolved_effect.get("result"), Mapping
                        ):
                            raise ValueError("Approved actions require a structured local effect")
                        investigation = resolved_effect.get("investigation")
                        if investigation is not None:
                            if not isinstance(investigation, Mapping):
                                raise ValueError("investigation effect must be an object")
                            action.investigation_id = self._create_agentic_investigation(
                                session,
                                specification=investigation,
                                actor=actor,
                            )
                        result = dict(resolved_effect["result"])
                        if action.investigation_id:
                            result["investigation_id"] = action.investigation_id
                        action.result = _json_safe(result)
                        action.status = "executed"
                except Exception as exc:
                    action = session.get(AgenticAction, action_id)
                    if action is None:  # pragma: no cover - protected by the claimed row
                        raise AgenticActionNotFoundError(action_id) from exc
                    action.investigation_id = None
                    action.result = {
                        "executed": False,
                        "error": "tool_execution_failed",
                        "error_type": type(exc).__name__,
                    }
                    action.status = "failed"
                    self._add_audit(
                        session,
                        actor="tool-executor",
                        action="action.failed",
                        entity_type="agentic_action",
                        entity_id=action.id,
                        details={
                            "action_key": action.action_key,
                            "error_type": type(exc).__name__,
                        },
                    )
                else:
                    self._add_audit(
                        session,
                        actor="tool-executor",
                        action="tool.executed",
                        entity_type="agentic_action",
                        entity_id=action.id,
                        details={
                            "action_key": action.action_key,
                            "investigation_id": action.investigation_id,
                        },
                    )
        return self.get_agentic_action(action_id)

    @classmethod
    def _create_agentic_investigation(
        cls,
        session: Session,
        *,
        specification: Mapping[str, Any],
        actor: str,
    ) -> str:
        title = str(specification.get("title", "")).strip()
        if not title:
            raise ValueError("Agentic investigation title must not be empty")
        investigation_id = str(uuid4())
        session.add(
            Investigation(
                id=investigation_id,
                title=title,
                description=str(specification.get("description", "")).strip(),
                status="new",
                run_id=None,
                model_version=str(specification.get("model_version", "agentic-rules-v1")),
                created_by=actor,
            )
        )
        gids = list(dict.fromkeys(str(gid) for gid in specification.get("gids", [])))
        for gid in gids:
            session.add(
                InvestigationNode(
                    investigation_id=investigation_id,
                    gid=gid,
                    added_by=actor,
                )
            )
        note_text = str(specification.get("note", "")).strip()
        if note_text:
            session.add(
                InvestigationNote(
                    id=str(uuid4()),
                    investigation_id=investigation_id,
                    body=note_text,
                    analyst=actor,
                )
            )
        cls._add_audit(
            session,
            actor=actor,
            action="investigation.created",
            entity_type="investigation",
            entity_id=investigation_id,
            details={"gids": gids, "source": "agentic_loop"},
        )
        # The action update references this row by FK; flush the local investigation
        # before assigning that identifier to keep SQLite's immediate FK checks atomic.
        session.flush()
        return investigation_id

    @staticmethod
    def _action_dict(action: AgenticAction) -> dict[str, Any]:
        execution_result = dict(action.result or {}) if action.status == "executed" else None
        return {
            "id": action.id,
            "alert_id": action.alert_id,
            "action_key": action.action_key,
            "title": AGENTIC_ACTION_TITLES[action.action_key],
            "recommendation_score": action.recommendation_score,
            "score_meaning": "next_step_suitability_not_violation_probability",
            "requires_human_approval": True,
            "rationale": action.rationale,
            "expected_outcome": action.expected_outcome,
            "status": action.status,
            "decision": action.decision,
            "decided_by": action.decided_by,
            "decided_at": _iso(action.decided_at),
            "result": dict(action.result or {}),
            "execution_result": execution_result,
            "investigation_id": action.investigation_id,
            "created_at": _iso(action.created_at),
        }

    def list_agentic_audit_events(self, *, limit: int, offset: int) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must not be negative")
        predicate = AuditEvent.action.in_(AGENTIC_AUDIT_ACTIONS)
        with self._session_factory() as session:
            total = int(
                session.scalar(select(func.count()).select_from(AuditEvent).where(predicate)) or 0
            )
            events = session.scalars(
                select(AuditEvent)
                .where(predicate)
                .order_by(AuditEvent.created_at, AuditEvent.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return {
                "items": [self._audit_dict(event) for event in events],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
