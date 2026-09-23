from __future__ import annotations

from typing import Any

from moneygraph.services.agentic_loop import AgenticLoopService
from moneygraph.services.review_document import build_aml_review_document


def _alert(*, with_ai: bool) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "daily_unique_payers": 8,
        "daily_incoming_kzt": 800_000.0,
        "daily_outgoing_kzt": 900_000.0,
        "cumulative_incoming_kzt": 1_000_000.0,
        "cumulative_outgoing_kzt": 900_000.0,
        "pass_through": 0.9,
        "fast_forward_0_2d_ratio": 0.9,
        "latest_transaction_date": "2026-07-02",
        "source_time_granularity": "day",
    }
    if with_ai:
        facts.update(
            {
                "ai_assessment": {
                    "summary": "Проверить гипотезу концентрации входящих переводов.",
                    "limitations": [
                        "Доступны только операции внутри предоставленной выборки.",
                    ],
                    "actions": [
                        {
                            "action_key": "prepare_aml_review_draft",
                            "rationale": "Собрать наблюдаемые признаки в единый review-документ.",
                            "evidence_keys": [
                                "daily_unique_payers",
                                "daily_incoming_kzt",
                            ],
                        }
                    ],
                },
                "ai_provider": "openai",
                "ai_model": "gpt-5-mini",
                "ai_status": "success",
            }
        )
    return {
        "id": "alert-1",
        "gid": "collector-001",
        "replay_date": "2026-07-02",
        "rule_keys": ["daily_unique_payers", "rapid_pass_through_0_2d"],
        "facts": facts,
        "role": "consolidator_candidate",
        "cluster_id": "replay-001",
        "priority_score": 0.88,
        "explanation": "Детерминированные правила выделили узел для проверки.",
        "limitations": [
            "Источник содержит календарные даты без внутридневного времени.",
        ],
        "actions": [
            {
                "action_key": "prepare_aml_review_draft",
                "rationale": "OpenAI · вариант 1. Собрать факты для проверки аналитиком.",
            }
        ],
    }


def test_document_contains_exact_facts_ai_provenance_and_review_rationale() -> None:
    document = build_aml_review_document(
        _alert(with_ai=True),
        action_rationale="OpenAI · вариант 1. Собрать факты для проверки аналитиком.",
    )

    assert "collector-001" in document.title
    assert "2026-07-02" in document.title
    assert "8" in document.markdown
    assert "800 000.00 KZT" in document.markdown
    assert "1 000 000.00 KZT" in document.markdown
    assert "0.9000" in document.markdown
    assert "Проверить гипотезу концентрации входящих переводов." in document.markdown
    assert "Доступны только операции внутри предоставленной выборки." in document.markdown
    assert "openai" in document.markdown
    assert "gpt-5-mini" in document.markdown
    assert "OpenAI · вариант 1. Собрать факты для проверки аналитиком." in document.markdown
    assert "решение и ответственность остаются за аналитиком" in document.markdown


def test_offline_document_is_explicitly_rules_only_without_model_provenance() -> None:
    document = build_aml_review_document(
        _alert(with_ai=False),
        action_rationale="Собрать факты для внутренней проверки.",
    )

    assert "Rules-only" in document.markdown
    assert "AI-оценка не использовалась" in document.markdown
    assert "**AI-провайдер:**" not in document.markdown
    assert "**AI-модель:**" not in document.markdown
    assert "openai" not in document.markdown.lower()
    assert "gpt-5-mini" not in document.markdown


def test_document_never_claims_submission_guilt_or_completed_compliance() -> None:
    document = build_aml_review_document(_alert(with_ai=True), action_rationale="Проверить факты.")
    lowered = document.markdown.lower()

    assert "сообщение отправлено" not in lowered
    assert "нарушение подтверждено" not in lowered
    assert "виновен" not in lowered
    assert "требования комплаенса выполнены" not in lowered
    assert "внешняя отправка не выполнялась" in lowered
    assert "не является выводом о нарушении" in lowered


def test_approved_draft_effect_embeds_document_and_useful_investigation_text() -> None:
    service = object.__new__(AgenticLoopService)

    effect = service._build_effect("prepare_aml_review_draft", _alert(with_ai=True))
    result = effect["result"]
    investigation = effect["investigation"]

    assert result["submitted"] is False
    assert result["external_effects"] == []
    assert "# Внутренний черновик AML-review" in result["document_markdown"]
    assert "800 000.00 KZT" in result["document_markdown"]
    assert "Проверить гипотезу концентрации" in investigation["description"]
    assert "8 уникальных плательщиков" in investigation["description"]
    assert "Внешняя отправка не выполнялась" in investigation["note"]
