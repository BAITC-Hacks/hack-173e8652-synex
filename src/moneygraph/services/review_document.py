"""Build a local, grounded AML-review draft from an already persisted alert."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

ANALYST_CHECKS = (
    "Сверить входящие операции с полной банковской историей вне demo-выборки.",
    "Проверить KYC-профиль и экономический смысл наблюдаемого потока.",
    "Проверить связанных плательщиков, получателей и возможные повторяющиеся маршруты.",
    "Зафиксировать собственное решение аналитика и основание approve/reject.",
)
DAY_LEVEL_LIMITATION = (
    "Источник содержит только календарную дату: внутридневной dwell и порядок операций "
    "внутри одного дня недоступны."
)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class AMLReviewDocument:
    """Finished local artifact plus concise text suitable for an investigation record."""

    title: str
    markdown: str
    investigation_description: str
    investigation_note: str
    model_version: str


def _safe_text(value: Any, *, limit: int = 1_200) -> str:
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    return text.replace("<", "&lt;").replace(">", "&gt;")[:limit]


def _format_amount(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "нет данных"
    if not math.isfinite(number):
        return "нет данных"
    return f"{number:,.2f}".replace(",", " ") + " KZT"


def _format_ratio(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "нет данных"
    if not math.isfinite(number):
        return "нет данных"
    return f"{number:.4f}"


def _format_count(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError, OverflowError):
        return "нет данных"


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value if (text := _safe_text(item, limit=500))]


def _selected_action_rationale(alert: Mapping[str, Any]) -> str:
    actions = alert.get("actions", [])
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
        return ""
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        if action.get("action_key") == "prepare_aml_review_draft":
            return _safe_text(action.get("rationale"), limit=1_000)
    return ""


def _ai_section(facts: Mapping[str, Any]) -> tuple[list[str], str, list[str], str]:
    assessment = facts.get("ai_assessment")
    if not isinstance(assessment, Mapping):
        return (
            [
                "### Rules-only гипотеза",
                "AI-оценка не использовалась. Раздел сформирован детерминированными правилами.",
            ],
            "Rules-only: детерминированные правила выделили узел для проверки.",
            [],
            "agentic-rules-v1",
        )

    summary = _safe_text(assessment.get("summary"), limit=1_200)
    provider = _safe_text(facts.get("ai_provider"), limit=100)
    model = _safe_text(facts.get("ai_model"), limit=100)
    status = _safe_text(facts.get("ai_status"), limit=60)
    if not summary:
        return (
            [
                "### Rules-only гипотеза",
                "AI-оценка не использовалась: сохранённый assessment не содержит вывода.",
            ],
            "Rules-only: детерминированные правила выделили узел для проверки.",
            [],
            "agentic-rules-v1",
        )

    lines = ["### Сохранённая AI-гипотеза", summary]
    if provider:
        lines.append(f"- **AI-провайдер:** `{provider}`")
    if model:
        lines.append(f"- **AI-модель:** `{model}`")
    if status:
        lines.append(f"- **Статус assessment:** `{status}`")
    provenance = ":".join(part for part in (provider, model) if part)
    version = f"agentic-rules-v1+{provenance}"[:128] if provenance else "agentic-rules-v1"
    return lines, summary, _text_items(assessment.get("limitations")), version


def build_aml_review_document(
    alert: Mapping[str, Any],
    *,
    action_rationale: str | None = None,
) -> AMLReviewDocument:
    """Render a factual internal draft without performing or claiming an external action."""

    gid = _safe_text(alert.get("gid"), limit=128).replace("`", "'") or "unknown"
    replay_date = _safe_text(alert.get("replay_date"), limit=32) or "не указана"
    role = _safe_text(alert.get("role"), limit=80) or "не определена"
    cluster_id = _safe_text(alert.get("cluster_id"), limit=128) or "не определён"
    facts_raw = alert.get("facts", {})
    facts = facts_raw if isinstance(facts_raw, Mapping) else {}
    priority = _format_ratio(alert.get("priority_score"))
    rules = _text_items(alert.get("rule_keys"))
    rules_text = ", ".join(f"`{rule}`" for rule in rules) or "нет"
    rationale = _safe_text(action_rationale, limit=1_000) or _selected_action_rationale(alert)
    if not rationale:
        rationale = "Собрать наблюдаемые факты и ограничения для проверки аналитиком."

    ai_lines, hypothesis, ai_limitations, model_version = _ai_section(facts)
    limitations = [
        DAY_LEVEL_LIMITATION,
        *_text_items(alert.get("limitations")),
        *ai_limitations,
        "Вывод ограничен предоставленным набором данных и не использует полную историю клиента.",
    ]
    limitations = list(dict.fromkeys(limitations))

    title = f"Внутренний черновик AML-review — GID {gid} — replay {replay_date}"
    markdown_lines = [
        f"# {title}",
        "",
        "> **Статус:** локальный внутренний черновик. Внешняя отправка не выполнялась. "
        "Документ не является выводом о нарушении; решение и ответственность остаются за аналитиком.",
        "",
        "## Контекст alert",
        "",
        f"- **GID:** `{gid}`",
        f"- **Дата demo replay:** `{replay_date}`",
        f"- **Роль-гипотеза:** `{role}`",
        f"- **Кластер:** `{cluster_id}`",
        f"- **Priority score:** `{priority}` — приоритет следующей проверки, не вероятность нарушения",
        f"- **Сработавшие правила:** {rules_text}",
        "",
        "## Наблюдаемые факты",
        "",
        "| Метрика | Фактическое значение |",
        "|---|---:|",
        f"| Уникальные плательщики за день | {_format_count(facts.get('daily_unique_payers'))} |",
        f"| Входящий поток за день | {_format_amount(facts.get('daily_incoming_kzt'))} |",
        f"| Исходящий поток за день | {_format_amount(facts.get('daily_outgoing_kzt'))} |",
        f"| Накопленный входящий поток к replay date | {_format_amount(facts.get('cumulative_incoming_kzt'))} |",
        f"| Накопленный исходящий поток к replay date | {_format_amount(facts.get('cumulative_outgoing_kzt'))} |",
        f"| Pass-through ratio | {_format_ratio(facts.get('pass_through'))} |",
        f"| Перенаправление за 0–2 дня | {_format_ratio(facts.get('fast_forward_0_2d_ratio'))} |",
        f"| Последняя наблюдаемая дата | {_safe_text(facts.get('latest_transaction_date'), limit=32) or 'нет данных'} |",
        "",
        "## Гипотеза и provenance",
        "",
        *ai_lines,
        "",
        "## Предложенный следующий шаг",
        "",
        f"**`prepare_aml_review_draft`:** {rationale}",
        "",
        "Инструмент только подготовил этот локальный материал. Он не блокировал счета, "
        "не менял операции и не создавал внешних сообщений.",
        "",
        "## Что должен проверить аналитик",
        "",
        *(f"- [ ] {check}" for check in ANALYST_CHECKS),
        "",
        "## Ограничения",
        "",
        *(f"- {item}" for item in limitations),
        "",
        "## Решение аналитика",
        "",
        "- Решение: `не зафиксировано`",
        "- Обоснование: `заполняется аналитиком`",
        "- Следующий контроль: `заполняется аналитиком`",
    ]
    markdown = "\n".join(markdown_lines).strip() + "\n"

    description = (
        f"Replay {replay_date}: узел {gid} выделен как {role}; "
        f"{_format_count(facts.get('daily_unique_payers'))} уникальных плательщиков, "
        f"входящий поток за день {_format_amount(facts.get('daily_incoming_kzt'))}. "
        f"Гипотеза: {hypothesis} Документ остаётся внутренним черновиком для решения аналитика."
    )
    note = (
        f"Подготовлен review-документ по GID {gid}. Внешняя отправка не выполнялась. "
        "Проверить полную историю, KYC-контекст, связанных участников и зафиксировать "
        "решение аналитика."
    )
    return AMLReviewDocument(
        title=title,
        markdown=markdown,
        investigation_description=description,
        investigation_note=note,
        model_version=model_version,
    )
