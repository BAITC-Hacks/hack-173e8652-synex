from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from moneygraph.ui.app import NAV_PAGES, ai_status_notice
from moneygraph.ui.client import APIClient, APIClientError
from moneygraph.ui.graph import ROLE_COLORS, build_ego_figure
from moneygraph.ui.helpers import as_records, format_kzt, format_period, parse_gid_list


def test_navigation_contains_every_required_workspace() -> None:
    assert NAV_PAGES == (
        "Agentic Loop",
        "Dashboard",
        "Network Explorer",
        "Top Nodes",
        "Clusters",
        "Flow Investigation",
        "Investigations",
        "AI Copilot",
    )


def test_parse_gid_list_preserves_opaque_strings_deduplicates_and_caps() -> None:
    parsed = parse_gid_list("001, alpha\n001; 42 | beta", max_items=3)

    assert parsed == ["001", "alpha", "42"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0 ₸"), (1250, "1 250 ₸"), (1_250_000.5, "1,25 млн ₸"), (None, "—")],
)
def test_format_kzt_is_compact_and_human_readable(value: float | None, expected: str) -> None:
    assert format_kzt(value) == expected


def test_period_dict_is_rendered_as_a_readable_range() -> None:
    assert format_period({"start": "2026-07-01", "end": "2026-07-31"}) == (
        "2026-07-01 — 2026-07-31"
    )


def test_as_records_supports_enveloped_and_plain_list_responses() -> None:
    assert as_records({"data": [{"gid": "1"}]}) == [{"gid": "1"}]
    assert as_records({"items": [{"gid": "2"}]}) == [{"gid": "2"}]
    assert as_records([{"gid": "3"}]) == [{"gid": "3"}]


def test_api_client_unwraps_success_envelope_and_keeps_string_gid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/nodes/00123/ego"
        assert request.url.params["depth"] == "2"
        return httpx.Response(
            200,
            json={
                "data": {
                    "root_gid": "00123",
                    "nodes": [{"gid": "00123", "role": "distributor"}],
                    "edges": [],
                },
                "meta": {"request_id": "test"},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    client = APIClient("http://test/", client=http_client)

    payload = client.ego("00123", depth=2)

    assert payload["root_gid"] == "00123"
    assert payload["nodes"][0]["gid"] == "00123"


def test_api_client_exposes_safe_api_error_message() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "node_not_found", "message": "Unknown gid"}},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    client = APIClient("http://test", client=http_client)

    with pytest.raises(APIClientError, match="Unknown gid"):
        client.node("missing")


def test_api_client_sends_assistant_context_without_serializing_gid_as_number() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": {
                    "answer": "Ответ",
                    "provider": "deterministic",
                    "fallback": True,
                    "tools_used": [],
                    "limitations": [],
                }
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    client = APIClient("http://test", client=http_client)
    result = client.ask_assistant("Почему?", gids=["001", "alpha"])

    assert captured == {"query": "Почему?", "gids": ["001", "alpha"], "cluster_id": None}
    assert result["fallback"] is True


def test_api_client_covers_all_workspace_endpoint_contracts() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path in {"/health", "/api/v1/summary"}:
            return httpx.Response(200, json={"data": {"status": "ok"}})
        if request.url.path.endswith("/export"):
            return httpx.Response(200, json={"data": {"ok": True}})
        return httpx.Response(200, json={"data": {"items": [], "ok": True}})

    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    client = APIClient("http://test", client=http_client)

    assert client.health()["status"] == "ok"
    assert client.summary()["status"] == "ok"
    client.top_nodes(limit=12, role="coordinator", cluster_id=3)
    client.node("gid/with slash")
    client.trace("001", "upstream", depth=2)
    client.trace("001", "downstream", depth=3)
    client.common_receivers(["001", "002"], max_depth=4)
    client.clusters(limit=25)
    client.cluster(3)
    client.resilience()
    client.investigations(limit=25)
    client.investigation(7)
    client.create_investigation("Case", description="Observed facts", gids=["001"])
    client.add_nodes(7, ["002"])
    client.add_note(7, "Analyst note")
    client.update_status(7, "in_review")

    # httpx exposes a decoded path to MockTransport; successful construction
    # still proves the opaque string reached the client without numeric coercion.
    assert ("GET", "/api/v1/nodes/gid/with slash") in calls
    assert ("PATCH", "/api/v1/investigations/7/status") in calls
    assert client.export_url(7, output_format="csv").endswith("/export?format=csv")


def test_api_client_rejects_unknown_trace_direction_without_request() -> None:
    client = APIClient("http://test")

    with pytest.raises(ValueError, match="upstream or downstream"):
        client.trace("001", "sideways")


def test_build_ego_figure_has_arrows_edge_tooltips_and_one_legend_item_per_role() -> None:
    figure = build_ego_figure(
        {
            "root_gid": "001",
            "nodes": [
                {"gid": "001", "role": "distributor", "priority_score": 0.91, "cluster_id": 2},
                {"gid": "010", "role": "consolidator", "priority_score": 0.73, "cluster_id": 2},
                {"gid": "alpha", "role": "consolidator", "priority_score": 0.4, "cluster_id": 3},
            ],
            "edges": [
                {"src": "001", "dst": "010", "sum_kzt": 25_000, "n_tx": 2},
                {"src": "010", "dst": "alpha", "sum_kzt": 10_000, "n_tx": 1},
            ],
        }
    )

    trace_names = [trace.name for trace in figure.data]
    node_text = " ".join(str(value) for trace in figure.data for value in (trace.text or ()))
    edge_hover = " ".join(str(value) for trace in figure.data for value in (trace.hovertext or ()))

    assert trace_names.count("distributor") == 1
    assert trace_names.count("consolidator") == 1
    assert len(figure.layout.annotations) == 2
    assert "001" in node_text
    assert "010" in node_text
    assert "25 000" in edge_hover
    assert "2 перевод" in edge_hover


def test_role_legend_covers_the_canonical_taxonomy() -> None:
    assert set(ROLE_COLORS) >= {
        "consolidator",
        "transit",
        "distributor",
        "terminal",
        "coordinator",
        "peripheral",
    }


def test_empty_ego_figure_returns_explanatory_annotation() -> None:
    figure = build_ego_figure({"nodes": [], "edges": []})

    assert not figure.data
    assert "нет узлов" in figure.layout.annotations[0].text.lower()


def test_ai_disabled_notice_is_clear_about_offline_fallback() -> None:
    notice = ai_status_notice(enabled=False, provider="openai")

    assert "отключ" in notice.lower()
    assert "детерминирован" in notice.lower()
    assert "api-ключ" not in notice.lower()


def test_sidebar_ai_status_uses_api_health_not_streamlit_environment() -> None:
    class LiveAIClient(_FakeAppClient):
        def health(self) -> dict[str, object]:
            return {
                "status": "ok",
                "agentic_narrative_enabled": True,
                "agentic_narrative_provider": "openai",
            }

    app_path = Path(__file__).parents[1] / "src/moneygraph/ui/app.py"
    with (
        patch("moneygraph.ui.client.APIClient", LiveAIClient),
        patch.dict("os.environ", {"AI_ENABLED": "false", "AI_PROVIDER": "fallback"}),
    ):
        app = AppTest.from_file(app_path, default_timeout=10).run()

    assert not app.exception
    assert any("AI: openai" in info.value for info in app.info)


def test_sidebar_ai_status_does_not_claim_ai_from_streamlit_environment() -> None:
    app_path = Path(__file__).parents[1] / "src/moneygraph/ui/app.py"
    with (
        patch("moneygraph.ui.client.APIClient", _FakeAppClient),
        patch.dict("os.environ", {"AI_ENABLED": "true", "AI_PROVIDER": "openai"}),
    ):
        app = AppTest.from_file(app_path, default_timeout=10).run()

    assert not app.exception
    assert any("AI: offline fallback" in item.value for item in app.caption)


def test_api_start_hint_uses_local_python_and_loopback() -> None:
    class UnavailableSummaryClient(_FakeAppClient):
        def summary(self) -> dict[str, object]:
            raise APIClientError("API unavailable")

    app_path = Path(__file__).parents[1] / "src/moneygraph/ui/app.py"
    with patch("moneygraph.ui.client.APIClient", UnavailableSummaryClient):
        app = AppTest.from_file(app_path, default_timeout=10).run()
        app.radio[0].set_value("Dashboard").run()

    assert not app.exception
    messages = " ".join(info.value for info in app.info)
    assert ".venv/bin/python -m uvicorn" in messages
    assert "--host 127.0.0.1" in messages
    assert "0.0.0.0" not in messages


class _FakeAppClient:
    def __init__(self, *_: object, **__: object) -> None:
        self.base_url = "http://fake-api"

    def health(self) -> dict[str, object]:
        return {"status": "ok"}

    def summary(self) -> dict[str, object]:
        return {
            "counts": {
                "nodes": 3,
                "edges": 2,
                "transactions": 4,
                "seed": 1,
            },
            "weakly_connected_components": 1,
            "clusters": 1,
            "role_distribution": {"distributor": 1, "consolidator": 2},
            "duration_seconds": 1.2,
            "period": {"start": "2026-07-01", "end": "2026-07-31"},
            "warnings": ["Depth-4 boundary is censored."],
        }

    def top_nodes(self, **_: object) -> list[dict[str, object]]:
        return [
            {"rank": 1, "gid": "001", "role": "distributor", "priority_score": 0.91},
            {"rank": 2, "gid": "010", "role": "consolidator", "priority_score": 0.73},
        ]

    def node(self, gid: str) -> dict[str, object]:
        return {
            "gid": gid,
            "role": "distributor" if gid == "001" else "consolidator",
            "priority_score": 0.91,
            "cluster_id": 2,
            "in_kzt": 10_000,
            "out_kzt": 20_000,
            "evidence": "priority driven by observed graph metrics",
        }

    def ego(self, gid: str, *, depth: int = 1) -> dict[str, object]:
        del depth
        return {
            "root_gid": gid,
            "nodes": [self.node(gid), self.node("010")],
            "edges": [{"src": gid, "dst": "010", "sum_kzt": 20_000, "n_tx": 2}],
        }

    def investigations(self, **_: object) -> list[dict[str, object]]:
        return [{"id": "case-7", "title": "Demo case", "status": "new"}]

    def investigation(self, investigation_id: str) -> dict[str, object]:
        return {
            "id": investigation_id,
            "title": "Demo case",
            "status": "new",
            "nodes": [self.node("001")],
        }

    def clusters(self, **_: object) -> list[dict[str, object]]:
        return [
            {
                "cluster_id": 2,
                "size": 2,
                "seed_count": 1,
                "internal_turnover_kzt": 30_000,
                "hypothesis": "Observed local concentration",
            }
        ]

    def resilience(self) -> list[dict[str, object]]:
        return [
            {"scenario": "priority", "n_removed": 1, "largest_component_size": 2},
            {"scenario": "baseline", "n_removed": 1, "largest_component_size": 3},
        ]

    def cluster(self, cluster_id: int) -> dict[str, object]:
        return {
            "cluster_id": cluster_id,
            "size": 2,
            "seed_count": 1,
            "internal_turnover_kzt": 30_000,
            "hypothesis": "Observed local concentration",
            "nodes": [self.node("001"), self.node("010")],
            "edges": [{"src": "001", "dst": "010", "sum_kzt": 20_000, "n_tx": 2}],
        }

    def trace(self, gid: str, direction: str, *, depth: int = 4) -> dict[str, object]:
        return {"paths": [{"gid": gid, "direction": direction, "depth": depth}]}

    def common_receivers(self, gids: list[str], *, max_depth: int = 4) -> dict[str, object]:
        return {"candidates": [{"gid": "010", "coverage_ratio": len(gids), "depth": max_depth}]}

    def create_investigation(self, *_: object, **__: object) -> dict[str, object]:
        return {"id": "case-8"}

    def add_nodes(self, *_: object, **__: object) -> dict[str, bool]:
        return {"ok": True}

    def add_note(self, *_: object, **__: object) -> dict[str, bool]:
        return {"ok": True}

    def update_status(self, *_: object, **__: object) -> dict[str, bool]:
        return {"ok": True}

    def export_url(self, investigation_id: int, *, output_format: str = "json") -> str:
        return f"http://fake-api/cases/{investigation_id}.{output_format}"

    def ask_assistant(self, *_: object, **__: object) -> dict[str, object]:
        return {
            "answer": "Факт: gid=001 имеет высокий наблюдаемый priority.",
            "provider": "deterministic",
            "fallback": True,
            "tools_used": ["get_node_profile"],
            "limitations": ["Observed graph only."],
        }

    def run_agentic_scan(self, *_: object, **__: object) -> dict[str, object]:
        return {"id": "scan-1", "simulation": True, "alerts": []}

    def agentic_scan(self, *_: object, **__: object) -> dict[str, object]:
        return self.run_agentic_scan()

    def agentic_monitoring(self) -> dict[str, object]:
        return {
            "enabled": False,
            "state": "disabled",
            "processed_days": 0,
            "total_days": 0,
            "recent_alerts": [],
            "latest_scan": None,
        }

    def propose_agentic_actions(self, *_: object, **__: object) -> dict[str, object]:
        return {"actions": []}

    def decide_agentic_action(self, *_: object, **__: object) -> dict[str, object]:
        return {"status": "rejected"}

    def agentic_audit(self, **_: object) -> list[dict[str, object]]:
        return []


def _button(app: AppTest, label: str):  # type: ignore[no-untyped-def]
    return next(button for button in app.button if button.label == label)


def test_streamlit_app_runs_every_workspace_and_core_interactions() -> None:
    with patch("moneygraph.ui.client.APIClient", _FakeAppClient):
        app_path = Path(__file__).parents[1] / "src/moneygraph/ui/app.py"
        app = AppTest.from_file(app_path, default_timeout=10).run()
        assert not app.exception

        app.radio[0].set_value("Dashboard").run()
        assert len(app.metric) >= 6
        assert [metric.value for metric in app.metric[:6]] == ["3", "2", "4", "1", "1", "1"]

        app.radio[0].set_value("Network Explorer").run()
        app.text_input[0].set_value("001").run()
        assert not app.exception
        _button(app, "↓ Downstream").click().run()
        assert not app.exception

        app.radio[0].set_value("Top Nodes").run()
        assert not app.exception

        app.radio[0].set_value("Clusters").run()
        assert not app.exception

        app.radio[0].set_value("Flow Investigation").run()
        app.text_area[0].set_value("001, 010").run()
        _button(app, "Common receivers").click().run()
        assert not app.exception

        app.radio[0].set_value("Investigations").run()
        assert not app.exception

        app.radio[0].set_value("AI Copilot").run()
        _button(app, "Проанализировать").click().run()
        assert not app.exception
        assert any("gid=001" in markdown.value for markdown in app.markdown)
