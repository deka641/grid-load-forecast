# Data source

All data comes from [SMARD.de](https://www.smard.de), the electricity market data platform of the Bundesnetzagentur. The pipeline reads the chart data files behind the SMARD website. They are not a documented API, so every response is validated strictly, and the [download centre](https://www.smard.de/home/downloadcenter/download-marktdaten) remains the fallback if the format changes.

## Series

The pipeline loads 15 series for Germany in quarter-hour resolution: grid load (filter 410), the SMARD grid load forecast (411), twelve generation sources (1223 to 1228 and 4066 to 4071) and the day-ahead price for the bidding zone Germany/Luxembourg (4169). The full list with keys is in the [data model](../reference/data-model.md).

Load and generation values are energies in MWh per quarter hour, available from 1 January 2015 (the first weekly chunk starts on 29 December 2014 with three days without values). Prices are in EUR/MWh from 1 October 2018. Up to and including 30 September 2025, SMARD repeats each hourly price for its four quarter hours; from 1 October 2025 (00:00 local time) the series holds a separate price per quarter hour, after the day-ahead market switched to 15-minute products. One quarter-hour series therefore covers both periods. Hourly values are derived from complete hours only: the sum of four quarter hours for energies and their mean for prices. SMARD rounds every published value to two decimals, so a derived hourly energy can differ from SMARD's own hourly value by up to 0.02 MWh, which the tests allow for.

## Weekly chunks

SMARD splits each series into weekly files that start on Monday 00:00 Europe/Berlin. An index file lists the start of every chunk. A regular week has 672 quarter hours, the week of the spring clock change 668 and the autumn week 676. The newest chunk ends with `null` values for points in the future.

## Validation rules

A chunk is stored only if all of these hold:

- the response is valid JSON with a non-empty series;
- the series starts at the chunk timestamp and advances in exact 15-minute steps;
- it covers the whole local week;
- values are finite numbers, and energies are not negative (negative and extreme prices are valid);
- a completed week contains at least one value.

A response that breaks a rule, an empty or truncated response, and any HTTP error are written to the load log, and nothing is stored for that chunk. The next run tries again.

`null` marks a quarter hour that SMARD does not publish. At the end of the newest chunk (or of a week that ended less than six hours ago) these values are simply not published yet. In older weeks they are permanent gaps in the source: the full backfill on 21 November 2026 found 20 such weeks across all series. Fourteen of them are the first week, where every series that already exists then lacks its first three days (values start on 1 January 2015). The other six are short gaps of one or two days in other renewables (November 2016), pumped storage (three weeks in 2018, one of them with a gap of two days) and the SMARD grid load forecast (January 2020), and the end of the nuclear series on 30 January 2024. The pipeline stores the values around a gap, never stores a missing quarter hour as a value, and records the number of missing quarter hours and the first of them in the load log. Hourly values are only derived from complete hours, so a gap never produces a partial hourly value. A gap in a week that ended within the last seven days makes the run `partial`, so it shows up in the status.

## Corrections and late values

Every run reloads the four newest weekly chunks and every chunk that was stored while its week was still open, however old it is, so late values are filled in even after a long downtime. A chunk fetched more than six hours after its week ended is final. A changed value overwrites the stored one and increases its revision counter; there are never two rows for the same timestamp. In an open week, SMARD may still withdraw the newest values: a short run of at most eight quarter hours at the end, newer than the newest value still published, is removed. Any other withdrawal, including a response without any value, is treated as a degraded response: the stored values are kept, newly published values are still taken over, and the chunk is reported as an error until SMARD serves it complete again or the withdrawal is accepted explicitly. In a completed week, stored values that turn missing are more likely a degraded response than a correction: they are kept and the chunk is reported as an error, unless the withdrawal is accepted explicitly (`gridload ingest --accept-withdrawals`). Such a withdrawal in the open week or in a week that ended within the last seven days makes the run `failed` only for the grid load, the series the forecast is trained on; for every other series the stored values are intact, so the run is `partial` and the error stays visible in the status. Corrections to older weeks are only picked up on request (`gridload ingest --refetch-weeks N`).

Requests are throttled to two per second, retried with exponential backoff on timeouts and overload responses (honouring `Retry-After`), and a run stops after five consecutive transient failures. The client identifies itself as `grid-load-forecast/0.1` and sends no personal data.

## Licence and attribution

SMARD data is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) with the attribution "Bundesnetzagentur | SMARD.de", which the dashboard shows on every page. The Bundesnetzagentur reviews the licensing of wholesale electricity prices in its HEDWIG procedure; until that review is concluded, prices are shown with that reservation and can be switched off. Details are in [DATA-LICENSE.md](https://github.com/deka641/grid-load-forecast/blob/master/DATA-LICENSE.md).
