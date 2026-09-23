"""Freedom MoneyGraph AML Streamlit entrypoint."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import streamlit as st

from moneygraph.ui.ai_runtime import runtime_status_view
from moneygraph.ui.client import APIClient, APIClientError
from moneygraph.ui.pages import render_page
from moneygraph.ui.theme import THEME_CSS, brand_markup

NAV_PAGES = (
    "Agentic Loop",
    "Dashboard",
    "Network Explorer",
    "Top Nodes",
    "Clusters",
    "Flow Investigation",
    "Investigations",
    "AI Copilot",
)


def ai_status_notice(*, enabled: bool, provider: str) -> str:
    if enabled:
        return (
            f"AI Copilot настроен: провайдер {provider}. Доступность проверяется при запросе; "
            "результаты требуют проверки аналитиком."
        )
    return (
        "AI Copilot отключён. Доступен детерминированный офлайн-разбор; "
        "аналитическое ядро и все обязательные результаты продолжают работать."
    )


def _api_url() -> str:
    return os.getenv(
        "MONEYGRAPH_API_URL",
        os.getenv("API_BASE_URL", "http://127.0.0.1:8000"),
    )


def _inject_styles() -> None:
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def _sidebar(client: APIClient) -> tuple[str, bool, str]:
    ai_enabled = False
    ai_provider = "deterministic"
    ai_runtime: Mapping[str, Any] | None = None
    with st.sidebar:
        st.markdown(
            brand_markup(
                product_name="MoneyGraph AML",
                descriptor="Graph intelligence",
            ),
            unsafe_allow_html=True,
        )
        page = st.radio("Рабочая область", NAV_PAGES, label_visibility="collapsed")
        st.divider()
        try:
            health = client.health()
            runtime = health.get("ai_runtime")
            if isinstance(runtime, Mapping):
                ai_runtime = runtime
            status = str(health.get("status", "ok"))
            st.success(f"API: {status}", icon="✅")
            if health.get("agentic_narrative_enabled") is True:
                provider = str(health.get("agentic_narrative_provider", ""))
                if provider in {"openai", "nvidia_nim"}:
                    ai_enabled = True
                    ai_provider = provider
        except APIClientError:
            st.error("API недоступен", icon="❌")
        if ai_enabled:
            if ai_runtime is not None:
                view = runtime_status_view(ai_runtime)
                getattr(st, view["level"])(f"AI: {view['message']}")
            else:
                st.info(f"AI: {ai_provider} настроен; успешный вызов ещё не подтверждён.", icon="✨")
        else:
            st.caption("AI: offline fallback")
        st.caption(f"API URL: `{client.base_url}`")
    return page, ai_enabled, ai_provider


def main() -> None:
    st.set_page_config(
        page_title="Freedom MoneyGraph AML",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()
    client = APIClient(_api_url())
    page, ai_enabled, ai_provider = _sidebar(client)
    render_page(
        page,
        client,
        ai_enabled=ai_enabled,
        ai_provider=ai_provider,
        ai_notice=ai_status_notice(enabled=ai_enabled, provider=ai_provider),
    )


if __name__ == "__main__":
    main()
