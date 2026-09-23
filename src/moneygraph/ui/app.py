"""Freedom MoneyGraph AML Streamlit entrypoint."""

from __future__ import annotations

import os

import streamlit as st

from moneygraph.ui.client import APIClient, APIClientError
from moneygraph.ui.helpers import bool_from_env
from moneygraph.ui.pages import render_page

NAV_PAGES = (
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
        return f"AI Copilot включён: провайдер {provider}. Результаты требуют проверки аналитиком."
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
    st.markdown(
        """
        <style>
          .block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1500px;}
          [data-testid="stMetric"] {background: rgba(30, 41, 59, .06); border: 1px solid
            rgba(100, 116, 139, .22); padding: .85rem; border-radius: 14px;}
          .mg-hero {padding: 1.25rem 1.5rem; border-radius: 18px; margin-bottom: 1.2rem;
            background: linear-gradient(120deg,#0f172a,#1d4ed8); color: white;}
          .mg-hero h1 {margin:0; font-size:2rem;}
          .mg-hero p {margin:.45rem 0 0; opacity:.86;}
          .mg-label {font-size:.78rem; text-transform:uppercase; letter-spacing:.08em;
            color:#64748b; font-weight:700;}
          div[data-testid="stDataFrame"] {border:1px solid rgba(100,116,139,.2);
            border-radius:12px; overflow:hidden;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _sidebar(client: APIClient, *, ai_enabled: bool, ai_provider: str) -> str:
    with st.sidebar:
        st.markdown("### MoneyGraph AML")
        st.caption("Объяснимая навигация по графу переводов")
        page = st.radio("Рабочая область", NAV_PAGES, label_visibility="collapsed")
        st.divider()
        try:
            health = client.health()
            status = str(health.get("status", "ok"))
            st.success(f"API: {status}", icon="✅")
        except APIClientError:
            st.error("API недоступен", icon="❌")
        if ai_enabled:
            st.info(f"AI: {ai_provider}", icon="✨")
        else:
            st.caption("AI: offline fallback")
        st.caption(f"API URL: `{client.base_url}`")
    return page


def main() -> None:
    st.set_page_config(
        page_title="Freedom MoneyGraph AML",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()
    ai_enabled = bool_from_env(os.getenv("AI_ENABLED", "false"))
    ai_provider = os.getenv("AI_PROVIDER", "fallback")
    client = APIClient(_api_url())
    page = _sidebar(client, ai_enabled=ai_enabled, ai_provider=ai_provider)
    render_page(
        page,
        client,
        ai_enabled=ai_enabled,
        ai_provider=ai_provider,
        ai_notice=ai_status_notice(enabled=ai_enabled, provider=ai_provider),
    )


if __name__ == "__main__":
    main()
