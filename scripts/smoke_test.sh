#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON_BIN:-}"

if [[ -z "${python_bin}" ]]; then
  if [[ -x "${repo_root}/.venv/bin/python" ]]; then
    python_bin="${repo_root}/.venv/bin/python"
  else
    python_bin="python3"
  fi
fi

data_dir="${DATA_DIR:-${repo_root}/data}"
out_dir="${OUT_DIR:-${repo_root}/out}"
artifacts_dir="${ARTIFACTS_DIR:-${repo_root}/artifacts}"
api_port="${API_PORT:-8000}"
ui_port="${UI_PORT:-8501}"
api_url="http://127.0.0.1:${api_port}"
ui_url="http://127.0.0.1:${ui_port}"
agentic_replay_date="${AGENTIC_REPLAY_DATE:-}"
tmp_base="${TMPDIR:-/tmp}"
tmp_base="${tmp_base%/}"
smoke_dir="$(mktemp -d "${tmp_base}/moneygraph-smoke.XXXXXX")"
api_pid=""
ui_pid=""

cleanup() {
  local status=$?
  if [[ -n "${ui_pid}" ]]; then
    kill "${ui_pid}" 2>/dev/null || true
    wait "${ui_pid}" 2>/dev/null || true
  fi
  if [[ -n "${api_pid}" ]]; then
    kill "${api_pid}" 2>/dev/null || true
    wait "${api_pid}" 2>/dev/null || true
  fi
  if [[ ${status} -ne 0 ]]; then
    printf '\nAPI log:\n' >&2
    tail -n 80 "${smoke_dir}/api.log" 2>/dev/null >&2 || true
    printf '\nUI log:\n' >&2
    tail -n 80 "${smoke_dir}/ui.log" 2>/dev/null >&2 || true
  fi
  case "${smoke_dir}" in
    "${tmp_base}"/moneygraph-smoke.*) rm -r -- "${smoke_dir}" ;;
  esac
  return "${status}"
}
trap cleanup EXIT INT TERM

wait_for_url() {
  local label=$1
  local url=$2
  local attempt
  for attempt in $(seq 1 60); do
    if "${python_bin}" -c \
      'import sys, urllib.request; response = urllib.request.urlopen(sys.argv[1], timeout=1); raise SystemExit(0 if 200 <= response.status < 400 else 1)' \
      "${url}" >/dev/null 2>&1; then
      printf '%s ready: %s\n' "${label}" "${url}"
      return 0
    fi
    sleep 1
  done
  printf '%s did not become ready within 60 seconds: %s\n' "${label}" "${url}" >&2
  return 1
}

for required_output in nodes_roles.csv clusters.csv top_nodes.csv; do
  if [[ ! -s "${out_dir}/${required_output}" ]]; then
    printf 'Missing required output %s. Run make analyze first.\n' "${out_dir}/${required_output}" >&2
    exit 1
  fi
done

export DATA_DIR="${data_dir}"
export OUT_DIR="${out_dir}"
export ARTIFACTS_DIR="${artifacts_dir}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///${smoke_dir}/smoke.db}"
export ANALYST_NAME="${ANALYST_NAME:-smoke-analyst}"
export AI_ENABLED=false
export AI_PROVIDER=fallback
export CORS_ORIGINS="${CORS_ORIGINS:-${ui_url}}"

"${python_bin}" -m uvicorn moneygraph.api.main:app \
  --host 127.0.0.1 --port "${api_port}" --log-level warning \
  >"${smoke_dir}/api.log" 2>&1 &
api_pid=$!

wait_for_url "API" "${api_url}/health"

export MONEYGRAPH_API_URL="${api_url}"
export API_BASE_URL="${api_url}"
"${python_bin}" -m streamlit run "${repo_root}/src/moneygraph/ui/app.py" \
  --server.address 127.0.0.1 --server.port "${ui_port}" \
  --server.headless true --browser.gatherUsageStats false \
  >"${smoke_dir}/ui.log" 2>&1 &
ui_pid=$!

wait_for_url "UI" "${ui_url}/_stcore/health"

"${python_bin}" - "${api_url}" "${ui_url}" "${data_dir}" "${agentic_replay_date}" <<'PY'
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
import urllib.request

import pandas as pd


def get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"GET {url} returned HTTP {response.status}")
        return json.load(response)


def post_json(
    url: str,
    payload: dict[str, object] | None,
    *,
    headers: dict[str, str] | None = None,
) -> object:
    request_headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status not in {200, 201}:
            raise RuntimeError(f"POST {url} returned HTTP {response.status}")
        return json.load(response)


def unwrap_data(payload: object) -> object:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def extract_top_gids(payload: object) -> list[str]:
    data = unwrap_data(payload)
    if isinstance(data, dict):
        for key in ("items", "nodes", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise RuntimeError("Top nodes response does not contain a list")
    gids = [str(item["gid"]) for item in data if isinstance(item, dict) and item.get("gid")]
    if not gids:
        raise RuntimeError("Top nodes response contains no gid")
    return gids


def select_agentic_replay_date(data_dir: str, override: str) -> str:
    if override:
        return override
    transactions = pd.read_parquet(
        Path(data_dir) / "transactions.parquet",
        columns=["src", "dst", "date", "sum_kzt"],
    )
    transactions = transactions.assign(date=pd.to_datetime(transactions["date"]).dt.date)
    daily = (
        transactions.groupby(["date", "dst"], as_index=False)
        .agg(daily_incoming_kzt=("sum_kzt", "sum"), daily_unique_payers=("src", "nunique"))
    )
    triggered = daily.loc[
        (daily["daily_incoming_kzt"] > 1_000_000)
        | (daily["daily_unique_payers"] >= 8)
    ]
    if triggered.empty:
        raise RuntimeError(
            "No calendar day triggers a deterministic monitoring rule; "
            "set AGENTIC_REPLAY_DATE to a known demo window"
        )
    counts = triggered.groupby("date").size().sort_values(ascending=False)
    return counts.index[0].isoformat()


api_url, ui_url, data_dir, replay_override = sys.argv[1:]
health = get_json(f"{api_url}/health")
openapi = get_json(f"{api_url}/openapi.json")
summary = get_json(f"{api_url}/api/v1/summary")
top_nodes = get_json(f"{api_url}/api/v1/top-nodes?limit=2")

paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
required_paths = {
    "/health",
    "/api/v1/summary",
    "/api/v1/top-nodes",
    "/api/v1/agentic/scans",
    "/api/v1/agentic/alerts/{alert_id}/proposals",
    "/api/v1/agentic/actions/{action_id}/decision",
    "/api/v1/agentic/audit",
}
missing_paths = sorted(required_paths - set(paths))
if missing_paths:
    raise RuntimeError(f"OpenAPI is missing required paths: {missing_paths}")
if not isinstance(health, dict):
    raise RuntimeError("Health response must be a JSON object")
if not isinstance(summary, dict):
    raise RuntimeError("Summary response must be a JSON object")
if not isinstance(top_nodes, (dict, list)):
    raise RuntimeError("Top nodes response must be a JSON object or array")

gids = extract_top_gids(top_nodes)
common_receivers = post_json(
    f"{api_url}/api/v1/common-receivers",
    {"gids": gids[:2], "max_depth": 4, "limit": 10},
)
if not isinstance(unwrap_data(common_receivers), dict):
    raise RuntimeError("Common receivers response must contain an object")

investigation = post_json(
    f"{api_url}/api/v1/investigations",
    {
        "title": "Smoke test investigation",
        "description": "Temporary end-to-end smoke check",
        "run_id": None,
        "model_version": "rules-v1",
        "gids": gids[:1],
    },
)
investigation_data = unwrap_data(investigation)
if not isinstance(investigation_data, dict) or not investigation_data.get("id"):
    raise RuntimeError("Investigation creation response contains no id")
investigation_id = investigation_data["id"]
exported = get_json(f"{api_url}/api/v1/investigations/{investigation_id}/export")
if not isinstance(unwrap_data(exported), dict):
    raise RuntimeError("Investigation export response must contain an object")

replay_date = select_agentic_replay_date(data_dir, replay_override)
scan_payload = post_json(
    f"{api_url}/api/v1/agentic/scans",
    {"replay_date": replay_date, "interval_minutes": 15, "limit": 50},
)
scan = unwrap_data(scan_payload)
if not isinstance(scan, dict) or not scan.get("id"):
    raise RuntimeError("Agentic scan response contains no id")
alerts = scan.get("alerts")
if not isinstance(alerts, list) or not alerts:
    raise RuntimeError(f"Agentic scan produced no alert for replay_date={replay_date}")
first_alert = alerts[0]
if not isinstance(first_alert, dict) or not first_alert.get("id"):
    raise RuntimeError("Agentic alert contains no id")
if first_alert.get("simulation") is not True:
    raise RuntimeError("Agentic alert must be explicitly marked as simulation")

proposal_payload = post_json(
    f"{api_url}/api/v1/agentic/alerts/{first_alert['id']}/proposals",
    None,
)
proposal_data = unwrap_data(proposal_payload)
if not isinstance(proposal_data, dict):
    raise RuntimeError("Agentic proposal response must contain an object")
actions = proposal_data.get("actions")
if not isinstance(actions, list) or len(actions) != 3:
    raise RuntimeError("Agentic proposal response must contain exactly three actions")
actions_by_key = {
    str(action.get("action_key")): action
    for action in actions
    if isinstance(action, dict) and action.get("id")
}
required_action_keys = {
    "prepare_aml_review_draft",
    "build_money_route",
    "create_local_watchlist",
}
if set(actions_by_key) != required_action_keys:
    raise RuntimeError(
        f"Agentic actions differ from server allowlist: {sorted(actions_by_key)}"
    )

key_suffix = uuid.uuid4().hex
rejected = unwrap_data(
    post_json(
        f"{api_url}/api/v1/agentic/actions/"
        f"{actions_by_key['build_money_route']['id']}/decision",
        {"decision": "reject"},
        headers={"Idempotency-Key": f"smoke-reject-{key_suffix}"},
    )
)
if not isinstance(rejected, dict) or rejected.get("status") != "rejected":
    raise RuntimeError("Rejected Agentic action did not reach rejected status")
rejected_result = rejected.get("result")
if not isinstance(rejected_result, dict) or rejected_result.get("executed") is not False:
    raise RuntimeError("Rejected Agentic action unexpectedly executed a tool")

approve_key = f"smoke-approve-{key_suffix}"
approve_url = (
    f"{api_url}/api/v1/agentic/actions/"
    f"{actions_by_key['prepare_aml_review_draft']['id']}/decision"
)
approved = unwrap_data(
    post_json(
        approve_url,
        {"decision": "approve", "confirmation": "APPROVE"},
        headers={"Idempotency-Key": approve_key},
    )
)
replayed_approval = unwrap_data(
    post_json(
        approve_url,
        {"decision": "approve", "confirmation": "APPROVE"},
        headers={"Idempotency-Key": approve_key},
    )
)
if not isinstance(approved, dict) or approved.get("status") != "executed":
    raise RuntimeError("Approved Agentic action did not reach executed status")
if approved != replayed_approval:
    raise RuntimeError("Idempotent Agentic approval returned a different result")
approved_result = approved.get("result")
if not isinstance(approved_result, dict):
    raise RuntimeError("Approved Agentic action contains no result object")
if approved_result.get("submitted") is not False:
    raise RuntimeError("AML review draft must remain local and unsubmitted")
if approved_result.get("external_effects") not in ([], None):
    raise RuntimeError("AML review draft reported an external side effect")

audit_payload = get_json(f"{api_url}/api/v1/agentic/audit?limit=100&offset=0")
audit_data = unwrap_data(audit_payload)
if not isinstance(audit_data, list):
    raise RuntimeError("Agentic audit response must contain a list")
audit_actions = {
    str(event.get("action")) for event in audit_data if isinstance(event, dict)
}
required_audit_actions = {
    "monitor.scan_completed",
    "alert.created",
    "actions.proposed",
    "action.rejected",
    "action.approved",
    "tool.executed",
}
missing_audit_actions = sorted(required_audit_actions - audit_actions)
if missing_audit_actions:
    raise RuntimeError(f"Agentic audit is missing transitions: {missing_audit_actions}")

with urllib.request.urlopen(f"{ui_url}/_stcore/health", timeout=5) as response:
    if response.status != 200:
        raise RuntimeError(f"UI health returned HTTP {response.status}")

print(f"API smoke passed: {api_url}")
print(f"UI smoke passed: {ui_url}")
print(f"Investigation smoke passed: id={investigation_id}")
print(
    "Agentic smoke passed: "
    f"replay_date={replay_date}, alerts={len(alerts)}, "
    f"rejected={actions_by_key['build_money_route']['id']}, "
    f"executed={actions_by_key['prepare_aml_review_draft']['id']}, "
    f"audit_events={len(audit_data)}"
)
PY
