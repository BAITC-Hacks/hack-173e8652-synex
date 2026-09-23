"""Deterministic structural what-if analysis for node removal."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

import networkx as nx
import pandas as pd

RESILIENCE_COLUMNS: tuple[str, ...] = (
    "scenario",
    "n_removed",
    "largest_component_size",
    "n_components",
    "largest_component_ratio",
    "fragmentation_delta",
)


def _gid_key(gid: Any) -> tuple[str, str]:
    return type(gid).__name__, str(gid)


def _component_metrics(graph: nx.Graph, denominator: int) -> tuple[int, int, float]:
    if graph.number_of_nodes() == 0:
        return 0, 0, 0.0
    undirected = graph.to_undirected(as_view=False)
    components = tuple(nx.connected_components(undirected))
    largest = max((len(component) for component in components), default=0)
    ratio = largest / denominator if denominator > 0 else 0.0
    return largest, len(components), ratio


def _ranked_gids(priorities: pd.DataFrame, *, descending: bool) -> list[str]:
    if not {"gid", "priority_score"}.issubset(priorities.columns):
        raise ValueError("priorities must contain gid and priority_score")
    normalized_gids = priorities["gid"].map(str)
    if normalized_gids.duplicated().any():
        raise ValueError("priorities must contain unique gid values")
    pairs = [
        (gid, float(cast(Any, score)))
        for gid, score in zip(
            normalized_gids,
            priorities["priority_score"],
            strict=True,
        )
    ]
    if descending:
        pairs.sort(key=lambda pair: (-pair[1], _gid_key(pair[0])))
    else:
        pairs.sort(key=lambda pair: (pair[1], _gid_key(pair[0])))
    return [gid for gid, _ in pairs]


def analyze_resilience(
    graph: nx.Graph,
    priorities: pd.DataFrame,
    removal_counts: Iterable[int] = (1, 3, 5, 10, 20),
) -> pd.DataFrame:
    """Compare priority-targeted removal with a low-priority baseline.

    This is a structural scenario over the observed graph, not a forecast of
    real customer behaviour.
    """

    counts = tuple(int(value) for value in removal_counts)
    if not counts or any(value <= 0 for value in counts):
        raise ValueError("removal_counts must contain positive integers")
    if len(set(counts)) != len(counts):
        raise ValueError("removal_counts must be unique")
    graph_node_by_gid = {str(node): node for node in graph.nodes}
    if len(graph_node_by_gid) != graph.number_of_nodes():
        raise ValueError("graph contains gids that collide after string normalization")
    priority_nodes = set(priorities["gid"].map(str)) if "gid" in priorities else set()
    if not set(graph_node_by_gid).issubset(priority_nodes):
        raise ValueError("priorities must contain every graph node")

    denominator = graph.number_of_nodes()
    baseline_largest, baseline_components, baseline_ratio = _component_metrics(graph, denominator)
    rows: list[dict[str, int | float | str]] = [
        {
            "scenario": "baseline",
            "n_removed": 0,
            "largest_component_size": baseline_largest,
            "n_components": baseline_components,
            "largest_component_ratio": baseline_ratio,
            "fragmentation_delta": 0,
        }
    ]
    rankings = {
        "priority": _ranked_gids(priorities, descending=True),
        "low_priority": _ranked_gids(priorities, descending=False),
    }
    for scenario, ranked in rankings.items():
        for requested_count in sorted(counts):
            actual_count = min(requested_count, denominator)
            reduced = graph.copy()
            reduced.remove_nodes_from(
                graph_node_by_gid[gid] for gid in ranked[:actual_count] if gid in graph_node_by_gid
            )
            largest, n_components, ratio = _component_metrics(reduced, denominator)
            rows.append(
                {
                    "scenario": scenario,
                    "n_removed": actual_count,
                    "largest_component_size": largest,
                    "n_components": n_components,
                    "largest_component_ratio": ratio,
                    "fragmentation_delta": n_components - baseline_components,
                }
            )
    return pd.DataFrame(rows, columns=RESILIENCE_COLUMNS)
