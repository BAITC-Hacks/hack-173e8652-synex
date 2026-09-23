from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

from moneygraph.ui.client import APIClient

TAB_LABELS = [
    "1 · Мониторинг",
    "2 · Alert + Explain",
    "3 · Decision Support",
    "4 · Approve → Execute → Audit",
]


def test_agentic_api_client_preserves_the_versioned_request_contract() -> None:
    calls: list[tuple[str, str, dict[str, object], dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body, dict(request.headers)))
        if request.url.path.endswith("/audit"):
            return httpx.Response(200, json={"data": [], "meta": {"total": 0}})
        return httpx.Response(200, json={"data": {"id": "saved"}})

    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    client = APIClient("http://test", client=http_client)

    client.run_agentic_scan("2026-07-31", interval_minutes=5, limit=10)
    client.agentic_scan("scan-001")
    client.propose_agentic_actions("alert-001")
    client.decide_agentic_action(
        "action-001",
        decision="approve",
        confirmation="APPROVE",
        idempotency_key="demo-decision-001",
    )
    audit = client.agentic_audit(limit=75, offset=25)

    assert calls[0][:3] == (
        "POST",
        "/api/v1/agentic/scans",
        {"replay_date": "2026-07-31", "interval_minutes": 5, "limit": 10},
    )
    assert calls[1][0:2] == ("GET", "/api/v1/agentic/scans/scan-001")
    assert calls[2][:3] == (
        "POST",
        "/api/v1/agentic/alerts/alert-001/proposals",
        {},
    )
    assert calls[3][:3] == (
        "POST",
        "/api/v1/agentic/actions/action-001/decision",
        {
            "decision": "approve",
            "confirmation": "APPROVE",
        },
    )
    assert calls[3][3]["idempotency-key"] == "demo-decision-001"
    assert calls[4][0:2] == ("GET", "/api/v1/agentic/audit")
    assert calls[4][2] == {}
    assert audit == []


class _AgenticFakeClient:
    decisions: ClassVar[list[dict[str, object]]] = []

    def __init__(self, *_: object, **__: object) -> None:
        self.base_url = "http://fake-api"

    def health(self) -> dict[str, object]:
        return {"status": "ok"}

    def run_agentic_scan(self, *_: object, **__: object) -> dict[str, object]:
        return {
            "id": "scan-001",
            "replay_date": "2026-07-31",
            "simulation": True,
            "source_time_granularity": "day",
            "alerts": [
                {
                    "id": "alert-001",
                    "gid": "001000000000000123",
                    "severity": "critical",
                    "status": "open",
                    "role": "consolidator",
                    "cluster_id": 2,
                    "priority_score": 0.93,
                    "rule_keys": ["daily_incoming_kzt"],
                    "facts": {
                        "daily_unique_payers": 11,
                        "daily_incoming_kzt": 1_200_000,
                        "pass_through": 0.97,
                        "fast_forward_0_2d_ratio": 0.91,
                    },
                    "explanation": "Узел получил средства от 11 разных плательщиков.",
                    "limitations": ["Исходник содержит только дату; dwell <2h не измеряется."],
                }
            ],
        }

    def agentic_scan(self, *_: object, **__: object) -> dict[str, object]:
        return self.run_agentic_scan()

    def propose_agentic_actions(self, *_: object, **__: object) -> dict[str, object]:
        return {
            "alert_id": "alert-001",
            "actions": [
                {
                    "id": "action-001",
                    "action_key": "prepare_aml_review_draft",
                    "title": "Подготовить черновик для внутренней проверки",
                    "recommendation_score": 0.82,
                    "rationale": "Три наблюдаемых признака поддерживают углублённую проверку.",
                    "expected_outcome": "Черновик без отправки во внешнюю систему.",
                    "status": "proposed",
                },
                {
                    "id": "action-002",
                    "action_key": "build_money_route",
                    "title": "Построить маршрут денег",
                    "recommendation_score": 0.75,
                    "rationale": "Покажет наблюдаемые исходящие связи.",
                    "expected_outcome": "Bounded граф до 4 колен.",
                    "status": "proposed",
                },
                {
                    "id": "action-003",
                    "action_key": "create_local_watchlist",
                    "title": "Создать локальный watchlist",
                    "recommendation_score": 0.68,
                    "rationale": "Связанные плательщики требуют проверки.",
                    "expected_outcome": "Локальный кейс наблюдения.",
                    "status": "proposed",
                },
            ],
        }

    def decide_agentic_action(self, action_id: str, **kwargs: object) -> dict[str, object]:
        decision = {"action_id": action_id, **kwargs}
        self.decisions.append(decision)
        return {
            "action_id": action_id,
            "status": "executed" if kwargs["decision"] == "approve" else "rejected",
            "result": {
                "tool": "prepare_aml_review_draft",
                "artifact_type": "aml_review_draft",
                "submitted": False,
                "investigation_id": "case-001",
            }
            if kwargs["decision"] == "approve"
            else None,
        }

    def agentic_audit(self, **_: object) -> list[dict[str, object]]:
        events: list[dict[str, object]] = [
            {
                "created_at": "2026-09-23T14:30:00Z",
                "action": "monitor.scan_completed",
                "actor": "rules-engine",
                "entity_id": "scan-001",
            },
            {
                "created_at": "2026-09-23T14:30:01Z",
                "action": "alert.created",
                "actor": "rules-engine",
                "entity_id": "alert-001",
            },
        ]
        if self.decisions:
            events.append(
                {
                    "created_at": "2026-09-23T14:31:00Z",
                    "action": f"action.{self.decisions[-1]['decision']}d",
                    "actor": "demo-analyst",
                    "entity_id": str(self.decisions[-1]["action_id"]),
                }
            )
        return events


def _button(app: AppTest, label: str):  # type: ignore[no-untyped-def]
    return next(button for button in app.button if button.label == label)


def _text_input(app: AppTest, label: str):  # type: ignore[no-untyped-def]
    return next(text_input for text_input in app.text_input if text_input.label == label)


def _agentic_app() -> AppTest:
    app_path = Path(__file__).parents[1] / "src/moneygraph/ui/app.py"
    return AppTest.from_file(app_path, default_timeout=10)


def test_agentic_loop_renders_four_ordered_tabs_and_day_level_safety_copy() -> None:
    _AgenticFakeClient.decisions = []
    with patch("moneygraph.ui.client.APIClient", _AgenticFakeClient):
        app = _agentic_app().run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == TAB_LABELS
    visible = " ".join(element.value for element in [*app.markdown, *app.caption, *app.info])
    assert "календарн" in visible.lower()
    assert "не блокир" in visible.lower()
    assert "≥ 8" in visible
    assert "> 1 000 000" in visible
    assert "0–2" in visible


def test_agentic_loop_requires_explicit_human_approval_before_execution() -> None:
    _AgenticFakeClient.decisions = []
    with patch("moneygraph.ui.client.APIClient", _AgenticFakeClient):
        app = _agentic_app().run()
        _button(app, "Запустить дневной replay").click().run()
        _button(app, "Сформировать 3 предложения").click().run()

        assert len(_AgenticFakeClient.decisions) == 0
        approve = _button(app, "Approve и исполнить")
        assert approve.disabled

        app.checkbox[0].check().run()
        _text_input(app, "Контрольная фраза").set_value("APPROVE").run()
        _button(app, "Approve и исполнить").click().run()

    assert len(_AgenticFakeClient.decisions) == 1
    assert _AgenticFakeClient.decisions[0]["decision"] == "approve"
    assert _AgenticFakeClient.decisions[0]["confirmation"] == "APPROVE"
    assert any("case-001" in str(item.value) for item in [*app.markdown, *app.json])
    assert not any(button.label == "Approve и исполнить" for button in app.button)


def test_agentic_loop_rejects_without_running_an_execution() -> None:
    _AgenticFakeClient.decisions = []
    with patch("moneygraph.ui.client.APIClient", _AgenticFakeClient):
        app = _agentic_app().run()
        _button(app, "Запустить дневной replay").click().run()
        _button(app, "Сформировать 3 предложения").click().run()
        _button(app, "Reject предложение").click().run()

    assert len(_AgenticFakeClient.decisions) == 1
    assert _AgenticFakeClient.decisions[0]["decision"] == "reject"
    assert _AgenticFakeClient.decisions[0]["confirmation"] is None
    assert not any("case-001" in str(item.value) for item in app.json)
    assert not any(button.label == "Approve и исполнить" for button in app.button)
