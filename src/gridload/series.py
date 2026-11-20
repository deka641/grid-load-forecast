"""Catalog of the SMARD series this project ingests."""

from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    LOAD = "load"
    GENERATION = "generation"
    PRICE = "price"
    FORECAST = "forecast"


class Aggregation(StrEnum):
    """How quarter-hour values combine into an hourly value."""

    SUM = "sum"  # energy per interval (MWh)
    MEAN = "mean"  # price (EUR/MWh)


@dataclass(frozen=True)
class Series:
    key: str
    filter_id: int
    label: str
    unit: str
    category: Category
    aggregation: Aggregation
    region: str = "DE"

    @property
    def non_negative(self) -> bool:
        """Energy series cannot be negative; prices can."""
        return self.aggregation is Aggregation.SUM


def _gen(key: str, filter_id: int, label: str) -> Series:
    return Series(key, filter_id, label, "MWh", Category.GENERATION, Aggregation.SUM)


CATALOG: tuple[Series, ...] = (
    Series("load", 410, "Grid load", "MWh", Category.LOAD, Aggregation.SUM),
    Series(
        "load_forecast_tso",
        411,
        "Grid load forecast (SMARD)",
        "MWh",
        Category.FORECAST,
        Aggregation.SUM,
    ),
    _gen("gen_lignite", 1223, "Lignite"),
    _gen("gen_nuclear", 1224, "Nuclear"),
    _gen("gen_wind_offshore", 1225, "Wind offshore"),
    _gen("gen_hydro", 1226, "Hydropower"),
    _gen("gen_other_conventional", 1227, "Other conventional"),
    _gen("gen_other_renewable", 1228, "Other renewables"),
    _gen("gen_biomass", 4066, "Biomass"),
    _gen("gen_wind_onshore", 4067, "Wind onshore"),
    _gen("gen_solar", 4068, "Photovoltaics"),
    _gen("gen_hard_coal", 4069, "Hard coal"),
    _gen("gen_pumped_storage", 4070, "Pumped storage"),
    _gen("gen_gas", 4071, "Natural gas"),
    Series(
        "price_day_ahead",
        4169,
        "Day-ahead price DE/LU",
        "EUR/MWh",
        Category.PRICE,
        Aggregation.MEAN,
    ),
)

BY_KEY: dict[str, Series] = {s.key: s for s in CATALOG}


def select(keys: list[str] | None = None, *, include_prices: bool = True) -> list[Series]:
    """Return catalog entries, optionally filtered by key and without prices."""
    if keys:
        unknown = sorted(set(keys) - BY_KEY.keys())
        if unknown:
            raise ValueError(f"unknown series: {', '.join(unknown)}")
        chosen = [BY_KEY[k] for k in keys]
    else:
        chosen = list(CATALOG)
    if not include_prices:
        if keys and any(s.category is Category.PRICE for s in chosen):
            raise ValueError("price series requested, but prices are switched off")
        chosen = [s for s in chosen if s.category is not Category.PRICE]
    return chosen


def price_keys() -> list[str]:
    return [s.key for s in CATALOG if s.category is Category.PRICE]
