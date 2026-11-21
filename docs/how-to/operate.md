# Operate the pipeline

## Check the state

```bash
docker compose ps                              # health of all services
docker compose exec pipeline gridload status   # runs, coverage per series, errors of the last run
curl -s http://127.0.0.1:8460/api/v1/status    # what the dashboard sees
```

A run with status `partial` loaded fresh data, but an older chunk or a follow-up step (forecast, backtest) failed, a week that just ended has a gap, or SMARD withdrew stored values from a recent week of a series other than the grid load (errors on chunks that ended more than seven days ago also count as `partial`); the details are in `gridload status` (errors and warnings of the last run) and in the pipeline log. `failed` means recent data could not be loaded, or recent grid load values were withdrawn, which the forecast depends on. A `partial` run counts as a success for the pipeline healthcheck (which also needs a load value younger than 12 hours), so a withdrawal that persists in a series other than the grid load shows up in the status, not as an unhealthy container.

## Pick up corrections to older data

The hourly run reloads the four newest weeks. To check older weeks for corrections:

```bash
docker compose exec pipeline gridload ingest --refetch-weeks 52
```

Unchanged chunks are recognised by their hash and skipped, changed values overwrite the stored ones and increase their revision.

## Accept values that SMARD removed

The pipeline keeps stored values that a new response no longer contains, unless only a short tail at the end of the current week is missing (at most 8 quarter hours, all newer than the newest value still published). It reports the chunk as an error with one of two messages:

- "N stored values are now missing in an open chunk (more than 8, not at its end or no value left); kept the stored values, wrote M new values" for the current week. New values are still stored, and the chunk is fetched again by every run.
- "N stored values are now missing in a completed chunk; kept the stored values" for a week that has ended.

For the grid load this fails the run while the week is open and for seven days after it ended, because the forecast depends on it; after that the run is `partial`, and the error stays in the status for as long as the hourly run still reloads that week (the newest four). If SMARD keeps serving the response without those values, every hourly run is `failed` for that whole time: `gridload status` lists the message under `errors_last_run`, `/api/v1/status` shows the last load run as `failed`, and once no run has succeeded for 26 hours the pipeline container turns unhealthy. Forecasts and snapshots continue meanwhile, computed from the stored values. For other series the run is `partial` and the container stays healthy.

First check on SMARD.de whether the values were removed on purpose. If they were not, wait: the next run fetches the week again and takes the values once SMARD serves them. If they were, accept the withdrawal for that series only, with N large enough to reach the reported week (the default of 4 covers the current week and the three before it):

```bash
docker compose exec pipeline gridload ingest --series KEY --refetch-weeks N --accept-withdrawals
```

This deletes the withdrawn values locally, stores the chunk, publishes a new snapshot and records the run as `ok` (`partial` if other chunks of the series still report errors; both count as a success for the healthcheck), so the container is healthy again after its next check. It accepts every withdrawal in the reloaded weeks of that series, so keep N as small as the report allows. Later scheduled runs no longer report the chunk, and if SMARD publishes the values again they are stored as usual. Like every writing command it fails with a lock error while the hourly run is active; run it again afterwards.

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
