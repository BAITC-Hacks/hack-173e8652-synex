from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import httpx
import streamlit as st
from streamlit.testing.v1 import AppTest

from moneygraph.ui.agentic import _route_figure_payload
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
    client.agentic_monitoring()

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
    assert calls[5][0:2] == ("GET", "/api/v1/agentic/monitoring")


class _AgenticFakeClient:
    decisions: ClassVar[list[dict[str, object]]] = []
    auto_monitoring: ClassVar[bool] = False

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

    def agentic_monitoring(self) -> dict[str, object]:
        if not self.auto_monitoring:
            return {
                "enabled": False,
                "state": "disabled",
                "processed_days": 0,
                "total_days": 0,
                "recent_alerts": [],
                "latest_scan": None,
            }
        scan = self.run_agentic_scan()
        alert = dict(scan["alerts"][0])  # type: ignore[index]
        alert["facts"] = {
            **alert["facts"],  # type: ignore[dict-item]
            "ai_narrative": "Проверьте концентрацию входящего потока.",
            "ai_provider": "test-model",
        }
        alert["actions"] = self.propose_agentic_actions()["actions"]
        alert["replay_date"] = "2026-07-16"
        scan["alerts"] = [alert]
        return {
            "enabled": True,
            "state": "running",
            "cadence_seconds": 2,
            "processed_days": 1,
            "total_days": 31,
            "recent_alerts": [alert],
            "latest_scan": scan,
        }

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


def _agentic_app() -> AppTest:
    app_path = Path(__file__).parents[1] / "src/moneygraph/ui/app.py"
    return AppTest.from_file(app_path, default_timeout=10)


def test_agentic_loop_renders_four_ordered_tabs_and_day_level_safety_copy() -> None:
    _AgenticFakeClient.decisions = []
    _AgenticFakeClient.auto_monitoring = False
    with patch("moneygraph.ui.client.APIClient", _AgenticFakeClient):
        app = _agentic_app().run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == TAB_LABELS
    visible = " ".join(
        element.value for element in [*app.markdown, *app.caption, *app.info, *app.success]
    )
    assert "календарн" in visible.lower()
    assert "не блокир" in visible.lower()
    assert "≥ 8" in visible
    assert "> 1 000 000" in visible
    assert "0–2" in visible


def test_agentic_loop_requires_explicit_human_approval_before_execution() -> None:
    _AgenticFakeClient.decisions = []
    _AgenticFakeClient.auto_monitoring = False
    with patch("moneygraph.ui.client.APIClient", _AgenticFakeClient):
        app = _agentic_app().run()
        _button(app, "Запустить дневной replay").click().run()
        _button(app, "Сформировать 3 предложения").click().run()

        assert len(_AgenticFakeClient.decisions) == 0
        approve = _button(app, "Approve и исполнить")
        assert approve.disabled
        assert not any(item.label == "Контрольная фраза" for item in app.text_input)

        app.checkbox[0].check().run()
        assert not _button(app, "Approve и исполнить").disabled
        _button(app, "Approve и исполнить").click().run()

    assert len(_AgenticFakeClient.decisions) == 1
    assert _AgenticFakeClient.decisions[0]["decision"] == "approve"
    assert _AgenticFakeClient.decisions[0]["confirmation"] == "APPROVE"
    assert any("case-001" in str(item.value) for item in [*app.markdown, *app.json])
    assert not any(button.label == "Approve и исполнить" for button in app.button)


def test_agentic_loop_rejects_without_running_an_execution() -> None:
    _AgenticFakeClient.decisions = []
    _AgenticFakeClient.auto_monitoring = False
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


def test_autonomous_feed_shows_alert_and_proposals_without_analyst_click() -> None:
    _AgenticFakeClient.decisions = []
    _AgenticFakeClient.auto_monitoring = True
    with patch("moneygraph.ui.client.APIClient", _AgenticFakeClient):
        app = _agentic_app().run()

    assert not app.exception
    visible = " ".join(
        element.value for element in [*app.markdown, *app.caption, *app.info, *app.success]
    )
    assert "1 из 31" in visible
    assert "Узел получил средства от 11 разных плательщиков" in visible
    assert any(metric.label == "Последний день" for metric in app.metric)
    assert "Дата alert: 2026-07-16" in visible
    assert "Дополнительная формулировка test-model" in visible
    assert "Вариант A" in visible
    assert "Вариант B" in visible
    assert "Вариант C" in visible
    assert _AgenticFakeClient.decisions == []
    assert _button(app, "Approve и исполнить").disabled


def test_approved_route_is_bounded_before_drawing() -> None:
    nodes = [f"gid-{index}" for index in range(100)]
    edges = [
        {"src": "gid-0", "dst": f"gid-{index}", "sum_kzt": 1000}
        for index in range(1, 100)
    ]
    result = _route_figure_payload({"target_gid": "gid-0", "nodes": nodes, "edges": edges})

    assert result["root_gid"] == "gid-0"
    assert len(result["nodes"]) == 80
    assert len(result["edges"]) == 79


def test_receipt_is_visible_only_for_the_selected_action() -> None:
    _AgenticFakeClient.decisions = []
    _AgenticFakeClient.auto_monitoring = True
    with patch("moneygraph.ui.client.APIClient", _AgenticFakeClient):
        app = _agentic_app().run()
        app.checkbox[0].check().run()
        _button(app, "Approve и исполнить").click().run()
        assert any("case-001" in str(item.value) for item in app.json)

        action_radio = next(radio for radio in app.radio if radio.label == "Выбранное действие")
        action_radio.set_value("action-002").run()

    assert not any("case-001" in str(item.value) for item in app.json)


def test_priority_case_is_selected_without_manual_scan_or_proposal_request() -> None:
    _AgenticFakeClient.decisions = []
    _AgenticFakeClient.auto_monitoring = True
    payload = _AgenticFakeClient().agentic_monitoring()
    priority = dict(payload["recent_alerts"][0])  # type: ignore[index]
    priority.update(
        {
            "id": "alert-priority",
            "gid": "001000000000000999",
            "replay_date": "2026-07-03",
            "priority_score": 0.99,
            "explanation": "Приоритетный узел выбран системой автоматически.",
        }
    )
    priority["actions"] = [
        {**action, "id": f"priority-{index}", "alert_id": "alert-priority"}
        for index, action in enumerate(priority["actions"], start=1)  # type: ignore[union-attr]
    ]
    payload["priority_queue"] = [priority]
    payload["queue_size"] = 1

    class PriorityClient(_AgenticFakeClient):
        def agentic_monitoring(self) -> dict[str, object]:
            return payload

        def run_agentic_scan(self, *_: object, **__: object) -> dict[str, object]:
            raise AssertionError("The landing must not require a manual scan")

        def propose_agentic_actions(self, *_: object, **__: object) -> dict[str, object]:
            raise AssertionError("Prepared actions must be shown without a manual request")

    with patch("moneygraph.ui.client.APIClient", PriorityClient):
        app = _agentic_app().run()

    assert not app.exception
    visible = " ".join(
        element.value for element in [*app.markdown, *app.caption, *app.info, *app.success]
    )
    assert "Приоритетный узел выбран системой автоматически" in visible
    assert "001000000000000999" in visible
    assert "Следующий кейс для проверки" in visible
    assert any(expander.label == "Ручной replay (диагностика)" for expander in app.expander)


def test_approved_aml_document_is_rendered_and_download_contains_real_document() -> None:
    document = "# Черновик AML-проверки\n\nGID collector: 8 плательщиков, 1200000 KZT.\nНе отправлен."
    script = f'''
import streamlit as st
from moneygraph.ui.agentic import _execution_result
st.session_state["agentic_decision_result"] = {{
    "action_id": "draft-action", "status": "executed", "result": {{
        "kind": "aml_review_draft", "subject_gid": "collector",
        "document_markdown": {document!r}, "submitted": False,
    }}
}}
_execution_result("draft-action")
'''
    with patch("moneygraph.ui.agentic.st.download_button", wraps=st.download_button) as download:
        app = AppTest.from_string(script).run()

    assert not app.exception
    assert any(document == item.value for item in app.markdown)
    assert download.call_count == 1
    assert download.call_args.kwargs["data"] == document.encode("utf-8")
    assert download.call_args.kwargs["mime"] == "text/markdown"
    assert download.call_args.kwargs["file_name"].endswith(".md")


def test_failed_execution_is_not_presented_as_a_successful_receipt() -> None:
    app = AppTest.from_string(
        '''
import streamlit as st
from moneygraph.ui.agentic import _execution_result
st.session_state["agentic_decision_result"] = {
    "action_id": "failed-action", "status": "failed",
    "result": {"executed": False, "error": "tool_execution_failed"},
}
_execution_result("failed-action")
'''
    ).run()

    assert not app.exception
    assert not app.success
    assert app.error


def test_saved_document_is_visible_without_a_session_receipt_after_reload() -> None:
    app = AppTest.from_string(
        '''
from moneygraph.ui.agentic import _execution_result
_execution_result("stored-action", {
    "id": "stored-action", "status": "executed", "result": {
        "kind": "aml_review_draft", "document_markdown": "# Сохранённый черновик",
        "subject_gid": "collector", "submitted": False,
    },
})
'''
    ).run()

    assert not app.exception
    assert any(item.value == "# Сохранённый черновик" for item in app.markdown)


def test_failed_approval_response_does_not_claim_the_tool_executed() -> None:
    script = '''
from moneygraph.ui.agentic import _submit_decision
class Feed:
    def decide_agentic_action(self, *args, **kwargs):
        return {"action_id": "failed-action", "status": "failed",
                "result": {"executed": False, "error": "tool_execution_failed"}}
_submit_decision(Feed(), "failed-action", "approve", "APPROVE")
'''
    with patch("moneygraph.ui.agentic.st.rerun"):
        app = AppTest.from_string(script).run()

    assert not app.exception
    assert not app.success
    assert app.error
