# Develop and verify changes

## Set up

Install [uv](https://docs.astral.sh/uv/) and Node.js 24, then:

```bash
make install
```

## The quality gates

```bash
make verify-fast   # during work, about two minutes
make verify        # before merging larger changes, before every push and release
```

Both gates first check that the environments were installed from the current lockfiles (otherwise they stop with a hint to run `make install`) and stop at the first failing step.

`make verify-fast` runs, in this order: ruff (lint and format check), ESLint and Prettier, mypy in strict mode and `tsc`, a check that no versioned text file contains emojis or em and en dashes, a secret scan of the working tree with gitleaks, and pytest and Vitest without coverage.

`make verify` runs the same lint, type, style and working-tree secret steps, then the dependency audit (pip-audit, npm audit; needs network), a secret scan over the whole git history, pytest and Vitest with coverage thresholds (100 % for the data source, pipeline, forecast and API packages), the freshness check of `openapi.json` and the generated TypeScript API types, the frontend build with its size budget, and the strict documentation build. It runs no browser.

Tests never reach the network: a guard fails any test that tries. gitleaks runs from a digest-pinned container, so Docker is needed.

## Dependencies

New package versions are only resolved once they are at least seven days old (`exclude-newer` in `pyproject.toml`, `min-release-age` in `web/.npmrc`), and npm does not run install scripts of dependencies. Dependabot proposes updates weekly with the same seven-day cooldown. Every new dependency needs a reason: purpose, maintenance and licence.

The Python image pins its base images by digest, which Dependabot updates, and the Debian packages it installs (`libgomp1`, `curl`, `ca-certificates`, `zlib1g`) by exact version in `docker/python.Dockerfile`, which nobody updates for you. The Debian mirror keeps only the current version of a package: a security update replaces the old version in `trixie-security`, and a point release replaces it in `trixie`. From then on `apt-get install` cannot find the pinned version and the image build fails with `Version '...' for '...' was not found`. If a new base image already ships a newer version than a pin, the build fails with a downgrade error instead. That is intended: a package changes only through a visible commit, never silently between two builds. To update the pins, query the candidate versions in the base image the Dockerfile pins:

```bash
docker run --rm python:3.15-slim-trixie@sha256:<digest from the Dockerfile> \
    sh -c 'apt-get update -qq && apt-cache policy libgomp1 curl ca-certificates zlib1g'
```

Take each `Candidate` and put it into the Dockerfile. Once the base image itself contains the pinned `zlib1g` version, remove the separate `--only-upgrade` step for it. Do the same whenever Dependabot moves the base image digest, then build the image and run `make prod-check`.

## Work with real data locally

```bash
GRIDLOAD_DATA_DIR=data uv run gridload backfill --since 2024-01-01
GRIDLOAD_DATA_DIR=data uv run gridload run
uv run uvicorn gridload.api.app:app --port 8000      # in a second terminal
npm --prefix web run dev                             # proxies /api to port 8000
```

## Reproduce the backtest report

The committed report was computed on the full history since 2015 up to the delivery day 2026-11-19. A warehouse loaded with `--since` trains the model on less history and gives other figures. Get a full warehouse in one of two ways:

```bash
GRIDLOAD_DATA_DIR=data uv run gridload backfill      # full history; the first backfill of the stack took 75 minutes
docker compose cp pipeline:/data/warehouse.duckdb data/warehouse.duckdb   # or copy it from the stack while no run is active
```

Then run the backtest for the same window and cross-check it:

```bash
uv run python scripts/backtest.py --last-day 2026-11-19
uv run --isolated --no-project --with scikit-learn \
    python scripts/crosscheck_metrics.py data/backtest_rows.csv reports/backtest.json
```

The first command writes `reports/backtest.json` and `reports/backtest.md`. Without `--last-day` the window ends with the last complete day of the warehouse, so every figure moves with the data. The option rejects a day that is not in the past, after the last complete day, or too early to leave training data. The second command recomputes every metric with scikit-learn in a separate environment and fails on any mismatch.

Both reports record a SHA-256 fingerprint of the hourly load and SMARD forecast values up to the last delivery day, and how many of those hours SMARD corrected after they were first loaded. SMARD still corrects values later, so a warehouse loaded at another time can give slightly different figures. If the fingerprint matches the committed one, the input data are the same; if it differs, the data differ and so may the figures.

## Release checks

```bash
make smoke        # live request against SMARD
make prod-check   # fixture browser checks, then the production stack on an isolated port with a data copy
make loadtest     # parallel dashboard requests against the isolated stack
```

The browser checks need Playwright's Chromium, installed once with `npx --prefix web playwright install chromium`.

`make prod-check` first runs `make e2e`: the frontend build with its size budget, then the browser suite in `web/e2e` against that build, served on port 8471 with the production Content-Security-Policy and mocked API responses. Its tests cover the 25 hour day of the autumn clock change, Berlin time in a browser set to another time zone, the degraded, stale, busy and first-start notices, unpublished and negative prices, a single forecast day, both themes at several widths and accessibility with axe. They took 1 min 47 s including the build on an Intel Core i7-14650HX, which is why they are not part of `make verify`.

`scripts/prod_check.sh` then builds the images, starts the stack as the project `grid-load-forecast-check` on port 8461 with a copy of `data/serving.duckdb` (the live stack on port 8460 and its volume are not touched), and checks:

- the images with Trivy: critical vulnerabilities with a fix fail the check, high ones are listed;
- that all three services become healthy;
- that the API logs the client address Caddy saw, not the web container's address and not a forged `X-Forwarded-For`;
- the security and cache headers, gzip compression of the hashed assets, a 404 for a missing asset, and the security headers without a `Server` header on the error responses Caddy answers itself;
- that the snapshot is initialised, the main endpoints answer 200 and the range limit answers 422;
- that `/health` and `/status` answer 200 for one client while another holds 400 connections, and that the web container survives 9,000 half-open connections (`scripts/loadtest_abuse.py`);
- the real-data dashboard in the browser (`web/live`): light and dark theme at 1440 and 390 px, no console errors or warnings, no Content-Security-Policy violations, no sideways scrolling and no serious or critical axe findings.

The stack keeps running for `make loadtest`; `make prod-down` removes it.

## Record new fixtures

```bash
uv run python scripts/record_fixtures.py
```

The selection (clock-change weeks, the switch to 15-minute prices, negative prices, the newest chunks) is documented in the script.
