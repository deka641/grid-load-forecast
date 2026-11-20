#!/usr/bin/env bash
# Production-like check: build the images, start an isolated stack on port 8461
# with a copy of the local data, and verify health, headers, sizes and caching.
# The live stack (project grid-load-forecast, port 8460, its images and volume)
# is not touched: the check stack has its own project name, port and image tags.
set -euo pipefail

PROJECT=grid-load-forecast-check
BASE=http://127.0.0.1:8461
DATA_DIR=${GRIDLOAD_DATA_DIR:-data}
COMPOSE=(docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.check.yml)
fail() { echo "FAIL: $*" >&2; exit 1; }

[[ -f "$DATA_DIR/serving.duckdb" ]] || fail "$DATA_DIR/serving.duckdb missing: run the pipeline first"

# Remove a previous check stack first (it is left running for the load test).
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
listeners=$(ss -Hltn 'sport = :8461') || fail "ss failed"
[[ -z "$listeners" ]] || fail "port 8461 is in use"
"${COMPOSE[@]}" build --quiet
"${COMPOSE[@]}" create --quiet-pull >/dev/null
# Copy the snapshot into the isolated volume before the API starts.
docker run --rm -v "${PROJECT}_gridload-data:/data" -v "$(realpath "$DATA_DIR"):/src:ro" \
    --user 0 grid-load-forecast-python:check \
    sh -c 'cp -p /src/serving.duckdb /data/ && chown -R 10001 /data'
"${COMPOSE[@]}" up -d

# Known vulnerabilities in the built images: critical findings with a fix block the
# release, high findings are listed for the record.
# Scanner image pin, updated by hand (Dependabot does not read this script).
TRIVY=aquasec/trivy:0.68.1@sha256:a93fd67162843c0f749002af9245fe9a2e5edc41445bd71d3949c803e95ef05b
for image in grid-load-forecast-python:check grid-load-forecast-web:check; do
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v grid-load-forecast-trivy-cache:/root/.cache \
        "$TRIVY" image --quiet --severity HIGH --ignore-unfixed --exit-code 0 "$image"
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v grid-load-forecast-trivy-cache:/root/.cache \
        "$TRIVY" image --quiet --severity CRITICAL --ignore-unfixed --exit-code 1 "$image" \
        || fail "Trivy failed or found critical vulnerabilities with a fix in $image"
done

for _ in $(seq 1 60); do
    state=$("${COMPOSE[@]}" ps --format '{{.Service}}={{.Health}}' | sort | tr '\n' ' ')
    [[ "$state" == "api=healthy pipeline=healthy web=healthy " ]] && break
    sleep 2
done
[[ "$state" == "api=healthy pipeline=healthy web=healthy " ]] || fail "services not healthy: $state"
echo "health: $state"

headers=$(curl -sfI "$BASE/")
for header in "Content-Security-Policy: default-src 'self'" "X-Content-Type-Options: nosniff" \
              "Referrer-Policy: no-referrer" "X-Frame-Options: DENY" "Cache-Control: no-cache"; do
    grep -qiF "$header" <<<"$headers" || fail "missing header on /: $header"
done
grep -qi '^server:' <<<"$headers" && fail "Server header is exposed"
grep -qi '^content-security-policy:' <<<"$(curl -s -D - -o /dev/null "$BASE/api/v1/health")" \
    || fail "CSP missing on API responses"

asset=$(curl -sf "$BASE/" | grep -o '/assets/[^"]*\.js' | head -1) || true
[[ -n "$asset" ]] || fail "no hashed script in index.html"
curl -sfI "$BASE$asset" | grep -qi 'cache-control: public, max-age=31536000, immutable' \
    || fail "hashed asset not cacheable"
[[ $(curl -s -o /dev/null -w '%{http_code}' "$BASE/assets/missing.js") == 404 ]] \
    || fail "missing asset does not return 404"
encoding=$(curl -s -D - -o /dev/null -H 'Accept-Encoding: gzip' "$BASE$asset")
grep -qi '^content-encoding: gzip' <<<"$encoding" || fail "static assets are not compressed"
gzip_size=$(curl -s -H 'Accept-Encoding: gzip' "$BASE$asset" | wc -c)
echo "main script over the wire (gzip): $((gzip_size / 1024)) kB"

status=$(curl -sf "$BASE/api/v1/status")
python3 -c 'import json,sys; s=json.load(sys.stdin); assert s["initialised"], s; print("snapshot:", s["snapshot_created_at"])' <<<"$status"
for path in /api/v1/forecasts /api/v1/backtest/metrics /api/v1/series; do
    code=$(curl -s -o /dev/null -w '%{http_code} %{size_download}' "$BASE$path")
    echo "$path -> $code bytes"
    [[ $code == 200* ]] || fail "$path returned $code"
done
[[ $(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/actuals?series=load&start=2025-01-01&end=2026-01-02") == 422 ]] \
    || fail "range limit not enforced"
"${COMPOSE[@]}" ps --format '{{.Name}} {{.Status}}'
echo "prod-check: server checks passed"

# Browser checks with real data: light and dark, 1440 and 390 px, axe, console, CSP.
SCREENSHOT_DIR=${SCREENSHOT_DIR:-$(pwd)/web/test-results-live} LIVE_BASE_URL=$BASE \
    npx --no-install --prefix web playwright test --config web/playwright.live.config.ts
echo "prod-check: browser checks passed"
