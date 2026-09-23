# Freedom MoneyGraph AML

<p align="center">
  <img src="docs/assets/moneygraph-cover.svg" alt="Freedom MoneyGraph AML — explain the signal, keep the decision human" width="100%">
</p>

Локальный pilot-ready продукт для AML-аналитика Freedom Bank: система строит объяснимую графовую модель обезличенных внутрибанковских переводов, ранжирует приоритет проверки и проводит аналитика через контролируемый Agentic AI Loop от сигнала до безопасного локального действия.

Главные вопросы продукта: кого смотреть первым, почему и какой следующий шаг можно подготовить после явного решения человека. **ИИ — второй пилот, не автопилот.**

Рабочий pipeline читает именно файлы кейса из `data/`: `nodes.parquet` (2 248 строк), `edges.parquet` (3 119), `transactions.parquet` (4 840). Автономный монитор читает этот же `transactions.parquet`; синтетические наборы существуют только в `tests/` и в рабочий результат не подмешиваются.

## Что реализовано

- Детерминированный pipeline `python -m moneygraph.cli analyze --data ./data --out ./out`.
- Строгая валидация трёх Parquet: schema, counts, date range, self-loop, node coverage, seed count, edge/transaction aggregation.
- NetworkX-граф с 2 248 узлами, включая изолированные seed.
- Graph/financial/temporal features, роли, `role_score`, `priority_score`, evidence до 200 символов.
- Кластеры, top nodes, resilience-анализ, validation report, manifest, расширенные Parquet artifacts.
- SQLite-хранилище запусков, node snapshots, investigations, notes и audit events.
- Agentic Loop: фоновый calendar-day replay → объяснимый alert → три автоматически подготовленных предложения → approve/reject → безопасный local tool → audit.
- FastAPI: summary, runs, top nodes, node profile, ego, upstream/downstream traces, common receivers, clusters, resilience, investigations, assistant и Agentic API.
- Streamlit UI: основной четырёхвкладочный Agentic workspace плюс dashboard, top priorities, node explorer, clusters, investigations и AI Copilot.
- Optional AI layer: OpenAI или NVIDIA NIM через env; по умолчанию безопасный deterministic fallback без сети.
- Human-in-the-Loop: нет автоматических block/freeze/send; approve требует `APPROVE`, повтор защищён idempotency key, локальный AML draft никуда не отправляется.
- Dockerfile и Docker Compose с non-root user, loopback ports, read-only containers, dropped capabilities.

## Быстрый запуск

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,ai]"
PYTHON=.venv/bin/python make analyze
.venv/bin/python scripts/verify_outputs.py --out ./out --expected-nodes 2248
DATA_DIR=./data OUT_DIR=./out ARTIFACTS_DIR=./artifacts DATABASE_URL=sqlite:///./moneygraph.db \
  AGENTIC_AUTO_MONITOR_ENABLED=true \
  .venv/bin/uvicorn moneygraph.api.main:app --host 127.0.0.1 --port 8000
MONEYGRAPH_API_URL=http://127.0.0.1:8000 \
  .venv/bin/streamlit run src/moneygraph/ui/app.py --server.address 127.0.0.1 --server.port 8501
```

API: http://127.0.0.1:8000  
Swagger: http://127.0.0.1:8000/docs  
UI: http://127.0.0.1:8501

## Быстрое Agentic-демо

В UI открыть `Agentic Loop` и пройти четыре вкладки:

1. увидеть, как сервис сам проходит даты исходного файла и заполняет ленту; ручной replay доступен для повторной проверки;
2. открыть alert и проверить факты/ограничения;
3. увидеть три уже подготовленных безопасных предложения;
4. отклонить одно, другое явно подтвердить и показать audit receipt.

`make dev` и Docker Compose включают автономный demo replay по умолчанию. API без этих команд включает его через `AGENTIC_AUTO_MONITOR_ENABLED=true`; скорость показа задаёт `AGENTIC_AUTO_MONITOR_CADENCE_SECONDS=2`. Сервис проходит каждую доступную дату один раз, сохраняет прогресс в SQLite и проверяет источник на новые даты. UI обновляет ленту каждые три секунды.

Для настоящих LLM-пояснений скопируйте `.env.example` в локальный `.env` и задайте `AI_ENABLED=true`, `AI_PROVIDER=openai`, `OPENAI_API_KEY` и `OPENAI_MODEL` (либо `AI_PROVIDER=nvidia`, `NVIDIA_API_KEY`, `NVIDIA_MODEL`). `.env` исключён из Git; не передавайте ключ в чат и не публикуйте `docker compose config` с ним. Docker Compose читает `.env` автоматически. При прямом запуске Python экспортируйте те же переменные в окружение API-процесса. Один внешний запрос делается только для ведущего alert каждого scan; в prompt попадают лишь псевдонимный ID и разрешённые числовые признаки, не сырые операции/список плательщиков. Действия и решения остаются под контролем сервера и аналитика, ответ LLM — только пояснение. Если ключ не настроен или вызов неуспешен, работает офлайн-разбор.

Автоматическая сквозная проверка поднимает API/UI с временной БД и проходит тот же безопасный сценарий:

```bash
./scripts/smoke_test.sh
```

Это не realtime: исходные транзакции имеют дату без внутридневного timestamp. Фоновый demo scheduler ускоренно проигрывает календарные дни; `interval_minutes` относится к ручному scan, а cadence в секундах — к показу replay. Система не заявляет `dwell < 2h`, а использует наблюдаемое перенаправление за 0–2 календарных дня. `recommendation_score` — поддержка следующего шага, не вероятность нарушения. При `AI_ENABLED=false` объяснения и предложения создаёт детерминированный аналитический движок; внешний LLM не выдаётся за активный в этом режиме.

## Проверенные результаты последнего полного запуска

- Latest full-dataset pipeline (`make analyze`, project output): `5.223s`
  internal stage total; `6.36s` wall-clock including process startup.
- Counts: `nodes=2248`, `edges=3119`, `transactions=4840`, `seed=81`.
- CSV rows excluding header: `nodes_roles.csv=2248`, `clusters.csv=88`, `top_nodes.csv=50`.
- Mandatory CSV hashes:
  - `nodes_roles.csv`: `cfae7c35e255feb03ab9987d9b2ce6816a77673d1cf06240a3788c1cf281bdc6`
  - `clusters.csv`: `21b7123977addbdffd9f10514e50b698ca588a0ca0c64596ddce8096e84d9a76`
  - `top_nodes.csv`: `36315d905c3a6d5da5b8423b9187b121cb21492f16a451a77d2c1af4a2b9a1c5`
- Determinism checked: repeated local runs produced identical mandatory CSV hashes.
- Live smoke covers `/health`, core analytics, investigations, Streamlit `/_stcore/health` and the Agentic flow: scan → proposals → reject → approve with confirmation/idempotency → audit.

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

Verified status: `137 passed`, coverage `83.34%`, Ruff clean, `mypy src` clean,
mandatory CSV verifier clean. A local API/UI live run replayed all 31 dates from
4,840 operations into 77 alerts and 231 proposals; approve, reject, audit, and
idempotent retry passed. One upstream Starlette/httpx deprecation warning remains.
On the verification host, a fresh Docker rebuild failed during image extraction
because Docker Desktop reported `read-only file system`; host free space was
about 117 MiB at that point. The current code was verified in the local `.venv`
instead. The older Docker containers were not used as evidence for this release.

## Data Caveats

The product intentionally frames every result as an analytical hypothesis for review, not as a claim of wrongdoing. Known limits are documented in `docs/methodology.md`, `docs/agentic_loop.md` and `docs/security.md`: calendar-day replay instead of realtime, depth 4 truncation, incomplete incoming seed flow, only observed outgoing in-bank transfers, 5 000 KZT threshold, no ground-truth labels, and no external enrichment.

## Documentation

- `docs/implementation_plan.md` - implementation plan and readiness criteria.
- `docs/architecture.md` - components and runtime architecture.
- `docs/methodology.md` - feature, role, priority and clustering methodology.
- `docs/agentic_loop.md` - Human-in-the-Loop capability contract, state machine and safe-tool boundary.
- `docs/security.md` - security/privacy posture and next controls.
- `docs/scaling.md` - scale-up path after hackathon.
- `docs/demo_script.md` and `docs/jury_pitch.md` - demo and presentation material.
