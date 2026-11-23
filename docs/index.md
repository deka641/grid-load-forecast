# grid-load-forecast

This project forecasts the German grid load for the next delivery day from public data published by the Bundesnetzagentur on SMARD.de. It loads the data with a validated, idempotent pipeline, trains a LightGBM model, measures its accuracy with a reproducible backtest against a naive baseline and the forecast SMARD publishes, and shows the result in a dashboard backed by a read-only API.

Where to start:

- To see it running, follow [Run the stack locally](tutorials/run-locally.md).
- To understand how good the forecast is, how its accuracy is measured and what limits the figures have, read [Forecast and backtest method](explanation/forecast.md) and the [backtest report](reference/backtest.md).
- To understand the moving parts, read [Architecture](explanation/architecture.md) and [Data source](explanation/data-source.md).
- For day-to-day tasks, see [Operate the pipeline](how-to/operate.md) and [Develop and verify changes](how-to/develop.md).
- For exact definitions, see the [data model](reference/data-model.md), the [HTTP API](reference/api.md), the [configuration](reference/configuration.md) and the [command line](reference/cli.md).

Data source: Bundesnetzagentur | SMARD.de, licensed under CC BY 4.0.
