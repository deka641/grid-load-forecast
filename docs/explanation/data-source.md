# Data source

All data comes from [SMARD.de](https://www.smard.de), the electricity market data platform of the Bundesnetzagentur. The pipeline reads the chart data files behind the SMARD website. They are not a documented API, so every response is validated strictly, and the [download centre](https://www.smard.de/home/downloadcenter/download-marktdaten) remains the fallback if the format changes.

## Series

The pipeline loads 15 series for Germany in quarter-hour resolution: grid load (filter 410), the SMARD grid load forecast (411), twelve generation sources (1223 to 1228 and 4066 to 4071) and the day-ahead price for the bidding zone Germany/Luxembourg (4169). The full list with keys is in the [data model](../reference/data-model.md).

Load and generation values are energies in MWh per quarter hour, available from 29 December 2014 (local time). Prices are in EUR/MWh from 1 October 2018. Before the switch of the day-ahead market to 15-minute products in the week of 29 September 2025, SMARD repeats each hourly price for its four quarter hours, so one quarter-hour series covers both periods. Hourly values are derived from complete hours only: the sum of four quarter hours for energies and their mean for prices. SMARD rounds every published value to two decimals, so a derived hourly energy can differ from SMARD's own hourly value by up to 0.02 MWh, which the tests allow for.

## Weekly chunks

SMARD splits each series into weekly files that start on Monday 00:00 Europe/Berlin. An index file lists the start of every chunk. A regular week has 672 quarter hours, the week of the spring clock change 668 and the autumn week 676. The newest chunk ends with `null` values for points in the future.

## Validation rules

A chunk is stored only if all of these hold:

- the response is valid JSON with a non-empty series;
- the series starts at the chunk timestamp and advances in exact 15-minute steps;
- it covers the whole local week;
- `null` appears only as a trailing run, and only in the newest chunk or in a chunk that ended less than six hours ago;
- values are finite numbers, and energies are not negative (negative and extreme prices are valid).

A response that breaks a rule, an empty or truncated response, and any HTTP error are written to the load log, and nothing is stored for that chunk. The next run tries again. Missing data is never stored as "no data".

## Corrections and late values

Every run reloads the four newest weekly chunks and every stored chunk that is still incomplete. A changed value overwrites the stored one and increases its revision counter; there are never two rows for the same timestamp. A value that SMARD no longer publishes is removed. Corrections to older weeks are only picked up on request (`gridload ingest --refetch-weeks N`).

Requests are throttled to two per second, retried with exponential backoff on timeouts and overload responses (honouring `Retry-After`), and a run stops after five consecutive transient failures. The client identifies itself as `grid-load-forecast/0.1` and sends no personal data.

## Licence and attribution

SMARD data is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) with the attribution "Bundesnetzagentur | SMARD.de", which the dashboard shows on every page. The Bundesnetzagentur reviews the licensing of wholesale electricity prices in its HEDWIG procedure; until that review is concluded, prices are shown with that reservation and can be switched off. Details are in [DATA-LICENSE.md](https://github.com/deka641/grid-load-forecast/blob/master/DATA-LICENSE.md).
