"""Human-in-the-loop Streamlit workflow backed only by the Agentic API."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import streamlit as st

from moneygraph.ui.client import APIClient, APIClientError
from moneygraph.ui.graph import build_ego_figure
from moneygraph.ui.helpers import format_kzt

TAB_LABELS = (
    "1 · Мониторинг",
    "2 · Alert + Explain",
    "3 · Decision Support",
    "4 · Approve → Execute → Audit",
)

ALLOWED_ACTION_KEYS = frozenset(
    {
        "prepare_aml_review_draft",
        "build_money_route",
        "create_local_watchlist",
    }
)


@st.fragment(run_every=timedelta(seconds=3))
def render_agentic_loop(client: APIClient) -> None:
    """Poll the autonomous feed while keeping the human decision inside the UI."""

    _hero()
    st.info(
        "Режим демо: сервис сам проходит календарные дни июля 2026 и проверяет новые окна. "
        "Исходник не содержит внутридневного времени."
    )
    st.caption(
        "AI ранжирует и готовит локальные артефакты, но не блокирует счета, "
        "не останавливает переводы и не отправляет сообщения в АФМ. Решение — за аналитиком."
    )

    monitoring = _api_call(client.agentic_monitoring)
    monitoring = dict(monitoring) if isinstance(monitoring, Mapping) else {}
    _auto_focus(monitoring)
    monitoring_tab, alert_tab, support_tab, execution_tab = st.tabs(TAB_LABELS)
    with monitoring_tab:
        scan = _render_monitoring(client, monitoring)
    alerts = _alerts(scan)
    with alert_tab:
        selected_alert = _render_alert(alerts)
    with support_tab:
        actions = _render_decision_support(client, selected_alert)
    with execution_tab:
        _render_execution_and_audit(client, selected_alert, actions)


def _hero() -> None:
    st.markdown(
        """
        <div class="mg-hero">
          <div class="mg-label" style="color:#bfdbfe">HUMAN-IN-THE-LOOP AML</div>
          <h1>Agentic AI Loop</h1>
          <p>Мониторинг → объяснение → рекомендации → решение аналитика → аудит</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _auto_focus(monitoring: Mapping[str, Any]) -> None:
    if monitoring.get("enabled") is not True:
        return
    queue = (
        _records(monitoring, "priority_queue")
        if "priority_queue" in monitoring
        else _records(monitoring, "recent_alerts")
    )
    if not queue:
        if monitoring.get("processed_days"):
            st.success("Очередь разобрана: новых кейсов, ожидающих решения, нет.")
        else:
            st.info("Система проверяет источник; приоритетный кейс появится автоматически.")
        return
    alert = queue[0]
    facts = alert.get("facts", {})
    facts = facts if isinstance(facts, Mapping) else {}
    actions = [
        action
        for action in _records(alert, "actions")
        if _action_key(action) in ALLOWED_ACTION_KEYS
    ]
    with st.container(border=True):
        st.markdown("### Следующий кейс для проверки")
        st.caption("Выбран автоматически из накопленной очереди сигналов; решение остаётся за аналитиком.")
        st.markdown(
            f"**{alert.get('replay_date', '—')} · {str(alert.get('severity', 'review')).upper()} "
            f"· GID {alert.get('gid', '—')}**"
        )
        columns = st.columns(3)
        columns[0].metric("Приоритет", _ratio(alert.get("priority_score")))
        columns[1].metric(
            "Входящий объём",
            format_kzt(_fact(facts, alert, "daily_incoming_kzt", "incoming_kzt")),
        )
        columns[2].metric(
            "Плательщиков за день",
            _fact(facts, alert, "daily_unique_payers", "unique_payers"),
        )
        if alert.get("explanation"):
            st.write(str(alert["explanation"]))
        if actions:
            st.markdown("**Уже подготовлены три следующих шага:**")
            st.write(" · ".join(str(action.get("title", _action_key(action))) for action in actions))
        st.caption("Откройте вкладки 2–4 для доказательств, выбора шага и явного подтверждения.")


def _render_monitoring(
    client: APIClient, monitoring: Mapping[str, Any]
) -> Mapping[str, Any]:
    st.subheader("Проактивный мониторинг")
    _monitoring_status(monitoring)
    st.caption("Автоматический replay обрабатывает даты из файла по очереди.")
    st.markdown(
        "**Правила replay:** уникальных плательщиков ≥ 8; или входящий объём "
        "> 1 000 000 ₸; или pass-through 0,90–1,10 с наблюдаемым forwarding 0–2 дня."
    )
    scan = _session_mapping("agentic_scan")
    with st.expander("Ручной replay (диагностика)", expanded=False):
        st.caption("Не требуется для автоматической очереди; используйте только для повторной проверки дня.")
        date_column, interval_column, limit_column = st.columns([1.3, 1, 1])
        replay_date = date_column.date_input(
            "Дата replay",
            value=date(2026, 7, 31),
            min_value=date(2026, 7, 1),
            max_value=date(2026, 7, 31),
            key="agentic_replay_date",
        )
        interval = int(
            interval_column.number_input(
                "Интервал, мин.", min_value=1, max_value=60, value=5, step=1
            )
        )
        limit = int(
            limit_column.number_input("Лимит alerts", min_value=1, max_value=50, value=20, step=1)
        )
        if st.button("Запустить дневной replay"):
            result = _api_call(
                client.run_agentic_scan,
                replay_date.isoformat(),
                interval_minutes=interval,
                limit=limit,
            )
            if isinstance(result, Mapping):
                scan = dict(result)
                st.session_state["agentic_scan"] = scan
                st.session_state["agentic_scan_source"] = "manual"
                st.session_state.pop("agentic_proposals", None)
                st.session_state.pop("agentic_decision_result", None)
                st.session_state.pop("agentic_pinned_alert", None)
                st.session_state.pop("agentic_selected_alert_id", None)
                st.success(
                    f"Скан {_identifier(scan, 'scan_id') or '—'} завершён: "
                    f"{len(_alerts(scan))} alert(s)."
                )

    if monitoring.get("enabled") is True:
        if st.session_state.get("agentic_scan_source") == "manual" and st.button(
            "Вернуться к автоматической ленте"
        ):
            st.session_state["agentic_scan_source"] = "auto"
            st.session_state.pop("agentic_proposals", None)
            st.session_state.pop("agentic_decision_result", None)
            st.session_state.pop("agentic_pinned_alert", None)
            st.session_state.pop("agentic_selected_alert_id", None)
        if st.session_state.get("agentic_scan_source") != "manual":
            latest = monitoring.get("latest_scan")
            latest_scan = dict(latest) if isinstance(latest, Mapping) else {}
            feed = _merge_alert_feeds(
                _records(monitoring, "priority_queue"),
                _records(monitoring, "recent_alerts"),
            )
            pinned = _session_mapping("agentic_pinned_alert")
            pinned_id = _identifier(pinned, "alert_id") if pinned else None
            if pinned_id and all(_identifier(item, "alert_id") != pinned_id for item in feed):
                feed.append(pinned)
            if latest_scan or feed:
                scan = {**latest_scan, "alerts": feed, "_feed_mode": True}

    if not scan:
        if monitoring.get("enabled") is True:
            st.info("Автоматический мониторинг собирает первую ленту; данные появятся здесь.")
        else:
            st.info("Запустите replay, чтобы получить объяснимую ленту alerts.")
        return {}

    _scan_summary(scan)
    feed = _alerts(scan)
    if feed:
        st.markdown("#### Лента обнаружений")
        if scan.get("_feed_mode"):
            st.caption("Лента объединяет alerts разных дней; дата каждого alert указана отдельно.")
        st.dataframe(
            [_alert_row(alert) for alert in feed],
            width="stretch",
            hide_index=True,
            height=min(420, 40 + len(feed) * 35),
        )
    else:
        st.success("В выбранном дне пороговые признаки не сработали.")
    return scan


def _monitoring_status(monitoring: Mapping[str, Any]) -> None:
    if monitoring.get("enabled") is not True:
        st.caption("Автоматический мониторинг выключен в этой конфигурации.")
        return
    processed = int(monitoring.get("processed_days") or 0)
    total = int(monitoring.get("total_days") or 0)
    cadence = monitoring.get("cadence_seconds", "—")
    transactions = monitoring.get("transactions_in_source", "—")
    state = str(monitoring.get("state", "running"))
    st.success(
        f"Автономный мониторинг: {processed} из {total} календарных дней; "
        f"demo-шаг каждые {cadence} сек. Источник: {transactions} транзакций. "
        f"Состояние: {state}."
    )
    if monitoring.get("queue_size") is not None:
        st.caption(f"Ожидают решения аналитика: {int(monitoring['queue_size'])} кейсов.")
    if monitoring.get("last_error"):
        st.error(f"Ошибка мониторинга: {monitoring['last_error']}")


def _scan_summary(scan: Mapping[str, Any]) -> None:
    columns = st.columns(4)
    is_feed = scan.get("_feed_mode") is True
    columns[0].metric("Последний Scan ID" if is_feed else "Scan ID", _identifier(scan, "scan_id") or "—")
    columns[1].metric(
        "Последний день" if is_feed else "Дата",
        scan.get("replay_date", scan.get("as_of_date", "—")),
    )
    columns[2].metric("Alerts в ленте" if is_feed else "Alerts", len(_alerts(scan)))
    columns[3].metric("Simulation", "yes" if scan.get("simulation", True) else "no")


def _render_alert(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    st.subheader("Alert + Explain")
    if not alerts:
        st.info("Сначала запустите replay и выберите сработавший alert.")
        return {}

    by_id = {
        alert_id: alert
        for alert in alerts
        if (alert_id := _identifier(alert, "alert_id")) is not None
    }
    if not by_id:
        st.warning("API не вернул alert_id; действия недоступны.")
        return alerts[0]
    selected_id = st.selectbox(
        "Выбрать alert",
        list(by_id),
        format_func=lambda value: _alert_label(by_id[value]),
        key="agentic_selected_alert_id",
    )
    alert = by_id[str(selected_id)]
    st.session_state["agentic_pinned_alert"] = dict(alert)
    if alert.get("replay_date"):
        st.caption(f"Дата alert: {alert['replay_date']}")
    severity = str(alert.get("severity", "review")).upper()
    message = f"ALERT {selected_id} · {severity} · gid={alert.get('gid', '—')}"
    if severity == "CRITICAL":
        st.error(message)
    else:
        st.warning(message)

    facts = alert.get("facts", {})
    if not isinstance(facts, Mapping):
        facts = {}
    columns = st.columns(4)
    columns[0].metric(
        "Плательщиков за день", _fact(facts, alert, "daily_unique_payers", "unique_payers")
    )
    columns[1].metric(
        "Входящий объём",
        format_kzt(_fact(facts, alert, "daily_incoming_kzt", "incoming_kzt")),
    )
    columns[2].metric("Pass-through", _ratio(_fact(facts, alert, "pass_through")))
    columns[3].metric(
        "Forwarding 0–2 дня",
        _ratio(_fact(facts, alert, "fast_forward_0_2d_ratio")),
    )
    st.write(f"**Роль:** {alert.get('role', '—')}  ")
    st.write(f"**Кластер:** {alert.get('cluster_id', '—')}  ")
    st.write(f"**Priority:** {_ratio(alert.get('priority_score'))}")
    explanation = alert.get("explanation", alert.get("summary"))
    if explanation:
        st.markdown(f"**Объяснение:** {explanation}")
    ai_narrative = facts.get("ai_narrative")
    if isinstance(ai_narrative, str) and ai_narrative.strip():
        provider = str(facts.get("ai_provider", "configured LLM"))
        st.info(f"Дополнительная формулировка {provider}: {ai_narrative[:1200]}")
        st.caption("Проверьте формулировку ИИ по числам и исходным операциям.")
    triggers = alert.get("rule_keys", alert.get("trigger_codes", alert.get("triggers", [])))
    if isinstance(triggers, Sequence) and not isinstance(triggers, (str, bytes)):
        st.caption("Сработавшие правила: " + ", ".join(map(str, triggers)))
    limitations = alert.get("limitations", [])
    if isinstance(limitations, Sequence) and not isinstance(limitations, (str, bytes)):
        for limitation in limitations:
            st.warning(str(limitation))
    st.caption(
        "Alert означает приоритет проверки, а не вывод о нарушении. Признак <2 часов недоступен "
        "на данных с дневной гранулярностью; показан наблюдаемый сигнал 0–2 дня."
    )
    with st.expander("Что проверить аналитику"):
        st.markdown(
            "- Входящие операции за пределами наблюдаемой выборки.\n"
            "- Историю до начала июля 2026.\n"
            "- Первичные KYC/AML-данные и назначение переводов."
        )
    return alert


def _render_decision_support(client: APIClient, alert: Mapping[str, Any]) -> list[dict[str, Any]]:
    st.subheader("Decision Support")
    st.caption(
        "Recommendation score = поддержка шага наблюдаемыми признаками; это не вероятность "
        "нарушения и не автоматическое решение."
    )
    alert_id = _identifier(alert, "alert_id")
    proposals = _session_mapping("agentic_proposals")
    if not alert_id:
        st.info("Выберите alert во второй вкладке.")
        return []
    if proposals.get("alert_id") != alert_id:
        proposals = {}
    provided_actions = _records(alert, "actions")
    if not provided_actions and st.button("Сформировать 3 предложения"):
        result = _api_call(client.propose_agentic_actions, alert_id)
        if isinstance(result, Mapping):
            proposals = dict(result)
            st.session_state["agentic_proposals"] = proposals
            st.session_state.pop("agentic_decision_result", None)

    actions = [
        action
        for action in (provided_actions or _records(proposals, "actions", "proposals"))
        if _action_key(action) in ALLOWED_ACTION_KEYS
    ]
    if not actions:
        st.info("Предложения готовятся автоматически или доступны по кнопке выше.")
        return []
    if len(actions) != 3:
        st.warning(f"API вернул {len(actions)} безопасных варианта(ов) вместо 3.")
    columns = st.columns(3)
    for index, (column, action) in enumerate(zip(columns, actions, strict=False), start=1):
        with column, st.container(border=True):
            st.markdown(f"**Вариант {chr(64 + index)}**")
            st.markdown(f"#### {action.get('title', _action_key(action))}")
            score = _score(action.get("recommendation_score", action.get("score")))
            st.metric("Поддержка признаками", f"{score:.0%}")
            st.write(str(action.get("rationale", "Обоснование не передано.")))
            st.caption(str(action.get("expected_outcome", "Ожидаемый результат не передан.")))
            st.code(_action_key(action), language=None)
    return actions


def _render_execution_and_audit(
    client: APIClient,
    alert: Mapping[str, Any],
    actions: list[dict[str, Any]],
) -> None:
    st.subheader("Human approval → safe local execution")
    analyst = os.getenv("ANALYST_NAME", "demo-analyst")
    st.warning(
        f"Актор: {analyst} (demo-only identity, без production IAM/RBAC). "
        "Финальное решение и ответственность остаются у аналитика."
    )
    selected_action_id: str | None = None
    if actions:
        action_ids = {
            action_id: action
            for action in actions
            if (action_id := _identifier(action, "action_id")) is not None
        }
        if action_ids:
            selected_action_id = st.radio(
                "Выбранное действие",
                list(action_ids),
                format_func=lambda value: str(
                    action_ids[value].get("title", _action_key(action_ids[value]))
                ),
                key="agentic_selected_action_id",
            )
            selected_action = action_ids[str(selected_action_id)]
            decision = _session_mapping("agentic_decision_result")
            decision_action_id = _identifier(decision, "action_id")
            decision_status = str(decision.get("status", ""))
            persisted_status = str(selected_action.get("status", "proposed"))
            effective_status = (
                decision_status
                if decision_action_id == str(selected_action_id)
                and decision_status in {"executed", "rejected", "failed"}
                else persisted_status
            )
            if effective_status in {"executed", "rejected", "failed"}:
                st.info(
                    f"Решение по этому действию уже записано: {effective_status}. "
                    "Повторный запуск из UI отключён."
                )
            else:
                _decision_controls(client, str(selected_action_id), selected_action)
        else:
            st.warning("API не вернул action_id; решение не может быть записано.")
    else:
        st.info("Сначала сформируйте предложения в третьей вкладке.")
    _execution_result(selected_action_id)
    _audit_timeline(client, alert)


def _decision_controls(client: APIClient, action_id: str, action: Mapping[str, Any]) -> None:
    st.caption(
        "Доступны только локальный draft, bounded-маршрут или local watchlist. "
        "Внешняя отправка и блокировка не входят в allowlist."
    )
    acknowledged = st.checkbox(
        "Я проверил(а) факты и поручаю выполнить выбранное локальное действие",
        key=f"agentic_ack_{action_id}",
    )
    reject_column, approve_column = st.columns(2)
    if reject_column.button("Reject предложение", width="stretch"):
        _submit_decision(client, action_id, "reject", None)
    if approve_column.button(
        "Approve и исполнить",
        type="primary",
        width="stretch",
        disabled=not acknowledged,
    ):
        _submit_decision(client, action_id, "approve", "APPROVE")
    if str(action.get("status", "proposed")) != "proposed":
        st.caption(f"Текущий статус API: {action.get('status')}")


def _submit_decision(
    client: APIClient, action_id: str, decision: str, confirmation: str | None
) -> None:
    result = _api_call(
        client.decide_agentic_action,
        action_id,
        decision=decision,
        confirmation=confirmation,
        idempotency_key=_idempotency_key(action_id, decision),
    )
    if isinstance(result, Mapping):
        st.session_state["agentic_decision_result"] = dict(result)
        if decision == "approve":
            st.success("Решение approve записано; разрешённый локальный tool исполнен.")
        else:
            st.info("Предложение отклонено; tool не запускался.")
        st.rerun()


def _execution_result(selected_action_id: str | None) -> None:
    result = _session_mapping("agentic_decision_result")
    if not result or _identifier(result, "action_id") != selected_action_id:
        return
    st.markdown("#### Квитанция решения")
    status = str(result.get("status", result.get("decision", "recorded")))
    if status == "rejected":
        st.info("Статус: rejected. Побочные эффекты отсутствуют.")
        return
    execution = result.get("execution", result.get("result"))
    if isinstance(execution, Mapping):
        receipt_id = (
            _identifier(execution, "execution_id")
            or _identifier(execution, "investigation_id")
            or _identifier(result, "investigation_id")
            or _identifier(result, "action_id")
            or "—"
        )
        st.success(f"Execution receipt: {receipt_id}")
        if execution.get("kind") == "observed_money_route":
            st.caption("Наблюдаемый локальный маршрут; максимум 80 узлов и 120 связей на схеме.")
            st.plotly_chart(
                build_ego_figure(_route_figure_payload(execution)),
                use_container_width=True,
            )
        elif execution.get("kind") == "aml_review_draft":
            st.subheader("Черновик для внутренней AML-проверки")
            st.write(f"GID: {execution.get('subject_gid', '—')}")
            facts = execution.get("observed_facts")
            if isinstance(facts, Mapping):
                st.json(dict(facts), expanded=False)
            checks = execution.get("required_checks")
            if isinstance(checks, list):
                for check in checks[:10]:
                    st.write(f"• {check}")
            st.caption("Черновик не отправлен во внешнюю систему.")
        elif execution.get("kind") == "local_watchlist":
            gids = execution.get("gids")
            if isinstance(gids, list):
                st.write(f"Локальный список наблюдения: {len(gids)} узлов.")
                st.code(", ".join(str(gid) for gid in gids[:20]))
        st.json(dict(execution), expanded=False)
    else:
        st.json(dict(result), expanded=False)


def _audit_timeline(client: APIClient, alert: Mapping[str, Any]) -> None:
    st.markdown("#### Append-only audit timeline")
    audit = _api_call(
        client.agentic_audit,
        limit=100,
        offset=0,
    )
    events = _records(audit, "events", "audit_events")
    if not events:
        st.caption("Журнал появится после первого шага workflow.")
        return
    for event in events:
        occurred = event.get("occurred_at", event.get("created_at", "—"))
        event_type = event.get("event_type", event.get("action", "event"))
        actor = event.get("actor", "system")
        entity = event.get("entity_id", event.get("entity", "—"))
        st.markdown(f"`{occurred}` · **{event_type}** · {actor} · `{entity}`")
    st.caption(
        "Demo использует SQLite audit. Для production нужны IAM/RBAC, maker-checker и "
        "tamper-evident/WORM-хранилище."
    )


def _api_call(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any | None:
    try:
        return operation(*args, **kwargs)
    except APIClientError as exc:
        st.error(str(exc))
        return None


def _session_mapping(key: str) -> dict[str, Any]:
    value = st.session_state.get(key, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _records(payload: Any, *keys: str) -> list[dict[str, Any]]:
    value = payload
    if isinstance(value, Mapping):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                value = candidate
                break
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _merge_alert_feeds(
    priority: list[dict[str, Any]], recent: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for alert in [*priority, *recent]:
        alert_id = _identifier(alert, "alert_id")
        if alert_id is None or alert_id in seen:
            continue
        seen.add(alert_id)
        merged.append(alert)
    return merged


def _alerts(scan: Mapping[str, Any]) -> list[dict[str, Any]]:
    direct = _records(scan, "alerts", "items")
    if direct:
        replay_date = scan.get("replay_date")
        return [
            {**alert, "replay_date": alert.get("replay_date", replay_date)}
            for alert in direct
        ]
    nested = scan.get("scan")
    return _records(nested, "alerts", "items")


def _identifier(item: Mapping[str, Any], preferred: str) -> str | None:
    value = item.get(preferred, item.get("id"))
    return str(value) if value is not None and str(value).strip() else None


def _action_key(action: Mapping[str, Any]) -> str:
    return str(action.get("action_key", action.get("type", action.get("key", ""))))


def _alert_label(alert: Mapping[str, Any]) -> str:
    return (
        f"{alert.get('replay_date', '—')} · {_identifier(alert, 'alert_id') or '—'} · "
        f"{alert.get('severity', 'review')} · "
        f"gid={alert.get('gid', '—')}"
    )


def _alert_row(alert: Mapping[str, Any]) -> dict[str, Any]:
    facts = alert.get("facts", {})
    if not isinstance(facts, Mapping):
        facts = {}
    return {
        "date": alert.get("replay_date"),
        "alert_id": _identifier(alert, "alert_id"),
        "severity": alert.get("severity"),
        "gid": str(alert.get("gid", "")),
        "role": alert.get("role"),
        "cluster": alert.get("cluster_id"),
        "payers": _fact(facts, alert, "daily_unique_payers", "unique_payers"),
        "incoming_kzt": _fact(facts, alert, "daily_incoming_kzt", "incoming_kzt"),
    }


def _fact(facts: Mapping[str, Any], alert: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if facts.get(name) is not None:
            return facts[name]
        if alert.get(name) is not None:
            return alert[name]
    return "—"


def _ratio(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, score))


def _idempotency_key(action_id: str, decision: str) -> str:
    state_key = f"agentic_idempotency_{action_id}_{decision}"
    value = st.session_state.get(state_key)
    if value is None:
        value = f"ui-{uuid4().hex}"
        st.session_state[state_key] = value
    return str(value)


def _route_figure_payload(execution: Mapping[str, Any]) -> dict[str, Any]:
    raw_nodes = execution.get("nodes")
    raw_edges = execution.get("edges")
    nodes = [
        {"gid": str(value)}
        for value in raw_nodes[:80]
        if str(value)
    ] if isinstance(raw_nodes, list) else []
    allowed = {item["gid"] for item in nodes}
    edges = [
        dict(edge)
        for edge in raw_edges
        if isinstance(edge, Mapping)
        and str(edge.get("src", "")) in allowed
        and str(edge.get("dst", "")) in allowed
    ][:120] if isinstance(raw_edges, list) else []
    return {
        "root_gid": str(execution.get("target_gid", "")),
        "nodes": nodes,
        "edges": edges,
    }
