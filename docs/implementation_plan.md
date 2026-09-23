# Freedom MoneyGraph AML Implementation Plan

> **For agentic workers:** implement each vertical slice test-first (RED → GREEN → REFACTOR), keep ownership boundaries explicit, and verify the full suite after every slice.

**Goal:** Build and verify a deterministic, local-first AML graph investigation product that ranks all 2,248 anonymized clients, explains why they should be reviewed, and exposes the result through CSV, API, SQLite-backed investigations, and a Streamlit UI.

**Architecture:** A pure analytical core loads immutable Parquet inputs, validates them, builds a directed weighted NetworkX graph, derives graph/financial/temporal features, detects stable Louvain communities, assigns explainable roles and review priority, and exports immutable artifacts. A service layer reads those artifacts for FastAPI and Streamlit; SQLAlchemy persists run metadata, snapshots, cases, notes, and audit events. Optional AI providers are isolated behind a safe deterministic fallback and never participate in mandatory scoring.

**Tech Stack:** Python 3.12, pandas, NumPy, PyArrow, NetworkX, PyYAML, FastAPI/Pydantic, SQLAlchemy/SQLite, Streamlit, Plotly, httpx, pytest/coverage, Ruff, mypy, Docker Compose.

**Spec:** [`prompt.md`](../prompt.md)

## Global Constraints

- Inputs are read-only: `data/nodes.parquet`, `data/edges.parquet`, `data/transactions.parquet`.
- Expected full-data facts: 2,248 nodes, 3,119 directed aggregated edges, 4,840 transactions, 81 seeds, `2026-07-01` through `2026-07-31`, traversal depth four, 5,000 KZT extraction threshold.
- No hard-coded `gid`, role, cluster, or top-node results; no invented customer attributes or external enrichment.
- `depth == 4 && out_deg == 0` is truncated, not automatically terminal; seed inflows are incomplete and seed pass-through is invalid.
- Mandatory outputs work offline and never depend on AI credentials.
- Outputs and community numbering are deterministic with seed 42 and stable sorting.
- Every claim is an analytical signal or hypothesis, never an allegation.
- Pipeline budget is under 300 seconds on the provided data.
- Work is confined to `HackAlem`; the parent Git worktree has unrelated user changes and will not be modified or committed.

## Source Audit

| Path | Observed purpose | Treatment |
|---|---|---|
| `prompt.md` | Complete product contract and Definition of Done | Source of truth |
| `starter/README.md` | Dataset traps and exact CSV expectations | Preserve semantics |
| `starter/starter.py` | Proven loading, sanity-check, graph, and baseline feature examples | Reference, not runtime dependency |
| `starter/requirements.txt` | Minimal analytical packages | Superseded by pinned project dependencies |
| `data/nodes.parquet` | Node identity, depth, seed flag | Read-only |
| `data/edges.parquet` | Directed aggregated transfers | Read-only |
| `data/transactions.parquet` | Dated individual transfers | Read-only and used for temporal/FIFO metrics |

## Review Focus

- Boundary truncation: a depth-four sink must be capped at terminal score 0.25 and must mention the boundary in evidence.
- Seed observation bias: pass-through validity must be false for seed nodes and temporal/retention signals must not dominate their transit score.
- Data mismatch: missing endpoints, duplicate keys, non-positive amounts, invalid dates, or edge/transaction aggregation mismatch must fail explicitly and appear in validation output.
- Determinism: repeated full runs over identical inputs must produce byte-stable mandatory CSV content apart from run metadata timestamps.
- Traversal safety: unknown IDs, excessive lists/depth, cyclic paths, and path explosions must return bounded, validated responses rather than hang or expose internals.

## Delivery Slices

### Task 1: Reproducible environment and configuration

**Files:** `pyproject.toml`, `requirements.txt`, `config/default.yaml`, `.env.example`, `.gitignore`, `src/moneygraph/config.py`, `tests/test_config.py`.

**Interfaces:** `Settings.from_env()`, `load_analysis_config(path) -> AnalysisConfig`.

- [ ] Write config tests for deterministic defaults, YAML weight validation, safe paths, CORS parsing, and absent AI keys.
- [ ] Run the focused tests and observe expected import failures (RED).
- [ ] Implement typed immutable settings/config models and declarative role/priority weights (GREEN).
- [ ] Run focused and complete suites; enforce Ruff/mypy-compatible code.

### Task 2: Input contracts, validation, and graph construction

**Files:** `src/moneygraph/pipeline/ingestion.py`, `validation.py`, `graph_builder.py`, `tests/test_validation.py`, `tests/test_graph_builder.py`, fixtures under `tests/fixtures/`.

**Interfaces:** `load_dataset(data_dir) -> DatasetBundle`, `validate_dataset(bundle) -> ValidationReport`, `build_graph(nodes, edges) -> nx.DiGraph`.

- [ ] Write synthetic tests for schemas, unique IDs/pairs, endpoints, non-positive amounts, self-loop warnings, exact transaction aggregation, isolated nodes, and all-node graph inclusion.
- [ ] Run and observe missing-module/behavior failures (RED).
- [ ] Implement explicit validation errors plus serializable warnings/report; never mutate or silently repair source frames (GREEN).
- [ ] Verify expected full-data counts and weak components after dependencies are installed.

### Task 3: Graph, financial, and temporal features

**Files:** `src/moneygraph/pipeline/features.py`, `temporal.py`, `tests/test_features.py`, `tests/test_temporal.py`, `tests/test_seed_handling.py`, `tests/test_depth4.py`.

**Interfaces:** `compute_graph_features(...) -> DataFrame`, `fifo_match_node(...) -> TemporalMetrics`, `compute_temporal_features(...) -> DataFrame`.

- [ ] Write literal synthetic expectations for degree/amount/transaction metrics, directed PageRank/HITS fallback, inverse-log distance, seed reach, round/repeated amounts, percentiles, same-day flow, FIFO no-future matching, and hold-time estimates.
- [ ] Prove each focused test fails before implementation (RED).
- [ ] Implement vectorized aggregation and bounded deterministic NetworkX calculations (GREEN).
- [ ] Refactor only after the feature and temporal tests remain green.

### Task 4: Stable communities, explainable roles, and priority

**Files:** `src/moneygraph/domain/roles.py`, `pipeline/clustering.py`, `role_engine.py`, `priority.py`, `explanations.py`, corresponding unit tests.

**Interfaces:** `detect_communities(graph) -> ClusterResult`, `score_roles(features, config) -> DataFrame`, `score_priority(features, config) -> DataFrame`, `build_evidence(row) -> str`.

- [ ] Add failing synthetic fixtures for consolidator, distributor, rapid transit, bridge coordinator, true terminal, depth-four truncated sink, and isolated peripheral.
- [ ] Add failing determinism/range/evidence tests; evidence must be Russian, numeric, non-empty, and at most 200 characters.
- [ ] Implement stable Louvain IDs (size descending, then minimum gid), per-role score components, documented overrides, margin-aware confidence, and driver contributions.
- [ ] Verify all synthetic role fixtures and re-run the whole suite.

### Task 5: Export, resilience, and auditable run orchestration

**Files:** `pipeline/exporters.py`, `resilience.py`, `service.py`, `cli.py`, `scripts/verify_outputs.py`, export/e2e tests.

**Interfaces:** `AnalysisService.run(data_dir, out_dir, artifacts_dir) -> RunResult`; CLI `python -m moneygraph.cli analyze --data ... --out ...`.

- [ ] Write failing tests for all output schemas, 2,248-row full-data mode, top-50 order, cluster consistency, hashes/durations, failure exit codes, and resilience scenarios.
- [ ] Implement one transactional pipeline coordinator with per-stage monotonic timings, SHA-256 input hashes, atomic output replacement, JSON-safe manifests, and non-zero critical failures.
- [ ] Export `node_features.parquet`, `edges_enriched.parquet`, validation/manifest/summary JSON, mandatory CSV, and resilience CSV.
- [ ] Run twice and compare deterministic CSV digests.

### Task 6: Persistence and investigation workflow

**Files:** `src/moneygraph/repository/database.py`, `models.py`, `repositories.py`, `tests/test_repository.py`.

**Interfaces:** repositories for runs, snapshots, investigations, nodes, notes, and audit events; SQLAlchemy URL configurable by `DATABASE_URL`.

- [ ] Write failing SQLite integration tests for create/list/get/status transitions, node deduplication, notes, audit events, and JSON/CSV exports.
- [ ] Implement parameterized ORM operations, transaction boundaries, timestamps, demo analyst attribution, and future PostgreSQL-compatible types.
- [ ] Verify case export content and isolated test databases.

### Task 7: Bounded graph investigation service and REST API

**Files:** `src/moneygraph/domain/schemas.py`, `services/investigation.py`, `api/main.py`, `api/dependencies.py`, `api/routes/*.py`, `tests/test_api.py`.

**Interfaces:** all endpoints listed in `prompt.md` under `/api/v1`, plus independent `/health` and OpenAPI docs.

- [ ] Write failing TestClient tests for health, summary, pagination, known/unknown nodes, ego, upstream/downstream, common receivers, clusters, runs, case lifecycle/export, malformed/oversized inputs, request IDs, and assistant fallback.
- [ ] Implement Pydantic boundary validation, bounded traversals, consistent HTTP errors, configurable CORS, request-ID middleware, structured logs, and database/service dependency injection.
- [ ] Verify OpenAPI generation and API behavior without AI keys.

### Task 8: Optional AI providers with deterministic fallback

**Files:** `src/moneygraph/ai/base.py`, `tools.py`, `deterministic_fallback.py`, `openai_provider.py`, `nvidia_provider.py`, `tests/test_ai.py`.

**Interfaces:** `AIProvider.query(question, context) -> AssistantResponse`; external providers receive only minimized structured metrics.

- [ ] Write failing tests for disabled mode, missing credentials, provider failures, prompt/context sanitization, and non-accusatory deterministic answers.
- [ ] Implement lazy optional SDK use, environment-only configuration, safe fallback, tool-call disclosure, and strict context minimization.
- [ ] Confirm core imports and API startup work with no provider package/key.

### Task 9: Streamlit analyst workspace

**Files:** `src/moneygraph/ui/app.py`, `ui/client.py`, `ui/charts.py`, `tests/test_ui_helpers.py`, `tests/e2e/test_smoke.py`.

**Interfaces:** UI defaults to `http://127.0.0.1:8000`, exposes Dashboard, Network Explorer, Top Nodes, Clusters, Flow Investigation, Investigations, and AI Copilot sections.

- [ ] Write tests for pure chart/format/filter helpers and a browser smoke journey that loads the dashboard and searches a real gid.
- [ ] Implement bounded Plotly ego graphs with arrows/tooltips/role legend, score drivers, limitation panels, common receiver flow, case creation/update/export, and fallback Copilot notice.
- [ ] Start API/UI on loopback ports, exercise a real user journey, capture screenshot evidence, and fix browser/runtime errors.

### Task 10: Packaging, Docker, CI, security, and project documentation

**Files:** `Makefile`, `Dockerfile`, `docker-compose.yml`, `scripts/*.sh`, `.github/workflows/ci.yml`, `README.md`, `docs/{architecture,methodology,demo_script,security,scaling}.md`, `docs/solution_flow.mmd`.

- [ ] Add executable synthetic smoke/verify commands before claiming container readiness.
- [ ] Implement non-root image, health checks, read-only input mount, writable output/artifact/database volumes, no secrets in image, and local-first startup.
- [ ] Document only implemented features and measured values; include Mermaid architecture, five-minute demo, scale path to one million nodes, and bank integration boundary.
- [ ] Run Docker Compose configuration validation/build/start when Docker daemon is available; otherwise record the exact blocker.

### Task 11: Independent review and final verification

**Commands:** `ruff check .`, `mypy src`, `pytest --cov=moneygraph --cov-report=term-missing`, full CLI twice, `python scripts/verify_outputs.py`, API curl smoke, browser UI smoke, `docker compose config`, `docker compose build`, `docker compose up` smoke, dependency/secret scan.

- [ ] Have independent reviewers inspect correctness/maintainability and security; fix all critical/high findings with regression tests.
- [ ] Run every verification command fresh and retain exact outputs/timings.
- [ ] Count CSV rows programmatically, verify schemas/content, and record API/UI addresses from live processes.
- [ ] Re-read `prompt.md` Definition of Done line by line and report any honest residual limitations.

## Readiness Criteria

The project is ready only when the full-data pipeline exits zero under five minutes; tests, lint, output verifier, live API health/node/common-receiver/investigation flows, and live UI search pass; all mandatory artifacts exist and contain valid deterministic data; AI-disabled mode is functional; Docker is built and smoked or a concrete environment blocker is documented; and the final report cites measured—not inferred—results.
