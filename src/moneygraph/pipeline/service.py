"""End-to-end orchestration for the deterministic offline analysis pipeline."""

from __future__ import annotations

import os
import platform
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from hashlib import sha256
from importlib import metadata
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd

from moneygraph import __version__
from moneygraph.config import AnalysisConfig, load_analysis_config
from moneygraph.pipeline.clustering import compute_cluster_bridge_features, detect_communities
from moneygraph.pipeline.explanations import build_evidence
from moneygraph.pipeline.exporters import (
    export_mandatory_csvs,
    sha256_file,
    write_csv_atomic,
    write_json_atomic,
    write_parquet_atomic,
)
from moneygraph.pipeline.features import compute_graph_features
from moneygraph.pipeline.graph_builder import build_graph
from moneygraph.pipeline.ingestion import DatasetBundle, load_dataset
from moneygraph.pipeline.priority import score_priority
from moneygraph.pipeline.resilience import analyze_resilience
from moneygraph.pipeline.role_engine import score_roles
from moneygraph.pipeline.temporal import compute_temporal_features
from moneygraph.pipeline.validation import ValidationReport, validate_dataset
from moneygraph.repository.database import Database
from moneygraph.repository.repositories import MoneyGraphRepository
from moneygraph.verification import verify_outputs

T = TypeVar("T")

EXPECTED_CASE_PROFILE: dict[str, Any] = {
    "nodes": 2_248,
    "edges": 3_119,
    "transactions": 4_840,
    "seed": 81,
    "period_start": "2026-07-01",
    "period_end": "2026-07-31",
    "depth_distribution": {"0": 81, "1": 472, "2": 462, "3": 789, "4": 444},
}


@dataclass(frozen=True, slots=True)
class AnalysisRunResult:
    """Paths and measured metadata from one completed analysis."""

    run_id: str
    out_dir: Path
    artifacts_dir: Path
    manifest: dict[str, Any]
    node_features: pd.DataFrame


def _timed(
    name: str,
    durations: dict[str, float],
    operation: Callable[[], T],
) -> T:
    started = time.perf_counter()
    result = operation()
    durations[name] = round(time.perf_counter() - started, 6)
    return result


def _plain_mapping(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_mapping(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_mapping(item) for item in value]
    return value


def _config_payload(config: AnalysisConfig) -> dict[str, Any]:
    return {field.name: _plain_mapping(getattr(config, field.name)) for field in fields(config)}


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in (
        "fastapi",
        "networkx",
        "numpy",
        "pandas",
        "pyarrow",
        "scipy",
        "sqlalchemy",
        "streamlit",
    ):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _profile_status(bundle: DatasetBundle, report: ValidationReport) -> dict[str, Any]:
    depth_counts = {
        str(int(str(depth))): int(count)
        for depth, count in bundle.nodes["depth"].value_counts().sort_index().items()
    }
    observed = {
        **report.counts,
        "period_start": report.min_date,
        "period_end": report.max_date,
        "depth_distribution": depth_counts,
    }
    matches = {
        key: observed.get(key) == expected for key, expected in EXPECTED_CASE_PROFILE.items()
    }
    return {"expected": EXPECTED_CASE_PROFILE, "observed": observed, "matches": matches}


def _merge_temporal(base: pd.DataFrame, temporal: pd.DataFrame) -> pd.DataFrame:
    duplicate_context = {"depth", "is_seed"}.intersection(temporal.columns)
    temporal_only = temporal.drop(columns=sorted(duplicate_context))
    return base.merge(temporal_only, on="gid", how="left", validate="one_to_one")


def _enrich_edges(edges: pd.DataFrame, node_features: pd.DataFrame) -> pd.DataFrame:
    context_columns = ["gid", "role", "cluster_id", "priority_score"]
    source = node_features[context_columns].rename(
        columns={
            "gid": "src",
            "role": "src_role",
            "cluster_id": "src_cluster_id",
            "priority_score": "src_priority_score",
        }
    )
    destination = node_features[context_columns].rename(
        columns={
            "gid": "dst",
            "role": "dst_role",
            "cluster_id": "dst_cluster_id",
            "priority_score": "dst_priority_score",
        }
    )
    return (
        edges.copy(deep=True)
        .merge(source, on="src", how="left", validate="many_to_one")
        .merge(destination, on="dst", how="left", validate="many_to_one")
        .sort_values(["src", "dst"], kind="stable")
        .reset_index(drop=True)
    )


def _summary_payload(
    report: ValidationReport,
    node_features: pd.DataFrame,
    cluster_count: int,
    duration_seconds: float,
) -> dict[str, Any]:
    ranked = node_features.sort_values(
        ["priority_score", "gid"], ascending=[False, True], kind="stable"
    )
    return {
        "counts": report.counts,
        "period": {"start": report.min_date, "end": report.max_date},
        "weakly_connected_components": report.weakly_connected_components,
        "clusters": int(cluster_count),
        "role_distribution": {
            str(role): int(count)
            for role, count in node_features["role"].value_counts().sort_index().items()
        },
        "top_gids": ranked["gid"].head(10).astype(str).tolist(),
        "validation_warnings": list(report.warnings),
        "duration_seconds": duration_seconds,
        "limitations": [
            "depth=4 ограничивает наблюдение исходящих связей",
            "входящие переводы seed извне выборки не наблюдаются",
            "видны только исходящие внутрибанковские переводы от 5 000 KZT",
            "роли и приоритет являются аналитическими гипотезами, а не выводом о виновности",
        ],
    }


def _publish(stage_dir: Path, destination: Path, *, manifest_last: bool = False) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    files = sorted(
        stage_dir.iterdir(),
        key=lambda path: (manifest_last and path.name == "run_manifest.json", path.name),
    )
    for source in files:
        if source.is_file():
            os.replace(source, destination / source.name)


def _persist_completed_run(
    database_url: str,
    *,
    run_id: str,
    manifest: Mapping[str, Any],
    node_features: pd.DataFrame,
    input_hashes: Mapping[str, str],
) -> None:
    """Persist the immutable run snapshot and its audit event."""

    database = Database(database_url)
    try:
        database.create_schema()
        repository = MoneyGraphRepository(database.session_factory)
        combined_hash = sha256(
            "|".join(f"{key}:{input_hashes[key]}" for key in sorted(input_hashes)).encode()
        ).hexdigest()
        raw_snapshots = node_features[
            ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
        ].to_dict(orient="records")
        snapshots: list[Mapping[str, Any]] = [
            {str(key): value for key, value in snapshot.items()} for snapshot in raw_snapshots
        ]
        actor = os.getenv("ANALYST_NAME", "pipeline").strip()[:128] or "pipeline"
        repository.create_run(
            run_id=run_id,
            status="completed",
            config_version=__version__,
            input_hash=combined_hash,
            manifest=manifest,
            node_snapshots=snapshots,
            actor=actor,
        )
    finally:
        database.dispose()


def run_analysis(
    data_dir: str | Path,
    out_dir: str | Path,
    artifacts_dir: str | Path,
    *,
    config_path: str | Path = "config/default.yaml",
    database_url: str | None = None,
) -> AnalysisRunResult:
    """Execute, verify, and publish one complete deterministic analysis run."""

    started_at = datetime.now(UTC)
    total_started = time.perf_counter()
    durations: dict[str, float] = {}
    data_path = Path(data_dir).resolve()
    output_path = Path(out_dir).resolve()
    artifact_path = Path(artifacts_dir).resolve()
    config_file = Path(config_path).resolve()

    config = _timed("config", durations, lambda: load_analysis_config(config_file))
    bundle = _timed("ingestion", durations, lambda: load_dataset(data_path))
    report = _timed("validation", durations, lambda: validate_dataset(bundle))
    graph = _timed("graph", durations, lambda: build_graph(bundle.nodes, bundle.edges))
    base_features = _timed(
        "graph_features",
        durations,
        lambda: compute_graph_features(
            graph,
            bundle.nodes,
            bundle.transactions,
            max_seed_hops=config.max_seed_hops,
            round_amount_multiple=config.round_amount_multiple,
            random_seed=config.random_seed,
        ),
    )
    temporal = _timed(
        "temporal_features",
        durations,
        lambda: compute_temporal_features(bundle.nodes, bundle.transactions),
    )
    features = _merge_temporal(base_features, temporal)
    clusters = _timed(
        "clustering",
        durations,
        lambda: detect_communities(graph, seed=config.random_seed),
    )
    features = features.merge(
        clusters.membership,
        on="gid",
        how="left",
        validate="one_to_one",
    )
    bridge_features = compute_cluster_bridge_features(graph, clusters.membership)
    features = features.merge(bridge_features, on="gid", how="left", validate="one_to_one")
    if features["cluster_id"].isna().any():
        raise ValueError("community detection did not assign every gid")
    features["cluster_id"] = features["cluster_id"].astype(int)
    features = _timed("role_scoring", durations, lambda: score_roles(features, config))
    features = _timed("priority_scoring", durations, lambda: score_priority(features, config))
    features["evidence"] = features.apply(build_evidence, axis=1)
    features = features.sort_values("gid", kind="stable").reset_index(drop=True)
    resilience = _timed(
        "resilience",
        durations,
        lambda: analyze_resilience(graph, features[["gid", "priority_score"]]),
    )
    edges_enriched = _enrich_edges(bundle.edges, features)

    run_id = str(uuid.uuid4())
    input_hashes = {
        name: sha256_file(data_path / filename)
        for name, filename in {
            "nodes": "nodes.parquet",
            "edges": "edges.parquet",
            "transactions": "transactions.parquet",
        }.items()
    }
    output_path.mkdir(parents=True, exist_ok=True)
    artifact_path.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix=".moneygraph-out-", dir=output_path) as out_temporary,
        tempfile.TemporaryDirectory(
            prefix=".moneygraph-artifacts-", dir=artifact_path
        ) as artifacts_temporary,
    ):
        stage_out = Path(out_temporary)
        stage_artifacts = Path(artifacts_temporary)
        export_started = time.perf_counter()
        export_paths = export_mandatory_csvs(
            features,
            bundle.edges,
            stage_out,
            top_n=config.top_n,
        )
        write_parquet_atomic(features, stage_artifacts / "node_features.parquet")
        write_parquet_atomic(edges_enriched, stage_artifacts / "edges_enriched.parquet")
        write_csv_atomic(resilience, stage_out / "resilience.csv")
        profile = _profile_status(bundle, report)
        validation_payload = {**report.to_dict(), "case_profile": profile}
        write_json_atomic(validation_payload, stage_out / "validation_report.json")
        durations["export"] = round(time.perf_counter() - export_started, 6)

        duration_seconds = round(time.perf_counter() - total_started, 6)
        summary = _summary_payload(report, features, clusters.cluster_count, duration_seconds)
        write_json_atomic(summary, stage_out / "summary.json")
        output_hashes = {path.name: sha256_file(path) for path in export_paths}
        manifest = {
            "run_id": run_id,
            "status": "completed",
            "rules_version": __version__,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "duration_seconds": duration_seconds,
            "stage_durations_seconds": durations,
            "counts": report.counts,
            "period": {"start": report.min_date, "end": report.max_date},
            "random_seed": config.random_seed,
            "config": _config_payload(config),
            "hashes": {
                "inputs": input_hashes,
                "config": sha256_file(config_file),
                "mandatory_outputs": output_hashes,
            },
            "versions": {
                "python": platform.python_version(),
                "moneygraph": __version__,
                **_package_versions(),
            },
            "database_url_configured": bool(database_url),
        }
        write_json_atomic(manifest, stage_out / "run_manifest.json")

        verification_errors = verify_outputs(stage_out, expected_nodes=len(bundle.nodes))
        if verification_errors:
            raise ValueError("Output verification failed: " + "; ".join(verification_errors))
        _publish(stage_artifacts, artifact_path)
        _publish(stage_out, output_path, manifest_last=True)

    if database_url:
        _persist_completed_run(
            database_url,
            run_id=run_id,
            manifest=manifest,
            node_features=features,
            input_hashes=input_hashes,
        )

    return AnalysisRunResult(
        run_id=run_id,
        out_dir=output_path,
        artifacts_dir=artifact_path,
        manifest=manifest,
        node_features=features,
    )
