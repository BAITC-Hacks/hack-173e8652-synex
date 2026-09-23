from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from moneygraph.api.main import create_app
from moneygraph.api.settings import APISettings

TARGET_GID = "100000000000009999"
PAYER_GIDS = [f"1000000000000000{index:02d}" for index in range(1, 9)]


def _write_transactions(data_dir: Path) -> None:
    data_dir.mkdir()
    rows = [
        {
            "src": payer,
            "dst": TARGET_GID,
            "date": "2026-07-03",
            "sum_kzt": 150_000.0,
        }
        for payer in PAYER_GIDS
    ]
    rows.append(
        {
            "src": TARGET_GID,
            "dst": "100000000000008888",
            "date": "2026-07-03",
            "sum_kzt": 1_164_000.0,
        }
    )
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    frame.to_parquet(data_dir / "transactions.parquet", index=False)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    data_dir = tmp_path / "data"
    _write_transactions(data_dir)
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'agentic.db'}",
        analyst_name="pytest-analyst",
        cors_origins=("http://localhost:8501",),
        data_dir=data_dir,
        out_dir=tmp_path / "out",
        artifacts_dir=tmp_path / "artifacts",
        agentic_auto_monitor_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _create_scan(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/agentic/scans",
        json={"replay_date": "2026-07-03", "interval_minutes": 5, "limit": 10},
    )
    assert response.status_code == 201
    return response.json()["data"]


def _create_proposals(client: TestClient) -> dict[str, object]:
    scan = _create_scan(client)
    alert = next(item for item in scan["alerts"] if item["gid"] == TARGET_GID)  # type: ignore[index,union-attr]
    response = client.post(f"/api/v1/agentic/alerts/{alert['id']}/proposals")
    assert response.status_code == 201
    return response.json()["data"]


def test_scan_and_readback_expose_honest_day_replay(client: TestClient) -> None:
    scan = _create_scan(client)

    readback = client.get(f"/api/v1/agentic/scans/{scan['id']}")

    assert readback.status_code == 200
    assert readback.json()["data"]["id"] == scan["id"]
    assert scan["replay_date"] == "2026-07-03"
    alert = next(item for item in scan["alerts"] if item["gid"] == TARGET_GID)  # type: ignore[index,union-attr]
    assert alert["gid"] == TARGET_GID
    assert alert["simulation"] is True
    assert alert["source_time_granularity"] == "day"
    assert "daily_unique_payers" in alert["rule_keys"]
    assert alert["facts"]["daily_unique_payers"] == 8
    assert "не вычисляется" in " ".join(alert["limitations"]).lower()


def test_proposals_are_exactly_the_server_allowlist(client: TestClient) -> None:
    result = _create_proposals(client)

    assert len(result["actions"]) == 3
    assert {action["action_key"] for action in result["actions"]} == {
        "prepare_aml_review_draft",
        "build_money_route",
        "create_local_watchlist",
    }
    assert all(action["status"] == "proposed" for action in result["actions"])
    assert all(action["requires_human_approval"] is True for action in result["actions"])
    assert all("recommendation_score" in action for action in result["actions"])


def test_approve_requires_confirmation_and_idempotency_and_executes_once(
    client: TestClient,
) -> None:
    proposals = _create_proposals(client)
    action_id = proposals["actions"][0]["id"]  # type: ignore[index]
    endpoint = f"/api/v1/agentic/actions/{action_id}/decision"

    missing_header = client.post(
        endpoint,
        json={"decision": "approve", "confirmation": "APPROVE"},
    )
    wrong_confirmation = client.post(
        endpoint,
        headers={"Idempotency-Key": "judge-demo-wrong"},
        json={"decision": "approve", "confirmation": "approve"},
    )
    approved = client.post(
        endpoint,
        headers={"Idempotency-Key": "judge-demo-001"},
        json={"decision": "approve", "confirmation": "APPROVE"},
    )
    repeated = client.post(
        endpoint,
        headers={"Idempotency-Key": "judge-demo-001"},
        json={"decision": "approve", "confirmation": "APPROVE"},
    )

    assert missing_header.status_code == 422
    assert missing_header.json()["error"]["code"] == "validation_error"
    assert wrong_confirmation.status_code == 422
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "executed"
    assert approved.json()["data"] == repeated.json()["data"]


def test_reject_never_executes_a_tool(client: TestClient) -> None:
    proposals = _create_proposals(client)
    action_id = proposals["actions"][1]["id"]  # type: ignore[index]

    response = client.post(
        f"/api/v1/agentic/actions/{action_id}/decision",
        json={"decision": "reject"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"
    assert response.json()["data"]["result"]["executed"] is False


def test_audit_lists_every_persisted_loop_transition(client: TestClient) -> None:
    proposals = _create_proposals(client)
    action_id = proposals["actions"][2]["id"]  # type: ignore[index]
    decision = client.post(
        f"/api/v1/agentic/actions/{action_id}/decision",
        headers={"Idempotency-Key": "judge-demo-audit"},
        json={"decision": "approve", "confirmation": "APPROVE"},
    )
    assert decision.status_code == 200

    response = client.get("/api/v1/agentic/audit", params={"limit": 100, "offset": 0})

    assert response.status_code == 200
    assert response.json()["meta"]["total"] >= 5
    actions = [event["action"] for event in response.json()["data"]]
    assert "monitor.scan_completed" in actions
    assert "alert.created" in actions
    assert "actions.proposed" in actions
    assert "action.approved" in actions
    assert "tool.executed" in actions


def test_newest_audit_page_shows_recent_approval_after_more_than_100_events(
    client: TestClient,
) -> None:
    for _ in range(55):
        _create_scan(client)
    proposals = _create_proposals(client)
    action_id = proposals["actions"][0]["id"]  # type: ignore[index]
    decision = client.post(
        f"/api/v1/agentic/actions/{action_id}/decision",
        headers={"Idempotency-Key": "recent-audit-decision"},
        json={"decision": "approve", "confirmation": "APPROVE"},
    )
    assert decision.status_code == 200

    recent = client.get(
        "/api/v1/agentic/audit", params={"limit": 100, "offset": 0, "newest_first": True}
    ).json()
    chronological = client.get("/api/v1/agentic/audit", params={"limit": 100}).json()

    assert recent["meta"]["total"] > 100
    assert [item["action"] for item in recent["data"][:2]] == [
        "tool.executed", "action.approved"
    ]
    assert recent["data"][0]["entity_id"] == action_id
    assert chronological["data"][0]["action"] == "monitor.scan_completed"
    assert all(item["action"] != "tool.executed" for item in chronological["data"])


def test_agentic_validation_rejects_tampering_and_unbounded_inputs(client: TestClient) -> None:
    proposals = _create_proposals(client)
    action_id = proposals["actions"][0]["id"]  # type: ignore[index]

    invalid_date = client.post(
        "/api/v1/agentic/scans",
        json={"replay_date": "03-07-2026", "interval_minutes": 5, "limit": 10},
    )
    unbounded = client.post(
        "/api/v1/agentic/scans",
        json={"replay_date": "2026-07-03", "interval_minutes": 61, "limit": 51},
    )
    tampered = client.post(
        f"/api/v1/agentic/actions/{action_id}/decision",
        headers={"Idempotency-Key": "judge-demo-tamper"},
        json={
            "decision": "approve",
            "confirmation": "APPROVE",
            "tool_args": {"depth": 99},
        },
    )

    assert invalid_date.status_code == 422
    assert unbounded.status_code == 422
    assert tampered.status_code == 422
    assert tampered.json()["error"]["code"] == "validation_error"


def test_unknown_ids_and_conflicts_use_structured_errors(client: TestClient) -> None:
    unknown_scan = client.get("/api/v1/agentic/scans/missing")
    unknown_alert = client.post("/api/v1/agentic/alerts/missing/proposals")
    unknown_action = client.post(
        "/api/v1/agentic/actions/missing/decision",
        headers={"Idempotency-Key": "judge-demo-missing"},
        json={"decision": "reject"},
    )

    proposals = _create_proposals(client)
    action_id = proposals["actions"][0]["id"]  # type: ignore[index]
    endpoint = f"/api/v1/agentic/actions/{action_id}/decision"
    first = client.post(
        endpoint,
        headers={"Idempotency-Key": "judge-demo-conflict"},
        json={"decision": "reject"},
    )
    conflicting = client.post(
        endpoint,
        headers={"Idempotency-Key": "judge-demo-conflict-2"},
        json={"decision": "approve", "confirmation": "APPROVE"},
    )

    assert unknown_scan.status_code == 404
    assert unknown_scan.json()["error"]["code"] == "scan_not_found"
    assert unknown_alert.status_code == 404
    assert unknown_action.status_code == 404
    assert first.status_code == 200
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "agentic_state_conflict"


def test_reusing_idempotency_key_for_another_action_is_a_conflict(client: TestClient) -> None:
    proposals = _create_proposals(client)
    first_action = proposals["actions"][0]["id"]  # type: ignore[index]
    second_action = proposals["actions"][1]["id"]  # type: ignore[index]

    first = client.post(
        f"/api/v1/agentic/actions/{first_action}/decision",
        headers={"Idempotency-Key": "judge-demo-shared"},
        json={"decision": "reject"},
    )
    conflict = client.post(
        f"/api/v1/agentic/actions/{second_action}/decision",
        headers={"Idempotency-Key": "judge-demo-shared"},
        json={"decision": "reject"},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_agentic_cors_allows_idempotency_header(client: TestClient) -> None:
    response = client.options(
        "/api/v1/agentic/actions/example/decision",
        headers={
            "Origin": "http://localhost:8501",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Idempotency-Key, Content-Type",
        },
    )

    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "idempotency-key" in allowed
