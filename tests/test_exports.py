from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from moneygraph.pipeline.exporters import (
    CLUSTERS_COLUMNS,
    NODES_ROLES_COLUMNS,
    TOP_NODES_COLUMNS,
    build_cluster_summary,
    export_mandatory_csvs,
)


def _node_features() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "gid": 3,
                "depth": 2,
                "is_seed": False,
                "role": "coordinator",
                "role_score": 0.91,
                "cluster_id": 0,
                "priority_score": 1.0,
                "evidence": "Посредничество 0.90; достигнуто seed: 4.",
            },
            {
                "gid": 1,
                "depth": 0,
                "is_seed": True,
                "role": "distributor",
                "role_score": 0.75,
                "cluster_id": 0,
                "priority_score": 0.6,
                "evidence": "Распределение: 3 получателя, 90 000 KZT; seed=1.",
            },
            {
                "gid": 2,
                "depth": 4,
                "is_seed": False,
                "role": "peripheral",
                "role_score": 0.64,
                "cluster_id": 1,
                "priority_score": 0.1,
                "evidence": "Граница depth=4: связей 1, terminal не подтверждён.",
            },
        ]
    )


def _edges() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"src": 1, "dst": 3, "sum_kzt": 90_000.0, "n_tx": 2},
            {"src": 3, "dst": 2, "sum_kzt": 50_000.0, "n_tx": 1},
        ]
    )


def test_exports_have_mandatory_schemas_rows_and_order(tmp_path: Path) -> None:
    paths = export_mandatory_csvs(_node_features(), _edges(), tmp_path, top_n=3)

    roles = pd.read_csv(paths.nodes_roles, dtype={"gid": "string"})
    clusters = pd.read_csv(paths.clusters)
    top = pd.read_csv(paths.top_nodes)

    assert list(roles.columns) == list(NODES_ROLES_COLUMNS)
    assert list(clusters.columns) == list(CLUSTERS_COLUMNS)
    assert list(top.columns) == list(TOP_NODES_COLUMNS)
    assert len(roles) == 3
    assert set(roles["gid"]) == {"1", "2", "3"}
    assert len(clusters) == 2
    assert list(top["rank"]) == [1, 2, 3]
    assert top["priority_score"].is_monotonic_decreasing
    assert top["why"].str.len().gt(0).all()


def test_mandatory_csv_exports_are_byte_deterministic(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = export_mandatory_csvs(
        _node_features().sample(frac=1.0, random_state=3),
        _edges().sample(frac=1.0, random_state=2),
        first_dir,
        top_n=3,
    )
    second = export_mandatory_csvs(_node_features(), _edges(), second_dir, top_n=3)

    for first_path, second_path in zip(first, second, strict=True):
        assert first_path.read_bytes() == second_path.read_bytes()


def test_external_gids_are_strings_and_sorted_lexicographically(tmp_path: Path) -> None:
    features = pd.concat(
        [
            _node_features().iloc[[0]].assign(gid=2, priority_score=0.5),
            _node_features().iloc[[1]].assign(gid=10, priority_score=0.5),
        ],
        ignore_index=True,
    )
    paths = export_mandatory_csvs(
        features,
        pd.DataFrame(columns=["src", "dst", "sum_kzt"]),
        tmp_path,
        top_n=2,
    )

    roles = pd.read_csv(paths.nodes_roles, dtype={"gid": "string"})
    top = pd.read_csv(paths.top_nodes, dtype={"gid": "string"})

    assert list(roles["gid"]) == ["10", "2"]
    assert list(top["gid"]) == ["10", "2"]


def test_cluster_summary_normalizes_mixed_gid_types() -> None:
    features = _node_features().query("cluster_id == 0")
    string_edges = pd.DataFrame([{"src": "1", "dst": "3", "sum_kzt": 90_000.0, "n_tx": 2}])

    cluster = build_cluster_summary(features, string_edges).iloc[0]

    assert cluster["sum_kzt_internal"] == 90_000.0
    assert "Изолированный" not in cluster["hypothesis"]


def test_mandatory_export_rejects_fewer_than_twenty_top_rows_on_large_input(
    tmp_path: Path,
) -> None:
    template = _node_features().iloc[0].to_dict()
    features = pd.DataFrame([{**template, "gid": gid} for gid in range(20)])

    with pytest.raises(ValueError, match="at least 20"):
        export_mandatory_csvs(
            features,
            pd.DataFrame(columns=["src", "dst", "sum_kzt"]),
            tmp_path,
            top_n=10,
        )
