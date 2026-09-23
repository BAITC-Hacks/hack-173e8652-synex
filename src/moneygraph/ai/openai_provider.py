"""Lazy OpenAI provider; importing the app never requires a key."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from moneygraph.ai.base import (
    AIConfigurationError,
    AIResult,
    build_messages,
    limitations_from_context,
    minimize_context,
    tools_from_context,
)
from moneygraph.ai.case_assessment import (
    PROMPT_VERSION,
    assessment_messages,
    assessment_response_format,
    validate_assessment,
)

ClientFactory = Callable[..., Any]


class OpenAIProvider:
    """Use the official OpenAI client only on the first actual request."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int = 320,
        assessment_max_tokens: int = 1200,
        temperature: float = 0.0,
        extra_body: Mapping[str, Any] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not api_key or not model:
            raise AIConfigurationError("OpenAI requires API key and model from environment")
        if max_tokens < 1:
            raise AIConfigurationError("AI max tokens must be positive")
        if not 1 <= assessment_max_tokens <= 1600:
            raise AIConfigurationError("AI assessment tokens must be between 1 and 1600")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._assessment_max_tokens = assessment_max_tokens
        self._temperature = temperature
        self._extra_body = MappingProxyType(dict(extra_body or {}))
        self._client_factory = client_factory
        self._client: Any | None = None

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    @property
    def cache_identity(self) -> str:
        extra_body_hash = hashlib.sha256(
            json.dumps(dict(self._extra_body), sort_keys=True, default=str).encode()
        ).hexdigest()
        return json.dumps(
            {
                "provider": self.name,
                "model": self._model,
                "max_tokens": self._max_tokens,
                "assessment_max_tokens": self._assessment_max_tokens,
                "temperature": self._temperature,
                "prompt_version": PROMPT_VERSION,
                "extra_body_hash": extra_body_hash,
            },
            sort_keys=True,
        )

    def __repr__(self) -> str:
        return f"OpenAIProvider(model={self._model!r}, api_key=<redacted>)"

    def _build_client(self) -> Any:
        if self._client_factory is None:
            from openai import OpenAI

            self._client_factory = OpenAI
        return self._client_factory(api_key=self._api_key, timeout=20.0, max_retries=0)

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def answer(self, query: str, context: Mapping[str, Any] | None = None) -> AIResult:
        minimized = minimize_context(context)
        response = self._get_client().chat.completions.create(
            model=self._model,
            messages=build_messages(query, minimized),
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            **({"extra_body": dict(self._extra_body)} if self._extra_body else {}),
        )
        content = _extract_content(response)
        return {
            "answer": content,
            "provider": self.name,
            "fallback": False,
            "tools_used": tools_from_context(minimized),
            "limitations": limitations_from_context(minimized),
            "model": self.model,
            "usage": _extract_usage(response),
            "cached": False,
            "ai_status": "completed",
        }

    def assess_alert(self, alert: Mapping[str, Any]) -> AIResult:
        """Rank allowlisted local next steps without granting execution authority."""

        messages = assessment_messages(alert)
        response = self._get_client().chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_tokens=self._assessment_max_tokens,
            response_format=assessment_response_format(),
            **({"extra_body": dict(self._extra_body)} if self._extra_body else {}),
        )
        content = _extract_content(response)
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) != "stop" or getattr(choice.message, "refusal", None):
            raise RuntimeError("AI assessment was incomplete or refused")
        try:
            decoded = json.loads(content)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Invalid AI assessment JSON") from exc
        assessment = validate_assessment(decoded, alert)
        return {
            "answer": assessment["summary"],
            "provider": self.name,
            "fallback": False,
            "tools_used": [],
            "limitations": assessment["limitations"],
            "assessment": assessment,
            "model": self.model,
            "usage": _extract_usage(response),
            "cached": False,
            "ai_status": "completed",
        }


def _extract_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    fields = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens"}
    return {
        key: value
        for key, source in fields.items()
        if type(value := getattr(usage, source, None)) is int and value >= 0
    }


def _extract_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError("AI provider returned an invalid response") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("AI provider returned an empty response")
    return content.strip()
