"""Environment-backed provider selection and failure containment."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from typing import Any

from moneygraph.ai.base import AIProvider, AIResult
from moneygraph.ai.deterministic_fallback import DeterministicFallbackProvider
from moneygraph.ai.errors import classify_failure
from moneygraph.ai.governance import GovernedProvider
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
        except Exception as error:
            failure = classify_failure(error)
            result = DeterministicFallbackProvider(failure.message).answer(query, context)
            return {**result, "ai_status": failure.code, "cached": False}


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
    max_tokens = min(
        1600,
        _as_positive_int(
            _value(settings, "ai_max_tokens", "AI_MAX_TOKENS", env, 320),
            default=320,
        ),
    )
    temperature = _as_float(
        _value(settings, "ai_temperature", "AI_TEMPERATURE", env, 0),
        default=0.0,
    )
    if not enabled:
        return DeterministicFallbackProvider("AI отключён настройкой AI_ENABLED.")
    if provider_name == "openai":
        key = _text(_value(settings, "openai_api_key", "OPENAI_API_KEY", env))
        model = _text(_value(settings, "openai_model", "OPENAI_MODEL", env))
        if not key or not model:
            return DeterministicFallbackProvider(
                "Для OpenAI не задан ключ или модель; применён офлайн-разбор."
            )
        primary = OpenAIProvider(
            api_key=key,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            client_factory=client_factory,
        )
        return _govern(primary, settings, env)
    if provider_name == "nvidia":
        key = _text(_value(settings, "nvidia_api_key", "NVIDIA_API_KEY", env))
        model = _text(_value(settings, "nvidia_model", "NVIDIA_MODEL", env))
        base_url = _text(_value(settings, "nvidia_base_url", "NVIDIA_BASE_URL", env))
        if not key or not model or not base_url:
            return DeterministicFallbackProvider(
                "Для NVIDIA NIM не заданы ключ, модель или base URL; применён офлайн-разбор."
            )
        disable_thinking = _as_bool(
            _value(settings, "nvidia_disable_thinking", "NVIDIA_DISABLE_THINKING", env, True)
        )
        primary = NvidiaNimProvider(
            api_key=key,
            model=model,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            disable_thinking=disable_thinking,
            client_factory=client_factory,
        )
        return _govern(primary, settings, env)
    return DeterministicFallbackProvider(
        "AI_PROVIDER не распознан; применён детерминированный офлайн-разбор."
    )


def provider_status(provider: AIProvider | None) -> dict[str, Any]:
    """Inspect local accounting only; this never probes a paid provider."""

    status = getattr(provider, "status", None)
    if callable(status):
        return dict(status())
    return {
        "configured": False,
        "provider": provider.name if provider else "deterministic",
        "model": None,
        "ai_status": "offline",
        "requests_today": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "cache_hits": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "last_success_at": None,
        "last_error": None,
        "last_error_code": None,
    }


def _govern(primary: AIProvider, settings: Any | None, env: Mapping[str, str]) -> GovernedProvider:
    raw_limit = _value(settings, "ai_daily_call_limit", "AI_DAILY_CALL_LIMIT", env, 100)
    try:
        daily_limit = max(0, int(str(raw_limit)))
    except (TypeError, ValueError):
        daily_limit = 100
    return GovernedProvider(
        primary,
        state_path=str(
            _value(settings, "ai_state_path", "AI_STATE_PATH", env, "./artifacts/ai_state.sqlite3")
        ),
        daily_call_limit=daily_limit,
        cache_ttl_seconds=_as_positive_int(
            _value(settings, "ai_cache_ttl_seconds", "AI_CACHE_TTL_SECONDS", env, 86400),
            default=86400,
        ),
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


def _as_positive_int(value: Any, *, default: int) -> int:
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _as_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(str(value).strip())
        return min(2.0, max(0.0, parsed)) if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
