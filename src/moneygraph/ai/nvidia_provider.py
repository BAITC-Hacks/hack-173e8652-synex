"""NVIDIA NIM provider through its OpenAI-compatible endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moneygraph.ai.base import AIConfigurationError
from moneygraph.ai.openai_provider import ClientFactory, OpenAIProvider


class NvidiaNimProvider(OpenAIProvider):
    """Use NVIDIA's compatible endpoint while preserving the same contract."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        max_tokens: int = 320,
        temperature: float = 0.0,
        extra_body: Mapping[str, Any] | None = None,
        disable_thinking: bool = True,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not base_url:
            raise AIConfigurationError("NVIDIA NIM requires base URL from environment")
        request_body = dict(extra_body or {})
        if disable_thinking:
            request_body = {
                **request_body,
                "chat_template_kwargs": {
                    **dict(request_body.get("chat_template_kwargs", {})),
                    "enable_thinking": False,
                },
            }
        super().__init__(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=request_body,
            client_factory=client_factory,
        )
        self._base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        return "nvidia_nim"

    def __repr__(self) -> str:
        return f"NvidiaNimProvider(model={self._model!r}, api_key=<redacted>)"

    def _build_client(self) -> Any:
        if self._client_factory is None:
            from openai import OpenAI

            self._client_factory = OpenAI
        return self._client_factory(api_key=self._api_key, base_url=self._base_url)
