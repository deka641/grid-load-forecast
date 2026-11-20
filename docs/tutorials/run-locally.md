# Run the stack locally

This tutorial starts the complete stack on your machine, loads the history from SMARD and opens the dashboard with the first forecast.

## Requirements

Docker with Compose v2, about 2 GB of free memory and 1 GB of disk space for the data volume. Port 8460 on `127.0.0.1` must be free.

## Start

```bash
git clone https://github.com/deka641/grid-load-forecast.git
cd grid-load-forecast
cp docker-compose.override.example.yml docker-compose.override.yml   # optional: no automatic restarts
docker compose up -d --build
```

Compose builds two images and starts three services: `pipeline`, `api` and `web`. Check that they are healthy:

```bash
docker compose ps
```

## First run: loading the history

On an empty data volume the pipeline loads all series since 29 December 2014. With requests throttled to two per second this took BACKFILL_DURATION in our measurement. Follow the progress with:

```bash
docker compose logs -f pipeline
docker compose exec pipeline gridload status
```

Until the first snapshot exists, the dashboard says that no data is available yet.

## The first forecast and backtest

After the history is loaded, the same run issues the forecast for the next delivery day (if it is already past 12:00 in Germany and the data up to 10:00 is available) and computes the backtest over the last 365 days, which took BACKTEST_DURATION. Before 12:00 the first forecast follows with the hourly run after noon.

Open [http://127.0.0.1:8460](http://127.0.0.1:8460). The page shows the forecast for the next delivery day with the baseline and the SMARD forecast, the backtest accuracy, and the generation mix and prices of the last seven days.

## Stop and start

```bash
docker compose stop      # keeps the data volume
docker compose start
docker compose down -v   # removes the containers and the data volume
```

The pipeline then continues every hour and loads only what is new.
