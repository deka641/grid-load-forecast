"""Runtime configuration from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://www.smard.de/app/chart_data"
USER_AGENT = "grid-load-forecast/0.1"


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


@dataclass(frozen=True)
class Settings:
    """Settings shared by the pipeline and the API."""

    data_dir: Path
    include_prices: bool = True
    base_url: str = DEFAULT_BASE_URL

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse.duckdb"

    @property
    def serving_path(self) -> Path:
        return self.data_dir / "serving.duckdb"

    @property
    def heartbeat_path(self) -> Path:
        return self.data_dir / "heartbeat.json"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        return cls(
            data_dir=Path(source.get("GRIDLOAD_DATA_DIR") or "data"),
            include_prices=_flag(source.get("GRIDLOAD_INCLUDE_PRICES"), default=True),
            base_url=source.get("GRIDLOAD_SMARD_BASE_URL") or DEFAULT_BASE_URL,
        )
