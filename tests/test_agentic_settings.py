from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from moneygraph.api.main import create_app
from moneygraph.api.settings import APISettings


def test_auto_monitor_is_enabled_without_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTIC_AUTO_MONITOR_ENABLED", raising=False)

    assert APISettings().agentic_auto_monitor_enabled is True


@pytest.mark.parametrize("raw", ["false", " FALSE "])
def test_auto_monitor_can_be_disabled_explicitly(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("AGENTIC_AUTO_MONITOR_ENABLED", raw)

    assert APISettings().agentic_auto_monitor_enabled is False


def test_default_api_startup_remains_healthy_without_transactions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AGENTIC_AUTO_MONITOR_ENABLED", raising=False)
    monkeypatch.setenv("AI_ENABLED", "false")
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'startup.db'}",
        data_dir=tmp_path / "missing-data",
        out_dir=tmp_path / "out",
        artifacts_dir=tmp_path / "artifacts",
        agentic_auto_monitor_cadence_seconds=0.05,
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = client.get("/api/v1/agentic/monitoring")
            if response.json()["data"]["state"] == "error":
                break
            time.sleep(0.02)

        assert response.status_code == 200
        assert response.json()["data"]["enabled"] is True
        assert response.json()["data"]["state"] == "error"
        assert response.json()["data"]["last_error"] == "transactions dataset is unavailable"


def test_default_api_startup_scans_available_transactions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AGENTIC_AUTO_MONITOR_ENABLED", raising=False)
    monkeypatch.setenv("AI_ENABLED", "false")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pd.DataFrame(
        [
            {
                "src": "payer",
                "dst": "collector",
                "date": "2026-07-03",
                "sum_kzt": 150_000.0,
            }
        ]
    ).to_parquet(data_dir / "transactions.parquet", index=False)
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'replay.db'}",
        data_dir=data_dir,
        out_dir=tmp_path / "out",
        artifacts_dir=tmp_path / "artifacts",
        agentic_auto_monitor_cadence_seconds=0.05,
    )

    with TestClient(create_app(settings)) as client:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.get("/api/v1/agentic/monitoring")
            if response.json()["data"]["processed_days"] == 1:
                break
            time.sleep(0.02)

        assert response.status_code == 200
        assert response.json()["data"]["enabled"] is True
        assert response.json()["data"]["processed_days"] == 1
        assert response.json()["data"]["latest_scan"]["replay_date"] == "2026-07-03"
