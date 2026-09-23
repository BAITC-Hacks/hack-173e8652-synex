from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from moneygraph.repository.models import (
    AuditEvent,
    Investigation,
    InvestigationNode,
    InvestigationNote,
    PipelineRun,
    RunNodeSnapshot,
)

INVESTIGATION_STATUSES = frozenset({"new", "in_review", "escalated", "closed"})
RUN_STATUSES = frozenset({"running", "completed", "failed"})


class InvestigationNotFoundError(LookupError):
    """Raised when a case identifier does not exist."""


class RunNotFoundError(LookupError):
    """Raised when a pipeline run identifier does not exist."""


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
