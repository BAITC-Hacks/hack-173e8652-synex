from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ALLOWED_ROLES = {
    "consolidator",
    "transit",
    "distributor",
    "terminal",
    "coordinator",
    "peripheral",
}
NODES_COLUMNS = {
    "gid",
    "role",
    "role_score",
    "cluster_id",
    "priority_score",
    "evidence",
}
CLUSTERS_COLUMNS = {
    "cluster_id",
    "n_nodes",
    "n_seed",
    "sum_kzt_internal",
    "top_gids",
    "hypothesis",
}
TOP_COLUMNS = {"rank", "gid", "role", "priority_score", "why"}


def _read_csv(path: Path, errors: list[str]) -> pd.DataFrame | None:
    if not path.is_file():
        errors.append(f"missing required file: {path.name}")
        return None
    try:
        return pd.read_csv(path, dtype={"gid": "string"})
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        errors.append(f"cannot read {path.name}: {exc}")
        return None


def _missing_columns(frame: pd.DataFrame, required: set[str]) -> list[str]:
    return sorted(required.difference(frame.columns))


def _validate_nodes(
    frame: pd.DataFrame,
    expected_nodes: int | None,
    errors: list[str],
) -> None:
    missing = _missing_columns(frame, NODES_COLUMNS)
    if missing:
        errors.append(f"nodes_roles.csv missing columns: {', '.join(missing)}")
        return
    if expected_nodes is not None and len(frame) != expected_nodes:
        errors.append(f"nodes_roles.csv must contain {expected_nodes} rows, found {len(frame)}")
    if frame["gid"].isna().any() or frame["gid"].duplicated().any():
        errors.append("nodes_roles.csv gid values must be non-empty and unique")
    invalid_roles = sorted(set(frame["role"].dropna()) - ALLOWED_ROLES)
    if frame["role"].isna().any() or invalid_roles:
        errors.append(f"nodes_roles.csv contains invalid roles: {invalid_roles}")
    evidence = frame["evidence"]
    if evidence.isna().any() or evidence.fillna("").astype(str).str.strip().eq("").any():
        errors.append("nodes_roles.csv evidence must be non-empty")
    if evidence.fillna("").astype(str).str.len().gt(200).any():
        errors.append("nodes_roles.csv evidence must not exceed 200 characters")
    if not evidence.fillna("").astype(str).str.contains(r"\d", regex=True).all():
        errors.append("nodes_roles.csv evidence must contain a numeric fact")
    if not frame["cluster_id"].notna().all():
        errors.append("nodes_roles.csv cluster_id must be filled")
    for column in ("role_score", "priority_score"):
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values).all() or not values.between(0, 1).all():
            errors.append(f"nodes_roles.csv {column} must be finite and in [0,1]")


def _validate_clusters(
    frame: pd.DataFrame,
    nodes: pd.DataFrame | None,
    errors: list[str],
) -> None:
    missing = _missing_columns(frame, CLUSTERS_COLUMNS)
    if missing:
        errors.append(f"clusters.csv missing columns: {', '.join(missing)}")
        return
    if frame.empty:
        errors.append("clusters.csv must not be empty")
        return
    if frame["cluster_id"].isna().any() or frame["cluster_id"].duplicated().any():
        errors.append("clusters.csv cluster_id values must be filled and unique")
    if frame["hypothesis"].isna().any() or frame["hypothesis"].astype(str).str.strip().eq("").any():
        errors.append("clusters.csv hypothesis must be non-empty")
    if nodes is not None and "cluster_id" in nodes and "cluster_id" in frame:
        node_ids = set(nodes["cluster_id"].dropna().astype(str))
        cluster_ids = set(frame["cluster_id"].dropna().astype(str))
        if node_ids != cluster_ids:
            errors.append("clusters.csv cluster ids must match nodes_roles.csv")


def _validate_top(
    frame: pd.DataFrame,
    expected_nodes: int | None,
    errors: list[str],
) -> None:
    missing = _missing_columns(frame, TOP_COLUMNS)
    if missing:
        errors.append(f"top_nodes.csv missing columns: {', '.join(missing)}")
        return
    minimum = min(expected_nodes, 20) if expected_nodes is not None else 20
    if len(frame) < minimum:
        errors.append(f"top_nodes.csv must contain at least {minimum} rows, found {len(frame)}")
    expected_ranks = list(range(1, len(frame) + 1))
    if pd.to_numeric(frame["rank"], errors="coerce").tolist() != expected_ranks:
        errors.append("top_nodes.csv ranks must be consecutive from 1")
    priorities = pd.to_numeric(frame["priority_score"], errors="coerce")
    if priorities.isna().any() or not priorities.between(0, 1).all():
        errors.append("top_nodes.csv priority_score must be in [0,1]")
    elif not priorities.is_monotonic_decreasing:
        errors.append("top_nodes.csv must be sorted by descending priority_score")
    if frame["why"].isna().any() or frame["why"].astype(str).str.strip().eq("").any():
        errors.append("top_nodes.csv why must be non-empty")


def _validate_manifest(
    path: Path,
    expected_nodes: int | None,
    errors: list[str],
) -> None:
    if not path.is_file():
        errors.append("missing required file: run_manifest.json")
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read run_manifest.json: {exc}")
        return
    duration = manifest.get("duration_seconds", manifest.get("total_duration_seconds"))
    if not isinstance(duration, (int, float)) or not np.isfinite(duration):
        errors.append("run_manifest.json duration_seconds must be finite")
    elif duration >= 300:
        errors.append(f"pipeline duration must be below 300 seconds, found {duration}")
    if expected_nodes is not None and manifest.get("counts", {}).get("nodes") != expected_nodes:
        errors.append("run_manifest.json node count does not match expected count")


def verify_outputs(out_dir: Path, expected_nodes: int | None = None) -> list[str]:
    """Validate mandatory analysis outputs and return human-readable errors."""
    output_path = Path(out_dir)
    errors: list[str] = []
    nodes = _read_csv(output_path / "nodes_roles.csv", errors)
    clusters = _read_csv(output_path / "clusters.csv", errors)
    top = _read_csv(output_path / "top_nodes.csv", errors)
    if nodes is not None:
        _validate_nodes(nodes, expected_nodes, errors)
    if clusters is not None:
        _validate_clusters(clusters, nodes, errors)
    if top is not None:
        _validate_top(top, expected_nodes, errors)
    _validate_manifest(output_path / "run_manifest.json", expected_nodes, errors)
    return errors
