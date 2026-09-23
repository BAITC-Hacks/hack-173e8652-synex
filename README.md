# Freedom MoneyGraph AML

Локальный pilot-ready продукт для AML-аналитика Freedom Bank: система строит объяснимую графовую модель обезличенных внутрибанковских переводов, назначает каждому из 2 248 клиентов функциональную роль, выделяет сообщества, ранжирует приоритет проверки и даёт API/UI для расследования потоков.

Главный вопрос, на который отвечает продукт: кого смотреть первым и почему.

## Что реализовано

- Детерминированный pipeline `python -m moneygraph.cli analyze --data ./data --out ./out`.
- Строгая валидация трёх Parquet: schema, counts, date range, self-loop, node coverage, seed count, edge/transaction aggregation.
- NetworkX-граф с 2 248 узлами, включая изолированные seed.
- Graph/financial/temporal features, роли, `role_score`, `priority_score`, evidence до 200 символов.
- Кластеры, top nodes, resilience-анализ, validation report, manifest, расширенные Parquet artifacts.
- SQLite-хранилище запусков, node snapshots, investigations, notes и audit events.
- FastAPI: summary, runs, top nodes, node profile, ego, upstream/downstream traces, common receivers, clusters, resilience, investigations, assistant.
- Streamlit UI: dashboard, топ приоритетов, node explorer, clusters, investigations, AI Copilot.
- Optional AI layer: OpenAI или NVIDIA NIM через env; по умолчанию безопасный deterministic fallback без сети.
- Dockerfile и Docker Compose с non-root user, loopback ports, read-only containers, dropped capabilities.

## Быстрый запуск

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev,ai]"
PYTHON=.venv/bin/python make analyze
.venv/bin/python scripts/verify_outputs.py --out ./out --expected-nodes 2248
DATA_DIR=./data OUT_DIR=./out ARTIFACTS_DIR=./artifacts DATABASE_URL=sqlite:///./moneygraph.db \
  .venv/bin/uvicorn moneygraph.api.main:app --host 127.0.0.1 --port 8000
MONEYGRAPH_API_URL=http://127.0.0.1:8000 \
  .venv/bin/streamlit run src/moneygraph/ui/app.py --server.address 127.0.0.1 --server.port 8501
```

API: http://127.0.0.1:8000  
Swagger: http://127.0.0.1:8000/docs  
UI: http://127.0.0.1:8501

## Проверенные результаты последнего полного запуска

- Full dataset pipeline in Docker analyzer: `5.495297s`.
- Counts: `nodes=2248`, `edges=3119`, `transactions=4840`, `seed=81`.
- CSV rows excluding header: `nodes_roles.csv=2248`, `clusters.csv=88`, `top_nodes.csv=50`.
- Mandatory CSV hashes:
  - `nodes_roles.csv`: `cfae7c35e255feb03ab9987d9b2ce6816a77673d1cf06240a3788c1cf281bdc6`
  - `clusters.csv`: `21b7123977addbdffd9f10514e50b698ca588a0ca0c64596ddce8096e84d9a76`
  - `top_nodes.csv`: `36315d905c3a6d5da5b8423b9187b121cb21492f16a451a77d2c1af4a2b9a1c5`
- Determinism checked: repeated local runs produced identical mandatory CSV hashes.
- Live smoke checked: `/health`, `/api/v1/summary`, `/api/v1/runs`, `/api/v1/top-nodes`, `/api/v1/nodes/{gid}`, `/ego`, `/upstream`, `/downstream`, `/clusters`, `/common-receivers`, `/assistant/query`, `/investigations`, and Streamlit `/_stcore/health`.

## Verification Commands

```bash
.venv/bin/pytest --cov=moneygraph --cov-report=term --cov-fail-under=80 -q
.venv/bin/ruff check .
.venv/bin/mypy src
PYTHON=.venv/bin/python make analyze
.venv/bin/python scripts/verify_outputs.py --out ./out --expected-nodes 2248
docker compose config --quiet
docker compose build
docker compose run --rm --no-deps analyzer
```

Verified status: `96 passed`, coverage `82.01%`, Ruff clean, mypy clean, Docker build clean, Docker analyzer clean.

## Data Caveats

The product intentionally frames every result as an analytical hypothesis for review, not as a claim of wrongdoing. Known limits are documented in `docs/methodology.md` and `docs/security.md`: depth 4 truncation, incomplete incoming seed flow, only observed outgoing in-bank transfers, 5 000 KZT threshold, no ground-truth labels, and no external enrichment.

## Documentation

- `docs/implementation_plan.md` - implementation plan and readiness criteria.
- `docs/architecture.md` - components and runtime architecture.
- `docs/methodology.md` - feature, role, priority and clustering methodology.
- `docs/security.md` - security/privacy posture and next controls.
- `docs/scaling.md` - scale-up path after hackathon.
- `docs/demo_script.md` and `docs/jury_pitch.md` - demo and presentation material.
