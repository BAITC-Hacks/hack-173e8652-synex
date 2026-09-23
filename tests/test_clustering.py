from __future__ import annotations

from moneygraph.pipeline.clustering import compute_cluster_bridge_features, detect_communities
from moneygraph.pipeline.graph_builder import build_graph


def test_community_ids_are_stable_and_isolates_are_separate(synthetic_frames) -> None:
    nodes, edges, _ = synthetic_frames
    graph = build_graph(nodes, edges)

    first = detect_communities(graph, seed=42).membership.sort_values("gid").reset_index(drop=True)
    second = detect_communities(graph, seed=42).membership.sort_values("gid").reset_index(drop=True)

    assert first.equals(second)
    isolate_cluster = first.loc[first["gid"] == "isolated", "cluster_id"].item()
    assert (first["cluster_id"] == isolate_cluster).sum() == 1


def test_cluster_ids_use_size_then_lexicographic_minimum_for_ties() -> None:
    import networkx as nx

    graph = nx.DiGraph()
    graph.add_edge("z", "zz", sum_kzt=1.0, n_tx=1, distance=1.0)
    graph.add_edge("a", "aa", sum_kzt=1.0, n_tx=1, distance=1.0)

    result = detect_communities(graph, seed=42).membership.set_index("gid")

    assert result.loc["a", "cluster_id"] == 0
    assert result.loc["z", "cluster_id"] == 1


def test_cross_cluster_bridge_features_are_directed_and_bounded() -> None:
    import networkx as nx
    import pandas as pd

    graph = nx.DiGraph()
    graph.add_edge("a", "b", sum_kzt=10.0, n_tx=1, distance=1.0)
    graph.add_edge("b", "c", sum_kzt=20.0, n_tx=1, distance=1.0)
    membership = pd.DataFrame(
        [
            {"gid": "a", "cluster_id": 0},
            {"gid": "b", "cluster_id": 0},
            {"gid": "c", "cluster_id": 1},
        ]
    )

    features = compute_cluster_bridge_features(graph, membership).set_index("gid")

    assert features.loc["b", "cross_cluster_out_edges"] == 1
    assert features.loc["c", "cross_cluster_in_edges"] == 1
    assert features.loc["a", "cross_cluster_bridge_signal"] == 0.0
    assert features["cross_cluster_bridge_signal"].between(0.0, 1.0).all()
