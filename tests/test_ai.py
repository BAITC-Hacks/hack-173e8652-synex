from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from moneygraph.ai.base import AIProvider, minimize_context
from moneygraph.ai.deterministic_fallback import DeterministicFallbackProvider
from moneygraph.ai.factory import ResilientProvider, build_provider
from moneygraph.ai.nvidia_provider import NvidiaNimProvider
from moneygraph.ai.openai_provider import OpenAIProvider


def test_minimize_context_keeps_analytical_fields_and_drops_sensitive_values() -> None:
    context = {
        "node": {
            "gid": "00017",
            "role": "transit",
            "priority_score": 0.91,
            "full_name": "Must not leave the process",
            "iin": "123456789012",
        },
        "tools_used": ["get_node_profile"],
        "raw_transactions": [{"src": "00017", "dst": "00018", "sum_kzt": 10_000}],
        "api_key": "secret-value",
    }

    minimized = minimize_context(context)

    assert minimized == {
        "node": {"gid": "00017", "role": "transit", "priority_score": 0.91},
        "tools_used": ["get_node_profile"],
    }


def test_fallback_is_deterministic_non_accusatory_and_cites_available_metrics() -> None:
    provider = DeterministicFallbackProvider(reason="AI отключён")
    context = {
        "node": {"gid": "00123", "role": "consolidator", "priority_score": 0.876},
        "limitations": ["Наблюдаются только исходящие переводы от seed."],
        "tools_used": ["get_node_profile", "explain_score"],
    }

    first = provider.answer("Почему этот узел в топе?", context)
    second = provider.answer("Почему этот узел в топе?", context)

    assert first == second
    assert first["provider"] == "deterministic"
    assert first["fallback"] is True
    assert first["tools_used"] == ["get_node_profile", "explain_score"]
    assert "gid=00123" in first["answer"]
    assert "0.876" in first["answer"]
    assert "гипотез" in first["answer"].lower()
    assert "огранич" in first["answer"].lower()
    assert "виновен" not in first["answer"].lower()
    assert "преступник" not in first["answer"].lower()


@dataclass(frozen=True)
class _Settings:
    ai_enabled: bool
    ai_provider: str = "fallback"
    ai_max_tokens: int = 320
    ai_temperature: float = 0.0
    openai_api_key: str | None = None
    openai_model: str | None = None
    nvidia_api_key: str | None = None
    nvidia_base_url: str | None = None
    nvidia_model: str | None = None
    nvidia_disable_thinking: bool = True


def test_build_provider_remains_offline_when_ai_is_disabled() -> None:
    provider = build_provider(_Settings(ai_enabled=False))

    assert isinstance(provider, AIProvider)
    assert isinstance(provider, DeterministicFallbackProvider)
    assert provider.answer("Сравни узлы", {})["fallback"] is True


def test_build_provider_falls_back_when_required_credentials_are_missing() -> None:
    provider = build_provider(
        _Settings(ai_enabled=True, ai_provider="openai", openai_model="configured-model")
    )

    result = provider.answer("Покажи мосты", {})

    assert isinstance(provider, DeterministicFallbackProvider)
    assert result["fallback"] is True
    assert "ключ" in result["limitations"][0].lower()


class _FakeCompletions:
    def __init__(self, *, content: str = "Структурированный ответ", error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    def __init__(self, completions: _FakeCompletions):
        self.chat = SimpleNamespace(completions=completions)


def test_openai_provider_is_lazy_and_sends_only_minimized_context() -> None:
    completions = _FakeCompletions()
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> _FakeClient:
        factory_calls.append(kwargs)
        return _FakeClient(completions)

    provider = OpenAIProvider(
        api_key="provided-through-env",
        model="configured-model",
        client_factory=factory,
    )

    assert factory_calls == []
    result = provider.answer(
        "Почему gid в топе?",
        {"node": {"gid": "0007", "priority_score": 0.8, "iin": "hidden"}},
    )

    assert result["fallback"] is False
    assert result["provider"] == "openai"
    assert factory_calls == [{"api_key": "provided-through-env", "timeout": 20.0, "max_retries": 0}]
    sent_prompt = completions.calls[0]["messages"][1]["content"]
    assert "0007" in sent_prompt
    assert "hidden" not in sent_prompt
    assert completions.calls[0]["model"] == "configured-model"
    assert completions.calls[0]["max_tokens"] == 320
    assert completions.calls[0]["temperature"] == 0.0


def test_nvidia_provider_uses_openai_compatible_base_url() -> None:
    completions = _FakeCompletions(content="NIM answer")
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> _FakeClient:
        factory_calls.append(kwargs)
        return _FakeClient(completions)

    provider = NvidiaNimProvider(
        api_key="provided-through-env",
        model="configured-nim-model",
        base_url="https://nim.example/v1/",
        client_factory=factory,
    )
    result = provider.answer("Сравни", {"gids": ["1", "2"]})

    assert result["provider"] == "nvidia_nim"
    assert factory_calls == [
        {"api_key": "provided-through-env", "base_url": "https://nim.example/v1"}
    ]
    assert completions.calls[0]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_build_provider_applies_cost_controls_from_configuration() -> None:
    completions = _FakeCompletions(content="NIM answer")

    def factory(**kwargs: Any) -> _FakeClient:
        return _FakeClient(completions)

    provider = build_provider(
        _Settings(
            ai_enabled=True,
            ai_provider="nvidia",
            ai_max_tokens=96,
            ai_temperature=0.2,
            nvidia_api_key="provided-through-env",
            nvidia_model="configured-nim-model",
            nvidia_base_url="https://nim.example/v1",
        ),
        client_factory=factory,
    )
    result = provider.answer("Сравни", {"gids": ["1", "2"]})

    assert result["fallback"] is False
    assert completions.calls[0]["max_tokens"] == 96
    assert completions.calls[0]["temperature"] == 0.2
    assert completions.calls[0]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_resilient_provider_returns_deterministic_answer_on_api_error() -> None:
    completions = _FakeCompletions(error=RuntimeError("remote service unavailable"))
    primary = OpenAIProvider(
        api_key="provided-through-env",
        model="configured-model",
        client_factory=lambda **_: _FakeClient(completions),
    )
    provider = ResilientProvider(primary, DeterministicFallbackProvider())

    result = provider.answer("Каких данных не хватает?", {"gids": ["a", "b"]})

    assert result["fallback"] is True
    assert result["provider"] == "deterministic"
    assert "недоступен" in result["limitations"][0].lower()
    assert "remote service unavailable" not in result["answer"]


@pytest.mark.parametrize("provider_name", ["unsupported", "", "OPENAI "])
def test_build_provider_handles_unknown_or_malformed_provider_without_crashing(
    provider_name: str,
) -> None:
    provider = build_provider(_Settings(ai_enabled=True, ai_provider=provider_name))

    assert provider.answer("test", {})["fallback"] is True
