from __future__ import annotations

import networkx as nx
import pandas as pd

from moneygraph.pipeline.resilience import analyze_resilience


def test_resilience_contains_baseline_and_all_required_removal_counts() -> None:
    graph = nx.path_graph(30, create_using=nx.DiGraph)
    priorities = pd.DataFrame(
        {
            "gid": list(graph.nodes),
            "priority_score": [1.0 - abs(node - 15) / 30 for node in graph.nodes],
        }
    )

    result = analyze_resilience(graph, priorities)

    assert list(result.columns) == [
        "scenario",
        "n_removed",
        "largest_component_size",
        "n_components",
        "largest_component_ratio",
        "fragmentation_delta",
    ]
    assert len(result.query("scenario == 'baseline'")) == 1
    for _scenario in ("priority", "low_priority"):
        assert set(result.query("scenario == @_scenario")["n_removed"]) == {1, 3, 5, 10, 20}
    assert result["largest_component_ratio"].between(0.0, 1.0).all()


def test_resilience_is_deterministic() -> None:
    graph = nx.cycle_graph(25, create_using=nx.DiGraph)
    priorities = pd.DataFrame(
        {"gid": list(graph.nodes), "priority_score": [node % 5 / 5 for node in graph.nodes]}
    )

    first = analyze_resilience(graph, priorities)
    second = analyze_resilience(graph, priorities.sample(frac=1.0, random_state=4))

    pd.testing.assert_frame_equal(first, second)


def test_resilience_normalizes_gid_types_and_reports_actual_small_graph_removals() -> None:
    graph = nx.path_graph(5, create_using=nx.DiGraph)
    priorities = pd.DataFrame(
        {"gid": [str(node) for node in graph.nodes], "priority_score": [0.1] * 5}
    )

    result = analyze_resilience(graph, priorities)

    assert result["n_removed"].max() == 5
    assert result["largest_component_size"].ge(0).all()
