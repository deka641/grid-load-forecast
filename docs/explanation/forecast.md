# Forecast and backtest method

This page explains what the forecast predicts, which information it may use, and how its accuracy is measured. The measured values themselves are in the generated [backtest report](../reference/backtest.md).

## What is forecast

The target is the German grid load as SMARD publishes it (filter 410), for every hour of the next local delivery day in Europe/Berlin. SMARD's article on [electricity consumption](https://www.smard.de/page/en/wiki-article/6078/6036/electricity-consumption) describes it as the electrical energy taken from the grid and notes that it is also called grid load; this project takes the series as published and adds no definition of its own. A delivery day has 24 hours, or 23 and 25 hours on the days of the clock changes. The forecast origin is 12:00 local time on the day before, the gate closure of the day-ahead electricity market, rather than "the next 24 hours". The forecast uses exactly the information available at the origin, but it is computed by the first hourly run after it that finds the load up to 10:00 loaded (at 12:05 at the earliest in the hourly schedule; if those values are still missing at 20:00, the forecast is issued anyway and marked as degraded, unless no load value of the 24 hours before the cutoff is available), so it is an evaluation of what a forecast at gate closure could achieve, not a forecast submitted to the auction.

Every stored forecast carries two times. `issued_at` is the origin, 12:00 on the day before delivery, and does not depend on when the forecast was computed. `created_at` is when the pipeline actually computed it, for example 12:05, or 20:05 for a forecast that waited for missing data. A client that needs to know when a forecast could first be seen should read `created_at`, not `issued_at`; the forecast reaches the API only with the snapshot the same run publishes afterwards.

## Which information it may use

SMARD publishes load values with a delay of one to two hours. A feature may therefore only use hours that ended at least two hours before the origin: for a forecast issued at 12:00, the newest usable hour is 09:00 to 10:00. Hourly values are stamped with the start of their hour, and the code treats a value as known only once its hour has ended. This rule is enforced in one place (`History` in `src/gridload/forecast/features.py`), and every history-based feature goes through it.

The features per target hour are:

- calendar: local hour, weekday, month, day of year, day type, nationwide public holidays (package `holidays`), bridge days, and the number of hours of the delivery day. Public holidays of single states are not modelled: a day such as Corpus Christi, a holiday in six states, is treated as an ordinary workday;
- lagged load at the same local hour 1, 2, 7 and 14 days earlier (the 1-day lag exists only for hours that ended before the cutoff, so it is empty from 10:00 onward);
- the mean load of the last 24 and 168 hours before the cutoff, and the newest known hourly value.

The model uses no weather data: no temperature, wind or solar input. Weather-driven changes in the load reach it only through the lagged load values, so a change in the weather between the cutoff and the delivery day is not anticipated.

"Same local hour" follows the wall clock. If that hour does not exist (02:00 on the spring clock-change day), the previous hour is used; if it exists twice (02:00 on the autumn day), the first occurrence (summer time) is used.

## Model and baseline

The model is LightGBM with an L1 objective, 600 boosting rounds and a fixed seed (parameters in `src/gridload/forecast/model.py`). It is trained on every hour with a measured value up to the end of the day before the origin day. In production it is retrained for every forecast.

The baseline repeats the load of the same local hour seven days earlier. It is simple, needs no training and captures the weekly pattern, which makes it the reference any model has to beat.

The third line is the grid load forecast that SMARD publishes (filter 411). It is a useful outside reference, with one limitation: SMARD does not state when each value was published or whether it was revised later, so scoring it on today's values may favour it, while the model's inputs include later load corrections, which favours the model; neither effect is quantified.

## Backtest

`scripts/backtest.py` replays the production procedure for each of the 365 days ending with `--last-day`, or with the last complete day of load data if that option is not given. For each delivery day it issues the forecast as of 12:00 on the previous day. The model is retrained every seven days on all hours up to the end of the day before the origin day (an expanding window with a hard cutoff). LightGBM runs deterministically with a fixed seed, so the same data gives the same report. The report records a SHA-256 fingerprint of the hourly load and SMARD forecast values up to the last delivery day, so a rerun shows whether it used the same data.

The errors are measured against the actual hourly load:

- MAE, the mean absolute error in MWh per hour (equal to MW on average);
- MAPE, the mean absolute percentage error;
- the bias (mean of forecast minus actual), the 95th percentile and the largest value of the absolute error, and the share of hours with an absolute percentage error above 5 %.

The model and the baseline are scored on every hour that has an actual value and both predictions. The SMARD forecast is scored on the subset of those hours where SMARD publishes a value, and on that subset the two other forecasts are scored again, so all three can be compared on equal terms. Results are reported overall, per local hour and per day type (workdays, Saturdays, Sundays and holidays).

Two checks guard the method. A leak test corrupts every value that was not yet known at an origin and confirms that features and forecasts stay identical, for the production path and for the backtest over several origins with retraining. A cross-check recomputes every reported MAE and MAPE with scikit-learn, and the bias, 95th percentile, largest error and share of hours above 5 % with the Python standard library, in a separate environment (`scripts/crosscheck_metrics.py`).

The replay uses the load values as they are stored today, including corrections SMARD made after the original publication; the warehouse keeps the newest value and a revision counter, not earlier versions. The leak test covers values not yet published at an origin, not later corrections. Two effects therefore point in opposite directions. The model's inputs include corrections that a forecast at 12:00 on the day before would not have had; this favours the model. The SMARD forecast is scored with the values SMARD publishes now, without its publication time; this may favour SMARD. Neither effect is quantified, so the gap between the model and SMARD in the report is an estimate, not a measured advantage.

### How to read these figures

The figures in this section are from the committed [backtest report](../reference/backtest.md) (delivery days 2025-11-20 to 2026-11-19). On average the model misses an hour by about 1.6 GW (MAE 1,611 MWh per hour); in 5 % of the hours the miss is 4.1 GW or more, the largest was 11.3 GW, and 18.3 % of the hours miss by more than 5 % of the load. The bias is small: +35 MWh per hour, so the forecast is slightly too high on average. The misses are larger at midday (MAPE 3.32 % to 4.14 % from 11:00 to 16:00, against 2.35 % to 3.26 % in the other hours) and on Sundays and holidays (3.50 %, against 2.91 % on workdays and 3.16 % on Saturdays). Two inputs are missing by design: public holidays of single states are not modelled, so such days are forecast as ordinary workdays, and the model has no weather input, so a change in the weather after the cutoff is not anticipated. The forecast is computed at 12:05 at the earliest, after the day-ahead auction has closed at 12:00, so it cannot inform bids in that auction; it shows what a forecast at gate closure could achieve. Its use is as an independent reference built from calendar and load history only: to check one's own load forecast or the SMARD grid load forecast, and to show in which hours and day types its error is largest, which is where a weather-based model would have to improve on it.

Only values produced by `scripts/backtest.py` are quoted in this documentation and in the README. The dashboard shows the backtest that the pipeline recomputes every week, so its period and values move on from the committed report over time.
