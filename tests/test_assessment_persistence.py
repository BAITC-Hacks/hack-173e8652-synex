from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from moneygraph.repository.database import Database
from moneygraph.repository.repositories import (
    MoneyGraphRepository,
    MonitoringAlertNotFoundError,
)


@pytest.fixture
def repository(tmp_path: Path) -> MoneyGraphRepository:
    database = Database(f"sqlite:///{tmp_path / 'assessment.db'}")
    database.create_schema()
    return MoneyGraphRepository(database.session_factory)


def _alert(repository: MoneyGraphRepository, **ai_facts: Any) -> dict[str, Any]:
    scan = repository.create_monitoring_scan(
        replay_date=date(2026, 7, 2),
        interval_minutes=15,
        summary={},
        alerts=[
            {
                "gid": "collector",
                "rule_keys": ["daily_unique_payers"],
                "facts": {"daily_unique_payers": 8, **ai_facts},
                "role": "consolidator",
                "cluster_id": "2",
                "priority_score": 0.8,
                "explanation": "Eight observed payers.",
            }
        ],
        actor="auto-monitor",
    )
    return scan["alerts"][0]


def _proposals(rationale: str = "Source evidence.") -> list[dict[str, Any]]:
    return [
        {
            "action_key": key,
            "rationale": rationale,
            "recommendation_score": 0.7,
            "expected_outcome": "Local artifact only.",
        }
        for key in (
            "prepare_aml_review_draft",
            "build_money_route",
            "create_local_watchlist",
        )
    ]


def test_assessment_merge_preserves_source_facts_and_action_identity(
    repository: MoneyGraphRepository,
) -> None:
    alert = _alert(repository)
    original = repository.create_action_proposals(
        alert_id=alert["id"], proposals=_proposals(), actor="auto-monitor"
    )["actions"]

    updated = repository.update_alert_assessment(
        alert["id"],
        {
            "daily_unique_payers": 999,
            "ai_narrative": "Review the observed consolidation hypothesis.",
            "ai_assessment": {"hypotheses": ["Possible consolidation"]},
            "ai_provider": "openai",
            "ai_model": "gpt-4o-mini",
            "ai_status": "success",
            "ai_cached": False,
            "ai_usage": {"input_tokens": 32, "output_tokens": 14},
            "ai_retry_after": 0.0,
            "untrusted_note": "Must not enter facts.",
        },
        _proposals("Check the eight observed payers."),
    )

    assert updated["facts"]["daily_unique_payers"] == 8
    assert "untrusted_note" not in updated["facts"]
    assert updated["facts"]["ai_status"] == "success"
    assert updated["facts"]["ai_retry_after"] == 0.0
    assert {item["id"] for item in updated["actions"]} == {item["id"] for item in original}
    assert all(item["status"] == "proposed" for item in updated["actions"])
    assert all(item["decision"] is None for item in updated["actions"])
    assert all(
        item["rationale"] == "Check the eight observed payers." for item in updated["actions"]
    )
    assert repository.get_monitoring_alert(alert["id"])["facts"] == updated["facts"]
    event = repository.list_agentic_audit_events(limit=100, offset=0)["items"][-1]
    assert event["action"] == "ai.assessment_completed"
    assert event["details"]["input_tokens"] == 32
    assert event["details"]["output_tokens"] == 14


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_assessment_never_rewrites_any_part_of_a_decided_case(
    repository: MoneyGraphRepository, decision: str
) -> None:
    alert = _alert(repository)
    actions = repository.create_action_proposals(
        alert_id=alert["id"], proposals=_proposals(), actor="auto-monitor"
    )["actions"]
    repository.decide_agentic_action(
        action_id=actions[0]["id"],
        decision=decision,  # type: ignore[arg-type]
        confirmation="APPROVE" if decision == "approve" else None,
        idempotency_key="decision-once",
        actor="analyst",
        effect={"result": {"submitted": False, "external_effects": []}},
    )
    before = repository.get_monitoring_alert(alert["id"])
    audit_before = repository.list_agentic_audit_events(limit=100, offset=0)["total"]

    updated = repository.update_alert_assessment(
        alert["id"], {"ai_status": "success", "ai_narrative": "Changed"}, _proposals("Changed")
    )

    assert updated == before
    assert repository.list_agentic_audit_events(limit=100, offset=0)["total"] == audit_before


@pytest.mark.parametrize("invalid", [["block_account"], ["build_money_route"] * 3, []])
def test_invalid_actions_cannot_partially_update_assessment(
    repository: MoneyGraphRepository, invalid: list[str]
) -> None:
    alert = _alert(repository)
    with pytest.raises(ValueError, match="allowlisted"):
        repository.update_alert_assessment(
            alert["id"],
            {"ai_status": "success"},
            [{"action_key": key, "rationale": "Unsafe"} for key in invalid],
        )
    assert repository.get_monitoring_alert(alert["id"])["facts"] == {"daily_unique_payers": 8}


@pytest.mark.parametrize(
    ("status", "event_action"),
    [
        ("success", "ai.assessment_completed"),
        ("cache_hit", "ai.assessment_completed"),
        ("budget_exhausted", "ai.assessment_fallback"),
    ],
)
def test_scan_journals_ai_enrichment_with_safe_metadata_only(
    repository: MoneyGraphRepository, status: str, event_action: str
) -> None:
    alert = _alert(
        repository,
        ai_status=status,
        ai_provider="openai",
        ai_model="gpt-4o-mini",
        ai_cached=status == "cache_hit",
        ai_narrative="Do not put private narrative into audit metadata.",
        ai_usage={"input_tokens": 10, "output_tokens": 5, "api_key": "private-fixture"},
    )
    events = repository.list_agentic_audit_events(limit=100, offset=0)["items"]
    assert [event["action"] for event in events] == [
        "monitor.scan_completed",
        "alert.created",
        event_action,
    ]
    assert events[-1]["entity_id"] == alert["id"]
    assert events[-1]["details"] == {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "status": status,
        "cached": status == "cache_hit",
        "input_tokens": 10,
        "output_tokens": 5,
    }


def test_assessment_missing_alert_and_no_ai_fields_have_no_audit_side_effect(
    repository: MoneyGraphRepository,
) -> None:
    with pytest.raises(MonitoringAlertNotFoundError):
        repository.update_alert_assessment("missing", {"ai_status": "success"})
    alert = _alert(repository)
    updated = repository.update_alert_assessment(alert["id"], {"daily_unique_payers": 99})
    assert updated["facts"] == {"daily_unique_payers": 8}
    assert repository.list_agentic_audit_events(limit=100, offset=0)["total"] == 2


def test_ai_metadata_never_leaks_freeform_provider_status_or_usage(
    repository: MoneyGraphRepository,
) -> None:
    _alert(
        repository,
        ai_status="unexpected private status",
        ai_provider="private-token",
        ai_model="private model secret",
        ai_usage={"input_tokens": -10, "output_tokens": "private-token"},
    )
    event = repository.list_agentic_audit_events(limit=100, offset=0)["items"][-1]
    assert event["action"] == "ai.assessment_fallback"
    assert event["details"] == {
        "provider": "unknown",
        "model": "unknown",
        "status": "unknown",
        "cached": False,
    }
