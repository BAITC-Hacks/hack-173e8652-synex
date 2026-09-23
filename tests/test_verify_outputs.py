from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from moneygraph.verification import verify_outputs


def _write_valid_outputs(out_dir: Path) -> None:
    out_dir.mkdir(exist_ok=True)
    nodes = pd.DataFrame(
        [
            {
                "gid": str(index),
                "role": "peripheral",
                "role_score": 0.2,
                "cluster_id": index,
                "priority_score": 1 - index / 100,
                "evidence": f"Наблюдается {index + 1} связь; специальных признаков недостаточно.",
            }
            for index in range(20)
        ]
    )
    nodes.to_csv(out_dir / "nodes_roles.csv", index=False)
    pd.DataFrame(
        [
            {
                "cluster_id": index,
                "n_nodes": 1,
                "n_seed": 0,
                "sum_kzt_internal": 0,
                "top_gids": f'["{index}"]',
                "hypothesis": "Изолированный узел; данных недостаточно для структурной гипотезы.",
            }
            for index in range(20)
        ]
    ).to_csv(out_dir / "clusters.csv", index=False)
    pd.DataFrame(
        [
            {
                "rank": index + 1,
                "gid": str(index),
                "role": "peripheral",
                "priority_score": 1 - index / 100,
                "why": f"Приоритет {1 - index / 100:.2f}; требуется проверка наблюдаемых связей.",
            }
            for index in range(20)
        ]
    ).to_csv(out_dir / "top_nodes.csv", index=False)
    (out_dir / "run_manifest.json").write_text(
        json.dumps({"duration_seconds": 1.25, "counts": {"nodes": 20}}),
        encoding="utf-8",
    )


def test_verifier_accepts_consistent_outputs(tmp_path: Path) -> None:
    _write_valid_outputs(tmp_path)

    errors = verify_outputs(tmp_path, expected_nodes=20)

    assert errors == []


def test_verifier_reports_empty_evidence_and_wrong_count(tmp_path: Path) -> None:
    _write_valid_outputs(tmp_path)
    nodes = pd.read_csv(tmp_path / "nodes_roles.csv", dtype={"gid": "string"})
    nodes.loc[0, "evidence"] = ""
    nodes.to_csv(tmp_path / "nodes_roles.csv", index=False)

    errors = verify_outputs(tmp_path, expected_nodes=21)

    assert any("21" in error for error in errors)
    assert any("evidence" in error for error in errors)


def test_verifier_rejects_evidence_without_numeric_fact(tmp_path: Path) -> None:
    _write_valid_outputs(tmp_path)
    nodes = pd.read_csv(tmp_path / "nodes_roles.csv", dtype={"gid": "string"})
    nodes.loc[0, "evidence"] = "Специальных признаков не выявлено."
    nodes.to_csv(tmp_path / "nodes_roles.csv", index=False)

    errors = verify_outputs(tmp_path, expected_nodes=20)

    assert any("numeric" in error for error in errors)
