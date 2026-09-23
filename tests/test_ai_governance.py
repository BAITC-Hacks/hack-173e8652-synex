from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import Any

import pytest

from moneygraph.ai.factory import build_provider, provider_status
from moneygraph.ai.governance import GovernedProvider


class FakeProvider:
    name = "openai"
    model = "test-model"
    cache_identity = "test-model:prompt-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Any]] = []
        self.error: Exception | None = None
        self.lock = Lock()

    def answer(self, query: str, context: Any = None) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("answer", query, context))
        if self.error:
            raise self.error
        return {
            "answer": "Наблюдаемый паттерн требует проверки.",
            "provider": "openai", "fallback": False, "tools_used": [], "limitations": [],
            "model": self.model, "usage": {"input_tokens": 80, "output_tokens": 20},
        }

    def assess_alert(self, alert: Any) -> dict[str, Any]:
        result = self.answer("assessment", alert)
        return {**result, "assessment": {"summary": result["answer"], "actions": []}}


def governed(tmp_path: Path, primary: Any = None, **kwargs: Any) -> GovernedProvider:
    return GovernedProvider(
        primary or FakeProvider(), state_path=tmp_path / "state.sqlite3", **kwargs
    )


def test_success_is_cached_across_instances_and_accounts_real_usage(tmp_path: Path) -> None:
    primary = FakeProvider()
    first = governed(tmp_path, primary).answer("query", {"gids": ["7"]})
    provider = governed(tmp_path, primary)
    second = provider.answer("query", {"gids": ["7"]})
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["ai_status"] == "cache_hit"
    assert len(primary.calls) == 1
    status = provider.status()
    assert status["requests_today"] == status["successful_calls"] == status["cache_hits"] == 1
    assert status["input_tokens"] == 80
    assert status["output_tokens"] == 20
    assert status["last_success_at"]
    assert status["model"] == "test-model"


def test_cache_identity_and_method_and_facts_are_distinct(tmp_path: Path) -> None:
    primary = FakeProvider()
    provider = governed(tmp_path, primary)
    provider.answer("query", {"gids": ["7"]})
    primary.cache_identity = "test-model:prompt-v2"
    governed(tmp_path, primary).answer("query", {"gids": ["7"]})
    alert = {"gid": "7", "facts": {"daily_unique_payers": 8}}
    provider.assess_alert(alert)
    provider.assess_alert({**alert, "facts": {"daily_unique_payers": 9}})
    assert len(primary.calls) == 4


def test_daily_budget_shared_by_methods_and_instances(tmp_path: Path) -> None:
    primary = FakeProvider()
    provider = governed(tmp_path, primary, daily_call_limit=1)
    provider.answer("first")
    fallback = governed(tmp_path, primary, daily_call_limit=1).assess_alert({"gid": "7"})
    assert fallback["fallback"] is True
    assert fallback["ai_status"] == "budget_exhausted"
    assert len(primary.calls) == 1
    assert provider.status()["remaining_calls"] == 0


def test_zero_daily_budget_disables_paid_requests(tmp_path: Path) -> None:
    primary = FakeProvider()
    result = governed(tmp_path, primary, daily_call_limit=0).answer("query")
    assert result["ai_status"] == "budget_exhausted"
    assert primary.calls == []


def test_parallel_reservations_cannot_overrun_daily_limit(tmp_path: Path) -> None:
    primary = FakeProvider()
    providers = [governed(tmp_path, primary, daily_call_limit=3) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda pair: pair[1].answer(str(pair[0])), enumerate(providers)))
    assert sum(not result["fallback"] for result in results) == 3
    assert len(primary.calls) == 3


def test_identical_inflight_request_does_not_trigger_second_call(tmp_path: Path) -> None:
    entered, release = Event(), Event()

    class BlockingProvider(FakeProvider):
        def answer(self, query: str, context: Any = None) -> dict[str, Any]:
            entered.set()
            assert release.wait(5)
            return super().answer(query, context)

    primary = BlockingProvider()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(governed(tmp_path, primary).answer, "query")
        assert entered.wait(5)
        duplicate = governed(tmp_path, primary).answer("query")
        release.set()
        assert pending.result()["fallback"] is False
    assert duplicate["ai_status"] == "in_flight"
    assert len(primary.calls) == 1


def test_provider_failure_is_generic_and_cooldown_survives_new_instance(tmp_path: Path) -> None:
    primary = FakeProvider()
    primary.error = RuntimeError("secret-provider-detail")
    provider = governed(tmp_path, primary)
    first = provider.answer("one")
    second = governed(tmp_path, primary).answer("different question")
    assert first["ai_status"] == "provider_unavailable"
    assert second["ai_status"] == "cooldown"
    assert len(primary.calls) == 1
    status = provider.status()
    assert status["failed_calls"] == 1
    assert "secret-provider-detail" not in json.dumps([first, second, status])
    assert status["cooldown_until"]


def test_cooldown_and_cache_expire_with_clock(tmp_path: Path) -> None:
    instant = [1_800_000_000.0]
    primary = FakeProvider()
    provider = governed(tmp_path, primary, clock=lambda: instant[0], cache_ttl_seconds=10)
    provider.answer("one")
    instant[0] += 11
    assert provider.answer("one")["cached"] is False
    primary.error = RuntimeError("failure")
    provider.answer("two")
    instant[0] += 61
    primary.error = None
    assert provider.answer("three")["fallback"] is False
    assert len(primary.calls) == 4


def test_utc_day_resets_budget_not_cache(tmp_path: Path) -> None:
    instant = [1_800_000_000.0]
    provider = governed(tmp_path, daily_call_limit=1, clock=lambda: instant[0])
    provider.answer("one")
    instant[0] += 86400
    assert provider.answer("two")["fallback"] is False
    assert provider.status()["requests_today"] == 1


def test_input_is_minimized_before_hash_and_remote_dispatch(tmp_path: Path) -> None:
    primary = FakeProvider()
    provider = governed(tmp_path, primary)
    provider.answer("x" * 3000, {"node": {"gid": "7", "iin": "private"}, "api_key": "private"})
    assert len(primary.calls[0][1]) == 2000
    assert primary.calls[0][2] == {"node": {"gid": "7"}}
    provider.assess_alert({
        "gid": "7", "note": "private", "rule_keys": ["daily_unique_payers", "evil"],
        "facts": {"daily_unique_payers": 9, "iin": "private", "pass_through": float("nan")},
    })
    sent = primary.calls[1][2]
    assert sent == {"gid": "7", "rule_keys": ["daily_unique_payers"], "facts": {"daily_unique_payers": 9}}
    with sqlite3.connect(tmp_path / "state.sqlite3") as db:
        dump = "\n".join(db.iterdump())
    assert "private" not in dump
    assert "x" * 2000 not in dump


def test_persistence_failure_fails_closed_and_does_not_call_provider(tmp_path: Path) -> None:
    primary = FakeProvider()
    provider = GovernedProvider(primary, state_path=tmp_path)
    assert provider.answer("one")["ai_status"] == "state_unavailable"
    assert provider.status()["ai_status"] == "state_unavailable"
    assert primary.calls == []


def test_invalid_result_is_not_cached(tmp_path: Path) -> None:
    class InvalidProvider(FakeProvider):
        def answer(self, query: str, context: Any = None) -> dict[str, Any]:
            return {**super().answer(query, context), "answer": ""}

    provider = governed(tmp_path, InvalidProvider())
    assert provider.answer("one")["fallback"] is True
    assert provider.status()["successful_calls"] == 0


def test_cache_is_bounded(tmp_path: Path) -> None:
    provider = governed(tmp_path, max_cache_entries=2)
    for question in ("one", "two", "three"):
        provider.answer(question)
    with sqlite3.connect(tmp_path / "state.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0] == 2


def test_factory_wraps_paid_provider_and_reports_offline_status(tmp_path: Path) -> None:
    provider = build_provider(environ={
        "AI_ENABLED": "true", "AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-only",
        "OPENAI_MODEL": "test-model", "AI_STATE_PATH": str(tmp_path / "state.sqlite3"),
        "AI_DAILY_CALL_LIMIT": "0",
    })
    assert isinstance(provider, GovernedProvider)
    assert provider.answer("question")["ai_status"] == "budget_exhausted"
    assert provider_status(provider)["configured"] is True
    assert provider_status(build_provider(environ={"AI_ENABLED": "false"}))["configured"] is False


@pytest.mark.parametrize("kwargs", [{"daily_call_limit": -1}, {"cache_ttl_seconds": 0}, {"max_cache_entries": 0}])
def test_invalid_governor_limits_are_rejected(tmp_path: Path, kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        governed(tmp_path, **kwargs)
