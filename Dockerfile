# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPYCACHEPREFIX=/tmp/pycache \
    HOME=/tmp/moneygraph

RUN groupadd --gid "${APP_GID}" moneygraph \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin moneygraph \
    && mkdir -p /app/out /app/artifacts /app/state /tmp/moneygraph /tmp/pycache \
    && chown -R "${APP_UID}:${APP_GID}" /app /tmp/moneygraph /tmp/pycache

WORKDIR /app

COPY --chown=${APP_UID}:${APP_GID} pyproject.toml README.md ./
COPY --chown=${APP_UID}:${APP_GID} src ./src
COPY --chown=${APP_UID}:${APP_GID} config ./config
COPY --chown=${APP_UID}:${APP_GID} scripts ./scripts

RUN python -m pip install ".[ai]" \
    && chmod 0555 /app/scripts/*.sh

USER moneygraph:moneygraph

EXPOSE 8000 8501

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

STOPSIGNAL SIGTERM

CMD ["python", "-m", "uvicorn", "moneygraph.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
