# HTTP API

The API is read-only and serves JSON under `/api/v1`. Behind Caddy it is reachable at `http://127.0.0.1:8460/api/v1/...`. The machine-readable description is [`openapi.json`](https://github.com/deka641/grid-load-forecast/blob/master/openapi.json) in the repository root (also served at `/api/openapi.json`); the frontend types in `web/src/api/schema.d.ts` are generated from it, and the gate fails if either is out of date.

All timestamps in responses are ISO 8601 with the Europe/Berlin offset (`+01:00` or `+02:00`), so the two 02:00 hours of the autumn clock-change day stay distinguishable. Energy values are MWh per interval: per quarter hour with `resolution=quarterhour`, per hour for hourly data and forecasts. Prices are in EUR/MWh.

## Endpoints

| Endpoint | Purpose | Parameters |
|---|---|---|
| `GET /health` | Liveness | none |
| `GET /series` | Catalog of available series (key, label, unit, category) | none |
| `GET /status` | Data freshness per series, last pipeline run, last forecast, snapshot time, attribution, licence and price note | none |
| `GET /actuals` | Observed values for one or more series | `series` (comma-separated keys, at most 20), `start` and `end` (local days `YYYY-MM-DD`, inclusive), `resolution` (`hour`, default, or `quarterhour`) |
| `GET /forecasts` | Hourly LightGBM forecast, baseline, SMARD forecast and actual load for one delivery day | `delivery_day` (local day; default: newest day with a LightGBM forecast) |
| `GET /forecasts/days` | Delivery days that have forecasts, newest first | `limit` (1 to 5000, default 400) |
| `GET /backtest/metrics` | Newest stored backtest: period, configuration and metrics per model and scope | none |

## Limits and errors

- `/actuals` accepts at most 31 days in quarter-hour resolution and 366 days in hourly resolution; `end` must not be before `start`. Unknown series keys, invalid dates and exceeded limits return 422 with the standard FastAPI error body.
- `/forecasts` returns 404 if no forecast exists for the day; `/backtest/metrics` returns 404 before the first backtest.
- `/actuals`, `/forecasts`, `/forecasts/days` and `/backtest/metrics` return 503 `{"detail": ...}` when no readable snapshot exists yet (first start).
- The data endpoints (including `/series`) also return a JSON 503 when the server is saturated: at most 64 data requests are in progress at once, requests for more than 50,000 values share two slots with a short queue, and responses waiting to be sent may hold at most 100 MB. Clients should retry later. `/health` and `/status` are never limited. These limits apply to all clients together; a public deployment needs a per-client rate limit in front (see SECURITY.md). Only above 256 open connections does the server itself answer 503 with a plain-text body.
- Large responses are sent in parts. A client that stops reading for 10 seconds, or reads slower than about 64 KiB per second of uncompressed data, is disconnected; behind Caddy with compression this corresponds to roughly 10 KiB per second on the wire.
- `/status` always answers and reports `initialised: false` without a snapshot; `/series` then lists the configured catalog.
- Series and models that are not in the current snapshot (for example prices when they are switched off) are not offered.

## Caching

Data responses carry `Cache-Control: no-cache` and an `ETag` that changes with every new snapshot, a restart of the API and the price setting. A request with a matching `If-None-Match` header gets `304 Not Modified` without running a query, so a dashboard that reloads within the hour transfers almost nothing.
