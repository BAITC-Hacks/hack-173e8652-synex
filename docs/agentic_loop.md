# Agentic AI Loop — Capability Contract

## CAPABILITY

AML-аналитик получает единый четырёхшаговый workflow: система воспроизводимо сканирует очередное дневное окно датасета, формирует объяснимую alert-карточку, предлагает ровно три безопасных действия, а после явного approve/reject исполняет только разрешённый локальный инструмент и записывает каждый переход в append-only audit log.

## CONSTRAINTS

- Финальное решение и ответственность всегда принадлежат аналитику.
- Ни AI, ни rules engine не блокируют клиента, перевод и счёт и не отправляют сообщение в АФМ.
- Core monitoring, alerts и предложения работают без LLM и без сети; LLM может только расширить объяснение из allowlist-метрик.
- Исходник имеет дневную, а не часовую гранулярность. Сигнал `dwell < 2h` недоступен и не симулируется; используется наблюдаемое перенаправление за 0–2 дня.
- Мониторинг является честно маркированным demo replay по датам июля 2026, а не заявлением о live ingestion.
- Разрешены только три server-owned action key: `prepare_aml_review_draft`, `build_money_route`, `create_local_watchlist`.
- Любое действие требует явного решения `approve` или `reject`; approve дополнительно требует строки подтверждения `APPROVE`.
- Повтор запроса с тем же idempotency key не создаёт второй кейс, draft или watchlist.
- `recommendation_score` означает пригодность следующего шага, а не вероятность нарушения.
- Все `gid` остаются непрозрачными строками; raw transactions и заметки не передаются внешнему AI.

## IMPLEMENTATION CONTRACT

### Actors

- `rules-engine` — детерминированно сканирует дневное окно.
- `copilot` — объясняет и предлагает только allowlisted действия.
- `analyst` — выбирает approve/reject и несёт ответственность за решение.
- `tool-executor` — выполняет подтверждённое локальное действие.

### Surfaces

Streamlit workspace `Agentic Loop` содержит ровно четыре вкладки:

1. `Мониторинг` — interval setting, replay date, scan, alert feed.
2. `Alert + Explain` — факты, гипотеза, причины priority, ограничения и impact.
3. `Decision Support` — три action cards с rationale и expected outcome.
4. `Approve → Execute → Audit` — approve/reject, результат инструмента и журнал.

### States and transitions

```text
scan.completed -> alert.open
alert.open -> actions.proposed
actions.proposed -> action.rejected
actions.proposed -> action.approved -> action.executed
action.approved -> action.failed
```

Переход из `proposed` в `executed` без `approved` запрещён.

### Monitoring rules

Для выбранной даты replay alert создаётся, если выполняется хотя бы одно правило:

- `daily_unique_payers >= 8`;
- `0.9 <= pass_through <= 1.1` и `fast_forward_0_2d_ratio >= 0.7`;
- `daily_incoming_kzt > 1_000_000`.

Alert показывает конкретные числа, исходную дату, `simulation=true`, роль, кластер, priority и ограничение дневной гранулярности.

### Safe tools

- `prepare_aml_review_draft`: создаёт investigation и локальный черновик для ПОД/ФТ; ничего не отправляет.
- `build_money_route`: строит bounded ego depth 2 и downstream paths до depth 4.
- `create_local_watchlist`: создаёт локальный investigation/watchlist из target и наблюдаемых прямых плательщиков; не изменяет банковские системы.

### Interfaces

- `POST /api/v1/agentic/scans`
- `GET /api/v1/agentic/scans/{scan_id}`
- `POST /api/v1/agentic/alerts/{alert_id}/proposals`
- `POST /api/v1/agentic/actions/{action_id}/decision`
- `GET /api/v1/agentic/audit`

Responses use the existing `{data, meta}` envelope and structured API errors.

### Persistence and audit

SQLite tables persist monitoring scans, alerts and action proposals/executions. Every write also appends an `audit_events` row containing actor, entity, transition and bounded JSON details. Existing investigation storage remains the destination for generated drafts and watchlists.

## NON-GOALS

- Real-time stream ingestion or claims of sub-day detection on date-only data.
- Automatic blocking, transaction cancellation or client restrictions.
- Direct submission to АФМ/ПОД/ФТ or external messaging.
- Legal conclusion, criminal-risk probability or autonomous escalation.
- Production IAM/SSO/RBAC; demo actor remains `ANALYST_NAME` and is labelled accordingly.

## OPEN QUESTIONS

No implementation blocker remains for the hackathon demo. Production rollout still requires the bank to define approval roles, official STR/SAR schema, retention policy, maker-checker rules and integration adapters.

## HANDOFF

Ready for direct implementation using TDD, followed by API/UI smoke, full regression, security review and browser verification.
