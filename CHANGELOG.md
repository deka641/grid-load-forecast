# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The project uses [Semantic Versioning](https://semver.org/) in its 0.x form: a minor version marks a new project phase, and patch releases may add features and tighten input validation.

## [Unreleased]

### Added

- `/forecasts` and `/status` report whether a forecast was issued with incomplete inputs (`degraded`) and which input checks failed (`degraded_checks`), so a client can mark such a forecast instead of presenting it as complete. Both are `null` for baseline runs and for runs stored before the checks were recorded.
- The dashboard marks a forecast that was computed with incomplete load data and names the missing inputs in plain words, so a reader knows it may be less accurate than the backtest suggests.

### Changed

- The price chart shades the hours for which SMARD has not yet published prices and names the last published hour below the chart, so its early end no longer looks like a rendering fault. When prices turn negative, the zero line is drawn darker than the other grid lines. The price summary states once that each hourly price is the mean of four quarter hours.
- With only one forecast day, the dashboard names the day as plain text instead of showing two buttons that can never be used.
- While the dashboard loads, each section keeps about the height of its charts free, so the page no longer jumps when the data arrives. In every error notice the Retry button sits below the message.
- Opened data tables keep their column names in view while they scroll. The backtest table keeps a readable width when it is stacked under the text, and its box is a keyboard stop only when it actually scrolls sideways.
- The web server compresses responses with gzip only. Under `make loadtest` the zstd encoders took the web container past its 128 MB memory limit, while gzip peaked at 56 MiB, so a client asking for zstd could exhaust that limit with parallel large downloads.
- Repeated large data requests, such as one year of all series hourly, are answered from an in-memory cache of at most 32 MiB until the next snapshot, so they no longer wait for one of the two slots for large requests. Under `make loadtest` the median of that request dropped from 3344 ms to 156 ms.
- The pipeline and API image runs on Python 3.15 instead of 3.14. The backtest in this image reproduces `reports/backtest.json`, `reports/backtest.md` and the scored rows byte for byte. Local development still works with Python 3.14.

### Fixed

- On the 25-hour day of the autumn clock change, the dashboard names the two 02:00 hours with their UTC offset in the headline, the chart and the clock-change note, and a dashed line marks the change in the chart. The time axis keeps its three-hour rhythm instead of showing two identical 02:00 labels.
- Between 641 and 760 px window width, the backtest table no longer needs sideways scrolling, and the page itself no longer scrolls sideways by a few pixels at 641 to 644 px.
- Hourly values in the API are exact: `43648.21` instead of `43648.21000000001`. Two snapshots of the same data now give the same values to the last digit, so a client comparing them sees no spurious changes. Forecast inputs and backtest results are unchanged.
- Restarting the API container no longer invalidates the ETags that clients hold. The image build stores a hash of the code and the locked dependencies as the build id, so only an image with changed code makes clients download data again.
- Values that SMARD withdraws from a recent week of a series other than the grid load no longer fail the run and, after 26 hours, mark the pipeline container unhealthy while load data and forecasts are fine. The run is now `partial` and the error is still shown in `gridload status`; withdrawals in the grid load still fail the run.
- Publishing the snapshot removes files left by an earlier crashed or failed publish, writes the snapshot completely into its file before it replaces `serving.duckdb`, and leaves no temporary files behind when it fails.

## [0.1.0] - 2026-11-20

### Added

- Data pipeline for 15 SMARD series (grid load, SMARD load forecast, twelve generation sources, day-ahead price): strict validation of every weekly chunk, quarter-hour storage in UTC with corrections as revisions, permanent source gaps kept as gaps and logged, hourly scheduled runs, atomic read-only snapshot.
- Day-ahead forecast of the hourly grid load with LightGBM, with 12:00 Europe/Berlin as origin and only hours that ended by 10:00, a same-hour-last-week baseline, and forecasts marked as degraded when key inputs are missing.
- Rolling-origin backtest over the last 365 days with leak tests and a scikit-learn cross-check; report in `reports/backtest.md`.
- Read-only HTTP API (FastAPI) with validated parameters and range limits, load shedding that keeps the health check available, and ETag revalidation; documented in `openapi.json`.
- Dashboard (React, TypeScript): forecast against actual load, baseline and SMARD forecast, backtest accuracy, generation mix and prices of the past week, data freshness and attribution, light and dark theme, mobile layout.
- Docker Compose stack with Caddy, healthchecks, memory limits, log rotation and non-root containers; local quality gates `make verify-fast` and `make verify` with style and secret scans; production-like check with image scan, and load test; seven-day wait period for new dependency versions.
- Documentation: tutorial, how-to guides, reference and explanation.

[Unreleased]: https://github.com/deka641/grid-load-forecast/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/deka641/grid-load-forecast/releases/tag/v0.1.0
