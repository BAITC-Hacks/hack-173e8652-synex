"""Transparent priority-for-review scoring with per-driver contributions."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

DEFAULT_PRIORITY_WEIGHTS: dict[str, float] = {
    "coordinator": 0.22,
    "consolidator": 0.18,
    "distributor": 0.12,
    "transit": 0.08,
    "pagerank": 0.12,
    "betweenness": 0.12,
    "seed_reach": 0.10,
    "turnover": 0.06,
}

PRIORITY_CONTRIBUTION_COLUMNS: list[str] = [
    "priority_coordinator",
    "priority_consolidator",
    "priority_distributor",
    "priority_transit",
    "priority_pagerank",
    "priority_betweenness",
    "priority_seed_reach",
    "priority_turnover",
]


def _numeric(frame: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return (
                pd.to_numeric(frame[name], errors="coerce")
                .replace([np.inf, -np.inf], np.nan)
                .fillna(0.0)
                .astype(float)
            )
    return pd.Series(0.0, index=frame.index, dtype=float)


def _percentile(values: pd.Series, *, logarithmic: bool = False) -> pd.Series:
    clean = values.clip(lower=0.0).astype(float)
    if logarithmic:
        clean = pd.Series(np.log1p(clean.to_numpy()), index=clean.index, dtype=float)
    if len(clean) <= 1:
        return clean.clip(0.0, 1.0)
    if clean.max() == clean.min():
        return pd.Series(0.0, index=clean.index, dtype=float)
    ranks = clean.rank(method="average", ascending=True)
    return ((ranks - 1.0) / (len(clean) - 1.0)).clip(0.0, 1.0)


def _percentile_or_raw(
    frame: pd.DataFrame,
    percentile_name: str,
    raw_name: str,
    *,
    logarithmic: bool = False,
) -> pd.Series:
    if percentile_name in frame.columns:
        return _numeric(frame, percentile_name).clip(0.0, 1.0)
    return _percentile(_numeric(frame, raw_name), logarithmic=logarithmic)


def _priority_weights(config: object | None) -> dict[str, float]:
    if config is None:
        return DEFAULT_PRIORITY_WEIGHTS.copy()
    candidate: object
    if isinstance(config, Mapping):
        candidate = config.get("priority", DEFAULT_PRIORITY_WEIGHTS)
    else:
        candidate = getattr(config, "priority", DEFAULT_PRIORITY_WEIGHTS)
    if not isinstance(candidate, Mapping):
        raise ValueError("priority weights must be a mapping")
    missing = sorted(set(DEFAULT_PRIORITY_WEIGHTS).difference(candidate))
    unknown = sorted(set(candidate).difference(DEFAULT_PRIORITY_WEIGHTS))
    if missing or unknown:
        raise ValueError(
            "priority weight keys mismatch; "
            f"missing={missing or 'none'}, unknown={unknown or 'none'}"
        )
    weights = {str(name): float(value) for name, value in candidate.items()}
    if any(value < 0.0 for value in weights.values()) or not np.isclose(sum(weights.values()), 1.0):
        raise ValueError("priority weights must be non-negative and sum to 1")
    return weights


def score_priority(features: pd.DataFrame, config: object | None = None) -> pd.DataFrame:
    """Calculate review priority and retain every weighted contribution."""

    if "gid" not in features.columns:
        raise ValueError("features must contain gid")
    if features["gid"].duplicated().any():
        raise ValueError("features must contain unique gid values")
    result = features.copy(deep=True)
    weights = _priority_weights(config)
    signals: dict[str, pd.Series] = {
        "coordinator": _numeric(result, "coordinator_score").clip(0.0, 1.0),
        "consolidator": _numeric(result, "consolidator_score").clip(0.0, 1.0),
        "distributor": _numeric(result, "distributor_score").clip(0.0, 1.0),
        "transit": _numeric(result, "transit_score").clip(0.0, 1.0),
        "pagerank": _percentile_or_raw(result, "pagerank_percentile", "pagerank"),
        "betweenness": _percentile_or_raw(result, "betweenness_percentile", "betweenness"),
        "seed_reach": _percentile_or_raw(result, "seed_reach_count_percentile", "seed_reach_count"),
        "turnover": _percentile_or_raw(
            result, "total_kzt_percentile", "total_kzt", logarithmic=True
        ),
    }
    for driver, signal in signals.items():
        result[f"priority_{driver}"] = signal * weights[driver]
    result["priority_raw"] = result[PRIORITY_CONTRIBUTION_COLUMNS].sum(axis=1)
    result["priority_score"] = _percentile(result["priority_raw"])
    return result
