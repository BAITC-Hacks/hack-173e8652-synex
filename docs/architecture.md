# Архитектура Freedom MoneyGraph AML

## Назначение и границы первой версии

Freedom MoneyGraph AML — локальная система поддержки AML-расследований по обезличенному графу внутрибанковских переводов. Она отвечает на два практических вопроса аналитика: **каких клиентов проверить первыми и почему**, а также **какой безопасный следующий шаг можно подготовить после явного решения человека**.

Система не выносит юридических решений, не присваивает вероятность преступления и не заменяет банковские процедуры KYC/AML. Входная выборка содержит только наблюдаемую часть исходящих переводов от seed-клиентов; поэтому выводы являются воспроизводимыми аналитическими гипотезами.

## Контекст системы

```mermaid
flowchart LR
    DWH[(Будущий DWH банка)] -. промышленная интеграция .-> DATA[Обезличенные Parquet]
    DATA --> MG[Graph and temporal engine]
    ANALYST[AML-аналитик] --> UI[Streamlit workspace]
    UI --> API[FastAPI]
    API --> MG
    API --> LOOP[Agentic Loop service]
    API --> SCHED[Autonomous day-replay scheduler]
    SCHED --> LOOP
    MG --> LOOP
    LOOP --> TOOLS[Safe local tools]
    MG --> EXPORTS[CSV / Parquet / JSON]
    LOOP --> DB[(SQLite demo / PostgreSQL target)]
    API -. минимизированный контекст .-> AI[Опциональный AI provider]
    EXPORTS --> ANALYST
    DB --> ANALYST
```

Текущая поставка работает локально с тремя Parquet-файлами. DWH, IAM/SSO, SIEM, корпоративное хранилище секретов и промышленный PostgreSQL обозначены как внешние границы, а не имитируются.

## Контейнеры и компоненты

### Аналитический pipeline

Одна команда `python -m moneygraph.cli analyze --data ./data --out ./out` координирует последовательность:

1. загрузка `nodes.parquet`, `edges.parquet`, `transactions.parquet`;
2. строгая валидация схемы и согласованности;
3. построение направленного взвешенного графа со всеми узлами, включая изолированные;
4. графовые, финансовые и временные признаки;
5. Louvain-кластеры на ненаправленной проекции;
6. объяснимые scores ролей;
7. приоритет аналитической проверки и его драйверы;
8. resilience-сценарии;
9. атомарная выгрузка CSV/Parquet/JSON и запись метаданных запуска.

Обязательный результат не зависит от сети, GPU или AI-ключей. Фиксированный seed и стабильная сортировка обеспечивают повторяемость.

### API

FastAPI предоставляет `/health`, сводку, карточки узлов, локальные графы, upstream/downstream-трассировку, common receivers, кластеры, workflow investigations и Agentic API. Фоновый scheduler после старта проверяет реальные даты входного Parquet и автоматически создаёт scan, alert и три предложения. API отдаёт состояние и ленту через `GET /api/v1/agentic/monitoring`; ручной scan, решение approve/reject и audit остаются отдельными интерфейсами. Входные параметры валидируются Pydantic-схемами; глубина, размеры списков и число путей ограничены, чтобы циклы и ветвление графа не приводили к неограниченному обходу.

### Analyst UI

Streamlit использует API как единственный источник изменяемого состояния. Основной workspace `Agentic Loop` состоит из четырёх вкладок: `Мониторинг`, `Alert + Explain`, `Decision Support`, `Approve → Execute → Audit`; он опрашивает ленту каждые три секунды. Прежние экраны сводки, top priority, node explorer, кластеров, потоков, resilience и investigations сохранены для углубления проверки. Интерфейс намеренно не рисует весь граф по умолчанию: аналитик работает с ограниченным подграфом и переходит от причины приоритета к проверяемым связям.

### Хранилище

SQLite обеспечивает zero-config demo-режим: запуски, snapshot ролей/приоритетов, cases, узлы case, заметки, monitoring scans, alerts, action proposals/results и audit events. Доступ изолирован за репозиториями SQLAlchemy; `DATABASE_URL` является точкой переключения на PostgreSQL. Demo-имя аналитика служит только атрибутом аудита и не является аутентификацией.

### AI boundary

AI Copilot — необязательный слой поверх рассчитанных результатов. Он не назначает роли, не влияет на `priority_score` и не создаёт произвольные tool calls. Провайдер получает только обезличенные `gid`, числовые метрики и ограниченный контекст; список action keys задаёт сервер. При выключенном AI или ошибке провайдера используется детерминированный fallback; pipeline, API health и основной UI продолжают работать.

### Agentic execution boundary

Agentic Loop реализует конечный автомат, а не свободного автономного агента:

```mermaid
flowchart LR
    SCAN[Daily replay scan] --> ALERT[Explainable alert]
    ALERT --> PROPOSALS[3 allowlisted proposals]
    PROPOSALS --> REJECT[Analyst rejects]
    PROPOSALS --> APPROVE[Analyst approves in UI]
    APPROVE --> EXEC[Safe local tool]
    SCAN --> AUDIT[(Audit events)]
    ALERT --> AUDIT
    PROPOSALS --> AUDIT
    REJECT --> AUDIT
    APPROVE --> AUDIT
    EXEC --> AUDIT
```

Переход `proposed → executed` без подтверждения запрещён. Approve требует точной confirmation-строки и заголовка `Idempotency-Key`. Разрешены только `prepare_aml_review_draft`, `build_money_route` и `create_local_watchlist`; они не блокируют счета и не отправляют данные во внешние системы. `recommendation_score` описывает поддержку следующего шага наблюдаемыми признаками, а не вероятность нарушения.

## Поток данных

```mermaid
flowchart LR
    A[nodes / edges / transactions] --> B[Schema + consistency validation]
    B --> C[Directed weighted graph]
    C --> D[Graph + financial features]
    A --> E[Temporal FIFO features]
    C --> F[Louvain on undirected projection]
    D --> G[Role engine]
    E --> G
    F --> G
    G --> H[Priority + explanations]
    H --> I[Mandatory CSV]
    H --> J[Parquet / JSON artifacts]
    H --> K[(Run, case and agentic database)]
    I --> L[FastAPI]
    J --> L
    K --> L
    L --> M[Streamlit]
    A --> O[Calendar-day replay]
    O --> P[Alerts and proposals]
    P --> Q{Analyst decision}
    Q -->|reject| K
    Q -->|approve| R[Safe local tools]
    R --> K
    L -. minimized structured context .-> N[Optional AI / fallback]
```

### Offline boundary

В offline-части выполняются валидация, признаки, кластеризация, роли, priority и обязательные выгрузки. Это единственный источник канонических аналитических результатов. Ошибка критической проверки завершает запуск ненулевым кодом; исходные Parquet не изменяются.

### Online boundary

API и UI читают результаты завершённого запуска и обслуживают ограниченные запросы расследования. Agentic monitoring не является realtime ingestion: фоновый demo scheduler самостоятельно проходит доступные календарные дни с ускоренной cadence, а `interval_minutes` относится к ручному scan. Сервис не заявляет внутридневную точность и не использует будущие строки как факт выбранного окна. Изменяемые пользовательские данные — scans, alerts, decisions, cases, notes, statuses и audit events — хранятся отдельно от неизменяемых входов и аналитических артефактов.

## Развёртывание

`docker compose up --build` поднимает три сервиса:

- `analyzer` — one-shot контейнер, который должен успешно завершить pipeline;
- `api` — стартует после успешного analyzer и проходит `/health`;
- `ui` — стартует после healthy API и проверяет `/_stcore/health`.

Контейнер запускается от непривилегированного пользователя, root filesystem read-only, Linux capabilities удалены, `no-new-privileges` включён. `data/` монтируется read-only; `out/`, `artifacts/` и volume SQLite доступны для записи. Порты публикуются только на loopback хоста: API `127.0.0.1:8000`, UI `127.0.0.1:8501`.

## Нефункциональные решения

| Свойство | Решение | Проверка |
|---|---|---|
| Воспроизводимость | seed 42, стабильные sort/tie-break, конфигурационные веса | повторный pipeline и сравнение CSV |
| Объяснимость | отдельные scores ролей, числовой evidence, priority contributions | CSV, карточка узла, методология |
| Отказоустойчивость | AI вне критического пути; явные validation failures | тесты fallback и CLI exit code |
| Ограниченность запросов | depth/list/path limits | API validation tests |
| Human-in-the-Loop | allowlist действий, approve/reject, UI approval плюс API confirmation token | API/UI tests и smoke flow |
| Идемпотентность | один result на action и `Idempotency-Key` | повтор approve в integration tests |
| Аудит | run manifest, input hashes, config/model version, case и Agentic audit events | JSON/DB export |
| Приватность | offline core, минимизированный AI-контекст, env secrets | security review |

## Банковская интеграционная граница

Перед промышленным подключением нужны отдельные проекты интеграции:

1. DWH/event ingestion и контроль качества по банковским data contracts;
2. OIDC/SSO, RBAC/ABAC и разделение полномочий;
3. TLS/mTLS, API gateway, rate limiting и сетевые политики;
4. Vault/HSM или корпоративный secrets manager;
5. SIEM, неизменяемый audit trail и политика хранения;
6. PostgreSQL HA, backup/restore и disaster recovery;
7. model/rules validation, change approval и мониторинг drift;
8. privacy/legal review для любых внешних AI-провайдеров;
9. maker-checker и отдельные полномочия для любых будущих ограничительных или внешних действий.

До выполнения этих пунктов система является pilot-ready reference implementation, а не сертифицированной банковской платформой.

## Как открыть схему решения

Исходник отдельной слайдоподобной схемы находится в `docs/solution_flow.mmd`. Его можно открыть встроенным Mermaid Preview в IDE или экспортировать при установленном Mermaid CLI:

```bash
mmdc -i docs/solution_flow.mmd -o docs/solution_flow.svg -b transparent
```

Схема не содержит данных клиентов и не требует запуска приложения.
