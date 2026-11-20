from pathlib import Path

import pytest

from gridload.config import DEFAULT_BASE_URL, Settings
from gridload.series import CATALOG, Category, select


def test_defaults() -> None:
    settings = Settings.from_env({})
    assert settings.data_dir == Path("data")
    assert settings.include_prices is True
    assert settings.base_url == DEFAULT_BASE_URL
    assert settings.warehouse_path == Path("data/warehouse.duckdb")
    assert settings.serving_path == Path("data/serving.duckdb")
    assert settings.heartbeat_path == Path("data/heartbeat.json")


@pytest.mark.parametrize(
    ("raw", "expected"), [("0", False), ("off", False), ("YES", True), ("", True)]
)
def test_price_switch(raw: str, expected: bool) -> None:
    settings = Settings.from_env({"GRIDLOAD_INCLUDE_PRICES": raw, "GRIDLOAD_DATA_DIR": "/x"})
    assert settings.include_prices is expected
    assert settings.data_dir == Path("/x")


def test_invalid_flag_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid boolean"):
        Settings.from_env({"GRIDLOAD_INCLUDE_PRICES": "maybe"})


def test_from_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIDLOAD_DATA_DIR", "/srv/data")
    assert Settings.from_env().data_dir == Path("/srv/data")


def test_catalog_keys_and_filters_are_unique() -> None:
    assert len({s.key for s in CATALOG}) == len(CATALOG)
    assert len({s.filter_id for s in CATALOG}) == len(CATALOG)


def test_select_filters_and_excludes_prices() -> None:
    assert [s.key for s in select(["load"])] == ["load"]
    assert all(s.category is not Category.PRICE for s in select(include_prices=False))
    assert select(["price_day_ahead"], include_prices=False) == []
    assert len(select()) == len(CATALOG)


def test_select_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown series: nope"):
        select(["load", "nope"])
