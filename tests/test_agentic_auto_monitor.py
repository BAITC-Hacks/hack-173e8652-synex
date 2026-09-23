from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier

import pandas as pd
from fastapi.testclient import TestClient

from moneygraph.api.main import create_app
from moneygraph.api.settings import APISettings
from moneygraph.repository.database import Database
from moneygraph.repository.repositories import MoneyGraphRepository
from moneygraph.services.agentic_loop import AgenticLoopService
from moneygraph.services.auto_monitor import AgenticAutoMonitor


def _write_fixture_transactions(data_dir: Path, *, include_fourth_day: bool = False) -> None:
    data_dir.mkdir(exist_ok=True)
    rows = [
        {"src": "older", "dst": "collector", "date": "2026-07-01", "sum_kzt": 200_000.0},
        *[
            {
                "src": f"payer-{index}",
                "dst": "collector",
                "date": "2026-07-02",
                "sum_kzt": 100_000.0,
            }
            for index in range(8)
        ],
        {"src": "collector", "dst": "sink", "date": "2026-07-02", "sum_kzt": 900_000.0},
        {
            "src": "future-whale",
            "dst": "collector",
            "date": "2026-07-03",
            "sum_kzt": 5_000_000.0,
        },
    ]
    if include_fourth_day:
        rows.append(
            {"src": "late", "dst": "collector", "date": "2026-07-04", "sum_kzt": 50_000.0}
        )
    pd.DataFrame(rows).to_parquet(data_dir / "transactions.parquet", index=False)


def _build_monitor(tmp_path: Path) -> tuple[AgenticAutoMonitor, MoneyGraphRepository]:
    database = Database(f"sqlite:///{tmp_path / 'monitor.db'}")
    database.create_schema()
    repository = MoneyGraphRepository(database.session_factory)
    service = AgenticLoopService(repository, tmp_path / "data", actor="test-analyst")
    return AgenticAutoMonitor(service, repository, enabled=True, cadence_seconds=0.05), repository


def test_auto_monitor_scans_each_day_without_lookahead_and_prepares_only_proposals(
    tmp_path: Path,
) -> None:
    _write_fixture_transactions(tmp_path / "data")
    monitor, repository = _build_monitor(tmp_path)

    first = monitor.tick_once()
    second = monitor.tick_once()

    assert first is not None and first["replay_date"] == "2026-07-01"
    assert second is not None and second["replay_date"] == "2026-07-02"
    assert second["summary"]["transactions_seen"] == 10
    assert second["summary"]["future_transactions_excluded"] == 1
    collector = next(alert for alert in second["alerts"] if alert["gid"] == "collector")
    assert collector["facts"]["daily_unique_payers"] == 8
    assert collector["facts"]["daily_incoming_kzt"] == 800_000.0
    assert "future-whale" not in collector["facts"]["direct_payers"]
    assert len(collector["actions"]) == 3
    assert all(action["status"] == "proposed" for action in collector["actions"])
    assert all(action["execution_result"] is None for action in collector["actions"])

    audit = repository.list_agentic_audit_events(limit=100, offset=0)["items"]
    assert "actions.proposed" in {entry["action"] for entry in audit}
    assert "tool.executed" not in {entry["action"] for entry in audit}
    assert "action.approved" not in {entry["action"] for entry in audit}


def test_auto_monitor_restart_skips_persisted_days_and_picks_up_new_source_day(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_fixture_transactions(data_dir)
    monitor, repository = _build_monitor(tmp_path)
    for _ in range(3):
        monitor.tick_once()
    assert monitor.status()["processed_days"] == 3
    assert monitor.status()["state"] == "caught_up"

    restarted, _ = _build_monitor(tmp_path)
    assert restarted.tick_once() is None
    assert restarted.status()["processed_days"] == 3
    assert len(repository.list_auto_monitoring_scans()) == 3

    _write_fixture_transactions(data_dir, include_fourth_day=True)
    added = restarted.tick_once()
    assert added is not None and added["replay_date"] == "2026-07-04"
    assert restarted.status()["processed_days"] == 4
    assert restarted.status()["total_days"] == 4


def test_status_prioritizes_older_high_score_then_removes_executed_case(tmp_path: Path) -> None:
    _write_fixture_transactions(tmp_path / "data")
    monitor, repository = _build_monitor(tmp_path)
    for _ in range(3):
        monitor.tick_once()

    scans = repository.list_auto_monitoring_scans()
    older = next(alert for alert in scans[1]["alerts"] if alert["gid"] == "collector")
    newer = next(alert for alert in scans[2]["alerts"] if alert["gid"] == "collector")
    assert older["priority_score"] > newer["priority_score"]

    status = monitor.status()
    assert status["queue_size"] == 2
    assert [item["id"] for item in status["priority_queue"]] == [older["id"], newer["id"]]
    assert [item["replay_date"] for item in status["priority_queue"]] == [
        "2026-07-02",
        "2026-07-03",
    ]
    assert all(len(item["actions"]) == 3 for item in status["priority_queue"])

    service = AgenticLoopService(repository, tmp_path / "data", actor="test-analyst", ai_enabled=False)
    service.decide_and_execute(
        older["actions"][0]["id"],
        "approve",
        confirmation="APPROVE",
        idempotency_key="older-case-approved",
    )

    restarted, _ = _build_monitor(tmp_path)
    after_decision = restarted.status()
    assert after_decision["queue_size"] == 1
    assert [item["id"] for item in after_decision["priority_queue"]] == [newer["id"]]


def test_status_keeps_case_with_remaining_proposals_after_one_rejection(tmp_path: Path) -> None:
    _write_fixture_transactions(tmp_path / "data")
    monitor, repository = _build_monitor(tmp_path)
    monitor.tick_once()
    scan = monitor.tick_once()
    assert scan is not None
    alert = next(item for item in scan["alerts"] if item["gid"] == "collector")

    service = AgenticLoopService(repository, tmp_path / "data", actor="test-analyst", ai_enabled=False)
    service.decide_and_execute(
        alert["actions"][0]["id"],
        "reject",
        confirmation=None,
        idempotency_key="one-option-rejected",
    )

    status = monitor.status()
    assert status["queue_size"] == 1
    assert [item["id"] for item in status["priority_queue"]] == [alert["id"]]
    assert [action["status"] for action in status["priority_queue"][0]["actions"]].count(
        "proposed"
    ) == 2


def test_status_uses_recency_for_equal_scores_and_excludes_alerts_without_actions(
    tmp_path: Path,
) -> None:
    monitor, repository = _build_monitor(tmp_path)
    service = AgenticLoopService(repository, tmp_path / "data", actor="test-analyst", ai_enabled=False)

    def alert_data(gid: str, score: float) -> dict[str, object]:
        return {
            "gid": gid,
            "rule_keys": ["daily_incoming_kzt"],
            "facts": {"daily_unique_payers": 1},
            "role": "collector",
            "cluster_id": gid,
            "priority_score": score,
            "explanation": "Persisted rule match",
        }

    older_scan = repository.create_monitoring_scan(
        replay_date=date(2026, 7, 1),
        interval_minutes=15,
        summary={},
        alerts=[alert_data("older", 0.8), alert_data("unprepared", 0.99)],
        actor="auto-monitor",
    )
    newer_scan = repository.create_monitoring_scan(
        replay_date=date(2026, 7, 2),
        interval_minutes=15,
        summary={},
        alerts=[alert_data("newer", 0.8)],
        actor="auto-monitor",
    )
    for scan, gid in ((older_scan, "older"), (newer_scan, "newer")):
        alert = next(item for item in scan["alerts"] if item["gid"] == gid)
        service.propose_actions(alert["id"])

    status = monitor.status()
    assert status["queue_size"] == 2
    assert [item["gid"] for item in status["priority_queue"]] == ["newer", "older"]
    assert [item["replay_date"] for item in status["priority_queue"]] == [
        "2026-07-02",
        "2026-07-01",
    ]


def test_api_startup_runs_monitor_without_manual_scan_and_exposes_feed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_fixture_transactions(data_dir)
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        data_dir=data_dir,
        out_dir=tmp_path / "out",
        artifacts_dir=tmp_path / "artifacts",
        agentic_auto_monitor_enabled=True,
        agentic_auto_monitor_cadence_seconds=0.05,
    )

    with TestClient(create_app(settings)) as client:
        payload = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.get("/api/v1/agentic/monitoring")
            assert response.status_code == 200
            payload = response.json()["data"]
            if payload["processed_days"] >= 2:
                break
            time.sleep(0.02)

        assert payload is not None and payload["processed_days"] >= 2
        assert payload["enabled"] is True
        assert payload["cadence_seconds"] == 0.05
        assert payload["total_days"] == 3
        assert payload["transactions_in_source"] == 11
        assert payload["source_time_granularity"] == "day"
        assert payload["simulation"] is True
        assert payload["latest_scan"]["replay_date"] == "2026-07-02"
        alert = next(item for item in payload["recent_alerts"] if item["gid"] == "collector")
        assert alert["replay_date"] == "2026-07-02"
        assert len(alert["actions"]) == 3

        audit = client.get("/api/v1/agentic/audit").json()["data"]
        assert "tool.executed" not in {entry["action"] for entry in audit}


def test_monitor_status_reports_disabled_without_scanning(tmp_path: Path) -> None:
    _write_fixture_transactions(tmp_path / "data")
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'disabled.db'}",
        data_dir=tmp_path / "data",
        out_dir=tmp_path / "out",
        artifacts_dir=tmp_path / "artifacts",
        agentic_auto_monitor_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/agentic/monitoring")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is False
    assert data["state"] == "disabled"
    assert data["processed_days"] == 0


def test_monitor_audits_source_failure_and_recovers_when_source_arrives(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'recovery.db'}",
        data_dir=data_dir,
        out_dir=tmp_path / "out",
        artifacts_dir=tmp_path / "artifacts",
        agentic_auto_monitor_enabled=True,
        agentic_auto_monitor_cadence_seconds=0.05,
    )
    with TestClient(create_app(settings)) as client:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = client.get("/api/v1/agentic/monitoring").json()["data"]
            if state["state"] == "error":
                break
            time.sleep(0.02)
        assert state["state"] == "error"
        assert state["last_error"] == "transactions dataset is unavailable"
        audit = client.get("/api/v1/agentic/audit").json()["data"]
        assert sum(event["action"] == "monitor.scan_failed" for event in audit) == 1

        _write_fixture_transactions(data_dir)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get("/api/v1/agentic/monitoring").json()["data"]
            if state["processed_days"] >= 1:
                break
            time.sleep(0.02)
        assert state["processed_days"] >= 1
        assert state["last_error"] is None


def test_two_monitor_instances_persist_only_one_automatic_scan_per_day(tmp_path: Path) -> None:
    _write_fixture_transactions(tmp_path / "data")
    first, first_repository = _build_monitor(tmp_path)
    second, second_repository = _build_monitor(tmp_path)
    barrier = Barrier(2)
    for repository in (first_repository, second_repository):
        original_list = repository.list_auto_monitoring_scans

        def synchronized_list(original=original_list):  # type: ignore[no-untyped-def]
            scans = original()
            if not scans:
                barrier.wait(timeout=5)
            return scans

        repository.list_auto_monitoring_scans = synchronized_list  # type: ignore[method-assign]

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_first = pool.submit(first.tick_once)
        future_second = pool.submit(second.tick_once)
        results = [future_first.result(timeout=10), future_second.result(timeout=10)]

    assert all(scan is not None for scan in results)
    assert results[0]["id"] == results[1]["id"]
    assert len(first_repository.list_auto_monitoring_scans()) == 1


def test_schema_upgrade_backfills_claims_from_existing_auto_scans(tmp_path: Path) -> None:
    _write_fixture_transactions(tmp_path / "data")
    database = Database(f"sqlite:///{tmp_path / 'upgrade.db'}")
    database.create_schema()
    repository = MoneyGraphRepository(database.session_factory)
    service = AgenticLoopService(repository, tmp_path / "data", actor="analyst")
    service.run_scan(pd.Timestamp("2026-07-01").date(), actor="auto-monitor")
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE auto_monitor_claims")

    database.create_schema()

    with database.engine.connect() as connection:
        claims = connection.exec_driver_sql("SELECT replay_date FROM auto_monitor_claims").all()
    assert claims == [("2026-07-01",)]
    assert len(repository.list_auto_monitoring_scans()) == 1


def test_manual_replay_can_still_repeat_the_same_day(tmp_path: Path) -> None:
    _write_fixture_transactions(tmp_path / "data")
    database = Database(f"sqlite:///{tmp_path / 'manual.db'}")
    database.create_schema()
    service = AgenticLoopService(
        MoneyGraphRepository(database.session_factory), tmp_path / "data", actor="analyst"
    )

    first = service.run_scan(pd.Timestamp("2026-07-01").date())
    second = service.run_scan(pd.Timestamp("2026-07-01").date())

    assert first["id"] != second["id"]
