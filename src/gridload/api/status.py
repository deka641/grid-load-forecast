"""The /status report: data freshness, licence and attribution."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import duckdb

from gridload.api import queries
from gridload.api.models import LastForecast, LoadRunInfo, SeriesFreshness, Status
from gridload.api.service import UNREADABLE
from gridload.api.store import Snapshot, SnapshotStore, SnapshotUnavailableError
from gridload.series import Series
from gridload.timezones import to_local

# Records keep the logger name of the application module.
log = logging.getLogger("gridload.api.app")

ATTRIBUTION = "Bundesnetzagentur | SMARD.de"
LICENCE = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
PRICE_NOTE = (
    "The Bundesnetzagentur reviews the licensing of the wholesale electricity prices "
    "shown on SMARD.de in its HEDWIG procedure. Until that review is concluded, price "
    "data is shown with that reservation, and price ingestion can be switched off."
)
PRICE_NOTE_OFF = (
    "Wholesale prices are switched off in this deployment. The Bundesnetzagentur reviews "
    "the licensing of the wholesale electricity prices shown on SMARD.de in its HEDWIG "
    "procedure."
)


@dataclass(frozen=True)
class StatusFacts:
    """The parts of /status that only change with the snapshot."""

    key: tuple[int, int]
    created_at: datetime
    schema_version: int | None
    last_timestamps: dict[str, datetime]
    last_load_run: LoadRunInfo | None
    last_forecast: LastForecast | None


def _status_facts(store: SnapshotStore, snapshot: Snapshot) -> StatusFacts:
    with store.cursor(snapshot) as (cur, created):
        return StatusFacts(
            key=snapshot.key,
            created_at=created,
            schema_version=queries.schema_version(cur),
            last_timestamps=queries.last_timestamps(cur),
            last_load_run=queries.last_load_run(cur),
            last_forecast=queries.last_forecast(cur),
        )


def build_status(
    store: SnapshotStore,
    cache: dict[str, StatusFacts],
    series: list[Series],
    now: datetime,
    price_note: str,
) -> Status:
    """Build /status; the snapshot queries run once per snapshot, the ages on every call."""
    base: dict[str, Any] = {
        "generated_at": to_local(now),
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "price_note": price_note,
    }
    try:
        snapshot = store.current()
        facts = cache.get("facts")
        if facts is None or facts.key != snapshot.key:
            facts = cache["facts"] = _status_facts(store, snapshot)
        return Status(
            initialised=True,
            snapshot_created_at=to_local(facts.created_at),
            snapshot_error=store.error,
            schema_version=facts.schema_version,
            series=queries.freshness(facts.last_timestamps, series, now),
            last_load_run=facts.last_load_run,
            last_forecast=facts.last_forecast,
            **base,
        )
    except SnapshotUnavailableError:
        error = store.error
    except duckdb.Error as exc:
        log.error("status query failed: %s", exc)
        error = UNREADABLE
    # Attribution and licence are reported even without readable data.
    return Status(
        initialised=False,
        snapshot_created_at=None,
        snapshot_error=error,
        schema_version=None,
        series=[
            SeriesFreshness(key=s.key, label=s.label, last_ts=None, age_seconds=None)
            for s in series
        ],
        last_load_run=None,
        last_forecast=None,
        **base,
    )
