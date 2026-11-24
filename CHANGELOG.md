# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The project uses [Semantic Versioning](https://semver.org/) in its 0.x form: a minor version marks a new project phase, and patch releases may add features and tighten input validation.

## [Unreleased]

## [0.1.2] - 2026-11-23

### Added

- The backtest report gives, per forecast, the mean bias, the 95th percentile and the largest absolute hourly error and the share of hours that miss by more than 5 %, next to MAE and MAPE. Over the backtest year the model misses by 4,134 MWh or more in one hour in twenty and by more than 5 % in 18.3 % of hours, figures that show the size of the rare large misses that the mean error hides. The README and the forecast explanation say how to read these figures and what the forecast is suited for.
- `scripts/backtest.py --last-day YYYY-MM-DD` fixes the end of the backtest window, and the report records a SHA-256 fingerprint of its input data. The committed report can be reproduced with the documented commands, and a rerun shows whether a different result comes from different data.
- The dashboard names delivery days for which no forecast was issued, for example after the pipeline was stopped, instead of skipping them silently in the day selector. The operations guide explains when a delivery day is forecast and that a missed day is not forecast later.
- While the dashboard loads, each section shows placeholder blocks in the layout of its content instead of a single line of text.

### Changed

- The price chart tells a source gap from prices that are not yet due. Hours whose prices SMARD should already have published but has not are shaded lighter, labelled "No price published by SMARD" and named in the summary with the number of hours covered, instead of being shown as "Not yet published" or as an unlabelled hole. The chart shows all published prices up to the end of today, not only the hours for which generation data exists.
- The backtest caveats are stated with their direction: the model is scored with load values as stored today, including later corrections it would not have had at the cutoff, which can favour the model; the SMARD forecast is scored with the values SMARD publishes now, which may favour SMARD. The dashboard note under the backtest table names both.
- The documentation states that regional public holidays are not modelled, so the forecast treats days such as Corpus Christi as ordinary working days.
- The forecast chart marks the peak hour named in the headline, uses whole 5 GW steps on its axis, draws its lines in one after another (not with reduced motion), and shows the hour under the pointer on the axis. The tooltip stays at the top of the chart on the side away from the pointer, so it no longer covers the lines being read. The forecast peak is larger and the chart frames have rounded, raised surfaces.
- Each client address may hold at most half of the API's 100 MiB budget for responses being built or waiting to be sent, cached responses included. One address that requests large responses and does not read them could previously fill the budget and get every other client's data requests refused.
- The pipeline healthcheck no longer imports the forecast stack: one probe took about 0.1 s of CPU and 21 MB instead of 0.65 s and 105 MB, measured on the host.

### Fixed

- A single missing hour inside the price chart is no longer drawn with the price of the hour before.
- The gap before an opening parenthesis or a vertical bar is visible again throughout the dashboard; the display font's narrow word space had swallowed it.
- The documentation of the late forecast issue, the rows of `load_log` and the script table of the CLI reference match the code.

## [0.1.1] - 2026-11-21

### Added

- `/forecasts` and `/status` report whether a forecast was issued with incomplete inputs (`degraded`) and which input checks failed (`degraded_checks`), so a client can mark such a forecast instead of presenting it as complete. Both are `null` for baseline runs and for runs stored before the checks were recorded.
- The OpenAPI description explains `issued_at` (the forecast origin, 12:00 on the day before delivery) and `created_at` (when the pipeline computed the forecast), so a client no longer takes the origin for the time the forecast became available.
- `make prod-check` first runs the browser checks of the production build with fixture data (`make e2e`, also available on its own), which cover the clock-change day, the degraded, stale and busy notices and unpublished prices.
- The dashboard marks a forecast that was computed with incomplete load data and names the missing inputs in plain words, so a reader knows it may be less accurate than the backtest suggests.
- Next to the forecast peak, the dashboard shows the backtest MAPE of the model, the baseline and the SMARD forecast, so the peak can be read against how accurate each forecast has been. The values are the same as in the backtest table further down. They sit beside the peak wherever there is room, which keeps the forecast chart above the fold at 1024 px window width, and their place is kept while the backtest loads, so the chart does not move down when they arrive.
- The dashboard has a favicon, a page description and a browser bar colour for the light and the dark theme.

### Changed

- A trailing slash on an API path, such as `/api/v1/series/`, now returns 404 instead of a redirect.
- Busy 503 responses of the API carry `Retry-After: 1`, so clients know when to retry.
- The forecast peak and its hour are the largest text on the dashboard, set in the colour of the model line, with the sentence that introduces them smaller above; the section is named by a small label instead of repeating the page title. Headings no longer leave a single word such as the hour alone on a line. On wide screens the page is centred instead of leaving an empty band on the right.
- Old data is announced in a band across the page instead of only in a small note in the header; the time of the data is then named only in that band. Before the first snapshot, the page shows the forecast section with the first-start text and an empty chart frame instead of a single line. Without a freshness line, the theme switch lines up with the page title.
- Buttons, the day list and the theme switch have borders with at least 3:1 contrast in both themes, so they read as controls. The dark theme uses the same contrast order as the light one for muted text and the baseline and SMARD lines.
- The forecast chart has calmer grid lines, the forecast lines draw in once when the page opens (not with reduced motion), and the tooltips stand off the chart with their own background, a visible border and a shadow.
- The price chart shades the hours for which SMARD has not yet published prices and names the last published hour below the chart, so its early end no longer looks like a rendering fault. When prices turn negative, the zero line is drawn darker than the other grid lines. The price summary states once that each hourly price is the mean of four quarter hours.
- With only one forecast day, the dashboard names the day as plain text instead of showing two buttons that can never be used.
- While the dashboard loads, each section keeps about the height of its charts free, so the page no longer jumps when the data arrives. In every error notice the Retry button sits below the message.
- Opened data tables keep their column names in view while they scroll. The backtest table keeps a readable width when it is stacked under the text, and its box is a keyboard stop only when it actually scrolls sideways.
- The web server compresses responses with gzip only. Under `make loadtest` the zstd encoders took the web container past its 128 MiB memory limit, while gzip peaked at 56 MiB, so a client asking for zstd could exhaust that limit with parallel large downloads.
- Repeated large data requests, such as one year of all series hourly, are answered from an in-memory cache of at most 32 MiB until the next snapshot, so they no longer wait for one of the two slots for large requests. Under `make loadtest` the median of that request dropped from 3344 ms to 156 ms.
- The pipeline and API image installs its Debian packages at exact versions, so a package changes only with a commit and two builds of the same commit get the same packages. When Debian replaces a pinned version, the build fails until the pin is updated (see the development guide).
- The pipeline and API image runs on Python 3.15 instead of 3.14. The backtest in this image reproduces `reports/backtest.json`, `reports/backtest.md` and the scored rows byte for byte. Local development still works with Python 3.14.

### Fixed

- When several sections fail for the same reason, such as an unreadable snapshot, the dashboard shows one error notice with one Retry that reloads all of them, instead of one identical notice per section. Each section it covers points to it in one quiet line instead of ending at its title.
- On phone-width screens, the forecast chart on clock-change days keeps its 00:00 and 06:00 labels (it labels every sixth hour there), the clock-change label no longer runs into the GW unit, and the "Not yet published" label of the price chart sits above the chart instead of over the prices. Between 761 and 900 px window width, the backtest table uses short row names, so "Sundays and holidays" no longer wraps to three lines.
- Response cache and ETag are keyed by the validated parameters, so unknown parameters or a different parameter order no longer bypass the cache or evict its entries.
- On the 25-hour day of the autumn clock change, the dashboard names the two 02:00 hours with their UTC offset in the headline, the chart and the clock-change note, and a dashed line marks the change in the chart. The note sits with the legend directly above the chart and repeats the dashed line as its key. The time axis keeps its three-hour rhythm instead of showing two identical 02:00 labels.
- Between 641 and 760 px window width, the backtest table no longer needs sideways scrolling, and the page itself no longer scrolls sideways by a few pixels at 641 to 644 px.
- Hourly values in the API are exact: `43648.21` instead of `43648.21000000001`. Two snapshots of the same data now give the same values to the last digit, so a client comparing them sees no spurious changes. Forecast inputs and backtest results are unchanged.
- Restarting the API container no longer invalidates the ETags that clients hold. The image build stores a hash of the code and the locked dependencies as the build id, so only an image with changed code makes clients download data again.
- Values that SMARD withdraws from a recent week of a series other than the grid load no longer fail the run and, after 26 hours, mark the pipeline container unhealthy while load data and forecasts are fine. The run is now `partial` and the error is still shown in `gridload status`; withdrawals in the grid load still fail the run.
- Publishing the snapshot removes files left by an earlier crashed or failed publish, writes the snapshot completely into its file before it replaces `serving.duckdb`, and leaves no temporary files behind when it fails.

### Security

- Responses that the web server answers itself, such as a missing asset, a refused method or a 502 while the API is down, carry the same security headers as all other responses and no longer name the server.
- One client can no longer crowd out others: the API allows each client address at most 16 data requests in progress, out of 64 in total, and answers further ones with the usual JSON 503. With `scripts/loadtest_abuse.py` on the `make prod-check` stack (21 November 2026), while one container held 100 parallel connections to `/forecasts/days` for 10 seconds, a second container received 200 for all 212 of its requests in two runs (see SECURITY.md). Locally all browsers on the host count as one client; behind another proxy all clients share one limit unless that proxy is trusted (see SECURITY.md).
- The API takes the client address from `X-Forwarded-For` only on connections from the web container, at a fixed address on an internal network. A client cannot choose the address it is counted under by sending that header, and the API logs show the client address instead of Caddy's.
- The API container has no network access beyond the web container: it no longer reaches the internet, and the pipeline and other containers no longer reach it.
- One client can no longer take down or block the stack with connections: Caddy closes connections without a complete request header after 10 seconds, holds at most about 4,000 at a time, and keeps its connections to the API below uvicorn's limit, so `/health` and `/status` keep answering under 400 connections from one client. The web container has 256 MiB instead of 128 MiB, enough for 9,000 half-open connections from one client, which `make prod-check` now checks.
- One client address holds at most two of the eight query slots, so a client keeping many medium-sized requests in flight no longer slows everyone else down: with `scripts/loadtest_abuse.py` on the `make prod-check` stack, another client's forecast took 80 ms in the median instead of 2,062 ms (11 ms without the load).
- All containers have CPU limits, and every pipeline run is stopped after 3 hours, so a source that answers too slowly cannot stall the hourly updates.

## [0.1.0] - 2026-11-20

### Added

- Data pipeline for 15 SMARD series (grid load, SMARD load forecast, twelve generation sources, day-ahead price): strict validation of every weekly chunk, quarter-hour storage in UTC with corrections as revisions, permanent source gaps kept as gaps and logged, hourly scheduled runs, atomic read-only snapshot.
- Day-ahead forecast of the hourly grid load with LightGBM, with 12:00 Europe/Berlin as origin and only hours that ended by 10:00, a same-hour-last-week baseline, and forecasts marked as degraded when key inputs are missing.
- Rolling-origin backtest over the last 365 days with leak tests and a scikit-learn cross-check; report in `reports/backtest.md`.
- Read-only HTTP API (FastAPI) with validated parameters and range limits, load shedding that keeps the health check available, and ETag revalidation; documented in `openapi.json`.
- Dashboard (React, TypeScript): forecast against actual load, baseline and SMARD forecast, backtest accuracy, generation mix and prices of the past week, data freshness and attribution, light and dark theme, mobile layout.
- Docker Compose stack with Caddy, healthchecks, memory limits, log rotation and non-root containers; local quality gates `make verify-fast` and `make verify` with style and secret scans; production-like check with image scan, and load test; seven-day wait period for new dependency versions.
- Documentation: tutorial, how-to guides, reference and explanation.

[Unreleased]: https://github.com/deka641/grid-load-forecast/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/deka641/grid-load-forecast/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/deka641/grid-load-forecast/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/deka641/grid-load-forecast/releases/tag/v0.1.0
