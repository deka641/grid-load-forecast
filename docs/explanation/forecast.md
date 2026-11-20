# Forecast and backtest method

This page explains what the forecast predicts, which information it may use, and how its accuracy is measured. The measured values themselves are in the generated [backtest report](../reference/backtest.md).

## What is forecast

The target is the German grid load (SMARD filter 410) for every hour of the next local delivery day in Europe/Berlin. A delivery day has 24 hours, or 23 and 25 hours on the days of the clock changes. The forecast origin is 12:00 local time on the day before, the gate closure of the day-ahead electricity market, rather than "the next 24 hours". The forecast uses exactly the information available at the origin, but it is computed by the first hourly run after it (at 12:05 at the earliest, once the data up to 10:00 is loaded), so it is an evaluation of what a forecast at gate closure could achieve, not a forecast submitted to the auction. The field `issued_at` holds the origin.

## Which information it may use

SMARD publishes load values with a delay of one to two hours. A feature may therefore only use hours that ended at least two hours before the origin: for a forecast issued at 12:00, the newest usable hour is 09:00 to 10:00. Hourly values are stamped with the start of their hour, and the code treats a value as known only once its hour has ended. This rule is enforced in one place (`History` in `src/gridload/forecast/features.py`), and every history-based feature goes through it.

The features per target hour are:

- calendar: local hour, weekday, month, day of year, day type, nationwide public holidays (package `holidays`), bridge days, and the number of hours of the delivery day;
- lagged load at the same local hour 1, 2, 7 and 14 days earlier (the 1-day lag exists only for hours that ended before the cutoff, so it is empty from 10:00 onward);
- the mean load of the last 24 and 168 hours before the cutoff, and the newest known hourly value.

"Same local hour" follows the wall clock. If that hour does not exist (02:00 on the spring clock-change day), the previous hour is used; if it exists twice (02:00 on the autumn day), the first occurrence (summer time) is used.

## Model and baseline

The model is LightGBM with an L1 objective, 600 boosting rounds and a fixed seed (parameters in `src/gridload/forecast/model.py`). It is trained on every hour with a measured value up to the end of the day before the origin day. In production it is retrained for every forecast.

The baseline repeats the load of the same local hour seven days earlier. It is simple, needs no training and captures the weekly pattern, which makes it the reference any model has to beat.

The third line is the grid load forecast that SMARD publishes (filter 411). It is a useful outside reference, with one limitation: SMARD does not state when each value was published or whether it was revised later, so the comparison may favour it.

## Backtest

`scripts/backtest.py` replays the production procedure for each of the 365 days ending with the last complete day of load data. For each delivery day it issues the forecast as of 12:00 on the previous day. The model is retrained every seven days on all hours up to the end of the day before the origin day (an expanding window with a hard cutoff). LightGBM runs deterministically with a fixed seed, so the same data gives the same report.

The errors are measured against the actual hourly load:

- MAE, the mean absolute error in MWh per hour (equal to MW on average);
- MAPE, the mean absolute percentage error.

The model and the baseline are scored on every hour that has an actual value and both predictions. The SMARD forecast is scored on the subset of those hours where SMARD publishes a value, and on that subset the two other forecasts are scored again, so all three can be compared on equal terms. Results are reported overall, per local hour and per day type (workdays, Saturdays, Sundays and holidays).

Two checks guard the method. A leak test corrupts every value that was not yet known at an origin and confirms that features and forecasts stay identical, for the production path and for the backtest over several origins with retraining. A cross-check recomputes every reported metric with scikit-learn in a separate environment (`scripts/crosscheck_metrics.py`).

The replay uses the load values as they are stored today, including corrections SMARD made after the original publication; the warehouse keeps the newest value and a revision counter, not earlier versions. The leak test covers values not yet published at an origin, not later corrections.

Only values produced by `scripts/backtest.py` are quoted in this documentation and in the README. The dashboard shows the backtest that the pipeline recomputes every week, so its period and values move on from the committed report over time.
