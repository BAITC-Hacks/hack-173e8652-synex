from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class ArtifactUnavailableError(RuntimeError):
    """Raised when the deterministic pipeline output is not available yet."""


def opaque_id(value: Any) -> str:
    """Return an identifier without converting it through a lossy float representation."""

    if value is None or value is pd.NA:
        return ""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        if math.isnan(float(value)):
            return ""
        if float(value).is_integer():
            return str(int(value))
    return str(value)


def json_value(value: Any) -> Any:
    """Normalize pandas/numpy scalars to values JSON encoders handle consistently."""

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (float, np.floating)) and math.isnan(float(value)):
        return None
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def string_key_record(raw: Mapping[Any, Any]) -> dict[str, Any]:
    """Normalize pandas records to JSON-safe values with string keys."""

    return {str(key): json_value(value) for key, value in raw.items()}


class ArtifactStore:
    """Load and cache deterministic CSV/Parquet outputs produced by the pipeline."""

    def __init__(self, out_dir: Path, artifacts_dir: Path, data_dir: Path) -> None:
        self.out_dir = Path(out_dir)
        self.artifacts_dir = Path(artifacts_dir)
        self.data_dir = Path(data_dir)
        self._lock = threading.RLock()
        self._signature: tuple[tuple[str, int, int], ...] = ()
        self._version = 0
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[dict[str, Any]] = []
        self._clusters: list[dict[str, Any]] = []
        self._top_nodes: list[dict[str, Any]] = []
        self._resilience: list[dict[str, Any]] = []

    @property
    def version(self) -> int:
        self.refresh()
        return self._version

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            signature = self._file_signature()
            if not force and signature == self._signature:
                return
            self._nodes = self._load_nodes()
            self._edges = self._load_edges()
            self._clusters = self._load_clusters()
            self._top_nodes = self._load_top_nodes()
            self._resilience = self._load_resilience()
            self._signature = signature
            self._version += 1

    def nodes(self) -> list[dict[str, Any]]:
        self.refresh()
        return [dict(record) for record in self._nodes.values()]

    def node(self, gid: str) -> dict[str, Any]:
        self.refresh()
        try:
            return dict(self._nodes[str(gid)])
        except KeyError as exc:
            raise KeyError(str(gid)) from exc

    def edges(self) -> list[dict[str, Any]]:
        self.refresh()
        return [dict(record) for record in self._edges]

    def top_nodes(self) -> list[dict[str, Any]]:
        self.refresh()
        return [dict(record) for record in self._top_nodes]

    def clusters(self) -> list[dict[str, Any]]:
        self.refresh()
        return [dict(record) for record in self._clusters]

    def resilience(self) -> list[dict[str, Any]]:
        self.refresh()
        return [dict(record) for record in self._resilience]

    def summary(self) -> dict[str, Any]:
        self.refresh()
        summary_path = self.out_dir / "summary.json"
        if summary_path.exists():
            with summary_path.open(encoding="utf-8") as handle:
                loaded = json.load(handle)
            summary = dict(loaded) if isinstance(loaded, dict) else {}
        else:
            summary = {}
        summary.setdefault("n_nodes", len(self._nodes))
        summary.setdefault("n_edges", len(self._edges))
        summary.setdefault("n_clusters", len(self._clusters))
        summary.setdefault(
            "n_seed", sum(bool(record.get("is_seed")) for record in self._nodes.values())
        )
        transactions_path = self.data_dir / "transactions.parquet"
        if "n_transactions" not in summary and transactions_path.exists():
            try:
                summary["n_transactions"] = len(pd.read_parquet(transactions_path, columns=["src"]))
            except (OSError, ValueError, KeyError):
                summary["n_transactions"] = None

        manifest_path = self.out_dir / "run_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                summary.setdefault("last_run", manifest)
            except (OSError, ValueError, TypeError):
                pass
        validation_path = self.out_dir / "validation_report.json"
        if validation_path.exists():
            try:
                validation = json.loads(validation_path.read_text(encoding="utf-8"))
                summary.setdefault("validation", validation)
            except (OSError, ValueError, TypeError):
                pass
        return summary

    def _file_signature(self) -> tuple[tuple[str, int, int], ...]:
        candidates = [
            self.out_dir / "nodes_roles.csv",
            self.out_dir / "top_nodes.csv",
            self.out_dir / "clusters.csv",
            self.out_dir / "resilience.csv",
            self.artifacts_dir / "node_features.parquet",
            self.artifacts_dir / "edges_enriched.parquet",
            self.data_dir / "edges.parquet",
        ]
        return tuple(
            (str(path), path.stat().st_mtime_ns, path.stat().st_size)
            for path in candidates
            if path.exists()
        )

    def _load_nodes(self) -> dict[str, dict[str, Any]]:
        roles_path = self.out_dir / "nodes_roles.csv"
        if not roles_path.exists():
            raise ArtifactUnavailableError(
                f"Missing {roles_path}; run `python -m moneygraph.cli analyze` first"
            )
        roles = pd.read_csv(roles_path, dtype={"gid": "string"})
        if "gid" not in roles.columns:
            raise ArtifactUnavailableError(f"{roles_path} has no gid column")

        records: dict[str, dict[str, Any]] = {}
        for raw in roles.to_dict(orient="records"):
            gid = opaque_id(raw.get("gid"))
            if not gid:
                continue
            record = string_key_record(raw)
            record["gid"] = gid
            if "cluster_id" in record and record["cluster_id"] is not None:
                record["cluster_id"] = json_value(record["cluster_id"])
            records[gid] = record

        features_path = self.artifacts_dir / "node_features.parquet"
        if features_path.exists():
            features = pd.read_parquet(features_path)
            if "gid" not in features.columns:
                raise ArtifactUnavailableError(f"{features_path} has no gid column")
            for raw in features.to_dict(orient="records"):
                gid = opaque_id(raw.get("gid"))
                feature_record = string_key_record(raw)
                feature_record["gid"] = gid
                if gid in records:
                    records[gid] = {**feature_record, **records[gid]}
                else:
                    records[gid] = feature_record
        return dict(sorted(records.items()))

    def _load_edges(self) -> list[dict[str, Any]]:
        edges_path = self.artifacts_dir / "edges_enriched.parquet"
        if not edges_path.exists():
            edges_path = self.data_dir / "edges.parquet"
        if not edges_path.exists():
            raise ArtifactUnavailableError(
                "Missing artifacts/edges_enriched.parquet and data/edges.parquet"
            )
        frame = pd.read_parquet(edges_path)
        required = {"src", "dst", "sum_kzt", "n_tx"}
        if missing := required.difference(frame.columns):
            raise ArtifactUnavailableError(f"{edges_path} is missing columns: {sorted(missing)}")
        records: list[dict[str, Any]] = []
        for raw in frame.to_dict(orient="records"):
            record = string_key_record(raw)
            record["src"] = opaque_id(raw["src"])
            record["dst"] = opaque_id(raw["dst"])
            records.append(record)
        return sorted(records, key=lambda item: (item["src"], item["dst"]))

    def _load_clusters(self) -> list[dict[str, Any]]:
        path = self.out_dir / "clusters.csv"
        if not path.exists():
            return []
        frame = pd.read_csv(path)
        records = [string_key_record(raw) for raw in frame.to_dict(orient="records")]
        for record in records:
            record["top_gids"] = self._parse_id_list(record.get("top_gids"))
        return sorted(records, key=lambda item: str(item.get("cluster_id", "")))

    def _load_top_nodes(self) -> list[dict[str, Any]]:
        path = self.out_dir / "top_nodes.csv"
        if not path.exists():
            return []
        frame = pd.read_csv(path, dtype={"gid": "string"})
        records: list[dict[str, Any]] = []
        for raw in frame.to_dict(orient="records"):
            record = string_key_record(raw)
            record["gid"] = opaque_id(raw.get("gid"))
            records.append(record)
        return sorted(
            records,
            key=lambda item: (
                int(item.get("rank") or 10**9),
                -float(item.get("priority_score") or 0.0),
                item["gid"],
            ),
        )

    def _load_resilience(self) -> list[dict[str, Any]]:
        path = self.out_dir / "resilience.csv"
        if not path.exists():
            return []
        frame = pd.read_csv(path)
        return [string_key_record(raw) for raw in frame.to_dict(orient="records")]

    @staticmethod
    def _parse_id_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [opaque_id(item) for item in value]
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                loaded = json.loads(text)
                if isinstance(loaded, list):
                    return [opaque_id(item) for item in loaded]
            except json.JSONDecodeError:
                pass
        separator = "|" if "|" in text else ","
        return [part.strip() for part in text.split(separator) if part.strip()]
