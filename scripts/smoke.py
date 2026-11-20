"""Live check against SMARD (not part of the offline gate).

Fetches the index and the newest chunk of a few series and runs the same
validation as the pipeline. Exits non-zero if the source changed or is down.
"""

import sys

from gridload.series import BY_KEY
from gridload.sources.smard import ChunkValidationError, SmardClient, SourceError, parse_chunk


def main() -> int:
    client = SmardClient.create()
    failures = 0
    try:
        for key in ("load", "load_forecast_tso", "gen_solar", "price_day_ahead"):
            series = BY_KEY[key]
            try:
                index = client.index(series)
                frame = parse_chunk(
                    series,
                    index[-1],
                    client.chunk(series, index[-1]).payload,
                    allow_trailing_nulls=True,
                )
            except (SourceError, ChunkValidationError) as exc:
                failures += 1
                print(f"FAIL {key}: {exc}")
                continue
            last = str(frame["ts_utc"].max()) if frame.height else "none"
            print(f"ok   {key}: {len(index)} chunks, newest has {frame.height} values, last {last}")
    finally:
        client.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
