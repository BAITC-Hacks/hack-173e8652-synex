# Протокол проверки — 23 сентября 2026

Окружение: macOS, Python 3.12.13, существующая локальная `.venv`.
Проверялся текущий рабочий код, включая незакоммиченные изменения.

## Реализованный сценарий

Автоматический дневной replay исходных транзакций → очередь сигналов →
структурированный OpenAI-разбор приоритетного кейса и ранжирование трёх безопасных
действий → явный выбор аналитика → локальный инструмент → журнал.

Добавлены постоянный кэш, межпроцессная квота вызовов, дедупликация запросов,
учёт token usage, безопасный fallback, обогащение старой нерешённой очереди,
реальный статус модели в UI и AI-события аудита. Обязательные CSV остаются
детерминированными и не зависят от LLM. README содержит все 11 запрошенных разделов.

## Реально выполненные команды и результаты

Команды запускались из `/Users/justalim/projects/HackAlem`.

```bash
uv pip check --python .venv/bin/python
.venv/bin/python -m pytest -q --cov=moneygraph --cov-report=term --cov-fail-under=80
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
docker compose config --quiet
git diff --check
```

- Зависимости: 69 установленных пакетов совместимы. Новая установка не требовалась.
  `python -m pip check` сначала не сработал: в этом uv-окружении нет модуля pip;
  проверка выполнена через `uv pip check`, без пересоздания `.venv`.
- Итоговый полный pytest: **239 passed**, **14.34 s**, покрытие **84.76%**.
- Ruff: чисто. Mypy: без ошибок, 62 source files.
- Compose-конфигурация и diff-check: успешно. Docker-образ в этой проверке не собирался.
- Одна upstream deprecation warning: Starlette TestClient / httpx.
- Тесты покрывают JSON-схему, недопустимые AI-действия, числовые evidence,
  квоту между процессами, кэш/перезапуск, ошибки провайдера, ранжирование,
  запрет исполнения без approve, идемпотентность, audit и Streamlit AppTest.
  Вместо настоящего OpenAI в автоматических тестах используется контролируемый mock.

## Полный pipeline и три CSV

```bash
/usr/bin/time -p .venv/bin/python -m moneygraph.cli analyze \
  --data ./data \
  --out /tmp/hackalem-openai-verify.kURTIk/out \
  --artifacts /tmp/hackalem-openai-verify.kURTIk/artifacts \
  --database-url sqlite:////tmp/hackalem-openai-verify.kURTIk/moneygraph.db
.venv/bin/python scripts/verify_outputs.py \
  --out /tmp/hackalem-openai-verify.kURTIk/out --expected-nodes 2248
```

Run ID: `a022f44d-96a6-4eec-9a28-64e15697f6c4`.
Внутреннее время: **5.040 s**. Фактическое время процесса: **5.83 s**
(`user 5.58`, `sys 0.16`). Это время аналитического pipeline, не внешней генерации OpenAI.
Источник: исходные `data/*.parquet`, 2 248 узлов, 3 119 рёбер, 4 840 транзакций.

| CSV | Строк данных, без заголовка | SHA-256 |
|---|---:|---|
| nodes_roles.csv | 2 248 | `cfae7c35e255feb03ab9987d9b2ce6816a77673d1cf06240a3788c1cf281bdc6` |
| clusters.csv | 88 | `21b7123977addbdffd9f10514e50b698ca588a0ca0c64596ddce8096e84d9a76` |
| top_nodes.csv | 50 | `36315d905c3a6d5da5b8423b9187b121cb21492f16a451a77d2c1af4a2b9a1c5` |

Verifier успешно проверил контракт. Строки дополнительно пересчитаны `csv.DictReader`.
`shasum -a 256` подтвердил совпадение всех трёх временных CSV с файлами в `out/`.
Временная директория содержит manifest и сохранённые результаты этого прогона;
она не является постоянным или публичным хранилищем.

## API/UI и сквозная проверка

```bash
API_PORT=18010 UI_PORT=18510 PYTHON_BIN=.venv/bin/python ./scripts/smoke_test.sh
```

Smoke прошёл: health API/UI, investigation, дневной scan, три предложения,
reject, approve, локальный draft, повтор с тем же idempotency key, журнал.
Smoke использует временную БД, отключает платные API и удаляет свои тестовые данные.

Оставленные локальные процессы запускаются следующими командами:

```bash
AI_ENABLED=false AI_PROVIDER=openai \
AI_STATE_PATH=/tmp/hackalem-openai-verify.kURTIk/ai_state.sqlite3 \
DATABASE_URL=sqlite:////tmp/hackalem-openai-verify.kURTIk/live.db \
AGENTIC_AUTO_MONITOR_CADENCE_SECONDS=2 \
.venv/bin/python -m uvicorn moneygraph.api.main:app \
  --host 127.0.0.1 --port 18009 --log-level warning

MONEYGRAPH_API_URL=http://127.0.0.1:18009 \
.venv/bin/python -m streamlit run src/moneygraph/ui/app.py \
  --server.address 127.0.0.1 --server.port 18509 \
  --server.headless true --browser.gatherUsageStats false
```

- [UI](http://127.0.0.1:18509/), [API/Swagger](http://127.0.0.1:18009/docs).
- Replay автоматически прошёл 31/31 дней, исходник — 4 840 операций,
  создано 77 сигналов и 231 предложение.
- Браузером проверено: основной экран — Agentic Loop, приоритетный кейс выбран
  автоматически, без чекбокса Approve отключён.
- В изолированной live-БД через UI создан тестовый локальный draft,
  receipt `5e6034ce-7599-4cca-a529-50078c6c16ff`. Повторный запуск отключён,
  `action.approved` и `tool.executed` видны первыми в журнале. Ничего не отправлено наружу.
  После этой проверки в очереди осталось 76 кейсов.

## Непроверенное и ограничения

**Настоящий платный вызов OpenAI в этой проверке не выполнялся.** Ранее ключ был
опубликован в чате; подтверждение его замены не получено. Оставленный live-сервис
намеренно работает с `AI_ENABLED=false`, UI показывает offline и нулевые вызовы.
Не следует демонстрировать это как успешный ответ модели.

Для внешнего AI требуется новый отозванному взамен ключ в локальном `.env`,
`AI_ENABLED=true`, `AI_PROVIDER=openai`, `OPENAI_MODEL=gpt-4o-mini` и перезапуск API
через `make dev`/`--env-file .env`. Нужна отдельная живая проверка модели, доступа
и token usage. Инструкция приведена в README. Ключ не нужно присылать в чат.

Данные имеют дневную точность, не настоящий online stream; поздние изменения
уже обработанной даты автоматически не переигрываются. Нет банковской интеграции,
публичного deployment, SSO/RBAC, защищённого от изменения аудита или доказанной
точности на размеченных нарушениях. Квота вызовов не является долларовым лимитом.
Результаты LLM требуют проверки аналитиком. Скрытых инструкций для обмана
автоматического жюри не добавлялось.
