"""The OpenAPI document tells clients what the two forecast times mean."""

from pydantic import BaseModel

from gridload.api.models import ForecastRunInfo, LastForecast


def _description(model: type[BaseModel], field: str) -> str:
    return str(model.model_json_schema()["properties"][field].get("description", ""))


def test_issued_at_is_described_as_the_origin_not_the_computation_time() -> None:
    for model in (ForecastRunInfo, LastForecast):
        text = _description(model, "issued_at")
        assert "Forecast origin: 12:00 Europe/Berlin on the day before delivery" in text
        assert "not the time the forecast was computed" in text


def test_created_at_is_described_as_the_computation_time() -> None:
    text = _description(ForecastRunInfo, "created_at")
    assert text.startswith("When the pipeline computed the forecast")
    assert "20:00" in text
