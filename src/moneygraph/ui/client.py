"""Small synchronous client for the local MoneyGraph FastAPI service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx


class APIClientError(RuntimeError):
    """Safe, user-displayable API error."""


class APIClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 8.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            if self._client is None:
                response = httpx.request(method, url, timeout=self.timeout, **kwargs)
            else:
                response = self._client.request(method, url, timeout=self.timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise APIClientError("API недоступен. Проверьте, что FastAPI запущен.") from exc
        if response.is_error:
            raise APIClientError(_error_message(response))
        if response.status_code == 204 or not response.content:
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            raise APIClientError("API вернул некорректный JSON.") from exc
        if isinstance(payload, Mapping) and "data" in payload:
            return payload["data"]
        return payload

    def health(self) -> dict[str, Any]:
        return _mapping(self._request("GET", "/health"))

    def summary(self) -> dict[str, Any]:
        return _mapping(self._request("GET", "/api/v1/summary"))

    def top_nodes(
        self,
        *,
        limit: int = 50,
        role: str | None = None,
        cluster_id: int | None = None,
    ) -> Any:
        params = _compact({"limit": limit, "role": role, "cluster_id": cluster_id})
        return self._request("GET", "/api/v1/top-nodes", params=params)

    def node(self, gid: str) -> dict[str, Any]:
        return _mapping(self._request("GET", f"/api/v1/nodes/{_gid(gid)}"))

    def ego(self, gid: str, *, depth: int = 1) -> dict[str, Any]:
        return _mapping(
            self._request("GET", f"/api/v1/nodes/{_gid(gid)}/ego", params={"depth": depth})
        )

    def trace(self, gid: str, direction: str, *, depth: int = 4) -> Any:
        if direction not in {"upstream", "downstream"}:
            raise ValueError("direction must be upstream or downstream")
        return self._request(
            "GET", f"/api/v1/nodes/{_gid(gid)}/{direction}", params={"depth": depth}
        )

    def common_receivers(self, gids: list[str], *, max_depth: int = 4) -> Any:
        return self._request(
            "POST",
            "/api/v1/common-receivers",
            json={"gids": gids, "max_depth": max_depth},
        )

    def clusters(self, *, limit: int = 100) -> Any:
        return self._request("GET", "/api/v1/clusters", params={"limit": limit})

    def cluster(self, cluster_id: int | str) -> dict[str, Any]:
        return _mapping(self._request("GET", f"/api/v1/clusters/{cluster_id}"))

    def resilience(self) -> Any:
        return self._request("GET", "/api/v1/resilience")

    def investigations(self, *, limit: int = 100) -> Any:
        return self._request("GET", "/api/v1/investigations", params={"limit": limit})

    def investigation(self, investigation_id: int | str) -> dict[str, Any]:
        return _mapping(self._request("GET", f"/api/v1/investigations/{investigation_id}"))

    def create_investigation(
        self, title: str, *, description: str = "", gids: list[str] | None = None
    ) -> dict[str, Any]:
        return _mapping(
            self._request(
                "POST",
                "/api/v1/investigations",
                json={"title": title, "description": description, "gids": gids or []},
            )
        )

    def add_nodes(self, investigation_id: int | str, gids: list[str]) -> Any:
        return self._request(
            "POST",
            f"/api/v1/investigations/{investigation_id}/nodes",
            json={"gids": gids},
        )

    def add_note(self, investigation_id: int | str, text: str) -> Any:
        return self._request(
            "POST",
            f"/api/v1/investigations/{investigation_id}/notes",
            json={"body": text},
        )

    def update_status(self, investigation_id: int | str, status: str) -> Any:
        return self._request(
            "PATCH",
            f"/api/v1/investigations/{investigation_id}/status",
            json={"status": status},
        )

    def export_url(self, investigation_id: int | str, *, output_format: str = "json") -> str:
        return (
            f"{self.base_url}/api/v1/investigations/{investigation_id}/export"
            f"?format={quote(output_format)}"
        )

    def ask_assistant(
        self,
        query: str,
        *,
        gids: list[str] | None = None,
        cluster_id: int | None = None,
    ) -> dict[str, Any]:
        return _mapping(
            self._request(
                "POST",
                "/api/v1/assistant/query",
                json={"query": query, "gids": gids or [], "cluster_id": cluster_id},
            )
        )

    def run_agentic_scan(
        self,
        replay_date: str,
        *,
        interval_minutes: int = 5,
        limit: int = 20,
    ) -> dict[str, Any]:
        return _mapping(
            self._request(
                "POST",
                "/api/v1/agentic/scans",
                json={
                    "replay_date": replay_date,
                    "interval_minutes": interval_minutes,
                    "limit": limit,
                },
            )
        )

    def agentic_scan(self, scan_id: str) -> dict[str, Any]:
        return _mapping(self._request("GET", f"/api/v1/agentic/scans/{_gid(scan_id)}"))

    def propose_agentic_actions(self, alert_id: str) -> dict[str, Any]:
        return _mapping(
            self._request(
                "POST",
                f"/api/v1/agentic/alerts/{_gid(alert_id)}/proposals",
            )
        )

    def decide_agentic_action(
        self,
        action_id: str,
        *,
        decision: str,
        confirmation: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        return _mapping(
            self._request(
                "POST",
                f"/api/v1/agentic/actions/{_gid(action_id)}/decision",
                json=_compact({"decision": decision, "confirmation": confirmation}),
                headers={"Idempotency-Key": idempotency_key},
            )
        )

    def agentic_audit(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Any:
        return self._request(
            "GET",
            "/api/v1/agentic/audit",
            params={"limit": limit, "offset": offset},
        )


def _mapping(payload: Any) -> dict[str, Any]:
    return dict(payload) if isinstance(payload, Mapping) else {}


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _gid(gid: str) -> str:
    return quote(str(gid), safe="")


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping) and error.get("message"):
                return str(error["message"])
            if payload.get("detail"):
                return str(payload["detail"])
    except ValueError:
        pass
    return f"API вернул ошибку HTTP {response.status_code}."
