# Operate the pipeline

## Check the state

```bash
docker compose ps                              # health of all services
docker compose exec pipeline gridload status   # runs, coverage per series, errors of the last run
curl -s http://127.0.0.1:8460/api/v1/status    # what the dashboard sees
```

A run with status `partial` loaded fresh data, but an older chunk or a follow-up step (forecast, backtest) failed, a week that just ended has a gap, or SMARD withdrew stored values from a recent week of a series other than the grid load (errors on chunks that ended more than seven days ago also count as `partial`); the details are in `gridload status` (errors and warnings of the last run) and in the pipeline log. `failed` means recent data could not be loaded, or recent grid load values were withdrawn, which the forecast depends on. The pipeline container stays healthy after `partial` runs, so a withdrawal that persists in a generation or price series shows up in the status, not as an unhealthy container.

## Pick up corrections to older data

The hourly run reloads the four newest weeks. To check older weeks for corrections:

```bash
docker compose exec pipeline gridload ingest --refetch-weeks 52
```

Unchanged chunks are recognised by their hash and skipped, changed values overwrite the stored ones and increase their revision.

## Accept values that SMARD removed

If the status reports "stored values are now missing in a completed chunk" for a week and you have checked on SMARD.de that the values were removed on purpose, accept the withdrawal for that series only, with N large enough to reach the reported week:

```bash
docker compose exec pipeline gridload ingest --series KEY --refetch-weeks N --accept-withdrawals
```

## Switch wholesale prices off

Set `GRIDLOAD_INCLUDE_PRICES=false` (in the shell or in `.env`) and recreate the services:

```bash
GRIDLOAD_INCLUDE_PRICES=false docker compose up -d
```

After the services are recreated, the API no longer offers prices and the dashboard hides the price chart; the next pipeline run publishes a snapshot without any price data.

## Run the backtest on demand

```bash
docker compose exec pipeline gridload backtest --days 365
```

The command needs the warehouse exclusively; if the hourly run is active, wait until it has finished.

## Copy the data out of the container

```bash
docker compose cp pipeline:/data/serving.duckdb ./serving-copy.duckdb
```
