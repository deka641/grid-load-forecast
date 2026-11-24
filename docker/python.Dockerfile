# Shared image for the pipeline and api services.
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE DATA-LICENSE.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
# Build id for the API's ETags: a hash of the code and the locked dependencies, so it
# changes exactly when they do and a container restart keeps every ETag valid. It is
# stored next to the installed package, where the API reads it.
RUN set -o pipefail \
    && find src pyproject.toml uv.lock -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum \
        | sha256sum | cut -c1-32 > /tmp/build-id \
    && grep -Eqx '[0-9a-f]{32}' /tmp/build-id \
    && uv sync --locked --no-dev --no-editable \
    && cp /tmp/build-id "$(/app/.venv/bin/python -c 'import gridload, pathlib; print(pathlib.Path(gridload.__file__).parent)')/BUILD_ID"

FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS runtime
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.51
ARG SUPERCRONIC_SHA256_amd64=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ARG SUPERCRONIC_SHA256_arm64=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94
# libgomp1: OpenMP runtime required by LightGBM. curl only for the supercronic download.
# Exact Debian trixie versions, so a package change shows up as a diff. When a security
# update supersedes a pinned version, the mirror drops it and the build fails: update the
# pins as described in docs/how-to/develop.md.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         libgomp1=14.2.0-19 curl=8.14.1-2+deb13u7 ca-certificates=20250419+deb13u1 \
    # Security fix newer than the base image (CVE-2026-85091), exempt from the wait period.
    # Remove once the pinned base image digest includes the fix.
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
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
