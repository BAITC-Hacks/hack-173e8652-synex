# Протокол проверки — 23 сентября 2026

Окружение: macOS, Python 3.12.13, существующая локальная `.venv`.
Проверялся текущий рабочий код, включая незакоммиченные изменения.

## Дополнительная проверка README с чистой установкой

После обновления инструкции для жюри создано отдельное окружение Python 3.12.13
в `/tmp/hackalem-readme-check.JGCPwc/venv`. Рабочая `.venv`, ключ и существующие
демонстрационные серверы не изменялись. Проверены именно установка из `pyproject.toml`
с extras `[dev,ai]` и команды Make; новые внешние OpenAI-вызовы не выполнялись.

```bash
python3.12 -m venv /tmp/hackalem-readme-check.JGCPwc/venv
/tmp/hackalem-readme-check.JGCPwc/venv/bin/python -m pip install -e '.[dev,ai]'
/tmp/hackalem-readme-check.JGCPwc/venv/bin/python -m pip check

AI_ENABLED=false PYTHON=/tmp/hackalem-readme-check.JGCPwc/venv/bin/python \
OUT_DIR=/tmp/hackalem-readme-check.JGCPwc/out \
ARTIFACTS_DIR=/tmp/hackalem-readme-check.JGCPwc/artifacts \
DATABASE_URL=sqlite:////tmp/hackalem-readme-check.JGCPwc/moneygraph.db make analyze

/tmp/hackalem-readme-check.JGCPwc/venv/bin/python scripts/verify_outputs.py \
  --out /tmp/hackalem-readme-check.JGCPwc/out --expected-nodes 2248

AI_ENABLED=false AI_PROVIDER=fallback \
/tmp/hackalem-readme-check.JGCPwc/venv/bin/python -m pytest -q \
  --cov=moneygraph --cov-report=term --cov-fail-under=80

env -u DATABASE_URL PYTHON=/tmp/hackalem-readme-check.JGCPwc/venv/bin/python \
OUT_DIR=/tmp/hackalem-readme-check.JGCPwc/out \
ARTIFACTS_DIR=/tmp/hackalem-readme-check.JGCPwc/artifacts \
API_PORT=18005 UI_PORT=18505 make smoke
```

- Установка завершилась успешно, `pip check`: `No broken requirements found`;
  импорты `moneygraph`, `openai`, `uvicorn`, `streamlit` проверены.
- Pipeline: run `0c8361f8-fb7d-4523-9df8-28bb4522df82`, **7.945 s внутреннего времени**.
  Выполнялся параллельно тестам; отдельное wall-clock время этого прогона не измерялось.
  Три CSV прошли verifier; исходные данные и рабочие `out/` не перезаписаны.
- Полный pytest в новом окружении: **287 passed**, **20.27 s**, покрытие **85.04%**.
  Одна прежняя upstream Starlette/httpx warning. Ruff и mypy — без ошибок.
- `make dev` с тем же Python и временными путями поднял API/UI на 18011/18511;
  `/health` подтвердил доступность БД/артефактов, UI health ответил `ok`.
  `Ctrl+C` остановил оба процесса; отсутствие listener на этих портах проверено.
- `make smoke` успешно проверил API, UI, investigation, reject/approve и 22 audit-события.
- Проверены локальные цели и якоря среди 24 Markdown-ссылок README, синтаксис всех
  12 Bash-блоков, наличие 11 разделов и `git diff --check`.

Это проверка инструкции установки, не новая оценка качества LLM. Живые ответы
OpenAI и предыдущий wall-clock замер pipeline описаны ниже отдельно.
После проверки удалено только созданное временное `venv`; его можно воспроизвести
командами выше. Временные результаты pipeline оставлены, рабочая `.venv` сохранена.

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
uv pip install --python .venv/bin/python 'openai>=1.60,<3.0'
uv pip check --python .venv/bin/python
.venv/bin/python -m pytest -q --cov=moneygraph --cov-report=term --cov-fail-under=80
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
docker compose config --quiet
git diff --check
```

- Зависимости: 74 установленных пакета совместимы. При живой проверке обнаружен
  отсутствующий optional SDK `openai`: до установки запрос завершался
  `ModuleNotFoundError`, не доходя до провайдера. Установлен `openai==2.54.0`
  и его зависимости. Теперь readiness отдельно обнаруживает отсутствие SDK.
  `python -m pip check` сначала не сработал: в этом uv-окружении нет модуля pip;
  проверка выполнена через `uv pip check`, без пересоздания `.venv`.
- Итоговый полный pytest: **287 passed**, **16.34 s**, покрытие **85.06%**.
- Ruff: чисто. Mypy: без ошибок, 64 source files.
- Compose-конфигурация и diff-check: успешно. Docker-образ в этой проверке не собирался.
- Одна upstream deprecation warning: Starlette TestClient / httpx.
- Тесты покрывают JSON-схему, недопустимые AI-действия, числовые evidence,
  квоту между процессами, кэш/перезапуск, ошибки провайдера, ранжирование,
  запрет исполнения без approve, идемпотентность, audit и Streamlit AppTest.
  Вместо настоящего OpenAI в автоматических тестах используется контролируемый mock.
- Исправлена гонка: монитор больше не объявляет день обработанным до сохранения
  всех трёх предложений каждого сигнала. Добавлен детерминированный concurrency-тест.
- Убраны ложные success-сообщения при ошибке инструмента; sidebar и основной экран
  используют фактический журнал вызовов, а не только наличие настройки.

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
AI_STATE_PATH=./artifacts/ai_live.sqlite3 AI_DAILY_CALL_LIMIT=20 \
DATABASE_URL=sqlite:///./artifacts/agentic_live.db \
.venv/bin/python -m uvicorn moneygraph.api.main:app \
  --env-file .env --host 127.0.0.1 --port 18009 --log-level warning

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
- Через UI создан локальный документ с **настоящим сохранённым ответом OpenAI**:
  GID `100000002547110100`, replay `2026-07-16`,
  action `b6c66844-ae67-4457-98a1-13968e59a2a9`,
  receipt `f38a94ea-aa00-4f20-a15c-9566c5fcaaa5`.
  Проверены текст документа, числовая таблица, модель, ограничения, кнопка скачивания
  Markdown и события `action.approved`/`tool.executed`. Повторный запуск отключён.
  Во внешние банковские системы ничего не отправлено.
- Вместе с более ранним offline smoke-draft разобрано два кейса; осталось 75.
- Live-БД сохранена в рабочей директории проекта без изменения исходной временной копии командой
  `sqlite3 /tmp/hackalem-openai-verify.kURTIk/live.db '.backup artifacts/agentic_live.db'`.
  Счётчики и кэш находятся в `artifacts/ai_live.sqlite3`; оба файла пережили
  проверенный перезапуск API. Эти runtime-артефакты не включаются в Git.

## Настоящие запросы OpenAI и аналитика

Проверка выполнена 23 сентября 2026, snapshot счётчиков — **12:17:29 UTC**.
Пользователь подтвердил использование локального `.env`. Backend перезапущен
с `AI_ENABLED=true`, `AI_PROVIDER=openai`, `OPENAI_MODEL=gpt-4o-mini` из этого файла.
Ранее работавший offline-процесс остановлен. Ключ не выводился и не сохранялся в отчёте.

| Проверка | Результат |
|---|---|
| Диагностический вызов `OpenAIProvider.assess_alert` на реальном alert | Успех, 5.579 s, 514 input + 301 output tokens |
| Автоматический разбор очереди в работающем API | 19 успешных структурированных assessment |
| `POST /api/v1/assistant/query`, конкретный GID и вопрос аналитика | `provider=openai`, `fallback=false`, 2.631 s, 500 input + 172 output tokens |
| Повтор точно того же HTTP-запроса | `cached=true`, `ai_status=cache_hit`, 0.003 s; нового внешнего вызова нет |
| Общий журнал приложения | 20 успешных внешних вызовов, 0 ошибок, 1 cache hit |
| Usage журнала приложения | 10 292 input + 5 828 output = **16 120 tokens** |
| Вместе с отдельным диагностическим вызовом | **21 успешный вызов**, 10 806 input + 6 129 output = **16 935 tokens** |

Счётчики получены из `usage` реальных ответов SDK, а не рассчитаны по длине текста.
Диагностический вызов выполнялся отдельно от governor, поэтому не включён в его 20 вызовов.
Для этой проверки установлен локальный лимит **20 новых вызовов за сутки UTC**:
он исчерпан, дальнейшие новые запросы до следующего дня не оплачиваются приложением;
сохранённые AI-карточки, кэш, правила и одобренные локальные инструменты доступны.
Стандартная настройка в `.env.example` — 100, это не долларовый лимит.

Пример фактической аналитики: модель разобрала входящий поток **1 026 000 KZT**
от **5 плательщиков** за 16 июля, **0 KZT исходящего** и накопленный приток
**1 500 000 KZT**. Она предложила внутренний AML-review, наблюдаемый маршрут
и локальный watchlist; server-side evidence содержит точные значения.
Это гипотеза для проверки, не доказательство нарушения.

Личный Usage Dashboard OpenAI не проверялся через авторизованный браузер.
Для сверки выберите нужную организацию, дату 23 сентября 2026 UTC и все проекты
в отдельном фильтре Usage Dashboard; API — Chat Completions.
[Официальное описание usage и фильтров](https://help.openai.com/en/articles/10478918).

## Непроверенное и ограничения

Перед публикацией выполнен `uvx pip-audit --path .venv/lib/python3.12/site-packages --progress-spinner off --skip-editable --timeout 10`.
Аудит завершился с кодом 1: две записи одного advisory **PYSEC-2026-113 / CVE-2026-25087**
для PyArrow 21.0.0, исправленная версия — 23.0.1. Это не «чистый» dependency audit.
Согласно [описанию advisory](https://github.com/pypa/advisory-database/blob/main/vulns/pyarrow/PYSEC-2026-113.yaml),
проблема затрагивает C++ IPC-file reader с явно включённым pre-buffering;
соответствующий API не доступен через Python bindings. В текущем коде используется
чтение Parquet через pandas; вызовов IPC reader/pre-buffering не найдено.
Для наблюдаемого Python/Parquet-сценария путь эксплуатации не выявлен.
Зависимости при публикации документации не менялись; перед расширением способов
загрузки данных или промышленным развёртыванием требуется повторная проверка.

Данные имеют дневную точность, не настоящий online stream; поздние изменения
уже обработанной даты автоматически не переигрываются. Нет банковской интеграции,
публичного deployment, SSO/RBAC, защищённого от изменения аудита или доказанной
точности на размеченных нарушениях. Квота вызовов не является долларовым лимитом.
Результаты LLM требуют проверки аналитиком. Скрытых инструкций для обмана
автоматического жюри не добавлялось.
