"""Auditable, score-based functional role assignment."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from moneygraph.domain.roles import CORE_ROLES, Role

DEFAULT_ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    Role.CONSOLIDATOR.value: {
        "in_degree": 0.35,
        "in_transactions": 0.15,
        "in_amount": 0.15,
        "authority": 0.10,
        "seed_reach": 0.10,
        "retention": 0.10,
        "daily_payers": 0.05,
    },
    Role.DISTRIBUTOR.value: {
        "out_degree": 0.40,
        "out_transactions": 0.20,
        "out_amount": 0.15,
        "hub": 0.10,
        "recipients_to_payers": 0.10,
        "daily_fan_out": 0.05,
    },
    Role.TRANSIT.value: {
        "pass_through": 0.25,
        "fast_forward": 0.25,
        "matched_out": 0.15,
        "degree_balance": 0.10,
        "amount_balance": 0.10,
        "betweenness": 0.10,
        "temporal_activity": 0.05,
    },
    Role.COORDINATOR.value: {
        "betweenness": 0.30,
        "pagerank": 0.20,
        "seed_reach": 0.20,
        "cross_cluster_bridge": 0.15,
        "component_structure": 0.10,
        "balanced_flow": 0.05,
    },
    Role.TERMINAL.value: {
        "observed_input": 0.20,
        "no_observed_output": 0.40,
        "in_amount": 0.15,
        "in_degree": 0.10,
        "retention": 0.10,
        "authority": 0.05,
    },
}


def _numeric(frame: pd.DataFrame, *names: str, default: float = 0.0) -> pd.Series:
    for name in names:
        if name in frame.columns:
            values = pd.to_numeric(frame[name], errors="coerce")
            return values.replace([np.inf, -np.inf], np.nan).fillna(default).astype(float)
    return pd.Series(default, index=frame.index, dtype=float)


def _boolean(frame: pd.DataFrame, name: str, *, default: bool = False) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    return frame[name].fillna(default).astype(bool)


def _unit(values: pd.Series) -> pd.Series:
    return values.clip(0.0, 1.0).fillna(0.0).astype(float)


def _percentile(values: pd.Series, *, logarithmic: bool = False) -> pd.Series:
    clean = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    clean = clean.clip(lower=0.0).astype(float)
    if logarithmic:
        clean = pd.Series(np.log1p(clean.to_numpy()), index=clean.index, dtype=float)
    if len(clean) <= 1 or clean.max() == clean.min():
        fill = 0.0 if clean.max() <= 0.0 else 0.5
        return pd.Series(fill, index=clean.index, dtype=float)
    ranks = clean.rank(method="average", ascending=True)
    return ((ranks - 1.0) / (len(clean) - 1.0)).clip(0.0, 1.0)


def _feature_percentile(
    frame: pd.DataFrame,
    percentile_name: str,
    raw_name: str,
    *,
    logarithmic: bool = False,
) -> pd.Series:
    if percentile_name in frame.columns:
        return _unit(_numeric(frame, percentile_name))
    return _percentile(_numeric(frame, raw_name), logarithmic=logarithmic)


def _balance(left: pd.Series, right: pd.Series) -> pd.Series:
    denominator = pd.concat([left.abs(), right.abs()], axis=1).max(axis=1)
    balanced = 1.0 - (left - right).abs().div(denominator.where(denominator > 0.0, 1.0))
    return _unit(balanced.where(denominator > 0.0, 0.0))


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _config_value(config: object | None, name: str, default: float) -> float:
    if config is None:
        return default
    if isinstance(config, Mapping):
        if name in config:
            return float(config[name])
        limits = config.get("limits", {})
    else:
        if hasattr(config, name):
            return float(getattr(config, name))
        limits = getattr(config, "limits", {})
    value = _mapping(limits).get(name, default)
    return float(value)


def _role_weights(config: object | None, role: Role) -> Mapping[str, float]:
    defaults = DEFAULT_ROLE_WEIGHTS[role.value]
    if config is None:
        return defaults
    roles = config.get("roles", {}) if isinstance(config, Mapping) else getattr(config, "roles", {})
    candidate = _mapping(roles).get(role.value)
    if candidate is None:
        return defaults
    candidate_mapping = _mapping(candidate)
    if not candidate_mapping:
        raise ValueError(f"weights for role {role.value} must be a non-empty mapping")
    weights = {key: float(value) for key, value in candidate_mapping.items()}
    if not np.isclose(sum(weights.values()), 1.0) or any(value < 0.0 for value in weights.values()):
        raise ValueError(f"weights for role {role.value} must be non-negative and sum to 1")
    return weights


def _weighted(components: Mapping[str, pd.Series], weights: Mapping[str, float]) -> pd.Series:
    unknown = sorted(set(weights).difference(components))
    if unknown:
        raise ValueError(f"unknown role weight components: {', '.join(unknown)}")
    result = pd.Series(0.0, index=next(iter(components.values())).index, dtype=float)
    for name, weight in weights.items():
        result = result + components[name] * float(weight)
    return _unit(result)


def score_roles(features: pd.DataFrame, config: object | None = None) -> pd.DataFrame:
    """Return a copy with six role scores, selected role, and confidence.

    Scores are analytical signals, not labels of wrongdoing.  Missing optional
    features degrade to zero, which keeps the engine usable during staged runs.
    """

    if "gid" not in features.columns:
        raise ValueError("features must contain gid")
    if features["gid"].duplicated().any():
        raise ValueError("features must contain unique gid values")

    result = features.copy(deep=True)
    in_deg = _numeric(result, "in_deg")
    out_deg = _numeric(result, "out_deg")
    in_kzt = _numeric(result, "in_kzt")
    out_kzt = _numeric(result, "out_kzt")
    is_seed = _boolean(result, "is_seed")
    reliable = _boolean(result, "temporal_observation_reliable", default=True)
    matched_out = _unit(_numeric(result, "matched_out_ratio"))

    in_degree_pct = _feature_percentile(result, "in_deg_percentile", "in_deg")
    out_degree_pct = _feature_percentile(result, "out_deg_percentile", "out_deg")
    in_tx_pct = _feature_percentile(result, "in_tx_percentile", "in_tx")
    out_tx_pct = _feature_percentile(result, "out_tx_percentile", "out_tx")
    in_amount_pct = _feature_percentile(result, "in_kzt_percentile", "in_kzt", logarithmic=True)
    out_amount_pct = _feature_percentile(result, "out_kzt_percentile", "out_kzt", logarithmic=True)
    authority_pct = _feature_percentile(result, "authority_percentile", "authority")
    hub_pct = _feature_percentile(result, "hub_percentile", "hub")
    pagerank_pct = _feature_percentile(result, "pagerank_percentile", "pagerank")
    betweenness_pct = _feature_percentile(result, "betweenness_percentile", "betweenness")
    seed_reach_pct = _feature_percentile(result, "seed_reach_count_percentile", "seed_reach_count")
    daily_payers_pct = _feature_percentile(
        result, "max_daily_unique_payers_percentile", "max_daily_unique_payers"
    )
    daily_recipients_pct = _feature_percentile(
        result,
        "max_daily_unique_recipients_percentile",
        "max_daily_unique_recipients",
    )
    component_pct = _feature_percentile(
        result, "weak_component_size_percentile", "weak_component_size", logarithmic=True
    )

    retention = _unit(1.0 - matched_out).where((in_kzt > 0.0) & reliable & ~is_seed, 0.0)
    recipients_to_payers = _unit(
        (out_deg - in_deg)
        .clip(lower=0.0)
        .div(pd.concat([out_deg, in_deg], axis=1).max(axis=1).where(lambda x: x > 0.0, 1.0))
    )
    pass_through = _numeric(result, "pass_through")
    pass_valid = _boolean(result, "pass_through_valid", default=True) & ~is_seed
    pass_similarity = _unit(1.0 - (pass_through - 1.0).abs()).where(pass_valid, 0.0)
    fast_forward = _unit(_numeric(result, "fast_forward_0_2d_ratio")).where(reliable, 0.0)
    degree_balance = _balance(in_deg, out_deg)
    amount_balance = _balance(in_kzt, out_kzt)
    active_in = _numeric(result, "active_in_days")
    active_out = _numeric(result, "active_out_days")
    temporal_activity = _balance(active_in, active_out).where(reliable, 0.0)
    cross_cluster = _unit(_numeric(result, "cross_cluster_bridge_signal"))
    observed_input = ((in_deg > 0.0) & (in_kzt > 0.0)).astype(float)
    no_observed_output = (out_deg <= 0.0).astype(float)

    consolidator_components = {
        "in_degree": in_degree_pct,
        "in_transactions": in_tx_pct,
        "in_amount": in_amount_pct,
        "incoming_amount": in_amount_pct,
        "authority": authority_pct,
        "seed_reach": seed_reach_pct,
        "retention": retention,
        "daily_payers": daily_payers_pct,
        "same_day_multi_payer": daily_payers_pct,
    }
    distributor_components = {
        "out_degree": out_degree_pct,
        "out_transactions": out_tx_pct,
        "out_amount": out_amount_pct,
        "outgoing_amount": out_amount_pct,
        "hub": hub_pct,
        "recipients_to_payers": recipients_to_payers,
        "daily_fan_out": daily_recipients_pct,
    }
    transit_components = {
        "pass_through": pass_similarity,
        "fast_forward": fast_forward,
        "matched_out": matched_out.where(reliable, 0.0),
        "degree_balance": degree_balance,
        "amount_balance": amount_balance,
        "betweenness": betweenness_pct,
        "temporal_activity": temporal_activity,
    }
    coordinator_components = {
        "betweenness": betweenness_pct,
        "pagerank": pagerank_pct,
        "seed_reach": seed_reach_pct,
        "cross_cluster_bridge": cross_cluster,
        "component_structure": component_pct,
        "balanced_flow": amount_balance,
    }
    terminal_components = {
        "observed_input": observed_input,
        "observed_incoming": observed_input,
        "no_observed_output": no_observed_output,
        "low_outgoing": no_observed_output,
        "in_amount": in_amount_pct,
        "incoming_amount": in_amount_pct,
        "in_degree": in_degree_pct,
        "retention": retention,
        "authority": authority_pct,
    }

    result["consolidator_score"] = _weighted(
        consolidator_components, _role_weights(config, Role.CONSOLIDATOR)
    )
    result["distributor_score"] = _weighted(
        distributor_components, _role_weights(config, Role.DISTRIBUTOR)
    )
    transit_score = _weighted(transit_components, _role_weights(config, Role.TRANSIT))
    seed_penalty = _config_value(config, "seed_transit_penalty", 0.60)
    seed_cap = _config_value(config, "seed_transit_cap", 1.0)
    penalized_seed = (transit_score * seed_penalty).clip(upper=seed_cap)
    result["transit_score"] = transit_score.where(~is_seed, penalized_seed)
    result["coordinator_score"] = _weighted(
        coordinator_components, _role_weights(config, Role.COORDINATOR)
    )
    terminal_score = _weighted(terminal_components, _role_weights(config, Role.TERMINAL))
    terminal_cap = _config_value(config, "depth4_terminal_cap", 0.25)
    truncated = _boolean(result, "truncated_by_depth")
    result["terminal_score"] = terminal_score.where(
        ~truncated, terminal_score.clip(upper=terminal_cap)
    )

    core_columns = [f"{role.value}_score" for role in CORE_ROLES]
    maximum_core = result[core_columns].max(axis=1)
    result["peripheral_score"] = _unit(1.0 - maximum_core)
    isolated = (in_deg <= 0.0) & (out_deg <= 0.0)
    result.loc[isolated, "peripheral_score"] = 1.0

    threshold = _config_value(config, "role_threshold", 0.40)
    selected_core = result[core_columns].idxmax(axis=1).str.removesuffix("_score")
    selected = selected_core.where(maximum_core >= threshold, Role.PERIPHERAL.value)
    selected = selected.where(~isolated, Role.PERIPHERAL.value)
    result["role"] = selected

    all_score_columns = [f"{role.value}_score" for role in Role]
    sorted_scores = np.sort(result[all_score_columns].to_numpy(dtype=float), axis=1)
    chosen = pd.Series(sorted_scores[:, -1], index=result.index)
    margin = pd.Series(sorted_scores[:, -1] - sorted_scores[:, -2], index=result.index)
    result["role_score"] = _unit(0.80 * chosen + 0.20 * margin)
    return result
