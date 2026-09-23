"""NVIDIA NIM provider through its OpenAI-compatible endpoint."""

from __future__ import annotations

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
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not base_url:
            raise AIConfigurationError("NVIDIA NIM requires base URL from environment")
        super().__init__(api_key=api_key, model=model, client_factory=client_factory)
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
