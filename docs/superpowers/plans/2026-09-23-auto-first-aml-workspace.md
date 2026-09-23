# Auto-first AML Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The analyst opens the app and immediately sees the highest-priority case, its evidence, three prepared next steps, and an explicit approve/reject path without entering IDs, dates, a prompt, or the word `APPROVE`.

**Architecture:** Keep the deterministic graph/rules scan over all source transactions and the server-owned action allowlist. Add an API priority queue spanning all persisted automatic scans, then make Streamlit foreground the first unhandled case. The optional LLM may explain safe numeric facts but cannot choose tools or execute a decision.

**Tech Stack:** FastAPI, SQLAlchemy/SQLite, Streamlit, pytest, optional OpenAI SDK.

**Spec:** `docs/agentic_loop.md` and the user's 2026-09-23 request for a fully automatic analyst-facing flow.

## Global Constraints

- Use the real `data/transactions.parquet`; no synthetic rows in runtime output.
- Keep four workflow stages visible; only human approval may execute one of the three allowlisted local tools.
- Do not use, log, commit, or transmit the API key pasted into chat. A replacement key must be entered privately in `.env`.
- Day-level replay is not marketed as live intraday detection.
- Keep the API bound to loopback for this unauthenticated demo.

## Review Focus

- All dates processed: top case must come from the full accumulated alert set, not only the last day's feed.
- Completed action: an already handled alert must not remain the first fresh case.
- API restart: durable scan and queue state must survive restart without duplicate replay dates.
- Provider failure or missing key: deterministic facts/actions still appear and no action executes.
- Approval UI: a rejected or unacknowledged action must never invoke a tool.

---

### Task 1: Durable priority queue

**Files:** Modify `src/moneygraph/services/auto_monitor.py`; test `tests/test_agentic_auto_monitor.py`.

**Interfaces:** `AgenticAutoMonitor.status() -> dict[str, Any]` adds `priority_queue` (top unhandled alerts ordered by priority, then recency) and `queue_size`. Each queue item retains `actions` and `replay_date`.

- [ ] Write a test with older high-priority and newer low-priority alerts. Assert the older alert appears first, and an executed action removes that alert from the fresh queue.
- [ ] Run `.venv/bin/python -m pytest tests/test_agentic_auto_monitor.py -q` and observe the expected missing-queue failure.
- [ ] Implement the queue from persisted automatic scans; do not rescan or call an LLM in `status()`.
- [ ] Rerun the focused test and full suite.

### Task 2: Automatic case landing

**Files:** Modify `src/moneygraph/ui/agentic.py`; test `tests/test_agentic_ui.py`.

**Interfaces:** `render_agentic_loop(client)` consumes the new `priority_queue` and still falls back to `recent_alerts` for older API responses.

- [ ] Write a UI test where the API supplies a priority queue and assert the top case appears without calls to `run_agentic_scan` or `propose_agentic_actions`.
- [ ] Run `.venv/bin/python -m pytest tests/test_agentic_ui.py -q` and observe the expected failure.
- [ ] Show a leading case summary and the three prepared action titles before secondary charts/tables. Move manual date, interval, limit, and replay button into a collapsed diagnostic expander.
- [ ] Rerun the focused test and full suite.

### Task 3: Click-only human decision

**Files:** Modify `src/moneygraph/ui/agentic.py`; test `tests/test_agentic_ui.py`.

**Interfaces:** Keep `APIClient.decide_agentic_action(action_id, decision, confirmation, idempotency_key)` unchanged. The UI sends `confirmation="APPROVE"` only after a checked acknowledgment and the analyst's approve click.

- [ ] Write a UI test asserting no text-entry control is required, the approve button is disabled before acknowledgment, and an acknowledged click sends the API confirmation; reject sends no tool authorization.
- [ ] Run `.venv/bin/python -m pytest tests/test_agentic_ui.py -q` and observe the expected failure.
- [ ] Replace the control-phrase input with the checked acknowledgment and explicit approve button; preserve server-side state/idempotency checks.
- [ ] Rerun the focused test and full suite.

### Task 4: Live verification and documentation

**Files:** Modify `README.md` and `docs/demo_script.md` only where observed behavior changes.

- [ ] Run Ruff, mypy, full pytest with coverage, full Parquet pipeline, CSV verifier, and API/UI smoke.
- [ ] Launch local API/UI, open all four stages in a browser, confirm priority queue appears without manual input, and exercise one reject plus one safe approve in an isolated local database.
- [ ] Confirm the pasted key is absent from source, tracked files, logs, and outputs; report that a fresh privately entered key is required for a real OpenAI call.
- [ ] Record exact observed results and limitations; do not claim the remote LLM or Docker build passed unless actually verified.
