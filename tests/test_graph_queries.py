from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from moneygraph.services.artifacts import ArtifactStore
from moneygraph.services.graph_queries import GraphQueryService

GID_A = "100000000000000001"
GID_B = "100000000000000002"
GID_C = "100000000000000003"
GID_D = "100000000000000004"
GID_E = "100000000000000005"


@pytest.fixture
def graph_service(tmp_path: Path) -> GraphQueryService:
    out_dir = tmp_path / "out"
    artifacts_dir = tmp_path / "artifacts"
    data_dir = tmp_path / "data"
    out_dir.mkdir()
    artifacts_dir.mkdir()
    data_dir.mkdir()

    pd.DataFrame(
        [
            {
                "gid": gid,
                "role": role,
                "role_score": score,
                "cluster_id": cluster_id,
                "priority_score": priority,
                "evidence": f"Наблюдается {index + 1} связь.",
            }
            for index, (gid, role, score, cluster_id, priority) in enumerate(
                [
                    (GID_A, "peripheral", 0.2, 1, 0.1),
                    (GID_B, "transit", 0.7, 1, 0.6),
                    (GID_C, "coordinator", 0.9, 1, 0.95),
                    (GID_D, "distributor", 0.8, 1, 0.7),
                    (GID_E, "terminal", 0.6, 2, 0.5),
                ]
            )
        ]
    ).to_csv(out_dir / "nodes_roles.csv", index=False)
    pd.DataFrame(
        [
            {
                "rank": 1,
                "gid": GID_C,
                "role": "coordinator",
                "priority_score": 0.95,
                "why": "3 связи",
            },
            {
                "rank": 2,
                "gid": GID_D,
                "role": "distributor",
                "priority_score": 0.7,
                "why": "2 связи",
            },
        ]
    ).to_csv(out_dir / "top_nodes.csv", index=False)
    pd.DataFrame(
        [
            {
                "cluster_id": 1,
                "n_nodes": 4,
                "n_seed": 1,
                "sum_kzt_internal": 240.0,
                "top_gids": f"{GID_C}|{GID_D}",
                "hypothesis": "Общий получатель.",
            },
            {
                "cluster_id": 2,
                "n_nodes": 1,
                "n_seed": 0,
                "sum_kzt_internal": 0.0,
                "top_gids": GID_E,
                "hypothesis": "Периферийный узел.",
            },
        ]
    ).to_csv(out_dir / "clusters.csv", index=False)
    pd.DataFrame(
        [
            {"gid": GID_A, "depth": 0, "is_seed": True, "in_deg": 0, "out_deg": 2},
            {"gid": GID_B, "depth": 1, "is_seed": False, "in_deg": 1, "out_deg": 1},
            {"gid": GID_C, "depth": 2, "is_seed": False, "in_deg": 3, "out_deg": 1},
            {"gid": GID_D, "depth": 0, "is_seed": True, "in_deg": 0, "out_deg": 1},
            {"gid": GID_E, "depth": 3, "is_seed": False, "in_deg": 1, "out_deg": 0},
        ]
    ).to_parquet(artifacts_dir / "node_features.parquet", index=False)
    pd.DataFrame(
        [
            {"src": GID_A, "dst": GID_B, "sum_kzt": 100.0, "n_tx": 1},
            {"src": GID_B, "dst": GID_C, "sum_kzt": 80.0, "n_tx": 2},
            {"src": GID_A, "dst": GID_C, "sum_kzt": 40.0, "n_tx": 1},
            {"src": GID_D, "dst": GID_C, "sum_kzt": 20.0, "n_tx": 1},
            {"src": GID_C, "dst": GID_E, "sum_kzt": 10.0, "n_tx": 1},
        ]
    ).to_parquet(artifacts_dir / "edges_enriched.parquet", index=False)

    return GraphQueryService(ArtifactStore(out_dir, artifacts_dir, data_dir))


def test_artifact_store_preserves_identifiers_and_merges_features(
    graph_service: GraphQueryService,
) -> None:
    profile = graph_service.get_node(GID_A)

    assert profile["gid"] == GID_A
    assert isinstance(profile["gid"], str)
    assert profile["is_seed"] is True
    assert profile["priority_score"] == pytest.approx(0.1)


def test_artifact_store_serializes_missing_dates_as_none(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    artifacts_dir = tmp_path / "artifacts"
    data_dir = tmp_path / "data"
    out_dir.mkdir()
    artifacts_dir.mkdir()
    data_dir.mkdir()
    pd.DataFrame(
        [
            {
                "gid": GID_A,
                "role": "terminal",
                "role_score": 0.8,
                "cluster_id": 1,
                "priority_score": 0.4,
                "evidence": "Нет исходящих в наблюдаемом графе.",
            }
        ]
    ).to_csv(out_dir / "nodes_roles.csv", index=False)
    pd.DataFrame(
        [{"gid": GID_A, "first_out_date": pd.NaT, "last_out_date": pd.NaT}]
    ).to_parquet(artifacts_dir / "node_features.parquet", index=False)
    pd.DataFrame(columns=["src", "dst", "sum_kzt", "n_tx"]).to_parquet(
        artifacts_dir / "edges_enriched.parquet", index=False
    )

    profile = GraphQueryService(ArtifactStore(out_dir, artifacts_dir, data_dir)).get_node(GID_A)

    assert profile["first_out_date"] is None
    assert profile["last_out_date"] is None


def test_ego_is_directional_and_bounded(graph_service: GraphQueryService) -> None:
    ego = graph_service.ego(GID_B, depth=1)

    assert ego["root_gid"] == GID_B
    assert {node["gid"] for node in ego["nodes"]} == {GID_A, GID_B, GID_C}
    assert {(edge["src"], edge["dst"]) for edge in ego["edges"]} == {
        (GID_A, GID_B),
        (GID_A, GID_C),
        (GID_B, GID_C),
    }
    with pytest.raises(ValueError, match="depth must be between 1 and 2"):
        graph_service.ego(GID_A, depth=3)


def test_traces_are_ranked_limited_and_never_exceed_depth_four(
    graph_service: GraphQueryService,
) -> None:
    downstream = graph_service.trace(GID_A, direction="downstream", depth=4, max_paths=2)
    upstream = graph_service.trace(GID_C, direction="upstream", depth=2, max_paths=10)

    assert len(downstream["paths"]) == 2
    assert downstream["paths"][0]["rank_score"] >= downstream["paths"][1]["rank_score"]
    assert all(len(path["path"]) - 1 <= 4 for path in downstream["paths"])
    assert {tuple(path["path"]) for path in upstream["paths"]} >= {
        (GID_C, GID_B),
        (GID_C, GID_D),
    }
    with pytest.raises(ValueError, match="depth must be between 1 and 4"):
        graph_service.trace(GID_A, direction="downstream", depth=5, max_paths=10)


def test_common_receivers_use_coverage_distance_priority_and_paths(
    graph_service: GraphQueryService,
) -> None:
    result = graph_service.common_receivers([GID_A, GID_D], max_depth=3, limit=10)

    assert result["candidates"][0]["gid"] == GID_C
    assert result["candidates"][0]["reached_by"] == 2
    assert result["candidates"][0]["coverage_ratio"] == 1.0
    assert result["candidates"][0]["min_distance"] == 1
    assert result["candidates"][0]["role"] == "coordinator"
    assert len(result["candidates"][0]["example_paths"]) == 2


def test_compare_nodes_and_unknown_gid(graph_service: GraphQueryService) -> None:
    comparison = graph_service.compare_nodes([GID_A, GID_C])

    assert [node["gid"] for node in comparison["nodes"]] == [GID_A, GID_C]
    assert comparison["shared_successors"] == [GID_E]
    with pytest.raises(KeyError, match="unknown"):
        graph_service.get_node("unknown")
