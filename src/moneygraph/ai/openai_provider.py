"""Lazy OpenAI provider; importing the app never requires a key."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from moneygraph.ai.base import (
    AIConfigurationError,
    AIResult,
    build_messages,
    limitations_from_context,
    minimize_context,
    tools_from_context,
)

ClientFactory = Callable[..., Any]


class OpenAIProvider:
    """Use the official OpenAI client only on the first actual request."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not api_key or not model:
            raise AIConfigurationError("OpenAI requires API key and model from environment")
        self._api_key = api_key
        self._model = model
        self._client_factory = client_factory
        self._client: Any | None = None

    @property
    def name(self) -> str:
        return "openai"

    def __repr__(self) -> str:
        return f"OpenAIProvider(model={self._model!r}, api_key=<redacted>)"

    def _build_client(self) -> Any:
        if self._client_factory is None:
            from openai import OpenAI

            self._client_factory = OpenAI
        return self._client_factory(api_key=self._api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def answer(self, query: str, context: Mapping[str, Any] | None = None) -> AIResult:
        minimized = minimize_context(context)
        response = self._get_client().chat.completions.create(
            model=self._model,
            messages=build_messages(query, minimized),
            temperature=0,
        )
        content = _extract_content(response)
        return {
            "answer": content,
            "provider": self.name,
            "fallback": False,
            "tools_used": tools_from_context(minimized),
            "limitations": limitations_from_context(minimized),
        }


def _extract_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError("AI provider returned an invalid response") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("AI provider returned an empty response")
    return content.strip()
