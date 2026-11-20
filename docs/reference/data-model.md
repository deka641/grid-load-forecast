# Data model

The warehouse (`warehouse.duckdb`) is created by versioned SQL migrations in `src/gridload/pipeline/migrations/`, recorded in `schema_version`. All timestamps are `TIMESTAMPTZ` in UTC; conversion to Europe/Berlin happens only in API responses and the dashboard.

## Tables

| Table | Content | Key |
|---|---|---|
| `series` | Catalog of ingested series: filter id, region, label, unit, category, aggregation (`sum` or `mean`) | `series_key` |
| `raw_chunks` | Latest verbatim response per weekly chunk (zlib-compressed JSON), time of the last fetch (also for unchanged responses) and SHA-256 | `series_key, resolution, chunk_ts` |
| `observations` | Cleaned quarter-hour values with `updated_at` and a `revision` counter that increases on every correction | `series_key, resolution, ts_utc` |
| `load_runs` | One row per pipeline run: mode, start, end, status (`running`, `ok`, `partial`, `failed`, `aborted`) | `run_id` |
| `load_log` | One row per chunk and run: status (`loaded`, `unchanged`, `error`, plus `warning` for a gap in a week that ended within seven days), values written, message (source gaps, removed values, error text) | none |
| `forecast_runs` | One row per issued forecast and model: origin (`issued_at`), local delivery day, details as JSON (cutoff, training rows, parameter hash, `degraded`) | `run_id` |
| `forecasts` | Hourly forecast values in MWh per hour | `run_id, target_ts_utc` |
| `backtest_runs` | One row per stored backtest: evaluation period and configuration as JSON | `run_id` |
| `backtest_metrics` | MAE (MWh per hour), MAPE (percent) and number of scored hours per model and scope | `run_id, model, scope, scope_value` |

The view `hourly` derives hourly values from complete hours (four quarter hours): the sum for energies, the mean for prices.

## Series keys

| Key | SMARD filter | Category | Unit |
|---|---:|---|---|
| `load` | 410 | load | MWh |
| `load_forecast_tso` | 411 | forecast | MWh |
| `gen_lignite` | 1223 | generation | MWh |
| `gen_nuclear` | 1224 | generation | MWh |
| `gen_wind_offshore` | 1225 | generation | MWh |
| `gen_hydro` | 1226 | generation | MWh |
| `gen_other_conventional` | 1227 | generation | MWh |
| `gen_other_renewable` | 1228 | generation | MWh |
| `gen_biomass` | 4066 | generation | MWh |
| `gen_wind_onshore` | 4067 | generation | MWh |
| `gen_solar` | 4068 | generation | MWh |
| `gen_hard_coal` | 4069 | generation | MWh |
| `gen_pumped_storage` | 4070 | generation | MWh |
| `gen_gas` | 4071 | generation | MWh |
| `price_day_ahead` | 4169 | price | EUR/MWh |

The nuclear series ends in January 2024.

## Forecast models and backtest scopes

Models: `lightgbm`, `baseline_week` (same local hour seven days earlier) and, in backtest metrics only, `tso` (the SMARD forecast, series `load_forecast_tso`).

Scopes in `backtest_metrics`:

| Scope | Values | Meaning |
|---|---|---|
| `overall` | `all` | All scored hours of the model |
| `hour` | `0` to `23` | Local hour; `2` includes both 02:00 hours of the autumn clock-change day |
| `day_type` | `workday`, `saturday`, `sunday_holiday` | Day type of the delivery day (nationwide holidays count as Sundays) |
| `overall_tso_hours` | `all` | LightGBM and baseline on the hours that also have a SMARD forecast |

## Serving snapshot

`serving.duckdb` contains `schema_version`, `series`, `load_runs`, `forecast_runs`, `forecasts`, `backtest_runs`, `backtest_metrics`, the quarter-hour `observations` (without revision columns), `hourly` as a table, and the `load_log` of the last 30 runs. Price series are left out entirely when prices are switched off.
