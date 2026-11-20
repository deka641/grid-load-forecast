# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The project uses [Semantic Versioning](https://semver.org/) in its 0.x form: a minor version marks a new project phase, and patch releases may add features and tighten input validation.

## [Unreleased]

### Added

- Data pipeline for 15 SMARD series (grid load, SMARD load forecast, twelve generation sources, day-ahead price): strict validation of every weekly chunk, quarter-hour storage in UTC with corrections as revisions, permanent source gaps kept as gaps and logged, hourly scheduled runs, atomic read-only snapshot.
- Day-ahead forecast of the hourly grid load with LightGBM, issued at 12:00 Europe/Berlin from hours that ended by 10:00, with a same-hour-last-week baseline.
- Rolling-origin backtest over the last 365 days with leak tests and a scikit-learn cross-check; report in `reports/backtest.md`.
- Read-only HTTP API (FastAPI) with validated parameters and range limits, documented in `openapi.json`.
- Dashboard (React, TypeScript): forecast against actual load, baseline and SMARD forecast, backtest accuracy, generation mix and prices of the past week, data freshness and attribution, light and dark theme, mobile layout.
- Docker Compose stack with Caddy, healthchecks and memory limits; local quality gate `make verify`; production-like check and load test.
- Documentation: tutorial, how-to guides, reference and explanation.

[Unreleased]: https://github.com/deka641/grid-load-forecast/commits/master
