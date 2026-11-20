# Shared image for the pipeline and api services.
FROM ghcr.io/astral-sh/uv:0.11.3 AS uv

FROM python:3.14-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie AS runtime
ARG SUPERCRONIC_VERSION=v0.2.51
ARG SUPERCRONIC_SHA256=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ADD --checksum=sha256:${SUPERCRONIC_SHA256} \
    https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 \
    /usr/local/bin/supercronic
RUN chmod 0755 /usr/local/bin/supercronic \
    && useradd --system --uid 10001 --home-dir /app gridload \
    && mkdir -p /data && chown gridload /data
COPY --from=build /app/.venv /app/.venv
COPY docker/crontab /app/crontab
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 GRIDLOAD_DATA_DIR=/data
WORKDIR /app
USER gridload
