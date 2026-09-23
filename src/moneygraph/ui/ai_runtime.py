"""Presentation helpers for measured AI activity, never inferred availability."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from moneygraph.ai.agentic_prompts import SAFE_ACTION_KEYS, SAFE_FACT_KEYS

AI_FAILURE_MESSAGES = {
    "sdk_missing": "Не установлены зависимости OpenAI SDK в окружении API; установите AI-зависимости проекта.",
    "authentication_failed": "OpenAI отклонил ключ: проверьте новый действующий ключ в локальном .env и перезапустите API.",
    "insufficient_quota": "OpenAI сообщил об исчерпанной квоте: проверьте баланс и лимиты выбранного API-проекта.",
    "rate_limited": "OpenAI ограничил частоту запросов; новые вызовы временно отложены.",
    "invalid_request": "OpenAI отклонил параметры запроса; проверьте доступ к модели и её поддержку JSON Schema.",
    "timeout": "Истекло время ожидания OpenAI; ответ не получен, повтор будет ограничен политикой запросов.",
    "provider_unavailable": "AI-провайдер недоступен или ответ не прошёл проверку; вывод модели не используется.",
    "budget_exhausted": "Дневной лимит AI-вызовов исчерпан; новый разбор отложен до следующего периода бюджета.",
    "state_unavailable": "Учёт AI-бюджета недоступен; платные вызовы остановлены для защиты лимита.",
    "cooldown": "После ошибки AI действует пауза; новый запрос будет возможен после её окончания.",
    "in_flight": "Разбор этого кейса уже выполняется; повторный AI-запрос не запускается.",
    "context_too_large": "Контекст кейса превышает безопасный лимит; требуется уменьшить объём запроса.",
}


def runtime_status_view(status: Mapping[str, Any]) -> dict[str, Any]:
    configured = status.get("configured") is True
    verified = configured and bool(status.get("last_success_at"))
    error = bool(status.get("last_error")) or status.get("ai_status") == "state_unavailable"
    provider = str(status.get("provider") or "LLM")
    if not configured:
        level, message = "warning", "LLM отключён или не настроен: работают правила и графовая аналитика."
    elif error:
        level = "warning"
        reason = status.get("last_error_code") or status.get("ai_status")
        message = AI_FAILURE_MESSAGES.get(str(reason), AI_FAILURE_MESSAGES["provider_unavailable"])
        message += " Пока работают правила и графовая аналитика."
    elif status.get("daily_call_limit") is not None and not _count(status.get("remaining_calls")):
        level = "warning"
        message = "Дневной лимит AI-вызовов исчерпан: доступны кэш и детерминированные правила."
    elif verified:
        level, message = "success", f"{provider}: успешный ответ модели подтверждён журналом вызовов."
    else:
        level = "info"
        message = f"{provider} настроен; успешный запрос к модели ещё не подтверждён."
    return {
        "verified": verified,
        "level": level,
        "message": message,
        "provider": provider,
        "model": str(status.get("model") or "—"),
        "successful_calls": _count(status.get("successful_calls")),
        "cache_hits": _count(status.get("cache_hits")),
        "tokens": _count(status.get("input_tokens")) + _count(status.get("output_tokens")),
        "remaining_calls": _count(status.get("remaining_calls")),
        "last_success_at": str(status.get("last_success_at") or "нет"),
        "period": "Счётчики за текущие сутки UTC; токены — фактический usage провайдера.",
    }


def render_ai_runtime(status: Mapping[str, Any]) -> None:
    view = runtime_status_view(status)
    with st.container(border=True):
        st.markdown("#### Реальная работа ИИ")
        getattr(st, view["level"])(view["message"])
        st.caption(f"Модель: {view['model']} · последний успешный ответ: {view['last_success_at']}")
        columns = st.columns(4)
        for column, label, key in zip(
            columns,
            ("Ответов модели", "Ответов из кэша", "Токенов", "Вызовов осталось"),
            ("successful_calls", "cache_hits", "tokens", "remaining_calls"),
            strict=True,
        ):
            column.metric(label, view[key])
        st.caption(view["period"])


def assessment_view(facts: Mapping[str, Any]) -> dict[str, Any] | None:
    assessment = facts.get("ai_assessment")
    if not isinstance(assessment, Mapping):
        return None
    summary = assessment.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    raw_actions = assessment.get("actions", [])
    actions = {}
    for action in raw_actions if isinstance(raw_actions, list) else []:
        if not isinstance(action, Mapping) or action.get("action_key") not in SAFE_ACTION_KEYS:
            continue
        evidence = action.get("evidence_keys", [])
        actions[str(action["action_key"])] = {
            "rationale": str(action.get("rationale") or "")[:1500],
            "evidence_keys": [key for key in evidence if key in SAFE_FACT_KEYS]
            if isinstance(evidence, list)
            else [],
        }
    limitations = assessment.get("limitations", [])
    return {
        "summary": summary[:2400],
        "provider": str(facts.get("ai_provider") or "LLM"),
        "model": str(facts.get("ai_model") or "—"),
        "cached": facts.get("ai_cached") is True,
        "actions": actions,
        "limitations": [item[:700] for item in limitations[:6] if isinstance(item, str)]
        if isinstance(limitations, list)
        else [],
    }


def render_assessment(facts: Mapping[str, Any], *, compact: bool = False) -> None:
    assessment = assessment_view(facts)
    if assessment is None:
        reason = AI_FAILURE_MESSAGES.get(str(facts.get("ai_status", "")))
        if reason:
            st.warning(f"{reason} Для этого кейса пока показаны правила.")
        else:
            st.caption("Кейс ещё не получил проверенный разбор модели; пока показаны правила.")
        return
    source = "сохранённый ответ из кэша" if assessment["cached"] else "сохранённый ответ модели"
    st.info(f"Разбор {assessment['provider']}: {assessment['summary']}")
    st.caption(f"{assessment['model']} · {source}. Гипотеза, а не решение о нарушении.")
    if not compact:
        for limitation in assessment["limitations"]:
            st.caption(f"Ограничение ИИ: {limitation}")


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0
