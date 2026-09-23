"""Construction of directed money-flow graphs and undirected projections."""

from __future__ import annotations

import math
from typing import Any, cast

import networkx as nx
import pandas as pd


def _gid(value: object) -> str:
    if bool(pd.isna(cast(Any, value))):
        raise ValueError("Graph identifiers cannot be null")
    return str(value)


def build_graph(nodes: pd.DataFrame, edges: pd.DataFrame) -> nx.DiGraph:
    """Build a deterministic weighted graph while retaining every input node."""

    required_nodes = {"gid", "depth", "is_seed"}
    required_edges = {"src", "dst", "sum_kzt", "n_tx", "depth"}
    if missing := required_nodes.difference(nodes.columns):
        raise ValueError(f"nodes missing required columns: {', '.join(sorted(missing))}")
    if missing := required_edges.difference(edges.columns):
        raise ValueError(f"edges missing required columns: {', '.join(sorted(missing))}")

    node_frame = nodes.copy(deep=True)
    edge_frame = edges.copy(deep=True)
    node_frame["gid"] = node_frame["gid"].map(_gid)
    edge_frame["src"] = edge_frame["src"].map(_gid)
    edge_frame["dst"] = edge_frame["dst"].map(_gid)
    if node_frame["gid"].duplicated().any():
        raise ValueError("nodes.gid must be unique before graph construction")
    if edge_frame.duplicated(["src", "dst"]).any():
        raise ValueError("edges src,dst pairs must be unique before graph construction")

    known = set(node_frame["gid"])
    referenced = set(edge_frame["src"]) | set(edge_frame["dst"])
    if unknown := referenced.difference(known):
        raise ValueError(
            f"edges reference gids absent from nodes: {', '.join(sorted(unknown)[:5])}"
        )

    graph = nx.DiGraph()
    for row in node_frame.sort_values("gid", kind="stable").itertuples(index=False):
        graph.add_node(
            str(row.gid),
            depth=int(cast(Any, row.depth)),
            is_seed=bool(row.is_seed),
        )

    edge_frame = edge_frame.sort_values(["src", "dst"], kind="stable")
    for row in edge_frame.itertuples(index=False):
        amount = float(cast(Any, row.sum_kzt))
        count = int(cast(Any, row.n_tx))
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError(f"Edge {row.src}->{row.dst} has a non-positive/non-finite amount")
        if count <= 0:
            raise ValueError(f"Edge {row.src}->{row.dst} has a non-positive transaction count")
        graph.add_edge(
            row.src,
            row.dst,
            sum_kzt=amount,
            weight=amount,
            n_tx=count,
            depth=int(cast(Any, row.depth)),
            distance=1.0 / math.log1p(amount),
        )
    return graph


def build_undirected_projection(graph: nx.DiGraph) -> nx.Graph:
    """Project directed flows to an undirected strength graph for clustering only."""

    projection = nx.Graph()
    for gid, attributes in sorted(graph.nodes(data=True), key=lambda item: str(item[0])):
        projection.add_node(str(gid), **dict(attributes))
    for src, dst, attributes in sorted(
        graph.edges(data=True), key=lambda item: (str(item[0]), str(item[1]))
    ):
        left, right = str(src), str(dst)
        amount = float(attributes.get("sum_kzt", 0.0))
        count = int(attributes.get("n_tx", 0))
        if projection.has_edge(left, right):
            projection[left][right]["sum_kzt"] += amount
            projection[left][right]["n_tx"] += count
        else:
            projection.add_edge(left, right, sum_kzt=amount, n_tx=count)
    for _, _, attributes in projection.edges(data=True):
        attributes["distance"] = 1.0 / math.log1p(float(attributes["sum_kzt"]))
    return projection
