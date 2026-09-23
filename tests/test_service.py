from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from moneygraph.pipeline.service import run_analysis
from moneygraph.repository.database import Database
from moneygraph.repository.repositories import MoneyGraphRepository


def test_run_analysis_publishes_verified_outputs_and_manifest(
    synthetic_data_dir: Path,
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    artifacts_dir = tmp_path / "artifacts"

    result = run_analysis(
        synthetic_data_dir,
        out_dir,
        artifacts_dir,
        database_url=f"sqlite:///{tmp_path / 'service.db'}",
    )

    nodes_roles = pd.read_csv(out_dir / "nodes_roles.csv", dtype={"gid": "string"})
    clusters = pd.read_csv(out_dir / "clusters.csv")
    top_nodes = pd.read_csv(out_dir / "top_nodes.csv", dtype={"gid": "string"})
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    validation = json.loads((out_dir / "validation_report.json").read_text(encoding="utf-8"))

    assert result.out_dir == out_dir.resolve()
    assert result.artifacts_dir == artifacts_dir.resolve()
    assert result.manifest["status"] == "completed"
    assert result.manifest["counts"] == {"nodes": 6, "edges": 4, "transactions": 5, "seed": 2}
    assert result.manifest["database_url_configured"] is True
    assert set(result.manifest["stage_durations_seconds"]) >= {
        "config",
        "ingestion",
        "validation",
        "graph",
        "graph_features",
        "temporal_features",
        "clustering",
        "role_scoring",
        "priority_scoring",
        "resilience",
        "export",
    }
    assert len(nodes_roles) == 6
    assert nodes_roles["evidence"].str.contains(r"\d", regex=True).all()
    assert not clusters.empty
    assert top_nodes["rank"].tolist() == list(range(1, len(top_nodes) + 1))
    assert summary["counts"]["nodes"] == 6
    assert validation["case_profile"]["observed"]["nodes"] == 6
    assert (artifacts_dir / "node_features.parquet").is_file()
    assert (artifacts_dir / "edges_enriched.parquet").is_file()

    database = Database(f"sqlite:///{tmp_path / 'service.db'}")
    repository = MoneyGraphRepository(database.session_factory)
    persisted = repository.get_run(result.run_id)
    assert persisted["id"] == result.run_id
    assert persisted["status"] == "completed"
    assert len(persisted["node_snapshots"]) == 6
    assert persisted["manifest"]["hashes"] == result.manifest["hashes"]
    database.dispose()


def test_run_analysis_is_deterministic_for_mandatory_csvs(
    synthetic_data_dir: Path,
    tmp_path: Path,
) -> None:
    first_out = tmp_path / "first-out"
    second_out = tmp_path / "second-out"

    run_analysis(synthetic_data_dir, first_out, tmp_path / "first-artifacts")
    run_analysis(synthetic_data_dir, second_out, tmp_path / "second-artifacts")

    for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
        assert (first_out / filename).read_bytes() == (second_out / filename).read_bytes()
