"""Functional Streamlit workspaces backed exclusively by the HTTP API."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pandas as pd
import streamlit as st

from moneygraph.ui.agentic import render_agentic_loop
from moneygraph.ui.client import APIClient, APIClientError
from moneygraph.ui.graph import build_ego_figure
from moneygraph.ui.helpers import (
    as_records,
    first_value,
    format_kzt,
    format_period,
    parse_gid_list,
)


def render_page(
    page: str,
    client: APIClient,
    *,
    ai_enabled: bool,
    ai_provider: str,
    ai_notice: str,
) -> None:
    renderers: dict[str, Callable[[], None]] = {
        "Agentic Loop": lambda: render_agentic_loop(client),
        "Dashboard": lambda: render_dashboard(client),
        "Network Explorer": lambda: render_network_explorer(client),
        "Top Nodes": lambda: render_top_nodes(client),
        "Clusters": lambda: render_clusters(client),
        "Flow Investigation": lambda: render_flow_investigation(client),
        "Investigations": lambda: render_investigations(client),
        "AI Copilot": lambda: render_ai_copilot(
            client,
            enabled=ai_enabled,
            provider=ai_provider,
            notice=ai_notice,
        ),
    }
    renderers.get(page, renderers["Agentic Loop"])()


def _hero(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="mg-hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def _call(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any | None:
    try:
        return operation(*args, **kwargs)
    except APIClientError as exc:
        st.error(str(exc))
        return None


def render_dashboard(client: APIClient) -> None:
    _hero("Dashboard", "Кого смотреть первым — и какие наблюдаемые признаки это объясняют")
    summary = _call(client.summary)
    if summary is None:
        _api_start_hint()
        return
    counts = summary.get("counts", summary) if isinstance(summary, Mapping) else {}
    columns = st.columns(6)
    metrics = (
        ("Узлы", first_value(counts, "nodes", "nodes_count", default="—")),
        ("Рёбра", first_value(counts, "edges", "edges_count", default="—")),
        ("Транзакции", first_value(counts, "transactions", "transactions_count", default="—")),
        ("Seed", first_value(counts, "seed", "seeds", "seed_count", default="—")),
        (
            "Компоненты",
            first_value(summary, "weakly_connected_components", default="—"),
        ),
        ("Кластеры", first_value(summary, "clusters", "n_clusters", default="—")),
    )
    for column, (label, value) in zip(columns, metrics, strict=True):
        column.metric(label, value)
    left, right = st.columns([1.05, 1.4])
    with left:
        _dashboard_run_details(summary)
        _dashboard_roles(summary)
    with right:
        st.subheader("Приоритет для проверки")
        top_payload = _call(client.top_nodes, limit=10)
        top_nodes = as_records(top_payload, "top_nodes") if top_payload is not None else []
        _dataframe(top_nodes, height=390)
    warnings = summary.get("warnings", summary.get("validation_warnings", []))
    if isinstance(warnings, list) and warnings:
        with st.expander(f"Предупреждения качества данных ({len(warnings)})"):
            for warning in warnings:
                st.warning(str(warning))
    _dashboard_resilience(client)


def _dashboard_run_details(summary: Mapping[str, Any]) -> None:
    st.subheader("Последний воспроизводимый запуск")
    run = summary.get("last_run", summary)
    if not isinstance(run, Mapping):
        run = summary
    period = first_value(run, "period", default=summary.get("period"))
    st.write(f"**Период:** {format_period(period)}")
    duration = first_value(run, "duration_seconds", "pipeline_duration_seconds")
    st.write(
        f"**Длительность:** {float(duration):.2f} сек."
        if duration is not None
        else "**Длительность:** —"
    )
    status = first_value(run, "status", default="completed")
    st.write(f"**Статус:** `{status}`")


def _dashboard_roles(summary: Mapping[str, Any]) -> None:
    distribution = summary.get("role_distribution", summary.get("roles", {}))
    st.subheader("Роли узлов")
    if isinstance(distribution, Mapping) and distribution:
        frame = pd.DataFrame(
            {"role": list(map(str, distribution.keys())), "count": list(distribution.values())}
        ).set_index("role")
        st.bar_chart(frame, color="#197367")
    elif isinstance(distribution, list) and distribution:
        st.bar_chart(pd.DataFrame(distribution).set_index("role"), color="#197367")
    else:
        st.caption("Распределение появится после завершения pipeline.")


def _dashboard_resilience(client: APIClient) -> None:
    st.subheader("Устойчивость сети")
    payload = _call(client.resilience)
    records = as_records(payload, "resilience") if payload is not None else []
    if not records:
        st.caption("Сценарии устойчивости появятся после завершения pipeline.")
        return
    frame = pd.DataFrame(records)
    required = {"scenario", "n_removed", "largest_component_size"}
    if required.issubset(frame.columns):
        chart = frame.pivot_table(
            index="n_removed",
            columns="scenario",
            values="largest_component_size",
            aggfunc="first",
        ).sort_index()
        st.line_chart(chart)
    _dataframe(records, height=250)
    st.caption(
        "Offline-сценарий фрагментации наблюдаемого snapshot; это не прогноз поведения сети."
    )


def render_network_explorer(client: APIClient) -> None:
    _hero("Network Explorer", "Направленный ego-граф без загрузки всей сети в браузер")
    search, depth_column = st.columns([4, 1])
    default_gid = str(st.session_state.get("selected_gid", ""))
    gid = search.text_input("GID", value=default_gid, placeholder="Введите точный gid")
    depth = depth_column.selectbox("Колена", [1, 2], index=0)
    if not gid.strip():
        st.info("Введите gid: значения считаются непрозрачными строками и не округляются.")
        return
    profile = _call(client.node, gid.strip())
    ego = _call(client.ego, gid.strip(), depth=depth)
    if profile is None or ego is None:
        return
    st.session_state["selected_gid"] = gid.strip()
    filtered = _graph_filters(ego)
    graph_column, profile_column = st.columns([2.25, 1])
    with graph_column:
        st.plotly_chart(build_ego_figure(filtered), use_container_width=True)
    with profile_column:
        _node_card(profile)
        _network_case_action(client, gid.strip())
    _network_actions(client, gid.strip())


def _graph_filters(ego: Mapping[str, Any]) -> dict[str, Any]:
    nodes = as_records(ego.get("nodes", []))
    edges = as_records(ego.get("edges", []))
    roles = sorted({str(node.get("role", "unknown")) for node in nodes})
    first, second, third = st.columns(3)
    selected_roles = first.multiselect("Роли", roles, default=roles)
    cluster_text = second.text_input("Cluster ID", placeholder="Все")
    minimum_priority = third.slider("Мин. priority", 0.0, 1.0, 0.0, 0.05)
    allowed: set[str] = set()
    for node in nodes:
        role_matches = str(node.get("role", "unknown")) in selected_roles
        cluster_matches = not cluster_text or str(node.get("cluster_id")) == cluster_text.strip()
        priority_matches = float(node.get("priority_score") or 0) >= minimum_priority
        if role_matches and cluster_matches and priority_matches:
            allowed.add(str(node.get("gid")))
    return {
        "root_gid": str(ego.get("root_gid", "")),
        "nodes": [node for node in nodes if str(node.get("gid")) in allowed],
        "edges": [
            edge
            for edge in edges
            if str(edge.get("src")) in allowed and str(edge.get("dst")) in allowed
        ],
    }


def _node_card(profile: Mapping[str, Any]) -> None:
    st.markdown('<div class="mg-label">Выбранный узел</div>', unsafe_allow_html=True)
    st.subheader(f"gid {profile.get('gid', '—')}")
    st.metric("Priority", f"{float(profile.get('priority_score') or 0):.3f}")
    st.write(f"**Роль:** {profile.get('role', '—')}")
    st.write(f"**Кластер:** {profile.get('cluster_id', '—')}")
    st.write(f"**Входящий поток:** {format_kzt(profile.get('in_kzt'))}")
    st.write(f"**Исходящий поток:** {format_kzt(profile.get('out_kzt'))}")
    evidence = profile.get("evidence") or profile.get("why")
    if evidence:
        st.caption(str(evidence))


def _network_case_action(client: APIClient, gid: str) -> None:
    cases_payload = _call(client.investigations, limit=100)
    cases = as_records(cases_payload, "investigations") if cases_payload is not None else []
    case_ids = [case_id for case in cases if (case_id := _case_id(case)) is not None]
    if not case_ids:
        st.caption("Создайте кейс в разделе Investigations, чтобы добавить узел.")
        return
    selected = st.selectbox("Кейс", case_ids, key="network_case_id")
    if (
        st.button("Добавить в кейс", use_container_width=True)
        and _call(client.add_nodes, selected, [gid]) is not None
    ):
        st.success(f"gid={gid} добавлен в кейс {selected}")


def _network_actions(client: APIClient, gid: str) -> None:
    st.subheader("Быстрые действия")
    upstream, downstream, common, ask = st.columns(4)
    if upstream.button("↑ Upstream", use_container_width=True):
        _show_payload("Входящие пути", _call(client.trace, gid, "upstream", depth=4))
    if downstream.button("↓ Downstream", use_container_width=True):
        _show_payload("Исходящие пути", _call(client.trace, gid, "downstream", depth=4))
    if common.button("Общие получатели", use_container_width=True):
        st.info("Для общей точки укажите минимум два gid в Flow Investigation.")
    if ask.button("Спросить AI", use_container_width=True):
        result = _call(client.ask_assistant, "Почему этот узел в топе?", gids=[gid])
        if isinstance(result, Mapping):
            st.info(str(result.get("answer", "Нет ответа")))


def render_top_nodes(client: APIClient) -> None:
    _hero("Top Nodes", "Объяснимый список приоритета, а не автоматическое обвинение")
    role_column, cluster_column = st.columns(2)
    role = role_column.selectbox(
        "Роль",
        [
            "Все",
            "consolidator",
            "transit",
            "distributor",
            "terminal",
            "coordinator",
            "peripheral",
        ],
    )
    cluster_text = cluster_column.text_input("Cluster ID", placeholder="Все кластеры")
    cluster_id = int(cluster_text) if cluster_text.strip().isdigit() else None
    payload = _call(
        client.top_nodes,
        limit=50,
        role=None if role == "Все" else role,
        cluster_id=cluster_id,
    )
    records = as_records(payload, "top_nodes") if payload is not None else []
    _dataframe(records, height=620)
    selected = st.text_input("Открыть gid в Network Explorer", key="top_selected_gid")
    if st.button("Запомнить выбранный gid") and selected.strip():
        st.session_state["selected_gid"] = selected.strip()
        st.success("GID сохранён. Перейдите в Network Explorer.")


def render_clusters(client: APIClient) -> None:
    _hero("Clusters", "Сообщества, внутренний оборот и проверяемые структурные гипотезы")
    payload = _call(client.clusters, limit=100)
    clusters = as_records(payload, "clusters") if payload is not None else []
    if not clusters:
        st.info("Кластеры пока не доступны.")
        return
    _dataframe(clusters, height=320)
    ids = [item["cluster_id"] for item in clusters if item.get("cluster_id") is not None]
    if not ids:
        return
    selected = st.selectbox("Исследовать кластер", ids)
    details = _call(client.cluster, selected)
    if not isinstance(details, Mapping):
        return
    first, second, third = st.columns(3)
    first.metric("Размер", details.get("size", "—"))
    second.metric("Seed", details.get("seed_count", "—"))
    third.metric("Внутренний оборот", format_kzt(details.get("internal_turnover_kzt")))
    if details.get("hypothesis"):
        st.info(f"Гипотеза для проверки: {details['hypothesis']}")
    if details.get("nodes") or details.get("edges"):
        limited = {
            "nodes": as_records(details.get("nodes", []))[:120],
            "edges": as_records(details.get("edges", []))[:300],
        }
        st.plotly_chart(build_ego_figure(limited), use_container_width=True)


def render_flow_investigation(client: APIClient) -> None:
    _hero("Flow Investigation", "Трассировка путей и общие точки сбора до четырёх колен")
    raw = st.text_area("GID (до 20, через запятую или новую строку)", height=100)
    gids = parse_gid_list(raw, max_items=20)
    depth = st.slider("Максимальная глубина", 1, 4, 4)
    if not gids:
        st.info("Введите один или несколько gid для сравнения и поиска общих получателей.")
        return
    profiles = [_call(client.node, gid) for gid in gids]
    _dataframe([profile for profile in profiles if isinstance(profile, Mapping)], height=260)
    first, second, third = st.columns(3)
    if first.button("Common receivers", use_container_width=True):
        if len(gids) < 2:
            st.info("Для общей точки нужны минимум два gid.")
        else:
            _show_payload("Общие получатели", _call(client.common_receivers, gids, max_depth=depth))
    if second.button("Upstream", use_container_width=True):
        for gid in gids[:5]:
            _show_payload(f"Upstream: {gid}", _call(client.trace, gid, "upstream", depth=depth))
    if third.button("Downstream", use_container_width=True):
        for gid in gids[:5]:
            _show_payload(f"Downstream: {gid}", _call(client.trace, gid, "downstream", depth=depth))


def render_investigations(client: APIClient) -> None:
    _hero("Investigations", "Кейсы, узлы, заметки и аудит действий аналитика")
    with st.expander("Создать investigation", expanded=False):
        with st.form("create_investigation"):
            title = st.text_input("Название")
            description = st.text_area("Описание")
            gids = parse_gid_list(st.text_input("Начальные gid (необязательно)"))
            submitted = st.form_submit_button("Создать")
        if submitted and title.strip():
            created = _call(
                client.create_investigation,
                title.strip(),
                description=description.strip(),
                gids=gids,
            )
            if created is not None:
                st.success("Кейс создан")
                st.rerun()
    payload = _call(client.investigations, limit=100)
    cases = as_records(payload, "investigations") if payload is not None else []
    if not cases:
        st.info("Кейсов пока нет.")
        return
    _dataframe(cases, height=260)
    options = {
        case_id: str(case.get("title", "Без названия"))
        for case in cases
        if (case_id := _case_id(case)) is not None
    }
    selected = st.selectbox(
        "Открыть кейс", list(options), format_func=lambda value: f"#{value} · {options[value]}"
    )
    details = _call(client.investigation, selected)
    if isinstance(details, Mapping):
        _investigation_workspace(client, selected, details)


def _investigation_workspace(
    client: APIClient, investigation_id: int | str, details: Mapping[str, Any]
) -> None:
    st.subheader(str(details.get("title", f"Investigation #{investigation_id}")))
    st.caption(str(details.get("description", "")))
    _dataframe(as_records(details.get("nodes", [])), height=220)
    add_column, note_column, status_column = st.columns(3)
    with add_column:
        gids = parse_gid_list(st.text_input("Добавить gid", key=f"case_gids_{investigation_id}"))
        if (
            st.button("Добавить узлы", key=f"add_nodes_{investigation_id}")
            and gids
            and _call(client.add_nodes, investigation_id, gids) is not None
        ):
            st.success("Узлы добавлены")
    with note_column:
        note = st.text_area("Заметка", key=f"case_note_{investigation_id}", height=80)
        if (
            st.button("Сохранить заметку", key=f"add_note_{investigation_id}")
            and note.strip()
            and _call(client.add_note, investigation_id, note.strip()) is not None
        ):
            st.success("Заметка сохранена")
    with status_column:
        statuses = ["new", "in_review", "escalated", "closed"]
        current = str(details.get("status", "new"))
        index = statuses.index(current) if current in statuses else 0
        status = st.selectbox("Статус", statuses, index=index)
        if (
            st.button("Обновить статус", key=f"case_status_{investigation_id}")
            and _call(client.update_status, investigation_id, status) is not None
        ):
            st.success("Статус обновлён")
    json_url = client.export_url(investigation_id, output_format="json")
    csv_url = client.export_url(investigation_id, output_format="csv")
    st.markdown(f"[Экспорт JSON]({json_url}) · [Экспорт CSV]({csv_url})")


def render_ai_copilot(client: APIClient, *, enabled: bool, provider: str, notice: str) -> None:
    _hero("AI Copilot", "Grounded-помощник поверх детерминированных API-инструментов")
    (st.warning if enabled else st.info)(notice)
    st.caption(
        "Copilot не назначает роли и не считает priority. В API передаются только выбранные "
        "gid и минимальный структурированный контекст без клиентских атрибутов."
    )
    examples = [
        "Почему этот узел в топе?",
        "Кто собирает деньги от этих gid?",
        "Сравни выбранные узлы",
        "Покажи ключевые мосты кластера",
        "Каких данных не хватает для проверки гипотезы?",
    ]
    example = st.selectbox("Шаблон запроса", examples)
    query = st.text_area("Вопрос", value=example, height=110, max_chars=2000)
    gids = parse_gid_list(st.text_input("GID (до 20)"), max_items=20)
    cluster_text = st.text_input("Cluster ID (необязательно)")
    cluster_id = int(cluster_text) if cluster_text.strip().isdigit() else None
    if st.button("Проанализировать", type="primary", use_container_width=True):
        result = _call(client.ask_assistant, query.strip(), gids=gids, cluster_id=cluster_id)
        if isinstance(result, Mapping):
            st.subheader("Ответ")
            st.markdown(str(result.get("answer", "Нет ответа")))
            st.caption(
                f"Provider: {result.get('provider', provider)} · "
                f"Fallback: {'да' if result.get('fallback') else 'нет'}"
            )
            tools = result.get("tools_used", [])
            if isinstance(tools, list) and tools:
                st.write("**Вызванные инструменты:** " + ", ".join(map(str, tools)))
            limitations = result.get("limitations", [])
            if isinstance(limitations, list) and limitations:
                with st.expander("Ограничения", expanded=True):
                    for limitation in limitations:
                        st.warning(str(limitation))


def _show_payload(title: str, payload: Any | None) -> None:
    if payload is None:
        return
    with st.expander(title, expanded=True):
        records = as_records(payload, "paths", "candidates", "common_receivers")
        if records:
            _dataframe(records, height=280)
        else:
            st.json(payload)


def _dataframe(records: Sequence[Mapping[str, Any]], *, height: int) -> None:
    if not records:
        st.caption("Нет данных для выбранных условий.")
        return
    frame = pd.DataFrame([dict(record) for record in records])
    st.dataframe(frame, use_container_width=True, hide_index=True, height=height)


def _case_id(case: Mapping[str, Any]) -> str | None:
    value = case.get("id", case.get("investigation_id"))
    return str(value) if value is not None and str(value).strip() else None


def _api_start_hint() -> None:
    st.info(
        "Запустите API командой `uvicorn moneygraph.api.main:app --host 0.0.0.0 --port 8000`, "
        "затем обновите страницу."
    )
