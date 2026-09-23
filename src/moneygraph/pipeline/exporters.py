"""Validated, atomic and deterministic analytical artifact writers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from moneygraph.domain.roles import ROLE_VALUES
from moneygraph.pipeline.explanations import build_priority_explanation

NODES_ROLES_COLUMNS: tuple[str, ...] = (
    "gid",
    "role",
    "role_score",
    "cluster_id",
    "priority_score",
    "evidence",
)
CLUSTERS_COLUMNS: tuple[str, ...] = (
    "cluster_id",
    "n_nodes",
    "n_seed",
    "sum_kzt_internal",
    "top_gids",
    "hypothesis",
)
TOP_NODES_COLUMNS: tuple[str, ...] = (
    "rank",
    "gid",
    "role",
    "priority_score",
    "why",
)


@dataclass(frozen=True)
class ExportPaths:
    nodes_roles: Path
    clusters: Path
    top_nodes: Path

    def __iter__(self) -> Iterator[Path]:
        return iter((self.nodes_roles, self.clusters, self.top_nodes))


def _gid_key(value: Any) -> str:
    return str(value)


def _sorted_by_gid(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    result["__gid_sort"] = result["gid"].map(_gid_key)
    result = result.sort_values("__gid_sort", kind="mergesort").drop(columns="__gid_sort")
    return result.reset_index(drop=True)


def _validate_node_features(features: pd.DataFrame) -> None:
    missing = set(NODES_ROLES_COLUMNS) - set(features.columns)
    if missing:
        raise ValueError(f"node features missing mandatory columns: {sorted(missing)}")
    if features.empty:
        raise ValueError("node features must not be empty")
    if features["gid"].isna().any() or features["gid"].duplicated().any():
        raise ValueError("gid must be non-null and unique")
    if features["gid"].map(str).duplicated().any():
        raise ValueError("gid must remain unique after string normalization")
    invalid_roles = set(features["role"].dropna().astype(str)) - ROLE_VALUES
    if invalid_roles or features["role"].isna().any():
        raise ValueError(f"invalid roles: {sorted(invalid_roles)}")
    for column in ("role_score", "priority_score"):
        values = pd.to_numeric(features[column], errors="coerce")
        if values.isna().any() or not values.between(0.0, 1.0).all():
            raise ValueError(f"{column} must be within [0, 1]")
    cluster_ids = pd.to_numeric(features["cluster_id"], errors="coerce")
    if cluster_ids.isna().any() or (cluster_ids < 0).any() or (cluster_ids % 1 != 0).any():
        raise ValueError("cluster_id must contain non-negative integers")
    evidence = features["evidence"].fillna("").astype(str)
    if evidence.str.len().lt(1).any() or evidence.str.len().gt(200).any():
        raise ValueError("evidence must be non-empty and at most 200 characters")


def _top_group_gids(group: pd.DataFrame, limit: int = 5) -> str:
    ranked = group.copy(deep=True)
    ranked["__gid_sort"] = ranked["gid"].map(_gid_key)
    ranked = ranked.sort_values(
        ["priority_score", "__gid_sort"], ascending=[False, True], kind="mergesort"
    )
    values = [
        str(value.item() if isinstance(value, np.generic) else value)
        for value in ranked["gid"].head(limit)
    ]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _cluster_hypothesis(group: pd.DataFrame, connected_gids: set[Any]) -> str:
    if len(group) == 1 and str(group.iloc[0]["gid"]) not in connected_gids:
        return "Изолированный узел; данных недостаточно для структурной гипотезы."
    counts = group["role"].value_counts()
    n_nodes = len(group)
    if counts.get("consolidator", 0) > 0 and counts.get("distributor", 0) > 0:
        return (
            f"Группа из {n_nodes} узлов сочетает признаки консолидации и последующего "
            "распределения наблюдаемого потока."
        )
    leading_role = str(counts.index[0])
    if leading_role == "transit":
        return f"В группе из {n_nodes} узлов преобладают наблюдаемые транзитные признаки."
    if leading_role == "peripheral":
        return f"Периферийный компонент из {n_nodes} узлов без выраженного ключевого узла."
    return f"Наблюдаемая группа из {n_nodes} узлов; ведущая функциональная роль — {leading_role}."


def build_cluster_summary(node_features: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Aggregate deterministic cluster facts after roles and priority exist."""

    required_edges = {"src", "dst", "sum_kzt"}
    if not required_edges.issubset(edges.columns):
        raise ValueError(f"edges missing columns: {sorted(required_edges - set(edges.columns))}")
    normalized_features = node_features[["gid", "cluster_id"]].copy(deep=True)
    normalized_features["gid"] = normalized_features["gid"].map(str)
    if normalized_features["gid"].duplicated().any():
        raise ValueError("gid must remain unique after string normalization")
    cluster_by_gid = normalized_features.set_index("gid")["cluster_id"].to_dict()
    internal_turnover: dict[int, float] = {}
    connected_gids: set[Any] = set()
    for edge in edges[["src", "dst", "sum_kzt"]].itertuples(index=False):
        source, destination = str(edge.src), str(edge.dst)
        connected_gids.update((source, destination))
        src_cluster = cluster_by_gid.get(source)
        dst_cluster = cluster_by_gid.get(destination)
        if src_cluster is not None and src_cluster == dst_cluster:
            edge_cluster_id = int(src_cluster)
            internal_turnover[edge_cluster_id] = internal_turnover.get(
                edge_cluster_id, 0.0
            ) + float(cast(Any, edge.sum_kzt))

    rows: list[dict[str, Any]] = []
    for group_cluster_id, group in node_features.groupby("cluster_id", sort=True):
        cluster_id = int(cast(Any, group_cluster_id))
        rows.append(
            {
                "cluster_id": cluster_id,
                "n_nodes": len(group),
                "n_seed": int(
                    group.get("is_seed", pd.Series(False, index=group.index)).astype(bool).sum()
                ),
                "sum_kzt_internal": float(internal_turnover.get(cluster_id, 0.0)),
                "top_gids": _top_group_gids(group),
                "hypothesis": _cluster_hypothesis(group, connected_gids),
            }
        )
    return pd.DataFrame(rows, columns=CLUSTERS_COLUMNS).sort_values(
        "cluster_id", kind="mergesort", ignore_index=True
    )


def build_top_nodes(node_features: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    ranked = node_features.copy(deep=True)
    ranked["__gid_sort"] = ranked["gid"].map(_gid_key)
    ranked = ranked.sort_values(
        ["priority_score", "__gid_sort"], ascending=[False, True], kind="mergesort"
    ).head(top_n)
    ranked["rank"] = np.arange(1, len(ranked) + 1, dtype=int)
    ranked["why"] = ranked.apply(build_priority_explanation, axis=1)
    ranked["gid"] = ranked["gid"].map(str)
    return ranked[list(TOP_NODES_COLUMNS)].reset_index(drop=True)


def _temporary_path(path: Path) -> Path:
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    return temporary


def write_csv_atomic(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        frame.to_csv(
            temporary,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
            float_format="%.10g",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def write_parquet_atomic(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def write_json_atomic(payload: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        temporary.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                default=_json_default,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_mandatory_csvs(
    node_features: pd.DataFrame,
    edges: pd.DataFrame,
    out_dir: Path,
    *,
    top_n: int = 50,
) -> ExportPaths:
    """Validate and atomically replace the three mandatory deterministic CSVs."""

    _validate_node_features(node_features)
    if len(node_features) >= 20 and top_n < 20:
        raise ValueError("mandatory top_nodes.csv must contain at least 20 rows")
    ordered = _sorted_by_gid(node_features)
    nodes_roles = ordered[list(NODES_ROLES_COLUMNS)].copy()
    nodes_roles["gid"] = nodes_roles["gid"].map(str)
    clusters = build_cluster_summary(ordered, edges)
    top_nodes = build_top_nodes(ordered, top_n=top_n)
    paths = ExportPaths(
        nodes_roles=Path(out_dir) / "nodes_roles.csv",
        clusters=Path(out_dir) / "clusters.csv",
        top_nodes=Path(out_dir) / "top_nodes.csv",
    )
    write_csv_atomic(nodes_roles, paths.nodes_roles)
    write_csv_atomic(clusters, paths.clusters)
    write_csv_atomic(top_nodes, paths.top_nodes)
    return paths
