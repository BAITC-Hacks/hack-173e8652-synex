"""Visual identity primitives for the analyst workspace."""

from __future__ import annotations

from html import escape

THEME_CSS = """
<style>
  :root {
    --mg-ink: #14272b;
    --mg-ink-soft: #1d3438;
    --mg-canvas: #f3f2ed;
    --mg-paper: #fffefa;
    --mg-line: #cdd3ce;
    --mg-line-strong: #aeb9b3;
    --mg-muted: #63716f;
    --mg-signal: #dc4c3e;
    --mg-signal-soft: #f9e5e0;
    --mg-teal: #197367;
    --mg-amber: #b87a17;
    --mg-radius: 7px;
  }

  html,
  body,
  [class*="st-"] {
    font-feature-settings: "ss01" 1, "cv02" 1, "cv03" 1;
  }

  [data-testid="stAppViewContainer"] {
    background: var(--mg-canvas);
  }

  header[data-testid="stHeader"] {
    background: color-mix(in srgb, var(--mg-canvas) 92%, transparent);
    border-bottom: 1px solid color-mix(in srgb, var(--mg-line) 70%, transparent);
    backdrop-filter: blur(10px);
  }

  .block-container {
    max-width: 1480px;
    padding-top: 2.8rem;
    padding-bottom: 4rem;
    padding-left: 2.25rem;
    padding-right: 2.25rem;
  }

  section[data-testid="stSidebar"] {
    background: var(--mg-ink);
    border-right: 1px solid #294146;
  }

  section[data-testid="stSidebar"] > div:first-child {
    padding-top: 1.35rem;
  }

  section[data-testid="stSidebar"] hr {
    border-color: #355055;
    margin: 1.35rem 0;
  }

  .mg-brand {
    display: flex;
    align-items: center;
    gap: 0.8rem;
    min-height: 48px;
    margin: 0.1rem 0 1.25rem;
  }

  .mg-brand__mark {
    display: grid;
    grid-template-columns: repeat(3, 5px);
    align-items: end;
    gap: 4px;
    width: 29px;
    height: 33px;
    padding: 5px;
    border: 1px solid #496267;
    background: #193035;
  }

  .mg-brand__mark span {
    display: block;
    background: var(--mg-signal);
  }

  .mg-brand__mark span:nth-child(1) { height: 9px; }
  .mg-brand__mark span:nth-child(2) { height: 18px; }
  .mg-brand__mark span:nth-child(3) { height: 25px; }

  .mg-brand__copy {
    display: grid;
    gap: 0.16rem;
  }

  .mg-brand__copy strong {
    color: #fffefa;
    font-size: 0.97rem;
    font-weight: 720;
    letter-spacing: -0.015em;
    line-height: 1.1;
  }

  .mg-brand__copy small {
    color: #aebfbc;
    font-size: 0.67rem;
    font-weight: 650;
    letter-spacing: 0.105em;
    line-height: 1.35;
    text-transform: uppercase;
  }

  section[data-testid="stSidebar"] [role="radiogroup"] {
    display: grid;
    gap: 0.22rem;
  }

  section[data-testid="stSidebar"] [role="radiogroup"] label {
    min-height: 2.42rem;
    padding: 0.48rem 0.62rem;
    border: 1px solid transparent;
    border-radius: 5px;
    transition: background-color 140ms ease, border-color 140ms ease;
  }

  section[data-testid="stSidebar"] [role="radiogroup"] label:hover {
    background: #1a3438;
    border-color: #314c50;
  }

  section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
    background: #203a3e;
    border-color: #3d5a5f;
    box-shadow: inset 3px 0 0 var(--mg-signal);
  }

  section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
    color: #aebfbc;
  }

  section[data-testid="stSidebar"] code {
    color: #d8e4e1;
    background: #1a3438;
    border: 1px solid #355055;
  }

  .mg-hero {
    position: relative;
    isolation: isolate;
    overflow: hidden;
    margin: 0 0 1.65rem;
    padding: 2.25rem 2.4rem 2rem;
    color: #fffefa;
    background: var(--mg-ink);
    border: 1px solid #294146;
    border-radius: var(--mg-radius);
    box-shadow: 0 12px 30px rgba(20, 39, 43, 0.12);
  }

  .mg-hero:not(:has(.mg-label))::before {
    display: block;
    margin-bottom: 1rem;
    color: #f08a7f;
    content: "AML / ANALYST WORKSPACE";
    font-size: 0.69rem;
    font-weight: 760;
    letter-spacing: 0.16em;
  }

  .mg-hero::after {
    position: absolute;
    z-index: -1;
    top: 0;
    right: 2.4rem;
    width: 72px;
    height: 100%;
    border-left: 1px solid #345056;
    border-right: 1px solid #345056;
    content: "";
    opacity: 0.58;
    transform: skewX(-13deg);
  }

  .mg-hero h1 {
    max-width: 900px;
    margin: 0;
    color: #fffefa;
    font-size: clamp(2rem, 3vw, 3.05rem);
    font-weight: 760;
    letter-spacing: -0.045em;
    line-height: 0.98;
  }

  .mg-hero p {
    max-width: 820px;
    margin: 0.9rem 0 0;
    color: #c7d3d0;
    font-size: 1rem;
    line-height: 1.55;
  }

  .mg-label {
    color: #f08a7f !important;
    font-size: 0.69rem;
    font-weight: 760;
    letter-spacing: 0.13em;
    text-transform: uppercase;
  }

  [data-testid="stMetric"] {
    min-height: 112px;
    padding: 1rem 1.05rem 0.92rem;
    background: var(--mg-paper);
    border: 1px solid var(--mg-line);
    border-radius: var(--mg-radius);
    box-shadow: none;
  }

  [data-testid="stMetric"]::before {
    display: block;
    width: 28px;
    height: 2px;
    margin-bottom: 0.65rem;
    background: var(--mg-signal);
    content: "";
  }

  [data-testid="stMetricLabel"] {
    color: var(--mg-muted);
    font-size: 0.68rem;
    font-weight: 730;
    letter-spacing: 0.055em;
    text-transform: uppercase;
  }

  [data-testid="stMetricLabel"] > div,
  [data-testid="stMetricLabel"] p {
    overflow: visible;
    white-space: normal;
    text-overflow: clip;
  }

  [data-testid="stMetricValue"] {
    color: var(--mg-ink);
    font-size: clamp(1.42rem, 2vw, 2.05rem);
    font-weight: 730;
    letter-spacing: -0.035em;
  }

  h2,
  h3 {
    color: var(--mg-ink);
    letter-spacing: -0.025em;
  }

  h2 {
    margin-top: 1.5rem;
    padding-top: 0.65rem;
    border-top: 1px solid var(--mg-line);
  }

  div[data-testid="stDataFrame"],
  div[data-testid="stTable"] {
    overflow: hidden;
    background: var(--mg-paper);
    border: 1px solid var(--mg-line);
    border-radius: var(--mg-radius);
    box-shadow: none;
  }

  div[data-testid="stPlotlyChart"] {
    overflow: hidden;
    background: var(--mg-paper);
    border: 1px solid var(--mg-line);
    border-radius: var(--mg-radius);
  }

  [data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--mg-paper);
    border-color: var(--mg-line) !important;
    border-radius: var(--mg-radius);
  }

  details[data-testid="stExpander"] {
    background: var(--mg-paper);
    border-color: var(--mg-line);
    border-radius: var(--mg-radius);
  }

  [data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap: 0.15rem;
    padding: 0.22rem;
    background: #e7e9e5;
    border: 1px solid var(--mg-line);
    border-radius: var(--mg-radius);
  }

  [data-testid="stTabs"] button[role="tab"] {
    min-height: 2.8rem;
    border-radius: 4px;
    color: var(--mg-muted);
    font-weight: 680;
  }

  [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: var(--mg-ink);
    background: var(--mg-paper);
    box-shadow: 0 1px 4px rgba(20, 39, 43, 0.08);
  }

  [data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    background: var(--mg-signal);
  }

  .stButton > button,
  .stDownloadButton > button,
  [data-testid="stFormSubmitButton"] > button,
  [data-testid="stPopover"] > button {
    min-height: 2.6rem;
    border-radius: 5px;
    font-weight: 690;
    letter-spacing: -0.01em;
    box-shadow: none;
    transition: border-color 140ms ease, background-color 140ms ease, color 140ms ease;
  }

  button[kind="primary"] {
    color: #fffefa;
    background: var(--mg-signal);
    border-color: var(--mg-signal);
  }

  button[kind="primary"]:hover {
    background: #c83e32;
    border-color: #c83e32;
  }

  button[kind="secondary"] {
    color: var(--mg-ink);
    background: var(--mg-paper);
    border-color: var(--mg-line-strong);
  }

  button[kind="secondary"]:hover {
    color: var(--mg-ink);
    background: #e8ebe7;
    border-color: #8f9c97;
  }

  [data-testid="stAlert"] {
    border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
    border-left-width: 4px;
    border-radius: 5px;
    box-shadow: none;
  }

  [data-baseweb="input"] > div,
  [data-baseweb="select"] > div,
  [data-baseweb="textarea"] > div {
    background: var(--mg-paper);
    border-color: var(--mg-line-strong);
    border-radius: 5px;
  }

  [data-testid="stStatusWidget"] {
    border-color: var(--mg-line);
    border-radius: var(--mg-radius);
  }

  a {
    text-decoration-thickness: 1px;
    text-underline-offset: 0.15em;
  }

  :where(button, a, input, textarea, select, [tabindex]):focus-visible {
    outline: 3px solid color-mix(in srgb, var(--mg-signal) 48%, transparent);
    outline-offset: 2px;
  }

  @media (max-width: 900px) {
    .block-container {
      padding-top: 2.25rem;
      padding-left: 1.25rem;
      padding-right: 1.25rem;
    }

    .mg-hero {
      padding: 1.7rem 1.5rem 1.55rem;
    }

    .mg-hero::after {
      right: 0.8rem;
      opacity: 0.35;
    }

    [data-testid="stMetric"] {
      min-height: 96px;
    }

    .block-container [data-testid="stHorizontalBlock"] {
      flex-wrap: wrap !important;
      row-gap: 1rem;
    }

    .block-container [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
      flex: 1 1 280px !important;
      width: auto !important;
      min-width: min(100%, 280px) !important;
    }

    .block-container
      [data-testid="stElementContainer"]:has(.mg-hero)
      + [data-testid="stLayoutWrapper"]
      [data-testid="stHorizontalBlock"]
      > [data-testid="stColumn"] {
      flex: 1 1 120px !important;
      min-width: 120px !important;
    }
  }

  @media (max-width: 640px) {
    .block-container {
      padding-left: 0.9rem;
      padding-right: 0.9rem;
    }

    .mg-hero::before {
      margin-bottom: 0.72rem;
    }

    .mg-hero h1 {
      font-size: 1.85rem;
      line-height: 1.04;
    }

    .mg-hero p {
      font-size: 0.92rem;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      scroll-behavior: auto !important;
      transition-duration: 0.01ms !important;
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
    }
  }
</style>
"""


def brand_markup(*, product_name: str, descriptor: str) -> str:
    """Return an accessible brand lockup with safely escaped copy."""

    safe_name = escape(product_name, quote=True)
    safe_descriptor = escape(descriptor, quote=True)
    accessible_name = f"{safe_name} — {safe_descriptor}"
    return (
        f'<div class="mg-brand" role="img" aria-label="{accessible_name}">'
        '<span class="mg-brand__mark" aria-hidden="true">'
        "<span></span><span></span><span></span>"
        "</span>"
        '<span class="mg-brand__copy">'
        f"<strong>{safe_name}</strong><small>{safe_descriptor}</small>"
        "</span>"
        "</div>"
    )
