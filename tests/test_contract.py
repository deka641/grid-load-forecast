"""Contract test across components: forecast output, snapshot and API responses.

The API tests insert rows that follow the documented schema; this test checks
the real producers instead, so a renamed model, scope or column breaks here.
"""

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from gridload.api.app import create_app
from gridload.config import Settings
from gridload.forecast import service
from gridload.pipeline import snapshot
from tests import synthetic


def test_forecast_and_backtest_reach_the_api_unchanged(tmp_path: Path) -> None:
    con = synthetic.warehouse(date(2025, 1, 1), date(2025, 4, 30))
    assert service.issue_forecast(con, date(2025, 4, 30)) is not None
    result = service.run_and_store_backtest(con, days=7)
    assert result is not None
    settings = Settings(data_dir=tmp_path)
    snapshot.publish(con, settings.serving_path)

    client = TestClient(create_app(settings))
    forecast = client.get("/api/v1/forecasts", params={"delivery_day": "2025-04-30"})
    assert forecast.status_code == 200
    body = forecast.json()
    assert set(body["runs"]) == {"lightgbm", "baseline_week"}
    assert len(body["points"]) == 24
    assert all(p["lightgbm"] is not None and p["actual"] is not None for p in body["points"])
    assert body["points"][0]["ts"] == "2025-04-30T00:00:00+02:00"

    metrics = client.get("/api/v1/backtest/metrics")
    assert metrics.status_code == 200
    data = metrics.json()
    assert set(data["metrics"]) == {"lightgbm", "baseline_week", "tso"}
    for model in ("lightgbm", "baseline_week"):
        scopes = data["metrics"][model]
        assert {"overall", "hour", "day_type", "overall_tso_hours"} <= set(scopes)
        assert [s["scope_value"] for s in scopes["hour"]] == [str(h) for h in range(24)]
    overall = {m: data["metrics"][m]["overall"][0] for m in data["metrics"]}
    stored = {m.model: m for m in result.metrics if m.scope == "overall"}
    for model, row in overall.items():
        assert row["n"] == stored[model].n
        assert abs(row["mape"] - stored[model].mape) < 1e-9
    assert json.loads(json.dumps(data["config"]))["seed"] == 42
