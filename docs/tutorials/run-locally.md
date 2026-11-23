# Run the stack locally

This tutorial starts the complete stack on your machine, loads the history from SMARD and opens the dashboard with the first forecast.

## Requirements

Docker with Compose v2 and a free port 8460 on `127.0.0.1`. The containers have memory limits of 2.75 GiB in total (2 GiB pipeline, 512 MiB API, 256 MiB web) and CPU limits of 4, 3 and 2 cores. After a full backfill the data files take about 0.58 GB on the volume (a run on 21 November 2026), and DuckDB may use up to 4 GB of temporary space there during large operations.

## Start

```bash
git clone https://github.com/deka641/grid-load-forecast.git
cd grid-load-forecast
cp compose.override.example.yaml compose.override.yaml   # optional: no automatic restarts
docker compose up -d --build
```

Compose builds two images and starts three services: `pipeline`, `api` and `web`. Check that they are healthy:

```bash
docker compose ps
```

## First run: loading the history

On an empty data volume the pipeline loads all series since 29 December 2014. With requests throttled to two per second, a full backfill on 21 November 2026 (8,973 weekly chunks) took 75 minutes; how long yours takes depends on how fast SMARD answers. The first run logs its summary only when it ends, and `gridload status` reports `initialised: false` until the first snapshot is published. Meanwhile `docker compose ps` shows the pipeline as healthy while it works, and the dashboard says that no data is available yet. To see the summary when it arrives:

```bash
docker compose logs -f pipeline
```

## The first forecast and backtest

After the history is loaded, the same run issues the forecast for the next delivery day (if it is already past 12:00 in Germany and the hour up to 10:00 is loaded; if that hour is still missing, from 20:00 on with the values available up to 10:00, provided at least one load value falls in the 24 hours before 10:00, and marked as degraded in its run details) and computes the backtest over the last 365 days, which takes about five minutes (`scripts/backtest.py` outside the container, on the data of 20 November 2026, took about 5 minutes on an Intel Core i7-14650HX). Before 12:00 the first forecast follows with the hourly run after noon.

Open [http://127.0.0.1:8460](http://127.0.0.1:8460). The page shows the forecast for the next delivery day with the baseline and the SMARD forecast, the backtest accuracy, and the generation mix and prices of the last seven days.

From then on the pipeline runs every hour and loads only what is new.

## Stop and start

```bash
docker compose stop      # keeps the data volume
docker compose start
docker compose down -v   # removes the containers and the data volume
```
