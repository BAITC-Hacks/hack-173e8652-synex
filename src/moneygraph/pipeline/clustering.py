"""Stable Louvain communities and cluster-level flow aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import networkx as nx
import numpy as np
import pandas as pd

from moneygraph.pipeline.graph_builder import build_undirected_projection


@dataclass(frozen=True, slots=True)
class ClusterResult:
    membership: pd.DataFrame
    communities: tuple[tuple[str, ...], ...]

    @property
    def cluster_count(self) -> int:
        return len(self.communities)


def detect_communities(
    graph: nx.DiGraph,
    *,
    seed: int = 42,
    resolution: float = 1.0,
) -> ClusterResult:
    """Run weighted Louvain on the undirected projection with stable IDs."""

    if resolution <= 0:
        raise ValueError("resolution must be positive")
    projection = build_undirected_projection(graph)
    isolates = sorted(str(gid) for gid in nx.isolates(projection))
    connected_nodes = sorted(set(projection.nodes).difference(isolates))
    communities: list[tuple[str, ...]] = []
    if connected_nodes:
        detected = nx.community.louvain_communities(
            projection.subgraph(connected_nodes).copy(),
            weight="sum_kzt",
            resolution=resolution,
            threshold=1e-7,
            seed=seed,
        )
        communities.extend(tuple(sorted(str(gid) for gid in community)) for community in detected)
    # Isolates carry no evidence of community membership and therefore remain
    # separate rather than being grouped into an artificial disconnected cluster.
    communities.extend((gid,) for gid in isolates)
    communities.sort(key=lambda community: (-len(community), community[0]))

    membership_rows = [
        {"gid": gid, "cluster_id": cluster_id}
        for cluster_id, community in enumerate(communities)
        for gid in community
    ]
    membership = pd.DataFrame(membership_rows, columns=["gid", "cluster_id"])
    if not membership.empty:
        membership = membership.sort_values("gid", kind="stable").reset_index(drop=True)
        membership["cluster_id"] = membership["cluster_id"].astype(int)
    return ClusterResult(membership=membership, communities=tuple(communities))


def summarize_clusters(
    graph: nx.DiGraph,
    nodes: pd.DataFrame,
    membership: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate internal turnover and directed boundary flows for each cluster."""

    required_membership = {"gid", "cluster_id"}
    if missing := required_membership.difference(membership.columns):
        raise ValueError(f"membership missing columns: {', '.join(sorted(missing))}")
    if missing := {"gid", "is_seed"}.difference(nodes.columns):
        raise ValueError(f"nodes missing columns: {', '.join(sorted(missing))}")
    member_frame = membership[["gid", "cluster_id"]].copy(deep=True)
    member_frame["gid"] = member_frame["gid"].astype("string")
    if member_frame["gid"].duplicated().any():
        raise ValueError("membership must contain each gid exactly once")
    node_frame = nodes[["gid", "is_seed"]].copy(deep=True)
    node_frame["gid"] = node_frame["gid"].astype("string")
    known = set(node_frame["gid"])
    if set(member_frame["gid"]) != known:
        raise ValueError("membership gids must exactly match nodes gids")

    seed_by_gid = dict(zip(node_frame["gid"], node_frame["is_seed"], strict=True))
    rows: list[dict[str, int | float]] = []
    for cluster_id, group in member_frame.groupby("cluster_id", sort=True):
        gids = set(group["gid"])
        internal_kzt = 0.0
        external_in_kzt = 0.0
        external_out_kzt = 0.0
        external_in_edges = 0
        external_out_edges = 0
        for src, dst, attributes in graph.edges(data=True):
            source, destination = str(src), str(dst)
            amount = float(attributes.get("sum_kzt", 0.0))
            if source in gids and destination in gids:
                internal_kzt += amount
            elif source not in gids and destination in gids:
                external_in_kzt += amount
                external_in_edges += 1
            elif source in gids and destination not in gids:
                external_out_kzt += amount
                external_out_edges += 1
        rows.append(
            {
                "cluster_id": int(cast(Any, cluster_id)),
                "n_nodes": len(gids),
                "n_seed": sum(bool(seed_by_gid[gid]) for gid in gids),
                "sum_kzt_internal": internal_kzt,
                "sum_kzt_external_in": external_in_kzt,
                "sum_kzt_external_out": external_out_kzt,
                "external_in_edges": external_in_edges,
                "external_out_edges": external_out_edges,
            }
        )
    return pd.DataFrame(rows).sort_values("cluster_id", kind="stable").reset_index(drop=True)


def compute_cluster_bridge_features(
    graph: nx.DiGraph,
    membership: pd.DataFrame,
) -> pd.DataFrame:
    """Return per-node directed boundary counts and a transparent bridge signal."""

    if missing := {"gid", "cluster_id"}.difference(membership.columns):
        raise ValueError(f"membership missing columns: {', '.join(sorted(missing))}")
    frame = membership[["gid", "cluster_id"]].copy(deep=True)
    frame["gid"] = frame["gid"].astype("string")
    if frame["gid"].duplicated().any():
        raise ValueError("membership must contain unique gids")
    cluster_by_gid = dict(zip(frame["gid"], frame["cluster_id"], strict=True))
    graph_gids = {str(gid) for gid in graph.nodes}
    if graph_gids != set(cluster_by_gid):
        raise ValueError("membership gids must exactly match graph nodes")

    counters: dict[str, dict[str, float | int]] = {
        gid: {
            "cross_cluster_in_edges": 0,
            "cross_cluster_out_edges": 0,
            "cross_cluster_in_kzt": 0.0,
            "cross_cluster_out_kzt": 0.0,
        }
        for gid in sorted(graph_gids)
    }
    for src, dst, attributes in graph.edges(data=True):
        source, destination = str(src), str(dst)
        if cluster_by_gid[source] == cluster_by_gid[destination]:
            continue
        amount = float(attributes.get("sum_kzt", 0.0))
        counters[source]["cross_cluster_out_edges"] += 1
        counters[source]["cross_cluster_out_kzt"] += amount
        counters[destination]["cross_cluster_in_edges"] += 1
        counters[destination]["cross_cluster_in_kzt"] += amount

    result = pd.DataFrame([{"gid": gid, **values} for gid, values in counters.items()]).sort_values(
        "gid", kind="stable", ignore_index=True
    )
    result["cross_cluster_edge_count"] = (
        result["cross_cluster_in_edges"] + result["cross_cluster_out_edges"]
    )
    result["cross_cluster_kzt"] = result["cross_cluster_in_kzt"] + result["cross_cluster_out_kzt"]
    max_count = float(result["cross_cluster_edge_count"].max()) if len(result) else 0.0
    log_amount = np.log1p(result["cross_cluster_kzt"].clip(lower=0.0))
    max_log_amount = float(log_amount.max()) if len(result) else 0.0
    count_signal = (
        result["cross_cluster_edge_count"].astype(float) / max_count
        if max_count > 0
        else pd.Series(0.0, index=result.index)
    )
    amount_signal = (
        log_amount / max_log_amount if max_log_amount > 0 else pd.Series(0.0, index=result.index)
    )
    result["cross_cluster_bridge_signal"] = (0.60 * count_signal + 0.40 * amount_signal).clip(
        0.0, 1.0
    )
    return result
