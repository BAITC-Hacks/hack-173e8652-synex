"""Application and auditable analysis configuration."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROLE_WEIGHT_KEYS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "consolidator": frozenset(
            {
                "in_degree",
                "in_transactions",
                "in_amount",
                "authority",
                "seed_reach",
                "retention",
                "daily_payers",
            }
        ),
        "distributor": frozenset(
            {
                "out_degree",
                "out_transactions",
                "out_amount",
                "hub",
                "recipients_to_payers",
                "daily_fan_out",
            }
        ),
        "transit": frozenset(
            {
                "pass_through",
                "fast_forward",
                "matched_out",
                "degree_balance",
                "amount_balance",
                "betweenness",
                "temporal_activity",
            }
        ),
        "coordinator": frozenset(
            {
                "betweenness",
                "pagerank",
                "seed_reach",
                "cross_cluster_bridge",
                "component_structure",
                "balanced_flow",
            }
        ),
        "terminal": frozenset(
            {
                "observed_input",
                "no_observed_output",
                "in_amount",
                "in_degree",
                "retention",
                "authority",
            }
        ),
    }
)
PRIORITY_WEIGHT_KEYS = frozenset(
    {
        "coordinator",
        "consolidator",
        "distributor",
        "transit",
        "pagerank",
        "betweenness",
        "seed_reach",
        "turnover",
    }
)
DEFAULT_PRIORITY_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "coordinator": 0.22,
        "consolidator": 0.18,
        "distributor": 0.12,
        "transit": 0.08,
        "pagerank": 0.12,
        "betweenness": 0.12,
        "seed_reach": 0.10,
        "turnover": 0.06,
    }
)


class Settings(BaseSettings):
    """Runtime settings; defaults keep the application completely local."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_dir: Path = Path("data")
    out_dir: Path = Path("out")
    artifacts_dir: Path = Path("artifacts")
    analysis_config: Path = Path("config/default.yaml")
    database_url: str = "sqlite:///./artifacts/moneygraph.db"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    ui_host: str = "127.0.0.1"
    ui_port: int = 8501
    cors_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://127.0.0.1:8501",
        "http://localhost:8501",
    )
    ai_enabled: bool = False
    openai_api_key: str | None = None
    nvidia_api_key: str | None = None
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Immutable analysis parameters loaded from YAML."""

    random_seed: int
    top_n: int
    role_threshold: float
    max_seed_hops: int
    round_amount_multiple: int
    roles: Mapping[str, Mapping[str, float]]
    priority: Mapping[str, float]
    limits: Mapping[str, float | int]
    depth4_terminal_cap: float
    seed_transit_penalty: float
    evidence_max_chars: int


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _validated_weights(raw: object, *, field: str) -> Mapping[str, float]:
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError(f"{field} must be a non-empty mapping")
    weights = {str(name): _number(value, field=f"{field}.{name}") for name, value in raw.items()}
    if any(value < 0 for value in weights.values()):
        raise ValueError(f"{field} weights must be non-negative")
    if not math.isclose(sum(weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"{field} weights must sum to 1")
    return MappingProxyType(weights)


def load_analysis_config(path: str | Path) -> AnalysisConfig:
    """Load and validate deterministic model configuration."""

    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Analysis config not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Analysis config root must be a mapping")

    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, dict) or not roles_raw:
        raise ValueError("roles must be a non-empty mapping")
    validated_roles: dict[str, Mapping[str, float]] = {}
    for raw_role, raw_weights in roles_raw.items():
        role = str(raw_role)
        weights = _validated_weights(raw_weights, field=f"roles.{role}")
        expected_keys = ROLE_WEIGHT_KEYS.get(role)
        if expected_keys is None:
            raise ValueError(f"roles contains unknown role: {role}")
        actual_keys = set(weights)
        if actual_keys != expected_keys:
            unknown = sorted(actual_keys.difference(expected_keys))
            missing = sorted(expected_keys.difference(actual_keys))
            details = []
            if unknown:
                details.append(f"unknown components: {', '.join(unknown)}")
            if missing:
                details.append(f"missing components: {', '.join(missing)}")
            raise ValueError(f"roles.{role} has " + "; ".join(details))
        validated_roles[role] = weights
    missing_roles = sorted(set(ROLE_WEIGHT_KEYS).difference(validated_roles))
    if missing_roles:
        raise ValueError(f"roles missing required roles: {', '.join(missing_roles)}")
    roles = MappingProxyType(validated_roles)

    priority_raw = raw.get("priority", DEFAULT_PRIORITY_WEIGHTS)
    priority = _validated_weights(priority_raw, field="priority")
    priority_keys = set(priority)
    if priority_keys != PRIORITY_WEIGHT_KEYS:
        unknown = sorted(priority_keys.difference(PRIORITY_WEIGHT_KEYS))
        missing = sorted(PRIORITY_WEIGHT_KEYS.difference(priority_keys))
        details = []
        if unknown:
            details.append(f"unknown components: {', '.join(unknown)}")
        if missing:
            details.append(f"missing components: {', '.join(missing)}")
        raise ValueError("priority has " + "; ".join(details))
    limits_raw = raw.get("limits", {})
    if not isinstance(limits_raw, dict):
        raise ValueError("limits must be a mapping")
    limits = MappingProxyType(
        {str(name): _number(value, field=f"limits.{name}") for name, value in limits_raw.items()}
    )

    random_seed = int(_number(raw.get("random_seed", 42), field="random_seed"))
    top_n = int(_number(raw.get("top_n", 50), field="top_n"))
    role_threshold = _number(raw.get("role_threshold", 0.4), field="role_threshold")
    max_seed_hops = int(_number(raw.get("max_seed_hops", 4), field="max_seed_hops"))
    round_amount_multiple = int(
        _number(raw.get("round_amount_multiple", 10_000), field="round_amount_multiple")
    )
    if random_seed < 0:
        raise ValueError("random_seed must be non-negative")
    if top_n < 20:
        raise ValueError("top_n must be at least 20")
    if not 0 <= role_threshold <= 1:
        raise ValueError("role_threshold must be between 0 and 1")
    if max_seed_hops < 1:
        raise ValueError("max_seed_hops must be positive")
    if round_amount_multiple < 1:
        raise ValueError("round_amount_multiple must be positive")
    depth4_terminal_cap = float(limits.get("depth4_terminal_cap", 0.25))
    seed_transit_penalty = float(limits.get("seed_transit_penalty", 0.60))
    evidence_max_chars = int(limits.get("evidence_max_chars", 200))
    if not 0 <= depth4_terminal_cap <= 1:
        raise ValueError("limits.depth4_terminal_cap must be between 0 and 1")
    if not 0 <= seed_transit_penalty <= 1:
        raise ValueError("limits.seed_transit_penalty must be between 0 and 1")
    if evidence_max_chars < 1:
        raise ValueError("limits.evidence_max_chars must be positive")

    return AnalysisConfig(
        random_seed=random_seed,
        top_n=top_n,
        role_threshold=role_threshold,
        max_seed_hops=max_seed_hops,
        round_amount_multiple=round_amount_multiple,
        roles=roles,
        priority=priority,
        limits=limits,
        depth4_terminal_cap=depth4_terminal_cap,
        seed_transit_penalty=seed_transit_penalty,
        evidence_max_chars=evidence_max_chars,
    )


def get_settings() -> Settings:
    """Construct settings at call time so tests and CLI can override environment variables."""

    return Settings()
