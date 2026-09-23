"""Plotly helpers for compact directed investigation graphs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import networkx as nx
import plotly.graph_objects as go

from moneygraph.ui.helpers import format_kzt

ROLE_COLORS = {
    "consolidator": "#f59e0b",
    "transit": "#38bdf8",
    "distributor": "#22c55e",
    "terminal": "#ef4444",
    "coordinator": "#a78bfa",
    "peripheral": "#94a3b8",
    "unknown": "#64748b",
}

ROLE_SYMBOLS = {
    "consolidator": "hexagon",
    "transit": "circle",
    "distributor": "diamond",
    "terminal": "triangle-down",
    "coordinator": "star",
    "peripheral": "square",
    "unknown": "circle-open",
}


def build_ego_figure(payload: Mapping[str, Any]) -> go.Figure:
    """Build a deterministic directed ego graph with string-safe GIDs."""

    nodes = _records(payload.get("nodes"))
    edges = _records(payload.get("edges"))
    if not nodes and not edges:
        return _empty_figure()
    graph, profiles = _build_graph(nodes, edges)
    positions = nx.spring_layout(graph, seed=42, weight="sum_kzt")
    figure = go.Figure()
    _add_edge_traces(figure, edges, positions)
    _add_node_traces(figure, graph, profiles, positions, str(payload.get("root_gid", "")))
    _style_figure(figure)
    return figure


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _build_graph(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[nx.DiGraph, dict[str, dict[str, Any]]]:
    graph = nx.DiGraph()
    profiles: dict[str, dict[str, Any]] = {}
    for node in nodes:
        gid = str(node.get("gid", ""))
        if gid:
            profiles[gid] = {**node, "gid": gid}
            graph.add_node(gid)
    for edge in edges:
        src, dst = str(edge.get("src", "")), str(edge.get("dst", ""))
        if src and dst:
            graph.add_edge(src, dst, sum_kzt=_number(edge.get("sum_kzt")))
            profiles.setdefault(src, {"gid": src, "role": "unknown"})
            profiles.setdefault(dst, {"gid": dst, "role": "unknown"})
    return graph, profiles


def _add_edge_traces(
    figure: go.Figure,
    edges: list[dict[str, Any]],
    positions: Mapping[str, Any],
) -> None:
    line_x: list[float | None] = []
    line_y: list[float | None] = []
    hover_x: list[float] = []
    hover_y: list[float] = []
    hover_text: list[str] = []
    for edge in edges:
        src, dst = str(edge.get("src", "")), str(edge.get("dst", ""))
        if src not in positions or dst not in positions:
            continue
        start, end = positions[src], positions[dst]
        line_x.extend([float(start[0]), float(end[0]), None])
        line_y.extend([float(start[1]), float(end[1]), None])
        hover_x.append(float(start[0] + end[0]) / 2)
        hover_y.append(float(start[1] + end[1]) / 2)
        n_tx = int(_number(edge.get("n_tx")))
        hover_text.append(
            f"{src} → {dst}<br>{format_kzt(_number(edge.get('sum_kzt')))}<br>{n_tx} перевод(ов)"
        )
        figure.add_annotation(
            x=float(end[0]),
            y=float(end[1]),
            ax=float(start[0]),
            ay=float(start[1]),
            xref="x",
            yref="y",
            axref="x",
            ayref="y",
            showarrow=True,
            arrowhead=3,
            arrowsize=1.2,
            arrowwidth=1.2,
            arrowcolor="rgba(100,116,139,0.7)",
            text="",
        )
    figure.add_trace(
        go.Scatter(
            x=line_x,
            y=line_y,
            mode="lines",
            line={"width": 1.5, "color": "rgba(100,116,139,0.45)"},
            hoverinfo="skip",
            showlegend=False,
            name="flows",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=hover_x,
            y=hover_y,
            mode="markers",
            marker={"size": 14, "color": "rgba(0,0,0,0.01)"},
            hovertext=hover_text,
            hovertemplate="%{hovertext}<extra></extra>",
            showlegend=False,
            name="edge details",
        )
    )


def _add_node_traces(
    figure: go.Figure,
    graph: nx.DiGraph,
    profiles: Mapping[str, Mapping[str, Any]],
    positions: Mapping[str, Any],
    root_gid: str,
) -> None:
    by_role: dict[str, list[str]] = defaultdict(list)
    for gid in graph.nodes:
        by_role[str(profiles[gid].get("role") or "unknown")].append(gid)
    for role in sorted(by_role):
        gids = sorted(by_role[role])
        figure.add_trace(
            go.Scatter(
                x=[float(positions[gid][0]) for gid in gids],
                y=[float(positions[gid][1]) for gid in gids],
                mode="markers+text",
                text=gids,
                textposition="top center",
                hovertext=[_node_tooltip(profiles[gid]) for gid in gids],
                hovertemplate="%{hovertext}<extra></extra>",
                marker={
                    "size": [24 if gid == root_gid else 17 for gid in gids],
                    "color": ROLE_COLORS.get(role, ROLE_COLORS["unknown"]),
                    "symbol": ROLE_SYMBOLS.get(role, ROLE_SYMBOLS["unknown"]),
                    "line": {"width": 2, "color": "#0f172a"},
                },
                name=role,
            )
        )


def _node_tooltip(node: Mapping[str, Any]) -> str:
    priority = node.get("priority_score")
    priority_text = f"{float(priority):.3f}" if isinstance(priority, (int, float)) else "—"
    return (
        f"gid={node.get('gid', '—')}<br>роль={node.get('role', 'unknown')}"
        f"<br>кластер={node.get('cluster_id', '—')}<br>priority={priority_text}"
        f"<br>depth={node.get('depth', '—')}"
    )


def _style_figure(figure: go.Figure) -> None:
    figure.update_layout(
        height=620,
        margin={"l": 12, "r": 12, "t": 20, "b": 12},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="closest",
        legend={"orientation": "h", "y": 1.04, "x": 0},
        xaxis={"visible": False},
        yaxis={"visible": False},
    )


def _empty_figure() -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(
        text="В локальном графе нет узлов для выбранных фильтров.",
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
    )
    _style_figure(figure)
    return figure


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
