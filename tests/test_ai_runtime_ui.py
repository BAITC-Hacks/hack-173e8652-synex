from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from moneygraph.api.routes.assistant import _optional_provider_answer
from moneygraph.ui.ai_runtime import assessment_view, runtime_status_view


def test_runtime_does_not_claim_a_configured_provider_has_successfully_run() -> None:
    view = runtime_status_view({"configured": True, "provider": "openai", "model": "gpt-4o-mini"})

    assert view["verified"] is False
    assert view["level"] == "info"
    assert "ещё не подтверждён" in view["message"]
    assert view["model"] == "gpt-4o-mini"


def test_runtime_shows_actual_token_usage_cache_and_previous_success() -> None:
    view = runtime_status_view(
        {
            "configured": True,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "last_success_at": "2026-09-23T12:00:00+00:00",
            "successful_calls": 3,
            "cache_hits": 7,
            "input_tokens": 1200,
            "output_tokens": 350,
            "remaining_calls": 17,
        }
    )

    assert view["verified"] is True
    assert view["level"] == "success"
    assert view["successful_calls"] == 3
    assert view["cache_hits"] == 7
    assert view["tokens"] == 1550
    assert view["remaining_calls"] == 17
    assert "UTC" in view["period"]


@pytest.mark.parametrize("status", [{}, {"configured": False, "state": "disabled"}])
def test_runtime_offline_fallback_is_never_presented_as_ai(status: dict[str, object]) -> None:
    view = runtime_status_view(status)

    assert view["verified"] is False
    assert view["level"] == "warning"
    assert "правила" in view["message"]


def test_runtime_reports_error_without_exposing_raw_provider_exception() -> None:
    view = runtime_status_view(
        {
            "configured": True,
            "provider": "openai",
            "last_error": "remote debug text with a sensitive credential",
            "ai_status": "state_unavailable",
            "input_tokens": "bad data",
            "output_tokens": -8,
        }
    )

    assert view["level"] == "warning"
    assert view["tokens"] == 0
    assert "sensitive credential" not in str(view)
    assert "недоступ" in view["message"]


def test_assessment_marks_cached_case_analysis_and_preserves_evidence_links() -> None:
    view = assessment_view(
        {
            "ai_provider": "openai",
            "ai_model": "gpt-4o-mini",
            "ai_cached": True,
            "ai_assessment": {
                "summary": "Наблюдается концентрация входящего потока.",
                "limitations": ["Время операции доступно только с точностью до дня."],
                "actions": [
                    {
                        "action_key": "build_money_route",
                        "rationale": "Проверить дальнейший наблюдаемый поток.",
                        "evidence_keys": ["daily_incoming_kzt", "pass_through"],
                    }
                ],
            },
        }
    )

    assert view is not None
    assert view["cached"] is True
    assert view["provider"] == "openai"
    assert view["actions"]["build_money_route"]["evidence_keys"] == [
        "daily_incoming_kzt", "pass_through"
    ]
    assert "точностью до дня" in view["limitations"][0]


@pytest.mark.parametrize("facts", [{}, {"ai_assessment": "bad"}, {"ai_assessment": {"summary": ""}}])
def test_missing_assessment_does_not_imply_llm_has_reviewed_the_case(facts: dict[str, object]) -> None:
    assert assessment_view(facts) is None


def test_sync_provider_runs_off_the_api_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    caller_thread = threading.get_ident()

    def answer(query: str, context: dict[str, object]) -> dict[str, object]:
        return {"answer": str(threading.get_ident()), "provider": "test", "fallback": False}

    monkeypatch.setattr("moneygraph.ai.factory.build_provider", lambda: SimpleNamespace(answer=answer))
    result = asyncio.run(_optional_provider_answer("query", {"limitations": []}))

    assert result is not None
    assert result["answer"] != str(caller_thread)


def test_async_test_provider_remains_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def answer(query: str, context: dict[str, object]) -> dict[str, object]:
        return {"answer": "async response", "provider": "test", "fallback": False}

    monkeypatch.setattr("moneygraph.ai.factory.build_provider", lambda: SimpleNamespace(answer=answer))
    result = asyncio.run(_optional_provider_answer("query", {"limitations": []}))

    assert result is not None
    assert result["answer"] == "async response"


def test_runtime_panel_renders_recorded_activity_and_cached_assessment() -> None:
    app = AppTest.from_string(
        '''
from moneygraph.ui.ai_runtime import render_ai_runtime, render_assessment
render_ai_runtime({
    "configured": True, "provider": "openai", "model": "gpt-4o-mini",
    "last_success_at": "2026-09-23T12:00:00+00:00", "successful_calls": 2,
    "cache_hits": 4, "input_tokens": 100, "output_tokens": 20, "remaining_calls": 8,
})
render_assessment({
    "ai_provider": "openai", "ai_model": "gpt-4o-mini", "ai_cached": True,
    "ai_assessment": {"summary": "Проверить входящий поток.", "actions": [],
                      "limitations": ["Неполная выборка."]},
})
'''
    ).run()

    assert not app.exception
    assert [metric.value for metric in app.metric] == ["2", "4", "120", "8"]
    assert "подтверждён" in app.success[0].value
    assert any("из кэша" in caption.value for caption in app.caption)
    assert any("Неполная выборка" in caption.value for caption in app.caption)
