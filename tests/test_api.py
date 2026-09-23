from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from moneygraph.api.main import create_app
from moneygraph.api.settings import APISettings

GID_A = "100000000000000001"
GID_B = "100000000000000002"
GID_C = "100000000000000003"


def _write_artifacts(root: Path) -> tuple[Path, Path, Path]:
    out_dir = root / "out"
    artifacts_dir = root / "artifacts"
    data_dir = root / "data"
    out_dir.mkdir()
    artifacts_dir.mkdir()
    data_dir.mkdir()
    pd.DataFrame(
        [
            {
                "gid": GID_A,
                "role": "peripheral",
                "role_score": 0.2,
                "cluster_id": 1,
                "priority_score": 0.1,
                "evidence": "Наблюдается 2 связи.",
            },
            {
                "gid": GID_B,
                "role": "transit",
                "role_score": 0.7,
                "cluster_id": 1,
                "priority_score": 0.6,
                "evidence": "Наблюдается 2 связи.",
            },
            {
                "gid": GID_C,
                "role": "coordinator",
                "role_score": 0.9,
                "cluster_id": 1,
                "priority_score": 0.95,
                "evidence": "Наблюдается 3 связи.",
            },
        ]
    ).to_csv(out_dir / "nodes_roles.csv", index=False)
    pd.DataFrame(
        [
            {
                "rank": 1,
                "gid": GID_C,
                "role": "coordinator",
                "priority_score": 0.95,
                "why": "Три наблюдаемые связи.",
            }
        ]
    ).to_csv(out_dir / "top_nodes.csv", index=False)
    pd.DataFrame(
        [
            {
                "cluster_id": 1,
                "n_nodes": 3,
                "n_seed": 1,
                "sum_kzt_internal": 180.0,
                "top_gids": GID_C,
                "hypothesis": "Короткая наблюдаемая цепочка.",
            }
        ]
    ).to_csv(out_dir / "clusters.csv", index=False)
    pd.DataFrame(
        [
            {"gid": GID_A, "depth": 0, "is_seed": True, "in_deg": 0, "out_deg": 2},
            {"gid": GID_B, "depth": 1, "is_seed": False, "in_deg": 1, "out_deg": 1},
            {"gid": GID_C, "depth": 2, "is_seed": False, "in_deg": 2, "out_deg": 0},
        ]
    ).to_parquet(artifacts_dir / "node_features.parquet", index=False)
    pd.DataFrame(
        [
            {"src": GID_A, "dst": GID_B, "sum_kzt": 100.0, "n_tx": 1},
            {"src": GID_B, "dst": GID_C, "sum_kzt": 80.0, "n_tx": 2},
            {"src": GID_A, "dst": GID_C, "sum_kzt": 20.0, "n_tx": 1},
        ]
    ).to_parquet(artifacts_dir / "edges_enriched.parquet", index=False)
    pd.DataFrame(
        [
            {
                "removed_n": 1,
                "strategy": "priority",
                "largest_component_size": 2,
                "n_components": 1,
            }
        ]
    ).to_csv(out_dir / "resilience.csv", index=False)
    (out_dir / "summary.json").write_text(
        '{"n_nodes":3,"n_edges":3,"n_transactions":4,"n_seed":1}', encoding="utf-8"
    )
    return out_dir, artifacts_dir, data_dir


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    out_dir, artifacts_dir, data_dir = _write_artifacts(tmp_path)
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        analyst_name="pytest-analyst",
        cors_origins=("http://localhost:8501",),
        data_dir=data_dir,
        out_dir=out_dir,
        artifacts_dir=artifacts_dir,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health_is_independent_of_artifacts_and_ai(tmp_path: Path) -> None:
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'empty.db'}",
        data_dir=tmp_path / "missing-data",
        out_dir=tmp_path / "missing-out",
        artifacts_dir=tmp_path / "missing-artifacts",
    )
    with TestClient(create_app(settings)) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


def test_health_remains_live_when_an_artifact_is_corrupt(tmp_path: Path) -> None:
    out_dir, artifacts_dir, data_dir = _write_artifacts(tmp_path)
    (artifacts_dir / "node_features.parquet").write_bytes(b"not a parquet file")
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'corrupt-health.db'}",
        data_dir=data_dir,
        out_dir=out_dir,
        artifacts_dir=artifacts_dir,
        agentic_auto_monitor_enabled=False,
    )

    with TestClient(create_app(settings), raise_server_exceptions=False) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert response.json()["data"]["artifacts"] == "pending"


@pytest.mark.parametrize(
    ("enabled", "key", "expected_provider"),
    [
        ("false", "fixture-key", None),
        ("true", "", None),
        ("true", "fixture-key", "openai"),
    ],
)
def test_health_reports_constructed_agentic_narrative_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enabled: str,
    key: str,
    expected_provider: str | None,
) -> None:
    monkeypatch.setenv("AI_ENABLED", enabled)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("OPENAI_MODEL", "fixture-model")
    settings = APISettings(
        database_url=f"sqlite:///{tmp_path / 'health.db'}",
        data_dir=tmp_path / "missing-data",
        out_dir=tmp_path / "missing-out",
        artifacts_dir=tmp_path / "missing-artifacts",
        agentic_auto_monitor_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        # The health response must describe the already-constructed API service.
        monkeypatch.setenv("AI_ENABLED", "false" if enabled == "true" else "true")
        response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert response.json()["data"]["agentic_narrative_enabled"] is (
        expected_provider is not None
    )
    assert response.json()["data"]["agentic_narrative_provider"] == expected_provider
    assert "fixture-key" not in response.text
    runtime = response.json()["data"]["ai_runtime"]
    assert runtime["configured"] is (expected_provider is not None)
    if expected_provider is not None:
        assert runtime["model"] == "fixture-model"
        assert not runtime.get("last_success_at")


def test_node_graph_summary_and_cluster_endpoints(client: TestClient) -> None:
    summary = client.get("/api/v1/summary")
    node = client.get(f"/api/v1/nodes/{GID_A}")
    ego = client.get(f"/api/v1/nodes/{GID_A}/ego", params={"depth": 2})
    downstream = client.get(f"/api/v1/nodes/{GID_A}/downstream", params={"depth": 4})
    upstream = client.get(f"/api/v1/nodes/{GID_C}/upstream", params={"depth": 4})
    top = client.get("/api/v1/top-nodes", params={"limit": 10, "offset": 0})
    clusters = client.get("/api/v1/clusters")
    cluster = client.get("/api/v1/clusters/1")
    resilience = client.get("/api/v1/resilience")

    assert summary.status_code == 200
    assert summary.json()["data"]["n_nodes"] == 3
    assert node.json()["data"]["gid"] == GID_A
    assert ego.json()["data"]["depth"] == 2
    assert downstream.json()["data"]["direction"] == "downstream"
    assert upstream.json()["data"]["direction"] == "upstream"
    assert top.json()["data"][0]["gid"] == GID_C
    assert top.json()["meta"]["total"] == 1
    assert clusters.json()["data"][0]["cluster_id"] == 1
    assert cluster.json()["data"]["nodes"][0]["gid"] in {GID_A, GID_B, GID_C}
    assert resilience.json()["data"][0]["strategy"] == "priority"


def test_common_receivers_and_validation_are_bounded(client: TestClient) -> None:
    response = client.post(
        "/api/v1/common-receivers",
        json={"gids": [GID_A, GID_B], "max_depth": 4, "limit": 10},
    )
    invalid_depth = client.get(f"/api/v1/nodes/{GID_A}/ego", params={"depth": 3})
    unsafe_number = client.post(
        "/api/v1/common-receivers",
        json={"gids": [100000000000000001, GID_B], "max_depth": 4},
    )

    assert response.status_code == 200
    assert response.json()["data"]["candidates"][0]["gid"] == GID_C
    assert invalid_depth.status_code == 422
    assert invalid_depth.json()["error"]["code"] == "validation_error"
    assert unsafe_number.status_code == 422


def test_unknown_gid_is_structured_and_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/api/v1/nodes/unknown", headers={"X-Request-ID": "judge-smoke-1"})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "judge-smoke-1"
    assert response.json() == {
        "error": {"code": "node_not_found", "message": "Unknown gid: unknown"},
        "request_id": "judge-smoke-1",
    }


def test_runs_and_investigation_workflow(client: TestClient) -> None:
    run_response = client.post(
        "/api/v1/runs",
        json={
            "status": "completed",
            "config_version": "rules-v1",
            "input_hash": "sha256:test",
            "manifest": {"duration_seconds": 0.5},
        },
    )
    run_id = run_response.json()["data"]["id"]
    created = client.post(
        "/api/v1/investigations",
        json={
            "title": "Общий получатель",
            "description": "Проверить наблюдаемые связи",
            "run_id": run_id,
            "model_version": "rules-v1",
            "gids": [GID_A],
        },
    )
    investigation_id = created.json()["data"]["id"]
    add_nodes = client.post(
        f"/api/v1/investigations/{investigation_id}/nodes", json={"gids": [GID_C]}
    )
    add_note = client.post(
        f"/api/v1/investigations/{investigation_id}/notes",
        json={"body": "Запросить внешние входящие операции."},
    )
    status = client.patch(
        f"/api/v1/investigations/{investigation_id}/status",
        json={"status": "in_review"},
    )
    listing = client.get("/api/v1/investigations")
    detail = client.get(f"/api/v1/investigations/{investigation_id}")
    export_json = client.get(f"/api/v1/investigations/{investigation_id}/export")
    export_csv = client.get(
        f"/api/v1/investigations/{investigation_id}/export", params={"format": "csv"}
    )

    assert run_response.status_code == 201
    assert client.get("/api/v1/runs").json()["meta"]["total"] == 1
    assert client.get(f"/api/v1/runs/{run_id}").status_code == 200
    assert created.status_code == 201
    assert add_nodes.status_code == 200
    assert add_note.status_code == 201
    assert status.json()["data"]["status"] == "in_review"
    assert listing.json()["meta"]["total"] == 1
    assert {node["gid"] for node in detail.json()["data"]["nodes"]} == {GID_A, GID_C}
    assert export_json.json()["data"]["investigation"]["id"] == investigation_id
    assert export_csv.headers["content-type"].startswith("text/csv")
    assert GID_C in export_csv.text


def test_assistant_uses_deterministic_fallback_without_keys(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/query",
        json={"query": "Почему этот узел в топе?", "gids": [GID_C]},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["fallback"] is True
    assert payload["provider"] == "deterministic"
    assert GID_C in payload["answer"]


def test_cors_is_restricted_to_configured_origins(client: TestClient) -> None:
    allowed = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:8501",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = client.options(
        "/health",
        headers={
            "Origin": "https://attacker.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8501"
    assert "access-control-allow-origin" not in denied.headers
