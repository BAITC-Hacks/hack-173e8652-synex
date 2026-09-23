from __future__ import annotations

import math
from collections import deque
from itertools import pairwise
from typing import Any, Literal

import networkx as nx

from moneygraph.services.artifacts import ArtifactStore

Direction = Literal["upstream", "downstream"]


class GraphQueryService:
    """Bounded, deterministic graph exploration over pipeline artifacts."""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self.artifacts = artifacts
        self._graph = nx.DiGraph()
        self._graph_version = -1

    def get_node(self, gid: str) -> dict[str, Any]:
        try:
            return self.artifacts.node(str(gid))
        except KeyError as exc:
            raise KeyError(str(gid)) from exc

    def ego(self, gid: str, *, depth: int) -> dict[str, Any]:
        if depth not in {1, 2}:
            raise ValueError("depth must be between 1 and 2")
        graph = self._current_graph()
        self.get_node(gid)
        undirected = graph.to_undirected(as_view=True)
        included = set(nx.single_source_shortest_path_length(undirected, gid, cutoff=depth))
        nodes = [self.get_node(node) for node in sorted(included)]
        edges = [
            self._edge_dict(src, dst, data)
            for src, dst, data in graph.edges(data=True)
            if src in included and dst in included
        ]
        return {
            "root_gid": str(gid),
            "depth": depth,
            "nodes": nodes,
            "edges": sorted(edges, key=lambda item: (item["src"], item["dst"])),
        }

    def trace(
        self,
        gid: str,
        *,
        direction: Direction,
        depth: int,
        max_paths: int,
    ) -> dict[str, Any]:
        if direction not in {"upstream", "downstream"}:
            raise ValueError("direction must be upstream or downstream")
        if not 1 <= depth <= 4:
            raise ValueError("depth must be between 1 and 4")
        if not 1 <= max_paths <= 100:
            raise ValueError("max_paths must be between 1 and 100")
        graph = self._current_graph()
        self.get_node(gid)
        traversal = graph if direction == "downstream" else graph.reverse(copy=False)
        paths = self._bounded_simple_paths(
            traversal, str(gid), depth=depth, candidate_cap=max_paths * 50
        )
        records = [self._path_record(path, direction=direction) for path in paths]
        max_flow = max((record["observed_flow_kzt"] for record in records), default=0.0)
        for record in records:
            flow_score = (
                math.log1p(record["observed_flow_kzt"]) / math.log1p(max_flow)
                if max_flow > 0
                else 0.0
            )
            endpoint_priority = float(record["endpoint_priority"] or 0.0)
            distance_score = 1.0 / record["hops"]
            record["rank_score"] = round(
                0.50 * flow_score + 0.25 * distance_score + 0.25 * endpoint_priority, 6
            )
        records.sort(
            key=lambda item: (
                -item["rank_score"],
                item["hops"],
                tuple(item["path"]),
            )
        )
        selected = records[:max_paths]
        return {
            "root_gid": str(gid),
            "direction": direction,
            "depth": depth,
            "max_paths": max_paths,
            "paths": selected,
            "truncated": len(records) > max_paths,
        }

    def common_receivers(self, gids: list[str], *, max_depth: int, limit: int) -> dict[str, Any]:
        if not 2 <= len(gids) <= 20:
            raise ValueError("gids must contain between 2 and 20 unique identifiers")
        if len(set(gids)) != len(gids):
            raise ValueError("gids must be unique")
        if not 1 <= max_depth <= 4:
            raise ValueError("max_depth must be between 1 and 4")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        graph = self._current_graph()
        selected = [str(gid) for gid in gids]
        for gid in selected:
            self.get_node(gid)

        paths_by_candidate: dict[str, dict[str, list[str]]] = {}
        for source in selected:
            paths = nx.single_source_shortest_path(graph, source, cutoff=max_depth)
            for candidate, path in paths.items():
                if candidate == source or candidate in selected:
                    continue
                paths_by_candidate.setdefault(candidate, {})[source] = path

        candidate_records: list[dict[str, Any]] = []
        for candidate, source_paths in paths_by_candidate.items():
            if len(source_paths) < 2:
                continue
            ordered_paths = [source_paths[source] for source in selected if source in source_paths]
            distances = [len(path) - 1 for path in ordered_paths]
            observed_flow = sum(self._path_flow(path) for path in ordered_paths)
            profile = self.get_node(candidate)
            candidate_records.append(
                {
                    "gid": candidate,
                    "reached_by": len(source_paths),
                    "coverage_ratio": round(len(source_paths) / len(selected), 6),
                    "min_distance": min(distances),
                    "avg_distance": round(sum(distances) / len(distances), 6),
                    "example_paths": ordered_paths[: min(5, len(ordered_paths))],
                    "observed_flow_kzt": round(observed_flow, 2),
                    "role": profile.get("role"),
                    "priority_score": float(profile.get("priority_score") or 0.0),
                }
            )

        max_flow = max(
            (candidate["observed_flow_kzt"] for candidate in candidate_records), default=0.0
        )
        for candidate in candidate_records:
            flow_score = (
                math.log1p(candidate["observed_flow_kzt"]) / math.log1p(max_flow)
                if max_flow > 0
                else 0.0
            )
            distance_score = 1.0 / candidate["avg_distance"]
            candidate["rank_score"] = round(
                0.50 * candidate["coverage_ratio"]
                + 0.20 * distance_score
                + 0.20 * candidate["priority_score"]
                + 0.10 * flow_score,
                6,
            )
        candidate_records.sort(
            key=lambda item: (
                -item["rank_score"],
                -item["coverage_ratio"],
                item["avg_distance"],
                item["gid"],
            )
        )
        return {
            "input_gids": selected,
            "max_depth": max_depth,
            "candidates": candidate_records[:limit],
            "truncated": len(candidate_records) > limit,
        }

    def compare_nodes(self, gids: list[str]) -> dict[str, Any]:
        if not 2 <= len(gids) <= 20:
            raise ValueError("gids must contain between 2 and 20 identifiers")
        graph = self._current_graph()
        profiles = [self.get_node(gid) for gid in gids]
        descendant_sets = [
            {
                node
                for node, distance in nx.single_source_shortest_path_length(
                    graph, gid, cutoff=4
                ).items()
                if distance > 0
            }
            for gid in gids
        ]
        shared = set.intersection(*descendant_sets) if descendant_sets else set()
        return {"nodes": profiles, "shared_successors": sorted(shared)}

    def cluster(self, cluster_id: str | int) -> dict[str, Any]:
        normalized = str(cluster_id)
        clusters = self.artifacts.clusters()
        cluster = next(
            (item for item in clusters if str(item.get("cluster_id")) == normalized), None
        )
        if cluster is None:
            raise KeyError(normalized)
        nodes = [
            node for node in self.artifacts.nodes() if str(node.get("cluster_id")) == normalized
        ]
        nodes.sort(key=lambda item: (-float(item.get("priority_score") or 0.0), item["gid"]))
        included = {node["gid"] for node in nodes}
        edges = [
            edge
            for edge in self.artifacts.edges()
            if edge["src"] in included and edge["dst"] in included
        ]
        return {**cluster, "nodes": nodes, "edges": edges}

    def _current_graph(self) -> nx.DiGraph:
        version = self.artifacts.version
        if version == self._graph_version:
            return self._graph
        graph = nx.DiGraph()
        graph.add_nodes_from(node["gid"] for node in self.artifacts.nodes())
        for edge in self.artifacts.edges():
            src, dst = edge["src"], edge["dst"]
            if graph.has_edge(src, dst):
                current = graph[src][dst]
                current["sum_kzt"] = float(current.get("sum_kzt", 0.0)) + float(
                    edge.get("sum_kzt") or 0.0
                )
                current["n_tx"] = int(current.get("n_tx", 0)) + int(edge.get("n_tx") or 0)
            else:
                graph.add_edge(src, dst, **edge)
        self._graph = graph
        self._graph_version = version
        return graph

    @staticmethod
    def _bounded_simple_paths(
        graph: nx.DiGraph, root: str, *, depth: int, candidate_cap: int
    ) -> list[list[str]]:
        paths: list[list[str]] = []
        queue: deque[list[str]] = deque([[root]])
        while queue and len(paths) < candidate_cap:
            path = queue.popleft()
            if len(path) - 1 >= depth:
                continue
            for neighbor in sorted(graph.successors(path[-1])):
                if neighbor in path:
                    continue
                next_path = [*path, neighbor]
                paths.append(next_path)
                if len(paths) >= candidate_cap:
                    break
                queue.append(next_path)
        return paths

    def _path_record(self, path: list[str], *, direction: Direction) -> dict[str, Any]:
        profile = self.get_node(path[-1])
        return {
            "path": path,
            "hops": len(path) - 1,
            "observed_flow_kzt": round(self._path_flow(path, direction=direction), 2),
            "endpoint_gid": path[-1],
            "endpoint_role": profile.get("role"),
            "endpoint_priority": float(profile.get("priority_score") or 0.0),
        }

    def _path_flow(self, path: list[str], *, direction: Direction = "downstream") -> float:
        graph = self._current_graph()
        amounts: list[float] = []
        for left, right in pairwise(path):
            src, dst = (left, right) if direction == "downstream" else (right, left)
            amounts.append(float(graph[src][dst].get("sum_kzt") or 0.0))
        return min(amounts, default=0.0)

    @staticmethod
    def _edge_dict(src: str, dst: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "src": src,
            "dst": dst,
            **{key: value for key, value in data.items() if key not in {"src", "dst"}},
        }
