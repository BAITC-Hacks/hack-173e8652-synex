# Agentic AI Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a complete, audited Human-in-the-Loop workflow with proactive demo replay, explainable alerts, three safe proposals and explicit approve/reject execution.

**Architecture:** A deterministic `AgenticLoopService` derives daily alerts from existing artifacts and transactions. SQLAlchemy persists scans, alerts and action state; FastAPI exposes bounded versioned resources; Streamlit adds one default workspace with four workflow tabs while retaining existing deep-dive screens. AI remains optional and cannot create action keys.

**Tech Stack:** Python 3.12, pandas, NetworkX, SQLAlchemy/SQLite, FastAPI/Pydantic, Streamlit, pytest.

**Spec:** `docs/agentic_loop.md`

## Global Constraints

- No real blocking, external submission or autonomous decision.
- Use day-level replay; never claim a two-hour dwell metric.
- Fixed server-side action allowlist and explicit `APPROVE` confirmation.
- Every transition writes an audit event; repeated idempotency keys are safe.
- Offline deterministic behavior is mandatory; remote AI is explanation-only.
- Preserve opaque string `gid`, bounded traversals and existing API/UI contracts.

## Review Focus

- Duplicate approve request must return the first execution and not create another investigation.
- Rejected action must never execute a tool or create an investigation.
- Unknown/tampered alert or action identifiers must return structured 404/409 errors.
- Date-only input must expose the two-hour limitation and use 0–2 day evidence.
- Empty replay windows must produce a valid zero-alert scan and audit record.

---

### Task 1: Persistent workflow and audit primitives

**Files:**
- Modify: `src/moneygraph/repository/models.py`
- Modify: `src/moneygraph/repository/repositories.py`
- Test: `tests/test_agentic_repository.py`

**Interfaces:**
- Produces: `create_monitoring_scan`, `get_monitoring_scan`, `create_action_proposals`, `decide_action`, `record_audit_event`, `list_audit_events`.
- States: proposal `proposed -> rejected` or `proposed -> approved -> executed|failed`.

- [ ] **Step 1: Write failing repository tests** for complete round-trip, invalid transition, rejection without execution and idempotent decision.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_agentic_repository.py -q` and verify missing model/method failures.
- [ ] **Step 3: Add immutable SQLAlchemy records** for scans, alerts and actions, plus transactional repository methods and audit rows.
- [ ] **Step 4: Re-run** the focused tests and keep the existing repository suite green.

### Task 2: Deterministic monitoring, recommendations and tools

**Files:**
- Create: `src/moneygraph/services/agentic_loop.py`
- Create: `src/moneygraph/ai/agentic_prompts.py`
- Test: `tests/test_agentic_loop.py`

**Interfaces:**
- Consumes: `ArtifactStore`, `GraphQueryService`, `MoneyGraphRepository`.
- Produces: `run_scan(replay_date, interval_minutes, limit)`, `propose_actions(alert_id)`, `decide_and_execute(action_id, decision, confirmation, idempotency_key)`.

- [ ] **Step 1: Write failing service tests** with literal daily transaction fixtures for all three triggers, zero alerts, exact three safe action keys, and no `dwell <2h` assertion.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_agentic_loop.py -q` and verify imports/behavior fail for the missing service.
- [ ] **Step 3: Implement daily replay** with actual `daily_unique_payers`, daily in/out volume and existing 0–2 day feature; include `simulation=true` and a granularity limitation.
- [ ] **Step 4: Implement deterministic action cards** with `recommendation_score`, rationale, expected outcome and server-owned keys; add a strict grounded LLM prompt builder that cannot expand the allowlist.
- [ ] **Step 5: Implement approved tools**: local review draft + investigation, bounded graph route, local watchlist + investigation. Reject and invalid confirmation execute nothing.
- [ ] **Step 6: Re-run service and repository tests** and refactor only after green.

### Task 3: Versioned API contract

**Files:**
- Create: `src/moneygraph/api/routes/agentic.py`
- Modify: `src/moneygraph/api/schemas.py`
- Modify: `src/moneygraph/api/dependencies.py`
- Modify: `src/moneygraph/api/main.py`
- Test: `tests/test_agentic_api.py`

**Interfaces:**
- Produces: the five endpoints in `docs/agentic_loop.md` with existing envelope/error conventions.

- [ ] **Step 1: Write failing API tests** for scan, proposals, approve, reject, repeat idempotency, audit, 422 validation and 404 identifiers.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_agentic_api.py -q` and verify 404/import failures.
- [ ] **Step 3: Add strict Pydantic inputs**: ISO replay date, interval 1–60, limit 1–50, decision enum, bounded idempotency key and conditional `APPROVE` confirmation.
- [ ] **Step 4: Add routes and DI**, mapping domain not-found/conflict errors to structured 404/409 without leaking internals.
- [ ] **Step 5: Re-run focused API tests** and existing `tests/test_api.py`.

### Task 4: Four-tab Streamlit demo

**Files:**
- Modify: `src/moneygraph/ui/client.py`
- Create: `src/moneygraph/ui/agentic.py`
- Modify: `src/moneygraph/ui/app.py`
- Modify: `src/moneygraph/ui/pages.py`
- Test: `tests/test_agentic_ui.py`
- Modify: `tests/test_ui_helpers.py`

**Interfaces:**
- Consumes: Agentic API responses only.
- Produces: default `Agentic Loop` workspace with exactly four `st.tabs` and a guarded execution form.

- [ ] **Step 1: Write failing client/UI tests** for request bodies, four tab labels, action cards, explicit checkbox/confirmation and visible audit timeline.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_agentic_ui.py tests/test_ui_helpers.py -q` and verify missing client/page failures.
- [ ] **Step 3: Add typed API client calls** without numeric gid coercion.
- [ ] **Step 4: Build four tabs**: Monitoring, Alert + Explain, Decision Support, Approve/Execute/Audit. Store only selected IDs/responses in Streamlit session state.
- [ ] **Step 5: Add safety copy**: demo replay, human decision, no real submission/block, recommendation score is not risk probability.
- [ ] **Step 6: Re-run UI tests** including AppTest interactions for scan -> proposals -> reject and scan -> proposals -> approve.

### Task 5: Demo assets and full verification

**Files:**
- Modify: `README.md`
- Modify: `docs/demo_script.md`
- Modify: `docs/jury_pitch.md`
- Modify: `docs/architecture.md`
- Modify: `docs/security.md`
- Modify: `scripts/smoke_test.sh`

**Interfaces:**
- Produces: verifiable 5-minute Human-in-the-Loop demo and live smoke coverage.

- [ ] **Step 1: Update docs** with the five-stage loop, four UI tabs, compliance phrase, date-granularity caveat and exact non-goals.
- [ ] **Step 2: Extend smoke** to run a scan, request proposals, reject one action, approve one action and verify audit/execution.
- [ ] **Step 3: Run focused and full verification**: pytest coverage gate, Ruff, format, mypy, full pipeline, CSV verifier, live API/UI, Docker config/build if affected.
- [ ] **Step 4: Perform browser demo** and verify all four tabs, real alert metrics, approve confirmation and audit timeline with zero console errors.
- [ ] **Step 5: Request independent code/security review**, fix all critical/high findings, then rerun the complete gate.

## Self-review

- Spec coverage: all capability states, safety boundaries, persistence, API, UI and audit have an owning task.
- Placeholder scan: no deferred implementation markers are used.
- Type consistency: route names, service names, action keys and states match `docs/agentic_loop.md`.
- Review-focus cases are explicitly assigned to Tasks 1–4.

Execution method was supplied by the user: implement immediately with agent-first TDD and independent final review.
