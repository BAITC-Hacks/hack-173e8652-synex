from __future__ import annotations

import math

from moneygraph.pipeline.features import compute_graph_features
from moneygraph.pipeline.graph_builder import build_graph


def test_graph_includes_isolates_and_uses_inverse_log_distance(synthetic_frames) -> None:
    nodes, edges, tx = synthetic_frames

    graph = build_graph(nodes, edges)
    features = compute_graph_features(graph, nodes, tx)

    assert set(graph.nodes) == set(nodes["gid"])
    assert graph["seed"]["collector"]["distance"] == pytest.approx(
        1.0 / math.log1p(30_000.0)
    )
    isolated = features.set_index("gid").loc["isolated"]
    assert isolated["in_deg"] == 0
    assert isolated["out_deg"] == 0


def test_seed_pass_through_is_invalid_and_depth_four_sink_is_truncated(synthetic_frames) -> None:
    nodes, edges, tx = synthetic_frames
    features = compute_graph_features(build_graph(nodes, edges), nodes, tx).set_index("gid")

    assert bool(features.loc["seed", "pass_through_valid"]) is False
    assert bool(features.loc["truncated", "truncated_by_depth"]) is True
    assert features.loc["collector", "pass_through"] == pytest.approx(25_000 / 30_000)


import pytest  # noqa: E402  (keeps the behavioral setup visually first)

