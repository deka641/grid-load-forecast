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

## Degraded forecasts

Every run in `runs` of `/forecasts` and the `last_forecast` of `/status` carry `degraded` and `degraded_checks`. A LightGBM forecast is degraded when some of its inputs were missing at the cutoff; it is still published, but its error is likely larger than the backtest suggests. `degraded_checks` names the failed checks:

| Check | Fails when |
|---|---|
| `cutoff_hour_missing` | the newest hour a forecast may use (09:00 to 10:00 local time on the day before delivery) was not yet published when the forecast was issued |
| `lag_1d`, `lag_2d`, `lag_7d`, `lag_14d` | the load of the same hour one, two, seven or 14 days earlier is missing for some delivery hour |
| `mean_24h`, `mean_168h` | the mean load of the 24 or 168 hours before the cutoff could not be computed |
| `coverage_168h` | fewer than 90 % of the 168 hours before the cutoff are present |

`degraded` is `true` if any check failed and `false` with an empty `degraded_checks` if all passed. Both are `null` when the run was not assessed: baseline runs (`baseline_week`) and runs stored before the assessment was recorded. A client should treat `null` as unknown, not as a complete forecast.

## Limits and errors

- `/actuals` accepts at most 31 days in quarter-hour resolution and 366 days in hourly resolution; `end` must not be before `start`. Unknown series keys, invalid dates and exceeded limits return 422 with the standard FastAPI error body.
- `/forecasts` returns 404 if no forecast exists for the day; `/backtest/metrics` returns 404 before the first backtest.
- `/actuals`, `/forecasts`, `/forecasts/days` and `/backtest/metrics` return 503 `{"detail": ...}` when no readable snapshot exists yet (first start).
- The data endpoints (including `/series`) also return a JSON 503 when the server is saturated: at most 64 data requests are in progress at once, requests for more than 50,000 values share two slots with a short queue, and responses waiting to be sent may hold at most 100 MB. Clients should retry later. `/health` and `/status` are never limited. These limits apply to all clients together; a public deployment needs a per-client rate limit in front (see SECURITY.md). Only above 256 open connections does the server itself answer 503 with a plain-text body.
- Large responses are sent in parts. A client that stops reading for 10 seconds, or reads slower than about 64 KiB per second of uncompressed data, is disconnected; behind Caddy with compression this corresponds to roughly 10 KiB per second on the wire. The client receives the status line and part of the body, then the connection closes; the body is shorter than its `Content-Length`, so an HTTP client reports an incomplete response. Each drop leaves two log lines in the API container: a WARNING from the API (`dropped a client that was too slow to read ...`) and an ERROR from uvicorn (`ASGI callable returned without completing response.`). The ERROR is expected for these drops. ASGI has no message that aborts a started response, so the API ends the response without its final part, and uvicorn reports every such response at that level; marking the body as complete instead would hand the client a truncated body as if it were whole.
- `/status` always answers and reports `initialised: false` without a snapshot; `/series` then lists the configured catalog.
- Series and models that are not in the current snapshot (for example prices when they are switched off) are not offered.

## Caching

Data responses carry `Cache-Control: no-cache` and an `ETag` that changes with every new snapshot, every image with changed code or dependencies, and the price setting; a restart of the same image keeps it (see [Configuration](configuration.md)). A request with a matching `If-None-Match` header gets `304 Not Modified` without running a query, so a dashboard that reloads within the hour transfers almost nothing.

The API also keeps the bodies of large responses in memory: every body of at least 1 MiB (`CACHE_MIN_BYTES`, about eight weeks of all series hourly), up to 32 MiB in total (`CACHE_BYTES` in `src/gridload/api/limits.py`), with the least recently used body evicted first. A repeated request with the same path and query is answered from memory without a query and without waiting for one of the two slots for large requests; it still counts against the 100 MB of responses waiting to be sent. A new snapshot empties the cache. Under `make loadtest` (10 clients for 60 seconds, same snapshot) this brought one year of all series hourly from 3344 ms to 156 ms in the median and from 4247 ms to 236 ms at the 95th percentile, with the API container at a peak of 124 MiB of its 512 MiB.
