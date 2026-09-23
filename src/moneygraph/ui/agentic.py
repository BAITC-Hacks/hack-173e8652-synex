"""Human-in-the-loop Streamlit workflow backed only by the Agentic API."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any
from uuid import uuid4

import streamlit as st

from moneygraph.ui.client import APIClient, APIClientError
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


def render_agentic_loop(client: APIClient) -> None:
    """Render the complete four-stage demo without bypassing the HTTP API."""

    _hero()
    st.info(
        "Режим демо: пошаговый replay по календарным дням июля 2026. "
        "Исходник не содержит внутридневного времени."
    )
    st.caption(
        "AI ранжирует и готовит локальные артефакты, но не блокирует счета, "
        "не останавливает переводы и не отправляет сообщения в АФМ. Решение — за аналитиком."
    )

    monitoring_tab, alert_tab, support_tab, execution_tab = st.tabs(TAB_LABELS)
    with monitoring_tab:
        scan = _render_monitoring(client)
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


def _render_monitoring(client: APIClient) -> Mapping[str, Any]:
    st.subheader("Проактивный мониторинг")
    st.caption(
        "Интервал задаёт частоту проверки в demo-цикле; каждый шаг всё равно анализирует "
        "один календарный день, а не live-транзакции."
    )
    st.markdown(
        "**Правила replay:** уникальных плательщиков ≥ 8; или входящий объём "
        "> 1 000 000 ₸; или pass-through 0,90–1,10 с наблюдаемым forwarding 0–2 дня."
    )
    date_column, interval_column, limit_column = st.columns([1.3, 1, 1])
    replay_date = date_column.date_input(
        "Дата replay",
        value=date(2026, 7, 31),
        min_value=date(2026, 7, 1),
        max_value=date(2026, 7, 31),
        key="agentic_replay_date",
    )
    interval = int(
        interval_column.number_input("Интервал, мин.", min_value=1, max_value=60, value=5, step=1)
    )
    limit = int(
        limit_column.number_input("Лимит alerts", min_value=1, max_value=50, value=20, step=1)
    )

    scan = _session_mapping("agentic_scan")
    if st.button("Запустить дневной replay", type="primary"):
        result = _api_call(
            client.run_agentic_scan,
            replay_date.isoformat(),
            interval_minutes=interval,
            limit=limit,
        )
        if isinstance(result, Mapping):
            scan = dict(result)
            st.session_state["agentic_scan"] = scan
            st.session_state.pop("agentic_proposals", None)
            st.session_state.pop("agentic_decision_result", None)
            st.success(
                f"Скан {_identifier(scan, 'scan_id') or '—'} завершён: "
                f"{len(_alerts(scan))} alert(s)."
            )

    if not scan:
        st.info("Запустите replay, чтобы получить объяснимую ленту alerts.")
        return {}

    _scan_summary(scan)
    feed = _alerts(scan)
    if feed:
        st.markdown("#### Лента обнаружений")
        st.dataframe(
            [_alert_row(alert) for alert in feed],
            width="stretch",
            hide_index=True,
            height=min(420, 40 + len(feed) * 35),
        )
    else:
        st.success("В выбранном дне пороговые признаки не сработали.")
    return scan


def _scan_summary(scan: Mapping[str, Any]) -> None:
    columns = st.columns(4)
    columns[0].metric("Scan ID", _identifier(scan, "scan_id") or "—")
    columns[1].metric("Дата", scan.get("replay_date", scan.get("as_of_date", "—")))
    columns[2].metric("Alerts", len(_alerts(scan)))
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
    if st.button("Сформировать 3 предложения"):
        result = _api_call(client.propose_agentic_actions, alert_id)
        if isinstance(result, Mapping):
            proposals = dict(result)
            st.session_state["agentic_proposals"] = proposals
            st.session_state.pop("agentic_decision_result", None)

    actions = [
        action
        for action in _records(proposals, "actions", "proposals")
        if _action_key(action) in ALLOWED_ACTION_KEYS
    ]
    if not actions:
        st.info("Запросите три allowlisted-варианта у API.")
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
            if decision_action_id == str(selected_action_id) and decision_status in {
                "executed",
                "rejected",
                "failed",
            }:
                st.info(
                    f"Решение по этому действию уже записано: {decision_status}. "
                    "Повторный запуск из UI отключён."
                )
            else:
                _decision_controls(client, str(selected_action_id), selected_action)
        else:
            st.warning("API не вернул action_id; решение не может быть записано.")
    else:
        st.info("Сначала сформируйте предложения в третьей вкладке.")
    _execution_result()
    _audit_timeline(client, alert)


def _decision_controls(client: APIClient, action_id: str, action: Mapping[str, Any]) -> None:
    st.caption(
        "Доступны только локальный draft, bounded-маршрут или local watchlist. "
        "Внешняя отправка и блокировка не входят в allowlist."
    )
    acknowledged = st.checkbox(
        "Я, аналитик, проверил(а) факты и принимаю решение",
        key=f"agentic_ack_{action_id}",
    )
    confirmation = st.text_input(
        "Контрольная фраза",
        help="Для approve введите точно APPROVE.",
        max_chars=20,
        key=f"agentic_confirmation_{action_id}",
    )
    reject_column, approve_column = st.columns(2)
    if reject_column.button("Reject предложение", width="stretch"):
        _submit_decision(client, action_id, "reject", None)
    if approve_column.button(
        "Approve и исполнить",
        type="primary",
        width="stretch",
        disabled=not acknowledged or confirmation != "APPROVE",
    ):
        _submit_decision(client, action_id, "approve", confirmation)
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


def _execution_result() -> None:
    result = _session_mapping("agentic_decision_result")
    if not result:
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


def _alerts(scan: Mapping[str, Any]) -> list[dict[str, Any]]:
    direct = _records(scan, "alerts", "items")
    if direct:
        return direct
    nested = scan.get("scan")
    return _records(nested, "alerts", "items")


def _identifier(item: Mapping[str, Any], preferred: str) -> str | None:
    value = item.get(preferred, item.get("id"))
    return str(value) if value is not None and str(value).strip() else None


def _action_key(action: Mapping[str, Any]) -> str:
    return str(action.get("action_key", action.get("type", action.get("key", ""))))


def _alert_label(alert: Mapping[str, Any]) -> str:
    return (
        f"{_identifier(alert, 'alert_id') or '—'} · {alert.get('severity', 'review')} · "
        f"gid={alert.get('gid', '—')}"
    )


def _alert_row(alert: Mapping[str, Any]) -> dict[str, Any]:
    facts = alert.get("facts", {})
    if not isinstance(facts, Mapping):
        facts = {}
    return {
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
