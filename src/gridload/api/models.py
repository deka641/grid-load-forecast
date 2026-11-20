"""Response models of the HTTP API.

Every timestamp is an ISO 8601 string with the Europe/Berlin offset of that
instant (``+01:00`` or ``+02:00``). Energy values are MWh per interval: per
quarter hour for quarter-hour data, per hour for hourly data and forecasts.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Resolution(StrEnum):
    HOUR = "hour"
    QUARTERHOUR = "quarterhour"


class Health(BaseModel):
    """Liveness response."""

    status: str
    version: str


class ErrorResponse(BaseModel):
    """Error body for 404 and 503 responses."""

    detail: str


class SeriesInfo(BaseModel):
    key: str
    label: str
    unit: str
    category: str


class SeriesCatalog(BaseModel):
    series: list[SeriesInfo]


class Point(BaseModel):
    ts: datetime = Field(description="Start of the interval, Europe/Berlin offset")
    value: float


class SeriesValues(BaseModel):
    key: str
    label: str
    unit: str
    points: list[Point]


class Actuals(BaseModel):
    resolution: Resolution
    start: date = Field(description="First local day (Europe/Berlin), inclusive")
    end: date = Field(description="Last local day (Europe/Berlin), inclusive")
    series: list[SeriesValues]


class ForecastPoint(BaseModel):
    ts: datetime = Field(description="Start of the delivery hour, Europe/Berlin offset")
    lightgbm: float | None
    baseline_week: float | None
    tso: float | None
    actual: float | None


DEGRADED = (
    "Whether the forecast was issued with incomplete inputs: true if any check in "
    "degraded_checks failed, false if all passed, null if the run was not assessed "
    "(baseline runs and runs stored before the checks existed)"
)
DEGRADED_CHECKS = (
    "Failed input checks of a degraded forecast: cutoff_hour_missing (the newest usable hour "
    "was missing at issue time), lag_1d, lag_2d, lag_7d, lag_14d (load of the same "
    "hour that many days earlier missing), mean_24h, mean_168h (mean load of the last "
    "24 or 168 hours not computable), coverage_168h (too few of the last 168 hours "
    "present); empty if none failed, null if the run was not assessed"
)


class ForecastRunInfo(BaseModel):
    run_id: str
    issued_at: datetime
    created_at: datetime
    degraded: bool | None = Field(default=None, description=DEGRADED)
    degraded_checks: list[str] | None = Field(default=None, description=DEGRADED_CHECKS)


class ForecastDay(BaseModel):
    delivery_day: date = Field(description="Local delivery day (Europe/Berlin)")
    unit: str = Field(description="Unit of all values: energy per hour")
    runs: dict[str, ForecastRunInfo] = Field(
        description="Run used per forecast model (lightgbm, baseline_week); missing if none"
    )
    points: list[ForecastPoint] = Field(
        description="One point per delivery hour: 23, 24 or 25 on clock-change days"
    )


class ForecastDays(BaseModel):
    days: list[date] = Field(description="Delivery days with a forecast, newest first")
    limit: int


class BacktestMetric(BaseModel):
    scope_value: str
    mae: float = Field(description="Mean absolute error in MWh per hour")
    mape: float = Field(description="Mean absolute percentage error in percent")
    n: int = Field(description="Number of evaluated hours")


class BacktestMetrics(BaseModel):
    run_id: str
    created_at: datetime
    first_day: date
    last_day: date
    config: dict[str, Any]
    metrics: dict[str, dict[str, list[BacktestMetric]]] = Field(
        description="Metrics by model, then by scope (overall, hour, day_type, overall_tso_hours)"
    )


class SeriesFreshness(BaseModel):
    key: str
    label: str
    last_ts: datetime | None = Field(description="Start of the newest quarter hour")
    age_seconds: float | None = Field(
        description="Seconds from the end of the newest quarter hour to now; "
        "negative for forecast series that extend into the future"
    )


class LoadRunInfo(BaseModel):
    run_id: str
    mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None


class LastForecast(BaseModel):
    model: str
    delivery_day: date
    issued_at: datetime
    degraded: bool | None = Field(default=None, description=DEGRADED)
    degraded_checks: list[str] | None = Field(default=None, description=DEGRADED_CHECKS)


class Status(BaseModel):
    initialised: bool = Field(description="Whether a data snapshot is available")
    generated_at: datetime
    snapshot_created_at: datetime | None
    snapshot_error: str | None = Field(
        default=None,
        description="Why the newest snapshot file cannot be opened or read; null if it can",
    )
    schema_version: int | None
    series: list[SeriesFreshness]
    last_load_run: LoadRunInfo | None
    last_forecast: LastForecast | None
    attribution: str
    licence: str
    licence_url: str
    price_note: str
