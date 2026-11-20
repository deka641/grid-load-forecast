# Develop and verify changes

## Set up

Install [uv](https://docs.astral.sh/uv/) and Node.js 24, then:

```bash
make install
```

## The quality gate

```bash
make verify
```

The gate checks that the locked environments are installed and then runs, in order and stopping at the first failure: the dependency audit (pip-audit, npm audit), ruff, ESLint and Prettier, mypy in strict mode and `tsc`, all tests with coverage (100 % for the data source, pipeline, forecast and API packages), the freshness check of the generated API types, the frontend build with its size budget, and the strict documentation build. Tests never reach the network: a guard fails any test that tries.

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
make prod-check   # production build behind Caddy on an isolated port with a data copy
make loadtest     # parallel dashboard requests against the isolated stack
```

## Record new fixtures

```bash
uv run python scripts/record_fixtures.py
```

The selection (clock-change weeks, the switch to 15-minute prices, negative prices, the newest chunks) is documented in the script.
