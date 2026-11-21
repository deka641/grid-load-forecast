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

The fast gate runs ruff, ESLint and Prettier, mypy in strict mode and `tsc`, a check that no versioned text file contains emojis or em and en dashes, a secret scan of the working tree with gitleaks, and all tests without coverage. The full gate adds the dependency audit (pip-audit, npm audit), a secret scan over the whole git history, the tests with coverage thresholds (100 % for the data source, pipeline, forecast and API packages), the freshness check of the generated API types, the frontend build with its size budget, and the strict documentation build. Tests never reach the network: a guard fails any test that tries. gitleaks runs from a digest-pinned container, so Docker is needed.

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

```bash
uv run python scripts/backtest.py
uv run --isolated --no-project --with scikit-learn \
    python scripts/crosscheck_metrics.py data/backtest_rows.csv reports/backtest.json
```

The first command writes `reports/backtest.json` and `reports/backtest.md`. The second recomputes every metric with scikit-learn in a separate environment and fails on any mismatch.

## Release checks

```bash
make smoke        # live request against SMARD
make prod-check   # production build behind Caddy on an isolated port with a data copy, image scan with Trivy
make loadtest     # parallel dashboard requests against the isolated stack
```

## Record new fixtures

```bash
uv run python scripts/record_fixtures.py
```

The selection (clock-change weeks, the switch to 15-minute prices, negative prices, the newest chunks) is documented in the script.
