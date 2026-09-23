from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pandas as pd

from moneygraph.ai.agentic_prompts import build_agentic_explanation_prompt
from moneygraph.ai.base import AIProvider
from moneygraph.ai.deterministic_fallback import DeterministicFallbackProvider
from moneygraph.ai.factory import build_provider
from moneygraph.repository.repositories import MoneyGraphRepository
from moneygraph.services.ai_decision_support import assess_case, explain_proposals

LOGGER = logging.getLogger("moneygraph.agentic_loop")
SAFE_PROVIDER_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
PUBLIC_NARRATIVE_PROVIDERS = frozenset({"openai", "nvidia_nim"})
MAX_AI_NARRATIVE_LENGTH = 1_200
DEFAULT_AI_MIN_PRIORITY_SCORE = 0.55
ACTION_ORDER = (
    "prepare_aml_review_draft",
    "build_money_route",
    "create_local_watchlist",
)
DAY_LEVEL_LIMITATION = (
    "Источник содержит календарные даты без внутридневного времени; "
    "признак dwell < 2 часов не вычисляется."
)


class AgenticValidationError(ValueError):
    """Raised when a replay or decision violates the capability contract."""


def _priority_threshold_from_env() -> float:
    raw = os.getenv("AGENTIC_AI_MIN_PRIORITY_SCORE", str(DEFAULT_AI_MIN_PRIORITY_SCORE))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_AI_MIN_PRIORITY_SCORE
    return min(1.0, max(0.0, value))


class AgenticLoopService:
    """Deterministic, human-approved AML copilot over an honest day-level replay."""

    def __init__(
        self,
        repository: MoneyGraphRepository,
        data_dir: Path,
        *,
        actor: str,
        ai_enabled: bool | None = None,
        narrative_provider: AIProvider | None = None,
        ai_min_priority_score: float | None = None,
    ) -> None:
        self._repository = repository
        self._data_dir = Path(data_dir)
        self._actor = actor
        enabled = (
            os.getenv("AI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
            if ai_enabled is None
            else ai_enabled
        )
        configured = narrative_provider or (build_provider() if enabled else None)
        self._narrative_provider = (
            configured
            if enabled and not isinstance(configured, DeterministicFallbackProvider)
            else None
        )
        self._ai_min_priority_score = (
            _priority_threshold_from_env()
            if ai_min_priority_score is None
            else ai_min_priority_score
        )
        self._narrative_cache: dict[str, dict[str, str]] = {}

    @property
    def narrative_enabled(self) -> bool:
        """Whether this service has a remote narrative provider configured."""

        return self._narrative_provider is not None

    @property
    def narrative_provider_name(self) -> str | None:
        """Return the provider's non-secret identifier, if it is safe to display."""

        provider = self._narrative_provider
        if provider is None:
            return None
        name = provider.name
        return name if name in PUBLIC_NARRATIVE_PROVIDERS else None

    @property
    def ai_status(self) -> dict[str, Any]:
        from moneygraph.ai.factory import provider_status

        return provider_status(self._narrative_provider)

    def run_scan(
        self,
        replay_date: date,
        *,
        interval_minutes: int = 15,
        limit: int = 50,
        actor: Literal["rules-engine", "auto-monitor"] = "rules-engine",
    ) -> dict[str, Any]:
        if not 1 <= interval_minutes <= 60:
            raise AgenticValidationError("interval_minutes must be between 1 and 60")
        if not 1 <= limit <= 50:
            raise AgenticValidationError("limit must be between 1 and 50")

        transactions = self._load_transactions()
        available_from = transactions["date"].min().date()
        available_to = transactions["date"].max().date()
        if replay_date < available_from or replay_date > available_to:
            raise AgenticValidationError(
                f"replay_date must be inside available range "
                f"{available_from.isoformat()}..{available_to.isoformat()}"
            )

        cutoff = pd.Timestamp(replay_date)
        historical = transactions.loc[transactions["date"] <= cutoff].copy()
        daily = historical.loc[historical["date"] == cutoff].copy()
        alerts = self._derive_alerts(historical, daily)
        alerts.sort(key=lambda item: (-float(item["priority_score"]), str(item["gid"])))
        selected = alerts[:limit]
        # One grounded narrative per scan caps remote requests and leaves rule outputs intact.
        if (
            selected
            and self._narrative_provider is not None
            and float(selected[0]["priority_score"]) >= self._ai_min_priority_score
        ):
            selected[0] = self._with_optional_narrative(selected[0])
        summary = {
            "transactions_seen": len(historical),
            "daily_transactions": len(daily),
            "future_transactions_excluded": int(len(transactions) - len(historical)),
            "candidate_alerts": len(alerts),
            "alerts_created": len(selected),
            "limit_applied": len(alerts) > limit,
            "simulation": True,
            "source_time_granularity": "day",
        }
        return self._repository.create_monitoring_scan(
            replay_date=replay_date,
            interval_minutes=interval_minutes,
            summary=summary,
            alerts=selected,
            actor=actor,
        )

    def available_days(self) -> tuple[list[date], int]:
        """Read real source dates for a day-level replay, including later arrivals."""

        transactions = self._load_transactions()
        days = sorted({timestamp.date() for timestamp in transactions["date"]})
        return days, len(transactions)

    def _with_optional_narrative(self, alert: dict[str, Any]) -> dict[str, Any]:
        provider = self._narrative_provider
        if provider is None:
            return alert
        if callable(getattr(provider, "assess_alert", None)):
            return assess_case(provider, alert)
        cache_key = self._narrative_cache_key(alert, provider.name)
        if cache_key in self._narrative_cache:
            cached = self._narrative_cache[cache_key]
            return {
                **alert,
                "facts": {
                    **alert["facts"],
                    "ai_narrative": cached["narrative"],
                    "ai_provider": cached["provider"],
                },
            }
        try:
            # The builder whitelists numeric facts and rules; no payer list or rows leave here.
            result = provider.answer(build_agentic_explanation_prompt(alert), None)
        except Exception:
            LOGGER.warning("Optional agentic narrative unavailable")
            return alert
        if result.get("fallback"):
            return alert
        narrative = str(result.get("answer", "")).strip()[:MAX_AI_NARRATIVE_LENGTH]
        provider_name = str(result.get("provider", ""))
        if not narrative or not SAFE_PROVIDER_NAME.fullmatch(provider_name):
            return alert
        self._narrative_cache[cache_key] = {
            "narrative": narrative,
            "provider": provider_name,
        }
        return {
            **alert,
            "facts": {
                **alert["facts"],
                "ai_narrative": narrative,
                "ai_provider": provider_name,
            },
        }

    @staticmethod
    def _narrative_cache_key(alert: Mapping[str, Any], provider_name: str) -> str:
        payload = {
            "provider": provider_name,
            "gid": alert.get("gid"),
            "rule_keys": alert.get("rule_keys"),
            "facts": {
                key: value
                for key, value in dict(alert.get("facts", {})).items()
                if key not in {"ai_narrative", "ai_provider"}
            },
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return sha256(encoded.encode("utf-8")).hexdigest()

    def get_scan(self, scan_id: str) -> dict[str, Any]:
        return self._repository.get_monitoring_scan(scan_id)

    def propose_actions(self, alert_id: str) -> dict[str, Any]:
        alert = self._repository.get_monitoring_alert(alert_id)
        return self._repository.create_action_proposals(
            alert_id=alert_id,
            proposals=self._proposals_for(alert),
            actor="copilot",
        )

    @staticmethod
    def _proposals_for(alert: Mapping[str, Any]) -> list[dict[str, Any]]:
        facts = alert["facts"]
        rule_count = len(alert["rule_keys"])
        proposals = [
            {
                "action_key": "prepare_aml_review_draft",
                "recommendation_score": min(0.95, 0.68 + 0.08 * rule_count),
                "rationale": (
                    "Собрать наблюдаемые факты и ограничения в проверяемый внутренний "
                    "черновик; документ останется на согласовании у аналитика."
                ),
                "expected_outcome": (
                    "Локальный AML-review draft и investigation без отправки в АФМ "
                    "или иную внешнюю систему."
                ),
            },
            {
                "action_key": "build_money_route",
                "recommendation_score": min(
                    0.9,
                    0.62 + 0.12 * float("rapid_pass_through_0_2d" in alert["rule_keys"]),
                ),
                "rationale": (
                    "Проверить, куда ушёл наблюдаемый поток, через ограниченный граф, "
                    "не выходящий за доступный snapshot."
                ),
                "expected_outcome": "Ego depth 2 и downstream-маршруты максимум до 4 колен.",
            },
            {
                "action_key": "create_local_watchlist",
                "recommendation_score": min(
                    0.88,
                    0.58 + 0.02 * min(10, int(facts.get("daily_unique_payers", 0))),
                ),
                "rationale": (
                    "Зафиксировать целевой узел и наблюдаемых прямых плательщиков "
                    "для последующей проверки аналитиком."
                ),
                "expected_outcome": (
                    "Локальный monitoring case; банковские счета и операции не меняются."
                ),
            },
        ]
        return explain_proposals(proposals, facts)

    def enrich_next_pending(self) -> bool:
        """Upgrade one historical pending case automatically, without changing decisions."""
        if not callable(getattr(self._narrative_provider, "assess_alert", None)):
            return False
        pending = [
            alert
            for scan in self._repository.list_auto_monitoring_scans()
            for alert in scan["alerts"]
            if float(alert["priority_score"]) >= self._ai_min_priority_score
            and not alert["facts"].get("ai_assessment")
            and float(alert["facts"].get("ai_retry_after", 0)) < time.time()
            and all(action["status"] == "proposed" for action in alert["actions"])
        ]
        if not pending:
            return False
        alert = max(pending, key=lambda item: float(item["priority_score"]))
        enriched = self._with_optional_narrative(alert)
        self._repository.update_alert_assessment(
            str(alert["id"]), enriched["facts"], self._proposals_for(enriched),
        )
        return True

    def decide_and_execute(
        self,
        action_id: str,
        decision: Literal["approve", "reject"],
        *,
        confirmation: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise AgenticValidationError("decision must be approve or reject")
        if decision == "approve" and confirmation != "APPROVE":
            raise AgenticValidationError("approve requires exact confirmation APPROVE")
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise AgenticValidationError(
                "idempotency_key must contain between 1 and 128 characters"
            )

        effect: Callable[[], Mapping[str, Any]] | None = None
        if decision == "approve":
            action = self._repository.get_agentic_action(action_id)
            alert = self._repository.get_monitoring_alert(str(action["alert_id"]))

            def build_approved_effect() -> Mapping[str, Any]:
                return self._build_effect(str(action["action_key"]), alert)

            effect = build_approved_effect
        return self._repository.decide_agentic_action(
            action_id=action_id,
            decision=decision,
            confirmation=confirmation,
            idempotency_key=idempotency_key,
            actor=self._actor,
            effect=effect,
        )

    def list_audit_events(self, *, limit: int, offset: int) -> dict[str, Any]:
        return self._repository.list_agentic_audit_events(limit=limit, offset=offset)

    def _load_transactions(self) -> pd.DataFrame:
        path = self._data_dir / "transactions.parquet"
        if not path.is_file():
            raise AgenticValidationError("transactions dataset is unavailable")
        frame = pd.read_parquet(path)
        required = {"src", "dst", "date", "sum_kzt"}
        missing = required.difference(frame.columns)
        if missing:
            raise AgenticValidationError(
                f"transactions.parquet is missing columns: {sorted(missing)}"
            )
        result = frame.loc[:, ["src", "dst", "date", "sum_kzt"]].copy()
        result["src"] = result["src"].astype(str)
        result["dst"] = result["dst"].astype(str)
        result["date"] = pd.to_datetime(result["date"], errors="raise").dt.normalize()
        result["sum_kzt"] = pd.to_numeric(result["sum_kzt"], errors="raise")
        result = result.sort_values(["date", "src", "dst", "sum_kzt"], kind="mergesort")
        if result.empty:
            raise AgenticValidationError("transactions.parquet contains no rows")
        return result.reset_index(drop=True)

    def _derive_alerts(
        self,
        historical: pd.DataFrame,
        daily: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        nodes = sorted(set(historical["src"]).union(historical["dst"]))
        cluster_ids = self._historical_clusters(historical, nodes)
        latest_date = historical["date"].max().date().isoformat()
        alerts: list[dict[str, Any]] = []
        for gid in nodes:
            incoming = historical.loc[historical["dst"] == gid]
            outgoing = historical.loc[historical["src"] == gid]
            daily_incoming = daily.loc[daily["dst"] == gid]
            daily_outgoing = daily.loc[daily["src"] == gid]
            unique_payers = int(daily_incoming["src"].nunique())
            incoming_kzt = float(daily_incoming["sum_kzt"].sum())
            outgoing_kzt = float(daily_outgoing["sum_kzt"].sum())
            cumulative_incoming = float(incoming["sum_kzt"].sum())
            cumulative_outgoing = float(outgoing["sum_kzt"].sum())
            pass_through = (
                cumulative_outgoing / cumulative_incoming if cumulative_incoming > 0 else None
            )
            fast_forward = self._fast_forward_ratio(incoming, outgoing)

            rule_keys: list[str] = []
            if unique_payers >= 8:
                rule_keys.append("daily_unique_payers")
            if incoming_kzt > 1_000_000:
                rule_keys.append("daily_incoming_kzt")
            has_daily_activity = not daily_incoming.empty or not daily_outgoing.empty
            if (
                has_daily_activity
                and pass_through is not None
                and 0.9 <= pass_through <= 1.1
                and fast_forward >= 0.7
            ):
                rule_keys.append("rapid_pass_through_0_2d")
            if not rule_keys:
                continue

            direct_payers = sorted(str(value) for value in incoming["src"].unique())
            facts: dict[str, Any] = {
                "daily_unique_payers": unique_payers,
                "daily_incoming_kzt": round(incoming_kzt, 2),
                "daily_outgoing_kzt": round(outgoing_kzt, 2),
                "cumulative_incoming_kzt": round(cumulative_incoming, 2),
                "cumulative_outgoing_kzt": round(cumulative_outgoing, 2),
                "pass_through": round(pass_through, 4) if pass_through is not None else None,
                "fast_forward_0_2d_ratio": round(fast_forward, 4),
                "direct_payers": direct_payers[:100],
                "direct_payers_truncated": len(direct_payers) > 100,
                "latest_transaction_date": latest_date,
                "source_time_granularity": "day",
            }
            role = self._historical_role(rule_keys)
            priority = min(
                0.99,
                0.45
                + 0.14 * len(rule_keys)
                + 0.08 * min(1.0, incoming_kzt / 1_000_000)
                + 0.05 * min(1.0, unique_payers / 8),
            )
            alerts.append(
                {
                    "gid": gid,
                    "rule_keys": rule_keys,
                    "facts": facts,
                    "role": role,
                    "cluster_id": cluster_ids[gid],
                    "priority_score": round(priority, 4),
                    "explanation": self._explain(gid, role, rule_keys, facts),
                }
            )
        return alerts

    @staticmethod
    def _fast_forward_ratio(incoming: pd.DataFrame, outgoing: pd.DataFrame) -> float:
        total_incoming = float(incoming["sum_kzt"].sum())
        if total_incoming <= 0 or outgoing.empty:
            return 0.0
        lots: deque[list[Any]] = deque(
            [row.date, float(str(row.sum_kzt))]
            for row in incoming.sort_values(["date", "src"], kind="mergesort").itertuples()
        )
        matched_fast = 0.0
        for transfer in outgoing.sort_values(["date", "dst"], kind="mergesort").itertuples():
            remaining = float(str(transfer.sum_kzt))
            while remaining > 0 and lots:
                lot_date, available = lots[0]
                if lot_date > transfer.date:
                    break
                used = min(remaining, available)
                delta_days = int((transfer.date - lot_date).days)
                if 0 <= delta_days <= 2:
                    matched_fast += used
                remaining -= used
                available -= used
                if available <= 1e-9:
                    lots.popleft()
                else:
                    lots[0][1] = available
        return min(1.0, matched_fast / total_incoming)

    @staticmethod
    def _historical_clusters(
        transactions: pd.DataFrame,
        nodes: Iterable[str],
    ) -> dict[str, str]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for row in transactions.loc[:, ["src", "dst"]].itertuples(index=False):
            adjacency[str(row.src)].add(str(row.dst))
            adjacency[str(row.dst)].add(str(row.src))
        unvisited = set(nodes)
        components: list[list[str]] = []
        while unvisited:
            start = min(unvisited)
            queue = deque([start])
            unvisited.remove(start)
            component: list[str] = []
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbour in sorted(adjacency[current]):
                    if neighbour in unvisited:
                        unvisited.remove(neighbour)
                        queue.append(neighbour)
            components.append(sorted(component))
        components.sort(key=lambda values: values[0])
        return {
            gid: f"replay-{index:03d}"
            for index, component in enumerate(components, start=1)
            for gid in component
        }

    @staticmethod
    def _historical_role(rule_keys: list[str]) -> str:
        if "daily_unique_payers" in rule_keys:
            return "consolidator_candidate"
        if "rapid_pass_through_0_2d" in rule_keys:
            return "transit_candidate"
        return "high_value_receiver_candidate"

    @staticmethod
    def _explain(
        gid: str,
        role: str,
        rule_keys: list[str],
        facts: Mapping[str, Any],
    ) -> str:
        return (
            f"Узел {gid}: гипотеза {role}; сработали правила {', '.join(rule_keys)}. "
            f"За день: {facts['daily_unique_payers']} уникальных плательщиков, "
            f"{facts['daily_incoming_kzt']:.2f} KZT входящего потока. "
            "Это приоритет проверки, а не вывод о нарушении."
        )

    def _build_effect(self, action_key: str, alert: Mapping[str, Any]) -> dict[str, Any]:
        gid = str(alert["gid"])
        facts = dict(alert["facts"])
        if action_key == "prepare_aml_review_draft":
            execution_id = str(uuid4())
            result = {
                "execution_id": execution_id,
                "kind": "aml_review_draft",
                "artifact_type": "aml_review_draft",
                "status": "draft",
                "destination": "internal_review",
                "submitted": False,
                "external_effects": [],
                "subject_gid": gid,
                "observed_facts": {
                    key: facts.get(key)
                    for key in (
                        "daily_unique_payers",
                        "daily_incoming_kzt",
                        "pass_through",
                        "fast_forward_0_2d_ratio",
                    )
                },
                "required_checks": [
                    "Проверить входящие операции вне текущей выборки.",
                    "Проверить историю до начала наблюдаемого периода.",
                    "Подтвердить выводы по банковским KYC/AML-данным.",
                ],
            }
            return {
                "result": result,
                "investigation": {
                    "title": f"AML review draft: {gid}",
                    "description": str(alert["explanation"]),
                    "model_version": "agentic-rules-v1",
                    "gids": [gid],
                    "note": (
                        "Автоматически подготовлен локальный черновик. "
                        "Не отправлен; требует проверки аналитиком."
                    ),
                },
            }
        if action_key == "build_money_route":
            return {"result": self._money_route(gid, str(alert["replay_date"]))}
        if action_key == "create_local_watchlist":
            payers = [str(value) for value in facts.get("direct_payers", [])]
            gids = list(dict.fromkeys([gid, *payers]))[:101]
            return {
                "result": {
                    "execution_id": str(uuid4()),
                    "kind": "local_watchlist",
                    "status": "created",
                    "local_only": True,
                    "gids": gids,
                    "external_effects": [],
                },
                "investigation": {
                    "title": f"Local monitoring watchlist: {gid}",
                    "description": (
                        "Target and observed direct payers selected for local analyst review."
                    ),
                    "model_version": "agentic-rules-v1",
                    "gids": gids,
                    "note": (
                        "Локальный список наблюдения; счета и операции в банковских "
                        "системах не изменены."
                    ),
                },
            }
        raise AgenticValidationError(f"Unsupported action_key: {action_key}")

    def _money_route(self, gid: str, replay_date: str) -> dict[str, Any]:
        transactions = self._load_transactions()
        observed = transactions.loc[transactions["date"] <= pd.Timestamp(replay_date)]
        adjacency: dict[str, list[str]] = defaultdict(list)
        reverse: dict[str, list[str]] = defaultdict(list)
        edge_rows: list[dict[str, Any]] = []
        for (src, dst), group in observed.groupby(["src", "dst"], sort=True):
            source, target = str(src), str(dst)
            adjacency[source].append(target)
            reverse[target].append(source)
            edge_rows.append(
                {
                    "src": source,
                    "dst": target,
                    "sum_kzt": round(float(group["sum_kzt"].sum()), 2),
                    "tx_count": len(group),
                }
            )

        paths: list[list[str]] = []
        queue: deque[list[str]] = deque([[gid]])
        while queue and len(paths) < 100:
            path = queue.popleft()
            if len(path) - 1 >= 4:
                continue
            for nxt in sorted(adjacency[path[-1]]):
                if nxt in path:
                    continue
                new_path = [*path, nxt]
                paths.append(new_path)
                queue.append(new_path)
                if len(paths) >= 100:
                    break

        ego = {gid}
        frontier = {gid}
        for _ in range(2):
            frontier = {
                neighbour
                for node in frontier
                for neighbour in [*adjacency[node], *reverse[node]]
                if neighbour not in ego
            }
            ego.update(frontier)
        ego_edges = [edge for edge in edge_rows if edge["src"] in ego and edge["dst"] in ego][:500]
        return {
            "execution_id": str(uuid4()),
            "kind": "observed_money_route",
            "status": "built",
            "target_gid": gid,
            "replay_date": replay_date,
            "ego_depth": 2,
            "max_depth": 4,
            "nodes": sorted(ego)[:500],
            "edges": ego_edges,
            "downstream_paths": paths,
            "observed_only": True,
            "external_effects": [],
        }
