"""Allowlisted AI failure diagnostics without serializing exception contents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

FAILURE_MESSAGES = {
    "sdk_missing": (
        "OpenAI SDK не установлен в среде API. Установите зависимости проекта с AI-extra "
        "(pip install -e '.[ai]') и перезапустите API. Сейчас используется офлайн-разбор без AI-модели."
    ),
    "authentication_failed": (
        "AI-провайдер отклонил авторизацию или доступ. Проверьте ключ в .env, проект и права "
        "на выбранную модель; не отправляйте ключ в чат. Сейчас используется офлайн-разбор."
    ),
    "insufficient_quota": (
        "У проекта AI-провайдера исчерпана квота или не активна оплата. Проверьте баланс "
        "и лимиты проекта, которому принадлежит ключ. Сейчас используется офлайн-разбор."
    ),
    "rate_limited": (
        "AI-провайдер ограничил частоту запросов. Повторите после паузы и проверьте лимиты "
        "запросов и токенов проекта. Сейчас используется офлайн-разбор."
    ),
    "invalid_request": (
        "AI-провайдер отклонил параметры запроса. Проверьте название и доступность модели, "
        "поддержку структурированного ответа и параметры API. Сейчас используется офлайн-разбор."
    ),
    "timeout": (
        "AI-провайдер не ответил вовремя. Проверьте соединение и повторите после паузы; "
        "автоматический платный повтор не выполнялся. Сейчас используется офлайн-разбор."
    ),
    "provider_unavailable": (
        "Внешний AI-провайдер временно недоступен или ответ не прошёл проверку. "
        "Проверьте доступность сервиса и повторите после паузы; применён офлайн-разбор."
    ),
}


@dataclass(frozen=True)
class ProviderFailure:
    code: str
    message: str


def classify_failure(error: Exception) -> ProviderFailure:
    """Use only type/status/allowlisted codes; never str(error) or raw body text."""

    code = _classify_code(error)
    return ProviderFailure(code=code, message=FAILURE_MESSAGES[code])


def _classify_code(error: Exception) -> str:
    if isinstance(error, ImportError) and _attribute(error, "name") == "openai":
        return "sdk_missing"
    if isinstance(error, TimeoutError) or type(error).__name__ in {
        "APITimeoutError",
        "ReadTimeout",
        "WriteTimeout",
        "ConnectTimeout",
        "PoolTimeout",
    }:
        return "timeout"
    status = _attribute(error, "status_code")
    codes = _metadata_codes(error)
    if codes & {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}:
        return "insufficient_quota"
    if status in (401, 403) or codes & {
        "invalid_api_key",
        "authentication_error",
        "permission_denied",
    }:
        return "authentication_failed"
    if status == 429 or type(error).__name__ == "RateLimitError":
        return "rate_limited"
    if status in (408, 504):
        return "timeout"
    if status in (400, 404, 422):
        return "invalid_request"
    return "provider_unavailable"


def _metadata_codes(error: Exception) -> set[str]:
    body = _attribute(error, "body")
    metadata: Mapping[str, Any] = body if isinstance(body, Mapping) else {}
    nested = metadata.get("error")
    if isinstance(nested, Mapping):
        metadata = nested
    candidates = (
        _attribute(error, "code"),
        _attribute(error, "type"),
        metadata.get("code"),
        metadata.get("type"),
    )
    return {value for value in candidates if isinstance(value, str) and len(value) <= 80}


def _attribute(error: Exception, name: str) -> Any:
    try:
        return getattr(error, name, None)
    except Exception:
        return None
