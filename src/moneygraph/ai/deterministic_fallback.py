"""Offline, deterministic assistant that never makes an accusation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moneygraph.ai.base import (
    AIResult,
    limitations_from_context,
    minimize_context,
    tools_from_context,
)

_DEFAULT_LIMITATION = (
    "Анализ основан только на наблюдаемом графе исходящих переводов; "
    "результат является приоритетом для проверки, а не выводом о нарушении."
)


class DeterministicFallbackProvider:
    """Create a useful offline explanation from computed facts only."""

    def __init__(self, reason: str | None = None) -> None:
        self._reason = reason

    @property
    def name(self) -> str:
        return "deterministic"

    def answer(self, query: str, context: Mapping[str, Any] | None = None) -> AIResult:
        minimized = minimize_context(context)
        facts = _fact_lines(minimized)
        hypothesis = _hypothesis_line(minimized)
        limitations = _unique(
            [self._reason, *limitations_from_context(minimized), _DEFAULT_LIMITATION]
        )
        sections = [
            "Детерминированный офлайн-разбор (без внешней AI-модели).",
            f"Запрос: {query.strip()[:300] or 'не указан'}.",
            *(f"Факт: {fact}" for fact in facts),
            f"Гипотеза для проверки: {hypothesis}",
            *(f"Ограничение: {item}" for item in limitations),
        ]
        return {
            "answer": "\n".join(sections),
            "provider": self.name,
            "fallback": True,
            "tools_used": tools_from_context(minimized),
            "limitations": limitations,
        }


def _fact_lines(context: Mapping[str, Any]) -> list[str]:
    node = context.get("node")
    if isinstance(node, Mapping):
        gid = str(node.get("gid", "не указан"))
        fields = [f"gid={gid}"]
        if node.get("role") is not None:
            fields.append(f"роль={node['role']}")
        if isinstance(node.get("priority_score"), (int, float)):
            fields.append(f"priority_score={float(node['priority_score']):.3f}")
        return [", ".join(fields) + "."]
    gids = context.get("gids")
    if isinstance(gids, list) and gids:
        return ["в запросе выбраны gid: " + ", ".join(map(str, gids[:20])) + "."]
    return ["структурированные результаты инструментов для выбранного узла не переданы."]


def _hypothesis_line(context: Mapping[str, Any]) -> str:
    node = context.get("node")
    if isinstance(node, Mapping) and node.get("role"):
        return (
            f"роль «{node['role']}» указывает на наблюдаемый структурный паттерн; "
            "проверьте связи, временную последовательность и первичные документы."
        )
    return "сначала получите профиль, связи и объяснение score для выбранных gid."


def _unique(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
