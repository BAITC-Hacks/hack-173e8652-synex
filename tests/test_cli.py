from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from moneygraph import cli


def test_cli_main_reports_stage_timings_with_in_process_analysis(
    synthetic_data_dir: Path,
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / "out-in-process"
    artifacts_dir = tmp_path / "artifacts-in-process"

    result = cli.main(
        [
            "analyze",
            "--data",
            str(synthetic_data_dir),
            "--out",
            str(out_dir),
            "--artifacts",
            str(artifacts_dir),
            "--database-url",
            f"sqlite:///{tmp_path / 'cli.db'}",
            "--log-level",
            "INFO",
        ]
    )

    captured = capsys.readouterr().out

    assert result == 0
    assert "run_id=" in captured
    assert "validation:" in captured
    assert "total:" in captured
    assert str(out_dir.resolve()) in captured
    assert (out_dir / "nodes_roles.csv").exists()


def test_cli_synthetic_pipeline_creates_complete_outputs(
    synthetic_data_dir: Path,
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    artifacts_dir = tmp_path / "artifacts"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "moneygraph.cli",
            "analyze",
            "--data",
            str(synthetic_data_dir),
            "--out",
            str(out_dir),
            "--artifacts",
            str(artifacts_dir),
            "--database-url",
            f"sqlite:///{tmp_path / 'test.db'}",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    nodes_roles = pd.read_csv(out_dir / "nodes_roles.csv", dtype={"gid": "string"})
    clusters = pd.read_csv(out_dir / "clusters.csv")
    top_nodes = pd.read_csv(out_dir / "top_nodes.csv", dtype={"gid": "string"})
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))

    assert list(nodes_roles.columns) == [
        "gid",
        "role",
        "role_score",
        "cluster_id",
        "priority_score",
        "evidence",
    ]
    assert len(nodes_roles) == 6
    assert nodes_roles["evidence"].str.contains(r"\d", regex=True).all()
    assert not clusters.empty
    assert len(top_nodes) == 6
    assert top_nodes["rank"].tolist() == list(range(1, 7))
    assert manifest["counts"]["nodes"] == 6
    assert manifest["duration_seconds"] < 60
    assert (artifacts_dir / "node_features.parquet").exists()
    assert (artifacts_dir / "edges_enriched.parquet").exists()
