from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _cors_from_env() -> tuple[str, ...]:
    raw = os.getenv("CORS_ORIGINS", "http://127.0.0.1:8501,http://localhost:8501")
    return tuple(origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())


@dataclass(frozen=True, slots=True)
class APISettings:
    """Environment-backed runtime settings with explicit test overrides."""

    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./moneygraph.db")
    )
    analyst_name: str = field(default_factory=lambda: os.getenv("ANALYST_NAME", "demo-analyst"))
    cors_origins: tuple[str, ...] = field(default_factory=_cors_from_env)
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    out_dir: Path = field(default_factory=lambda: Path(os.getenv("OUT_DIR", "./out")))
    artifacts_dir: Path = field(
        default_factory=lambda: Path(os.getenv("ARTIFACTS_DIR", "./artifacts"))
    )
    api_title: str = "Freedom MoneyGraph AML API"
    api_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not self.database_url.strip():
            raise ValueError("DATABASE_URL must not be empty")
        if not self.analyst_name.strip():
            raise ValueError("ANALYST_NAME must not be empty")
        if any(origin == "*" for origin in self.cors_origins):
            raise ValueError("CORS_ORIGINS must list explicit origins; wildcard is not allowed")
