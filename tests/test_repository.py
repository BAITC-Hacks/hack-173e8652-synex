from __future__ import annotations

import csv
import io
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from moneygraph.repository.database import Database
from moneygraph.repository.repositories import (
    InvestigationNotFoundError,
    MoneyGraphRepository,
)

OPAQUE_GID = "100000003684369100"


@pytest.fixture
def repository(tmp_path: Path) -> MoneyGraphRepository:
    database = Database(f"sqlite:///{tmp_path / 'repository.db'}")
    database.create_schema()
    return MoneyGraphRepository(database.session_factory)


def test_run_snapshot_round_trip_preserves_opaque_gid(
    repository: MoneyGraphRepository,
) -> None:
    run = repository.create_run(
        status="completed",
        config_version="rules-v1",
        input_hash="sha256:fixture",
        manifest={"duration_seconds": 1.25},
        node_snapshots=[
            {
                "gid": OPAQUE_GID,
                "role": "coordinator",
                "role_score": 0.82,
                "cluster_id": 7,
                "priority_score": 0.91,
                "evidence": "Наблюдается 3 связи.",
            }
        ],
        actor="pytest",
    )

    stored = repository.get_run(run["id"])

    assert stored["id"] == run["id"]
    assert stored["input_hash"] == "sha256:fixture"
    assert stored["manifest"]["duration_seconds"] == 1.25
    assert stored["node_snapshots"][0]["gid"] == OPAQUE_GID
    assert repository.list_runs(limit=10, offset=0)["total"] == 1


def test_run_accepts_pipeline_id_and_json_normalizes_feature_scalars(
    repository: MoneyGraphRepository,
) -> None:
    run = repository.create_run(
        run_id="pipeline-20260923T141500Z",
        status="completed",
        config_version="rules-v1",
        input_hash="sha256:fixture",
        manifest={"duration_seconds": np.float64(1.5)},
        node_snapshots=[
            {
                "gid": OPAQUE_GID,
                "role": "coordinator",
                "role_score": np.float64(0.82),
                "cluster_id": np.int64(7),
                "priority_score": np.float64(0.91),
                "evidence": "Наблюдается 3 связи.",
                "calculated_at": pd.Timestamp("2026-09-23T14:15:00Z"),
            }
        ],
        actor="pipeline",
    )

    assert run["id"] == "pipeline-20260923T141500Z"
    assert run["manifest"]["duration_seconds"] == 1.5
    assert run["node_snapshots"][0]["cluster_id"] == "7"
    assert run["node_snapshots"][0]["calculated_at"] == "2026-09-23T14:15:00+00:00"


def test_investigation_workflow_is_idempotent_audited_and_exportable(
    repository: MoneyGraphRepository,
) -> None:
    created = repository.create_investigation(
        title="Проверка общего получателя",
        description="Наблюдаемая гипотеза",
        run_id=None,
        model_version="rules-v1",
        gids=[OPAQUE_GID],
        actor="analyst-a",
    )

    repository.add_investigation_nodes(
        created["id"], [OPAQUE_GID, "100000003037660100"], actor="analyst-a"
    )
    note = repository.add_note(
        created["id"], "Проверить внешние входящие операции.", actor="analyst-a"
    )
    updated = repository.update_investigation_status(created["id"], "in_review", actor="analyst-a")

    assert updated["status"] == "in_review"
    assert {item["gid"] for item in updated["nodes"]} == {
        OPAQUE_GID,
        "100000003037660100",
    }
    assert updated["notes"][0]["id"] == note["id"]
    assert updated["notes"][0]["body"] == "Проверить внешние входящие операции."

    exported_json = repository.export_investigation(created["id"], format="json")
    assert exported_json["investigation"]["id"] == created["id"]
    assert len(exported_json["audit_events"]) == 4

    exported_csv = repository.export_investigation(created["id"], format="csv")
    rows = list(csv.DictReader(io.StringIO(exported_csv)))
    assert {row["record_type"] for row in rows} == {"investigation", "node", "note"}
    assert OPAQUE_GID in {row["gid"] for row in rows}


def test_repository_rejects_unknown_investigation_and_invalid_status(
    repository: MoneyGraphRepository,
) -> None:
    with pytest.raises(InvestigationNotFoundError):
        repository.get_investigation("missing")

    created = repository.create_investigation(
        title="Case",
        description="",
        run_id=None,
        model_version="rules-v1",
        gids=[],
        actor="pytest",
    )
    with pytest.raises(ValueError, match="Unsupported investigation status"):
        repository.update_investigation_status(created["id"], "deleted", actor="pytest")
