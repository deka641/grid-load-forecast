-- Reference list of ingested series.
CREATE TABLE series (
    series_key   VARCHAR PRIMARY KEY,
    filter_id    INTEGER NOT NULL,
    region       VARCHAR NOT NULL,
    label        VARCHAR NOT NULL,
    unit         VARCHAR NOT NULL,
    category     VARCHAR NOT NULL,
    aggregation  VARCHAR NOT NULL CHECK (aggregation IN ('sum', 'mean'))
);

-- Verbatim source responses, one row per series and weekly chunk (latest version).
CREATE TABLE raw_chunks (
    series_key   VARCHAR NOT NULL,
    resolution   VARCHAR NOT NULL,
    chunk_ts     TIMESTAMPTZ NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    sha256       VARCHAR NOT NULL,
    payload      BLOB NOT NULL,          -- zlib-compressed JSON
    PRIMARY KEY (series_key, resolution, chunk_ts)
);

-- Cleaned values in UTC. Corrections overwrite and increase the revision.
CREATE TABLE observations (
    series_key   VARCHAR NOT NULL,
    resolution   VARCHAR NOT NULL,
    ts_utc       TIMESTAMPTZ NOT NULL,
    value        DOUBLE NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    revision     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (series_key, resolution, ts_utc)
);

CREATE TABLE load_runs (
    run_id       VARCHAR PRIMARY KEY,
    mode         VARCHAR NOT NULL,       -- backfill | incremental
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ,
    status       VARCHAR NOT NULL        -- running | ok | failed
);

CREATE TABLE load_log (
    run_id       VARCHAR NOT NULL,
    series_key   VARCHAR NOT NULL,
    chunk_ts     TIMESTAMPTZ,            -- NULL for index-level errors
    status       VARCHAR NOT NULL,       -- loaded | unchanged | error
    rows_written INTEGER NOT NULL DEFAULT 0,
    message      VARCHAR,
    logged_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE forecast_runs (
    run_id       VARCHAR PRIMARY KEY,
    model        VARCHAR NOT NULL,
    issued_at    TIMESTAMPTZ NOT NULL,   -- forecast origin
    delivery_day DATE NOT NULL,          -- local delivery day (Europe/Berlin)
    created_at   TIMESTAMPTZ NOT NULL,
    details      JSON
);

CREATE TABLE forecasts (
    run_id        VARCHAR NOT NULL,
    target_ts_utc TIMESTAMPTZ NOT NULL,
    value         DOUBLE NOT NULL,
    PRIMARY KEY (run_id, target_ts_utc)
);

CREATE TABLE backtest_runs (
    run_id       VARCHAR PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL,
    first_day    DATE NOT NULL,
    last_day     DATE NOT NULL,
    config       JSON NOT NULL
);

CREATE TABLE backtest_metrics (
    run_id       VARCHAR NOT NULL,
    model        VARCHAR NOT NULL,
    scope        VARCHAR NOT NULL,       -- overall | hour | day_type
    scope_value  VARCHAR NOT NULL,
    mae          DOUBLE NOT NULL,
    mape         DOUBLE NOT NULL,
    n            INTEGER NOT NULL,
    PRIMARY KEY (run_id, model, scope, scope_value)
);

-- Hourly values derived from complete hours only (four quarter hours).
CREATE VIEW hourly AS
SELECT
    o.series_key,
    time_bucket(INTERVAL 1 HOUR, o.ts_utc) AS ts_utc,
    CASE s.aggregation WHEN 'sum' THEN sum(o.value) ELSE avg(o.value) END AS value
FROM observations o
JOIN series s USING (series_key)
WHERE o.resolution = 'quarterhour'
GROUP BY o.series_key, s.aggregation, time_bucket(INTERVAL 1 HOUR, o.ts_utc)
HAVING count(*) = 4;
