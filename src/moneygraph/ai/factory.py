"""Environment-backed provider selection and failure containment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from moneygraph.ai.base import AIProvider, AIResult
from moneygraph.ai.deterministic_fallback import DeterministicFallbackProvider
from moneygraph.ai.nvidia_provider import NvidiaNimProvider
from moneygraph.ai.openai_provider import ClientFactory, OpenAIProvider


class ResilientProvider:
    """Contain every remote provider failure behind the offline fallback."""

    def __init__(self, primary: AIProvider, fallback: AIProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def name(self) -> str:
        return self._primary.name

    def answer(self, query: str, context: Mapping[str, Any] | None = None) -> AIResult:
        try:
            return self._primary.answer(query, context)
        except Exception:
            safe_fallback = DeterministicFallbackProvider(
                "Внешний AI-провайдер временно недоступен; применён офлайн-разбор."
            )
            return safe_fallback.answer(query, context)


def build_provider(
    settings: Any | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: ClientFactory | None = None,
) -> AIProvider:
    """Build a provider without network calls or mandatory SDK credentials."""

    env = os.environ if environ is None else environ
    enabled = _as_bool(_value(settings, "ai_enabled", "AI_ENABLED", env, False))
    provider_name = str(_value(settings, "ai_provider", "AI_PROVIDER", env, "fallback"))
    if not enabled:
        return DeterministicFallbackProvider("AI отключён настройкой AI_ENABLED.")
    if provider_name == "openai":
        key = _text(_value(settings, "openai_api_key", "OPENAI_API_KEY", env))
        model = _text(_value(settings, "openai_model", "OPENAI_MODEL", env))
        if not key or not model:
            return DeterministicFallbackProvider(
                "Для OpenAI не задан ключ или модель; применён офлайн-разбор."
            )
        primary = OpenAIProvider(api_key=key, model=model, client_factory=client_factory)
        return ResilientProvider(primary, DeterministicFallbackProvider())
    if provider_name == "nvidia":
        key = _text(_value(settings, "nvidia_api_key", "NVIDIA_API_KEY", env))
        model = _text(_value(settings, "nvidia_model", "NVIDIA_MODEL", env))
        base_url = _text(_value(settings, "nvidia_base_url", "NVIDIA_BASE_URL", env))
        if not key or not model or not base_url:
            return DeterministicFallbackProvider(
                "Для NVIDIA NIM не заданы ключ, модель или base URL; применён офлайн-разбор."
            )
        primary = NvidiaNimProvider(
            api_key=key,
            model=model,
            base_url=base_url,
            client_factory=client_factory,
        )
        return ResilientProvider(primary, DeterministicFallbackProvider())
    return DeterministicFallbackProvider(
        "AI_PROVIDER не распознан; применён детерминированный офлайн-разбор."
    )


def _value(
    settings: Any | None,
    attribute: str,
    env_name: str,
    environ: Mapping[str, str],
    default: Any = None,
) -> Any:
    if settings is not None:
        if isinstance(settings, Mapping):
            if attribute in settings:
                return settings[attribute]
            if env_name in settings:
                return settings[env_name]
        elif hasattr(settings, attribute):
            return getattr(settings, attribute)
    return environ.get(env_name, default)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
