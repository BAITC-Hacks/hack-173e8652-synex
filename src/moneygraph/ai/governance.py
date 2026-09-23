"""Persistent, fail-closed admission control for paid AI calls.

Only request fingerprints and validated responses are stored. SQLite's write
transaction reserves quota before dispatch, including across API processes.
Failed/abandoned calls remain charged against the call allowance because a
remote service may have received them even when no response was obtained.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from moneygraph.ai.base import AIProvider, AIResult, minimize_context
from moneygraph.ai.case_assessment import sanitize_alert, validate_assessment
from moneygraph.ai.deterministic_fallback import DeterministicFallbackProvider
from moneygraph.ai.errors import FAILURE_MESSAGES, ProviderFailure, classify_failure

_REASONS = {
    **FAILURE_MESSAGES,
    "budget_exhausted": "Дневной лимит AI-вызовов исчерпан; применён офлайн-разбор.",
    "in_flight": "Такой AI-разбор уже выполняется; повторный платный вызов не создан.",
    "cooldown": "После ошибки AI действует короткая пауза; применён офлайн-разбор.",
    "state_unavailable": "Учёт AI-расходов недоступен; платный вызов безопасно отменён.",
    "context_too_large": "Контекст превышает безопасный лимит AI-запроса; сузьте выборку.",
}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_calls (
    id INTEGER PRIMARY KEY, request_key TEXT NOT NULL, provider_key TEXT NOT NULL,
    day TEXT NOT NULL, started REAL NOT NULL, finished REAL,
    state TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE INDEX IF NOT EXISTS ai_calls_day ON ai_calls(day);
CREATE INDEX IF NOT EXISTS ai_calls_request ON ai_calls(request_key, state);
CREATE TABLE IF NOT EXISTS ai_call_failures (call_id INTEGER PRIMARY KEY, code TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_cache (
    request_key TEXT PRIMARY KEY, result TEXT NOT NULL, expires REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_cooldown (provider_key TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ai_cache_metrics (
    day TEXT NOT NULL, provider_key TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY(day, provider_key)
);
"""


class GovernedProvider:
    """Apply one daily allowance to assistant replies and case assessments."""

    def __init__(
        self,
        primary: AIProvider,
        *,
        state_path: str | Path = "./artifacts/ai_state.sqlite3",
        daily_call_limit: int = 100,
        cache_ttl_seconds: int = 86400,
        max_cache_entries: int = 1000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if daily_call_limit < 0 or cache_ttl_seconds < 1 or max_cache_entries < 1:
            raise ValueError("AI limits must be non-negative with positive cache bounds")
        self._primary = primary
        self._path = Path(state_path)
        self._daily_limit = daily_call_limit
        self._ttl = cache_ttl_seconds
        self._max_entries = max_cache_entries
        self._clock = clock

    @property
    def name(self) -> str:
        return self._primary.name

    @property
    def model(self) -> str | None:
        value = getattr(self._primary, "model", None)
        return (
            value
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", value)
            else None
        )

    def answer(self, query: str, context: Mapping[str, Any] | None = None) -> AIResult:
        safe_context = minimize_context(_bounded(context or {}))
        if len(json.dumps(safe_context, ensure_ascii=False, allow_nan=False)) > 24000:
            return _fallback("context_too_large", query[:2000], {})
        return self._run("answer", {"query": query[:2000], "context": safe_context})

    def assess_alert(self, alert: Mapping[str, Any]) -> AIResult:
        return self._run("assess_alert", sanitize_alert(alert))

    def status(self) -> dict[str, Any]:
        now = self._clock()
        base = {
            "configured": True,
            "provider": self.name,
            "model": self.model,
            "ai_status": "ready",
            "accounting_period": "UTC day",
            "day": _day(now),
            "daily_call_limit": self._daily_limit,
            "requests_today": 0,
            "remaining_calls": self._daily_limit,
            "successful_calls": 0,
            "failed_calls": 0,
            "cache_hits": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "last_success_at": None,
            "last_error": None,
            "last_error_code": None,
            "cooldown_until": None,
        }
        try:
            with self._db() as db:
                current = {**base, **self._status_rows(db, now)}
                issue = self._configuration_issue()
                if issue:
                    return {
                        **current,
                        "ai_status": issue,
                        "last_error_code": issue,
                        "last_error": FAILURE_MESSAGES[issue],
                    }
                return current
        except (OSError, sqlite3.Error, ValueError):
            return {
                **base,
                "ai_status": "state_unavailable",
                "remaining_calls": 0,
                "last_error": _REASONS["state_unavailable"],
                "last_error_code": "state_unavailable",
            }

    def _run(self, method: str, payload: dict[str, Any]) -> AIResult:
        query = payload.get("query", "Объясни наблюдаемый AML-паттерн")
        context = payload.get("context", {"node": {"gid": payload.get("gid", "")}})
        issue = self._configuration_issue()
        if issue:
            return _fallback(issue, query, context)
        try:
            request_key = _digest(
                {"identity": self._identity(), "method": method, "payload": payload}
            )
            state, cached, reservation = self._reserve(request_key)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return _fallback("state_unavailable", query, context)
        if cached is not None:
            try:
                cached = _validated_result(cached, self.name, method, payload)
            except (ValueError, RuntimeError, TypeError):
                return _fallback("state_unavailable", query, context)
            return cast(AIResult, {**cached, "cached": True, "ai_status": "cache_hit"})
        if state != "reserved":
            return _fallback(state, query, context)
        try:
            if method == "answer":
                result = self._primary.answer(query, context)
            else:
                assessor = getattr(self._primary, "assess_alert", None)
                if not callable(assessor):
                    raise ValueError("Provider does not support case assessments")
                result = assessor(payload)
            validated = _validated_result(result, self.name, method, payload)
        except Exception as error:
            failure = classify_failure(error)
            try:
                self._finish_failure(reservation, failure)
            except (OSError, sqlite3.Error):
                return _fallback("state_unavailable", query, context)
            return _fallback(failure.code, query, context)
        try:
            self._finish_success(reservation, request_key, validated)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return _fallback("state_unavailable", query, context)
        return cast(AIResult, {**validated, "cached": False, "ai_status": "success"})

    def _reserve(self, key: str) -> tuple[str, dict[str, Any] | None, int]:
        now = self._clock()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            cache = db.execute("SELECT result FROM ai_cache WHERE request_key=?", (key,)).fetchone()
            if cache:
                result = json.loads(cache[0])
                if not isinstance(result, dict):
                    raise ValueError("Invalid cache entry")
                db.execute(
                    "INSERT INTO ai_cache_metrics(day,provider_key,count) VALUES(?,?,1) "
                    "ON CONFLICT(day,provider_key) DO UPDATE SET count=count+1",
                    (_day(now), self._identity()),
                )
                return "cache_hit", result, 0
            pending = db.execute(
                "SELECT 1 FROM ai_calls WHERE request_key=? AND state='pending'",
                (key,),
            ).fetchone()
            if pending:
                return "in_flight", None, 0
            cooldown = db.execute(
                "SELECT 1 FROM ai_cooldown WHERE provider_key=? AND until>?",
                (self._identity(), now),
            ).fetchone()
            if cooldown:
                return "cooldown", None, 0
            count = db.execute(
                "SELECT COUNT(*) FROM ai_calls WHERE day=?", (_day(now),)
            ).fetchone()[0]
            if count >= self._daily_limit:
                return "budget_exhausted", None, 0
            cursor = db.execute(
                "INSERT INTO ai_calls(request_key,provider_key,day,started,state) VALUES(?,?,?,?,'pending')",
                (key, self._identity(), _day(now), now),
            )
            return "reserved", None, int(cursor.lastrowid or 0)

    def _finish_success(self, reservation: int, key: str, result: dict[str, Any]) -> None:
        now = self._clock()
        usage = result.get("usage", {})
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(serialized) > 40000:
            raise ValueError("AI response exceeds cache size boundary")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE ai_calls SET state='success',finished=?,input_tokens=?,output_tokens=? WHERE id=?",
                (now, usage.get("input_tokens", 0), usage.get("output_tokens", 0), reservation),
            )
            db.execute(
                "INSERT OR REPLACE INTO ai_cache(request_key,result,expires,updated) VALUES(?,?,?,?)",
                (key, serialized, now + self._ttl, now),
            )
            db.execute(
                "DELETE FROM ai_cache WHERE request_key NOT IN "
                "(SELECT request_key FROM ai_cache ORDER BY updated DESC,rowid DESC LIMIT ?)",
                (self._max_entries,),
            )

    def _finish_failure(self, reservation: int, failure: ProviderFailure) -> None:
        now = self._clock()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE ai_calls SET state='failed',finished=?,error=? WHERE id=?",
                (now, failure.message, reservation),
            )
            db.execute(
                "INSERT OR REPLACE INTO ai_call_failures(call_id,code) VALUES(?,?)",
                (reservation, failure.code),
            )
            db.execute(
                "INSERT OR REPLACE INTO ai_cooldown(provider_key,until) VALUES(?,?)",
                (self._identity(), now + 60),
            )

    def _status_rows(self, db: sqlite3.Connection, now: float) -> dict[str, Any]:
        identity = self._identity()
        requests = db.execute("SELECT COUNT(*) FROM ai_calls WHERE day=?", (_day(now),)).fetchone()[
            0
        ]
        row = db.execute(
            "SELECT COALESCE(SUM(state='success'),0),COALESCE(SUM(state='failed'),0),"
            "COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0) "
            "FROM ai_calls WHERE day=? AND provider_key=?",
            (_day(now), identity),
        ).fetchone()
        hits = db.execute(
            "SELECT count FROM ai_cache_metrics WHERE day=? AND provider_key=?",
            (_day(now), identity),
        ).fetchone()
        success = db.execute(
            "SELECT finished,id FROM ai_calls WHERE state='success' AND provider_key=? "
            "ORDER BY finished DESC,id DESC LIMIT 1",
            (identity,),
        ).fetchone()
        error = db.execute(
            "SELECT error,finished,code,ai_calls.id FROM ai_calls LEFT JOIN ai_call_failures ON call_id=ai_calls.id "
            "WHERE state='failed' AND provider_key=? ORDER BY finished DESC,ai_calls.id DESC LIMIT 1",
            (identity,),
        ).fetchone()
        cooldown = db.execute(
            "SELECT until FROM ai_cooldown WHERE provider_key=? AND until>?",
            (self._identity(), now),
        ).fetchone()
        error_code = None
        if error and (success is None or (error[1], error[3]) > (success[0], success[1])):
            error_code = error[2] if error[2] in FAILURE_MESSAGES else "provider_unavailable"
        return {
            "ai_status": error_code or "ready",
            "requests_today": requests,
            "remaining_calls": max(0, self._daily_limit - requests),
            "successful_calls": row[0],
            "failed_calls": row[1],
            "input_tokens": row[2],
            "output_tokens": row[3],
            "cache_hits": hits[0] if hits else 0,
            "last_success_at": _iso(success[0]) if success else None,
            "last_error": FAILURE_MESSAGES[error_code] if error_code else None,
            "last_error_code": error_code,
            "cooldown_until": _iso(cooldown[0]) if cooldown else None,
        }

    def _prune(self, db: sqlite3.Connection, now: float) -> None:
        db.execute("DELETE FROM ai_cache WHERE expires<=?", (now,))
        db.execute("DELETE FROM ai_cooldown WHERE until<=?", (now,))
        db.execute("DELETE FROM ai_calls WHERE day<?", (_day(now - 90 * 86400),))
        db.execute("DELETE FROM ai_call_failures WHERE call_id NOT IN (SELECT id FROM ai_calls)")
        db.execute("DELETE FROM ai_cache_metrics WHERE day<?", (_day(now - 90 * 86400),))
        db.execute(
            "INSERT OR IGNORE INTO ai_call_failures(call_id,code) "
            "SELECT id,'timeout' FROM ai_calls WHERE state='pending' AND started<?",
            (now - 180,),
        )
        db.execute(
            "UPDATE ai_calls SET state='failed',finished=?,error=? WHERE state='pending' AND started<?",
            (now, _REASONS["provider_unavailable"], now - 180),
        )

    def _identity(self) -> str:
        identity = getattr(
            self._primary, "cache_identity", {"provider": self.name, "model": self.model}
        )
        return _digest(identity)

    def _configuration_issue(self) -> str | None:
        try:
            issue = getattr(self._primary, "configuration_issue", None)
        except Exception as error:
            return classify_failure(error).code
        return issue if isinstance(issue, str) and issue in FAILURE_MESSAGES else None

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path, timeout=5)
        try:
            self._path.chmod(0o600)
            db.executescript(_SCHEMA)
            with db:
                yield db
        finally:
            db.close()


def _bounded(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(value, Mapping):
        return {str(k): _bounded(v, depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if value is None or isinstance(value, (int, float, bool)) else None


def _validated_result(
    result: Any, provider: str, method: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if (
        not isinstance(result, dict)
        or result.get("fallback") is not False
        or result.get("provider") != provider
        or not isinstance(result.get("answer"), str)
        or not result["answer"].strip()
        or len(result["answer"]) > 12000
    ):
        raise ValueError("Provider did not return a successful bounded answer")
    assessment = (
        validate_assessment(result.get("assessment"), payload) if method == "assess_alert" else None
    )
    usage = result.get("usage", {})
    if not isinstance(usage, dict) or any(
        type(usage.get(key, 0)) is not int or usage.get(key, 0) < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("Invalid provider usage")
    allowed = {
        "answer",
        "provider",
        "fallback",
        "tools_used",
        "limitations",
        "model",
        "usage",
        "assessment",
    }
    clean = {key: value for key, value in result.items() if key in allowed}
    if assessment is not None:
        clean = {**clean, "assessment": assessment}
    if len(json.dumps(clean, ensure_ascii=False, allow_nan=False)) > 40000:
        raise ValueError("Provider response exceeds cache size boundary")
    return clean


def _fallback(state: str, query: str, context: Mapping[str, Any]) -> AIResult:
    result = DeterministicFallbackProvider(_REASONS[state]).answer(query, context)
    return cast(AIResult, {**result, "cached": False, "ai_status": state})


def _digest(value: Any) -> str:
    data = json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    return hashlib.sha256(data.encode()).hexdigest()


def _day(instant: float) -> str:
    return datetime.fromtimestamp(instant, UTC).date().isoformat()


def _iso(instant: float | None) -> str | None:
    return datetime.fromtimestamp(instant, UTC).isoformat() if instant is not None else None
