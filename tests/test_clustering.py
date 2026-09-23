from __future__ import annotations

from moneygraph.pipeline.clustering import detect_communities
from moneygraph.pipeline.graph_builder import build_graph


def test_community_ids_are_stable_and_isolates_are_separate(synthetic_frames) -> None:
    nodes, edges, _ = synthetic_frames
    graph = build_graph(nodes, edges)

    first = detect_communities(graph, seed=42).membership.sort_values("gid").reset_index(drop=True)
    second = detect_communities(graph, seed=42).membership.sort_values("gid").reset_index(drop=True)

    assert first.equals(second)
    isolate_cluster = first.loc[first["gid"] == "isolated", "cluster_id"].item()
    assert (first["cluster_id"] == isolate_cluster).sum() == 1

