# Безопасность и приватность

## Статус документа

Это threat model pilot-ready локальной версии. Он не является заключением банковского security review и не утверждает готовность к промышленной обработке персональных или банковских данных.

## Активы

- обезличенные Parquet и их хэши;
- аналитические CSV/Parquet/JSON;
- роли, priority и объяснения;
- investigations, заметки и audit events;
- конфигурация правил и версии запусков;
- опциональные API-ключи AI-провайдеров.

В кейсе нет ФИО, ИИН, устройств, адресов, дохода или иных придуманных атрибутов клиента. `gid` остаётся устойчивым идентификатором и поэтому всё равно считается чувствительным аналитическим контекстом.

## Trust boundaries

```mermaid
flowchart LR
    subgraph Local[Локальная доверенная среда]
        DATA[(Parquet read-only)] --> CORE[Deterministic core]
        CORE --> OUT[(Artifacts)]
        CORE --> API[FastAPI]
        API --> UI[Streamlit]
        API --> DB[(Cases + audit)]
    end
    API -. только минимизированные метрики .-> EXT[Внешний AI provider]
    USER[Локальный аналитик] --> UI
```

Граница внешнего AI по умолчанию закрыта: `AI_ENABLED=false`. Core pipeline не имеет сетевой зависимости.

## Threat model

| Угроза | Возможный ущерб | Текущая мера | Остаточный риск / следующий шаг |
|---|---|---|---|
| Подмена входных файлов | неверные роли и приоритеты | строгая валидация, SHA-256 в manifest, read-only mount | подпись/каталог данных и lineage из DWH |
| Path traversal или запись во вход | изменение данных/файлов | в Compose вход смонтирован `data:ro`, выходы отделены; API не принимает файловые пути | CLI в локальном режиме доверяет оператору; нужны allowlist путей и service account |
| Неограниченный обход графа | DoS API | Pydantic validation, предел depth/list/path, bounded ego | gateway quotas и load testing |
| SQL injection | чтение/изменение cases | SQLAlchemy и параметризованные операции | SAST/DAST и PostgreSQL least privilege |
| Утечка ключа | доступ к внешнему provider | только env, `.env` игнорируется, ключи не логируются и не входят в image | Vault/HSM, rotation, egress policy |
| Отправка датасета в LLM | утечка графа | AI вне core; только выбранные обезличенные метрики | DLP, approved on-prem provider, legal/privacy review |
| Prompt injection | неверное AI-описание/действие | tool results — единственный источник фактов; fallback; AI не влияет на scores | tool allowlist, output validation, human confirmation |
| Неавторизованный доступ | раскрытие графа/cases | порты Compose только loopback | обязательные OIDC/SSO, RBAC/ABAC, TLS/mTLS |
| Подмена audit log | потеря трассируемости | structured audit events в БД | append-only/WORM storage и SIEM |
| Supply-chain dependency | выполнение уязвимого кода | ограниченные version ranges, CI install/build | lockfile/hash pinning, SBOM, dependency scan |
| CSV formula injection | выполнение формулы при открытии экспорта | значения домена формируются системой и не должны начинаться с формул | явное escaping пользовательских notes в CSV и тесты |

## Реализованные меры

### Данные и файловая система

- Входные Parquet не модифицируются.
- Критические ошибки validation завершают pipeline ненулевым кодом.
- Docker монтирует `data/` read-only, а выходы и БД — отдельными writable volumes.
- Контейнер использует непривилегированного пользователя, read-only root filesystem, `cap_drop: ALL` и `no-new-privileges`.
- Артефакты и manifest связываются с hash входов и версией правил.

### API и вычисления

- Входы описаны Pydantic-схемами; неизвестные `gid` и неверные диапазоны получают явные HTTP-коды.
- Глубина обхода и размеры запросов ограничены.
- CORS задаётся через `CORS_ORIGINS`, а не открыт безусловно.
- `/health` не зависит от AI и не раскрывает секреты.
- Детали исключений предназначены для server logs; клиент получает контролируемую ошибку и request ID.

### Секреты и AI

- `OPENAI_API_KEY` и `NVIDIA_API_KEY` читаются только из environment variables.
- `.env.example` содержит пустые значения, реальный `.env` исключён из Git.
- AI выключен по умолчанию и не участвует в role/priority pipeline.
- Внешнему provider передаётся минимальный структурированный контекст, не полный raw dataset.
- Ошибка или отсутствие ключа приводит к deterministic fallback, а не к падению приложения.

### Аудит

- Pipeline run хранит конфигурацию/версию и input hashes.
- Действия с investigation сохраняют аналитика demo-session и audit event.
- `ANALYST_NAME` — атрибут демо-аудита, **не механизм аутентификации**.

## Явно не реализовано в pilot

- IAM/SSO, MFA, RBAC/ABAC и segregation of duties;
- TLS/mTLS, WAF/API gateway и rate limiting;
- неизменяемый централизованный audit log и SIEM;
- Vault/HSM, автоматическая rotation и egress allowlist;
- PostgreSQL HA, шифрование backups и disaster recovery;
- data retention/deletion policy и классификация данных;
- SBOM, подписанные images, container/dependency/SAST/DAST scanning;
- formal model validation, fairness review и change approval;
- промышленная защита Streamlit как internet-facing приложения.

Локальный demo нельзя публиковать в недоверенную сеть без этих компенсирующих мер.

## Минимальный production checklist Freedom Bank

1. Разместить сервисы в закрытом сегменте; разрешить egress только утверждённым адресам.
2. Интегрировать OIDC/SSO и enforce роли viewer/analyst/supervisor/admin.
3. Поставить API за gateway с TLS/mTLS, rate limits, WAF и request size limits.
4. Перенести секреты в корпоративный manager, включить rotation и короткоживущие credentials.
5. Перенести БД в PostgreSQL с отдельными ролями, encryption at rest, backup/restore drills.
6. Отправлять security/audit events в SIEM; защищать их от изменения.
7. Добавить SBOM, image signing, dependency/SAST/DAST/container scans в release gate.
8. Провести privacy/legal review AI; для чувствительных данных использовать approved private endpoint либо полностью offline fallback.
9. Зафиксировать правила model governance: версия конфигурации, dual control, validation set, drift monitoring и rollback.
10. Провести threat modeling и penetration test в целевой инфраструктуре.

## Реакция на секрет в репозитории

Если ключ обнаружен в коде, истории Git или логе, его нужно считать скомпрометированным: немедленно отозвать и перевыпустить у провайдера, удалить из текущей версии и истории по согласованной процедуре, проверить логи использования и добавить регрессионный secret scan. Простого удаления строки из последнего commit недостаточно.
