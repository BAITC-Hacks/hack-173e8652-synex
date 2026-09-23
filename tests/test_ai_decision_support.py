from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from moneygraph.services.ai_decision_support import assess_case


@pytest.mark.parametrize("status", ["sdk_missing", "authentication_failed", "insufficient_quota", "rate_limited", "invalid_request", "timeout"])
def test_actionable_provider_failures_survive_case_storage(status: str) -> None:
    provider = SimpleNamespace(assess_alert=lambda _: {"fallback": True, "ai_status": status})
    result = assess_case(provider, {"facts": {"daily_unique_payers": 8}})
    assert result["facts"]["ai_status"] == status
    assert "ai_assessment" not in result["facts"]


def test_budget_retry_waits_for_next_utc_day() -> None:
    provider = SimpleNamespace(
        assess_alert=lambda _: {
            "fallback": True,
            "ai_status": "budget_exhausted",
        }
    )
    start = time.time()
    result = assess_case(provider, {"facts": {"daily_unique_payers": 8}})
    expected = (int(start) // 86400 + 1) * 86400
    assert result["facts"]["ai_retry_after"] == expected
    assert result["facts"]["ai_status"] == "budget_exhausted"
    assert "ai_assessment" not in result["facts"]


def test_invalid_assessment_cannot_change_source_or_be_labeled_success() -> None:
    provider = SimpleNamespace(
        assess_alert=lambda _: {
            "fallback": False,
            "assessment": {"actions": [{"action_key": "block_account"}]},
        }
    )
    result = assess_case(provider, {"facts": {"daily_unique_payers": 8}})
    assert result["facts"]["daily_unique_payers"] == 8
    assert result["facts"]["ai_status"] == "provider_unavailable"
    assert "ai_assessment" not in result["facts"]
