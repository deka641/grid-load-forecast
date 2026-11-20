# Configuration

The pipeline and the API read the same environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `GRIDLOAD_DATA_DIR` | `data` (in containers `/data`) | Directory with `warehouse.duckdb`, `serving.duckdb`, the heartbeat and the backtest state file |
| `GRIDLOAD_INCLUDE_PRICES` | `true` | `false` stops loading wholesale prices and removes them from the published snapshot and the API |
| `GRIDLOAD_SMARD_BASE_URL` | `https://www.smard.de/app/chart_data` | Source base URL (for tests or a mirror) |

## Compose

`compose.yaml` sets the project name `grid-load-forecast`, binds the dashboard to `127.0.0.1:8460`, and defines healthchecks, memory limits (pipeline 2 GB, api 512 MB, web 128 MB) and `restart: unless-stopped`. For local work, copy `compose.override.example.yaml` to `compose.override.yaml` (git-ignored) to stop containers from restarting on their own. `GRIDLOAD_INCLUDE_PRICES` can be set in the shell or in a `.env` file next to the compose file.
