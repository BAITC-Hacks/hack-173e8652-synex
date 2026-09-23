"""Explainable graph, flow, and transaction-amount features."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping
from typing import Any, cast

import networkx as nx
import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)


def _normalized_strength(values: Mapping[str, float]) -> dict[str, float]:
    non_negative = {str(key): max(float(value), 0.0) for key, value in values.items()}
    total = sum(non_negative.values())
    if total <= 0:
        return {key: 0.0 for key in non_negative}
    return {key: value / total for key, value in non_negative.items()}


def _safe_pagerank(graph: nx.DiGraph) -> dict[str, float]:
    if graph.number_of_nodes() == 0:
        return {}
    try:
        return {
            str(key): float(value) for key, value in nx.pagerank(graph, weight="sum_kzt").items()
        }
    except Exception as exc:  # numerical backends expose version-specific convergence errors
        LOGGER.warning("PageRank failed; using deterministic uniform fallback: %s", exc)
        uniform = 1.0 / graph.number_of_nodes()
        return {str(gid): uniform for gid in graph.nodes}


def _safe_hits(graph: nx.DiGraph) -> tuple[dict[str, float], dict[str, float]]:
    if graph.number_of_nodes() == 0:
        return {}, {}
    if graph.number_of_edges() == 0:
        zeros = {str(gid): 0.0 for gid in graph.nodes}
        return zeros, zeros.copy()
    try:
        hubs, authorities = nx.hits(graph, max_iter=1_000, tol=1e-10, normalized=True)
        clean_hubs = _normalized_strength(
            {str(key): abs(float(value)) for key, value in hubs.items()}
        )
        clean_authorities = _normalized_strength(
            {str(key): abs(float(value)) for key, value in authorities.items()}
        )
        return clean_hubs, clean_authorities
    except Exception as exc:  # HITS may fail through NetworkX, SciPy ARPACK, or missing backend
        LOGGER.warning("HITS failed; using weighted-degree fallback: %s", exc)
        hubs = _normalized_strength(
            {str(gid): float(value) for gid, value in graph.out_degree(weight="sum_kzt")}
        )
        authorities = _normalized_strength(
            {str(gid): float(value) for gid, value in graph.in_degree(weight="sum_kzt")}
        )
        return hubs, authorities


def _stable_components(graph: nx.DiGraph) -> tuple[dict[str, int], dict[str, int]]:
    components = [
        tuple(sorted(str(node) for node in component))
        for component in nx.weakly_connected_components(graph)
    ]
    components.sort(key=lambda component: (-len(component), component[0]))
    component_id: dict[str, int] = {}
    component_size: dict[str, int] = {}
    for identifier, component in enumerate(components):
        for gid in component:
            component_id[gid] = identifier
            component_size[gid] = len(component)
    return component_id, component_size


def _seed_reach(
    graph: nx.DiGraph,
    seed_gids: set[str],
    *,
    cutoff: int,
) -> tuple[dict[str, int], dict[str, float], dict[str, float]]:
    distances: defaultdict[str, list[int]] = defaultdict(list)
    for seed in sorted(seed_gids):
        if seed not in graph:
            continue
        for gid, distance in nx.single_source_shortest_path_length(
            graph, seed, cutoff=cutoff
        ).items():
            # A zero-edge path conveys no cross-client reach. This also prevents every
            # isolated seed receiving a misleading positive seed-reach signal.
            if distance > 0:
                distances[str(gid)].append(int(distance))
    counts = {gid: len(values) for gid, values in distances.items()}
    minima = {gid: float(min(values)) for gid, values in distances.items()}
    means = {gid: float(np.mean(values)) for gid, values in distances.items()}
    return counts, minima, means


def _transaction_amount_statistics(
    transactions: pd.DataFrame,
    *,
    round_amount_multiple: int,
) -> dict[str, dict[str, float]]:
    amounts: defaultdict[str, list[float]] = defaultdict(list)
    for row in transactions[["src", "dst", "sum_kzt"]].itertuples(index=False):
        source = str(row.src)
        destination = str(row.dst)
        amount = float(cast(Any, row.sum_kzt))
        amounts[source].append(amount)
        if destination != source:
            amounts[destination].append(amount)

    statistics: dict[str, dict[str, float]] = {}
    for gid, observed in amounts.items():
        values = np.asarray(observed, dtype=float)
        unique, counts = np.unique(values, return_counts=True)
        repeated_values = set(unique[counts > 1].tolist())
        repeated_ratio = float(np.mean([value in repeated_values for value in values]))
        remainders = np.mod(values, float(round_amount_multiple))
        round_ratio = float(np.mean(np.isclose(remainders, 0.0, atol=0.01)))
        statistics[gid] = {
            "tx_amount_mean": float(np.mean(values)),
            "tx_amount_median": float(np.median(values)),
            "tx_amount_max": float(np.max(values)),
            "tx_amount_std": float(np.std(values)),
            "repeated_amount_ratio": repeated_ratio,
            "round_amount_ratio": round_ratio,
        }
    return statistics


def _side_amount_statistics(
    transactions: pd.DataFrame,
    *,
    side: str,
    prefix: str,
) -> pd.DataFrame:
    if transactions.empty:
        return pd.DataFrame(
            columns=[
                "gid",
                f"{prefix}_amount_mean",
                f"{prefix}_amount_median",
                f"{prefix}_amount_max",
                f"{prefix}_amount_std",
            ]
        )
    return (
        transactions.groupby(side, sort=True)["sum_kzt"]
        .agg(["mean", "median", "max", "std"])
        .fillna(0.0)
        .rename(
            columns={
                "mean": f"{prefix}_amount_mean",
                "median": f"{prefix}_amount_median",
                "max": f"{prefix}_amount_max",
                "std": f"{prefix}_amount_std",
            }
        )
        .rename_axis("gid")
        .reset_index()
    )


def _percentile(values: pd.Series, *, logarithmic: bool = False) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    transformed = np.log1p(numeric.clip(lower=0)) if logarithmic else numeric
    result = transformed.rank(method="average", pct=True, na_option="keep")
    return result.fillna(0.0).astype(float)


def compute_graph_features(
    graph: nx.DiGraph,
    nodes: pd.DataFrame,
    transactions: pd.DataFrame,
    *,
    max_seed_hops: int = 4,
    round_amount_multiple: int = 10_000,
    random_seed: int = 42,
    betweenness_k: int | None = None,
) -> pd.DataFrame:
    """Compute deterministic per-node structural and financial features."""

    if max_seed_hops < 1:
        raise ValueError("max_seed_hops must be positive")
    if round_amount_multiple < 1:
        raise ValueError("round_amount_multiple must be positive")
    required_nodes = {"gid", "depth", "is_seed"}
    required_transactions = {"src", "dst", "sum_kzt"}
    if missing := required_nodes.difference(nodes.columns):
        raise ValueError(f"nodes missing required columns: {', '.join(sorted(missing))}")
    if missing := required_transactions.difference(transactions.columns):
        raise ValueError(f"transactions missing required columns: {', '.join(sorted(missing))}")

    node_frame = nodes[["gid", "depth", "is_seed"]].copy(deep=True)
    node_frame["gid"] = node_frame["gid"].astype("string")
    node_frame = node_frame.sort_values("gid", kind="stable").reset_index(drop=True)
    transaction_frame = transactions.copy(deep=True)
    transaction_frame["src"] = transaction_frame["src"].astype("string")
    transaction_frame["dst"] = transaction_frame["dst"].astype("string")

    graph_gids = {str(gid) for gid in graph.nodes}
    expected_gids = set(node_frame["gid"])
    if graph_gids != expected_gids:
        missing_graph = expected_gids.difference(graph_gids)
        extra_graph = graph_gids.difference(expected_gids)
        raise ValueError(
            "graph/nodes identifier mismatch "
            f"(missing={len(missing_graph)}, extra={len(extra_graph)})"
        )

    in_deg = {str(gid): int(value) for gid, value in graph.in_degree()}
    out_deg = {str(gid): int(value) for gid, value in graph.out_degree()}
    in_kzt = {str(gid): float(value) for gid, value in graph.in_degree(weight="sum_kzt")}
    out_kzt = {str(gid): float(value) for gid, value in graph.out_degree(weight="sum_kzt")}
    in_tx = {str(gid): int(value) for gid, value in graph.in_degree(weight="n_tx")}
    out_tx = {str(gid): int(value) for gid, value in graph.out_degree(weight="n_tx")}
    pagerank = _safe_pagerank(graph)
    hubs, authorities = _safe_hits(graph)
    if graph.number_of_nodes() == 0:
        betweenness: dict[str, float] = {}
    else:
        sample_size = None
        if betweenness_k is not None:
            sample_size = min(max(int(betweenness_k), 1), graph.number_of_nodes())
        betweenness = {
            str(gid): float(value)
            for gid, value in nx.betweenness_centrality(
                graph,
                k=sample_size,
                normalized=True,
                weight="distance",
                seed=random_seed,
            ).items()
        }
    component_ids, component_sizes = _stable_components(graph)
    seed_gids = set(node_frame.loc[node_frame["is_seed"], "gid"])
    seed_counts, seed_minima, seed_means = _seed_reach(graph, seed_gids, cutoff=max_seed_hops)

    features = node_frame
    for name, mapping, default in (
        ("in_deg", in_deg, 0),
        ("out_deg", out_deg, 0),
        ("in_kzt", in_kzt, 0.0),
        ("out_kzt", out_kzt, 0.0),
        ("in_tx", in_tx, 0),
        ("out_tx", out_tx, 0),
        ("pagerank", pagerank, 0.0),
        ("authority", authorities, 0.0),
        ("hub", hubs, 0.0),
        ("betweenness", betweenness, 0.0),
        ("weak_component_id", component_ids, -1),
        ("weak_component_size", component_sizes, 0),
        ("seed_reach_count", seed_counts, 0),
        ("min_seed_distance", seed_minima, np.nan),
        ("mean_seed_distance", seed_means, np.nan),
    ):
        features[name] = features["gid"].map(mapping).fillna(default)

    integer_columns = [
        "in_deg",
        "out_deg",
        "in_tx",
        "out_tx",
        "weak_component_id",
        "weak_component_size",
        "seed_reach_count",
    ]
    features[integer_columns] = features[integer_columns].astype(int)
    features["component_id"] = features["weak_component_id"]
    features["component_size"] = features["weak_component_size"]
    features["total_kzt"] = features["in_kzt"] + features["out_kzt"]
    features["total_tx"] = features["in_tx"] + features["out_tx"]
    features["pass_through"] = np.where(
        features["in_kzt"] > 0,
        features["out_kzt"] / features["in_kzt"],
        np.nan,
    )
    features["pass_through_valid"] = (~features["is_seed"]) & (features["in_kzt"] > 0)
    features["truncated_by_depth"] = (features["depth"] == 4) & (features["out_deg"] == 0)
    counterparty_total = features["in_deg"] + features["out_deg"]
    features["in_counterparty_share"] = np.divide(
        features["in_deg"],
        counterparty_total,
        out=np.zeros(len(features), dtype=float),
        where=counterparty_total.to_numpy() > 0,
    )
    features["out_counterparty_share"] = np.divide(
        features["out_deg"],
        counterparty_total,
        out=np.zeros(len(features), dtype=float),
        where=counterparty_total.to_numpy() > 0,
    )

    direct_seed_predecessors = {
        gid: sum(1 for predecessor in graph.predecessors(gid) if str(predecessor) in seed_gids)
        for gid in graph.nodes
    }
    features["direct_seed_predecessors"] = (
        features["gid"].map(direct_seed_predecessors).fillna(0).astype(int)
    )

    incoming_stats = _side_amount_statistics(transaction_frame, side="dst", prefix="incoming")
    outgoing_stats = _side_amount_statistics(transaction_frame, side="src", prefix="outgoing")
    features = features.merge(incoming_stats, on="gid", how="left", validate="one_to_one")
    features = features.merge(outgoing_stats, on="gid", how="left", validate="one_to_one")
    amount_stats = _transaction_amount_statistics(
        transaction_frame, round_amount_multiple=round_amount_multiple
    )
    for column in (
        "tx_amount_mean",
        "tx_amount_median",
        "tx_amount_max",
        "tx_amount_std",
        "repeated_amount_ratio",
        "round_amount_ratio",
    ):
        features[column] = features["gid"].map(
            {gid: values[column] for gid, values in amount_stats.items()}
        )
    amount_columns = [
        column
        for column in features.columns
        if column.startswith(("incoming_amount_", "outgoing_amount_", "tx_amount_"))
        or column in {"repeated_amount_ratio", "round_amount_ratio"}
    ]
    features[amount_columns] = features[amount_columns].fillna(0.0)

    percentile_specs = {
        "in_deg": False,
        "out_deg": False,
        "in_tx": False,
        "out_tx": False,
        "in_kzt": True,
        "out_kzt": True,
        "total_kzt": True,
        "total_tx": False,
        "pagerank": False,
        "authority": False,
        "hub": False,
        "betweenness": False,
        "weak_component_size": True,
        "seed_reach_count": False,
    }
    for column, logarithmic in percentile_specs.items():
        features[f"{column}_percentile"] = _percentile(features[column], logarithmic=logarithmic)
    features["seed_reach_percentile"] = features["seed_reach_count_percentile"]
    features["turnover_percentile"] = features["total_kzt_percentile"]

    return features.sort_values("gid", kind="stable").reset_index(drop=True)
