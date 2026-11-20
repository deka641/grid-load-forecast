# Shared image for the pipeline and api services.
FROM ghcr.io/astral-sh/uv:0.11.3@sha256:90bbb3c16635e9627f49eec6539f956d70746c409209041800a0280b93152823 AS uv

FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE DATA-LICENSE.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS runtime
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.51
ARG SUPERCRONIC_SHA256_amd64=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ARG SUPERCRONIC_SHA256_arm64=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94
# libgomp1: OpenMP runtime required by LightGBM. curl only for the supercronic download.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl ca-certificates \
    # Security fix newer than the base image (CVE-2026-85091), exempt from the wait period.
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && case "${TARGETARCH}" in \
         amd64) sha="${SUPERCRONIC_SHA256_amd64}" ;; \
         arm64) sha="${SUPERCRONIC_SHA256_arm64}" ;; \
         *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /usr/local/bin/supercronic \
         "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
    && echo "${sha}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/supercronic \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    # The app runs from /app/.venv; the base image's pip and its vendored packages are not needed.
    && python -m pip uninstall -y pip \
    && useradd --system --uid 10001 --home-dir /app gridload \
    && mkdir -p /data && chown gridload /data
COPY --from=build /app/.venv /app/.venv
COPY docker/crontab /app/crontab
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 GRIDLOAD_DATA_DIR=/data
# Fail the build early if a native dependency cannot be loaded.
RUN python -c "import duckdb, fastapi, lightgbm, numpy, polars, pyarrow"
WORKDIR /app
USER gridload
