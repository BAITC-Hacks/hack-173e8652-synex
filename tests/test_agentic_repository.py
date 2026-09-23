from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from moneygraph.repository.database import Database
from moneygraph.repository.repositories import (
    ALLOWED_AGENTIC_ACTIONS,
    AgenticStateConflictError,
    IdempotencyConflictError,
    MoneyGraphRepository,
)


@pytest.fixture
def repository(tmp_path: Path) -> MoneyGraphRepository:
    database = Database(f"sqlite:///{tmp_path / 'agentic.db'}")
    database.create_schema()
    return MoneyGraphRepository(database.session_factory)


def _create_alert(repository: MoneyGraphRepository) -> dict[str, object]:
    scan = repository.create_monitoring_scan(
        replay_date=date(2026, 7, 2),
        interval_minutes=15,
        summary={"transactions_seen": 10, "alerts_created": 1},
        alerts=[
            {
                "gid": "collector",
                "rule_keys": ["daily_unique_payers"],
                "facts": {"daily_unique_payers": 8, "daily_incoming_kzt": 800_000.0},
                "role": "consolidator",
                "cluster_id": "replay-1",
                "priority_score": 0.75,
                "explanation": "8 уникальных плательщиков за выбранный день.",
            }
        ],
        actor="rules-engine",
    )
    return scan["alerts"][0]


def _propose(repository: MoneyGraphRepository, alert_id: str) -> list[dict[str, object]]:
    proposals = [
        {
            "action_key": action_key,
            "recommendation_score": score,
            "rationale": f"Почему {action_key}",
            "expected_outcome": f"Результат {action_key}",
        }
        for score, action_key in zip((0.9, 0.8, 0.7), sorted(ALLOWED_AGENTIC_ACTIONS), strict=True)
    ]
    return repository.create_action_proposals(
        alert_id=alert_id,
        proposals=proposals,
        actor="copilot",
    )["actions"]


def test_scan_and_proposals_append_every_transition_to_audit(
    repository: MoneyGraphRepository,
) -> None:
    alert = _create_alert(repository)

    actions = _propose(repository, str(alert["id"]))
    audit = repository.list_agentic_audit_events(limit=50, offset=0)

    assert {action["action_key"] for action in actions} == ALLOWED_AGENTIC_ACTIONS
    assert len(actions) == 3
    assert [event["action"] for event in audit["items"]] == [
        "monitor.scan_completed",
        "alert.created",
        "actions.proposed",
    ]


def test_repository_rejects_non_allowlisted_action(repository: MoneyGraphRepository) -> None:
    alert = _create_alert(repository)

    with pytest.raises(ValueError, match="Unsupported agentic action"):
        repository.create_action_proposals(
            alert_id=str(alert["id"]),
            proposals=[
                {
                    "action_key": "block_account",
                    "recommendation_score": 1.0,
                    "rationale": "unsafe",
                    "expected_outcome": "unsafe",
                }
            ],
            actor="copilot",
        )


def test_approved_action_is_atomic_and_idempotent(repository: MoneyGraphRepository) -> None:
    alert = _create_alert(repository)
    actions = _propose(repository, str(alert["id"]))
    draft = next(action for action in actions if action["action_key"] == "prepare_aml_review_draft")
    effect = {
        "result": {
            "kind": "aml_review_draft",
            "submitted": False,
            "message": "Локальный черновик; во внешние системы не отправлен.",
        },
        "investigation": {
            "title": "AML review: collector",
            "description": "Draft generated from observed replay facts.",
            "model_version": "agentic-rules-v1",
            "gids": ["collector"],
            "note": "Черновик требует проверки аналитиком.",
        },
    }

    effect_calls = 0

    def build_effect() -> dict[str, object]:
        nonlocal effect_calls
        effect_calls += 1
        return effect

    first = repository.decide_agentic_action(
        action_id=str(draft["id"]),
        decision="approve",
        confirmation="APPROVE",
        idempotency_key="idem-draft-1",
        actor="analyst-a",
        effect=build_effect,
    )
    replay = repository.decide_agentic_action(
        action_id=str(draft["id"]),
        decision="approve",
        confirmation="APPROVE",
        idempotency_key="idem-draft-1",
        actor="analyst-a",
        effect=build_effect,
    )

    assert first == replay
    assert effect_calls == 1
    assert first["status"] == "executed"
    assert first["result"]["submitted"] is False
    assert first["investigation_id"]
    assert repository.list_investigations(limit=20, offset=0)["total"] == 1
    assert [
        event["action"]
        for event in repository.list_agentic_audit_events(limit=50, offset=0)["items"]
    ][-2:] == ["action.approved", "tool.executed"]


def test_decision_requires_confirmation_and_key_cannot_change_payload(
    repository: MoneyGraphRepository,
) -> None:
    alert = _create_alert(repository)
    actions = _propose(repository, str(alert["id"]))
    route = next(action for action in actions if action["action_key"] == "build_money_route")

    with pytest.raises(AgenticStateConflictError, match="APPROVE"):
        repository.decide_agentic_action(
            action_id=str(route["id"]),
            decision="approve",
            confirmation="yes",
            idempotency_key="idem-route-1",
            actor="analyst-a",
            effect={"result": {"kind": "money_route"}},
        )

    rejected = repository.decide_agentic_action(
        action_id=str(route["id"]),
        decision="reject",
        confirmation=None,
        idempotency_key="idem-route-1",
        actor="analyst-a",
        effect=None,
    )
    assert rejected["status"] == "rejected"

    other = next(action for action in actions if action["action_key"] == "create_local_watchlist")
    with pytest.raises(IdempotencyConflictError):
        repository.decide_agentic_action(
            action_id=str(other["id"]),
            decision="reject",
            confirmation=None,
            idempotency_key="idem-route-1",
            actor="analyst-a",
            effect=None,
        )


def test_failed_approved_tool_is_audited_without_partial_effect(
    repository: MoneyGraphRepository,
) -> None:
    alert = _create_alert(repository)
    action = next(
        item
        for item in _propose(repository, str(alert["id"]))
        if item["action_key"] == "prepare_aml_review_draft"
    )

    def fail_tool() -> dict[str, object]:
        raise RuntimeError("synthetic tool failure")

    result = repository.decide_agentic_action(
        action_id=str(action["id"]),
        decision="approve",
        confirmation="APPROVE",
        idempotency_key="idem-failed-tool",
        actor="analyst-a",
        effect=fail_tool,
    )

    assert result["status"] == "failed"
    assert result["result"]["executed"] is False
    assert result["investigation_id"] is None
    audit_actions = [
        event["action"]
        for event in repository.list_agentic_audit_events(limit=50, offset=0)["items"]
    ]
    assert audit_actions[-2:] == ["action.approved", "action.failed"]
    assert "tool.executed" not in audit_actions
