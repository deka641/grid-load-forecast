# Configuration

The pipeline and the API read the same environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `GRIDLOAD_DATA_DIR` | `data` (in containers `/data`) | Directory with `warehouse.duckdb`, `serving.duckdb`, the heartbeat and the backtest state file |
| `GRIDLOAD_INCLUDE_PRICES` | `true` | `false` stops loading wholesale prices and removes them from the published snapshot and the API |
| `GRIDLOAD_SMARD_BASE_URL` | `https://www.smard.de/app/chart_data` | Source base URL (for tests or a mirror) |
| `GRIDLOAD_BUILD_ID` | the build id of the image | Part of every ETag of the API; a new value invalidates all ETags clients hold |

The image build computes the build id as a hash of `src/`, `pyproject.toml` and `uv.lock` and stores it next to the installed package. It changes exactly when the code or the locked dependencies change, so restarting or recreating the API container keeps every ETag valid and clients keep getting `304 Not Modified`. Set `GRIDLOAD_BUILD_ID` only to override it. A local run outside the image has no stored build id and uses the start time of the API process instead, so its ETags change with every restart.

## Compose

`compose.yaml` sets the project name `grid-load-forecast`, binds the dashboard to `127.0.0.1:8460`, and defines healthchecks, memory limits (pipeline 2 GiB, api 512 MiB, web 256 MiB), CPU limits (pipeline 4, api 3, web 2 cores), a limit of 4,096 open files for the web server, a run time limit of 3 hours for every pipeline run, and `restart: unless-stopped`. Container logs rotate at 10 MB with three files kept. For local work, copy `compose.override.example.yaml` to `compose.override.yaml` (git-ignored): containers then do not restart on their own. The override also sets the dashboard port to `127.0.0.1:8460`, which repeats the default, so a later change to the port in `compose.yaml` cannot expose the dashboard on a development machine. Settings such as `GRIDLOAD_INCLUDE_PRICES` go into a `.env` file next to the compose file; `.env.example` lists them.

The API and the web container share an internal network with the fixed subnet `10.46.60.0/29`; the web container has `10.46.60.2`, the API `10.46.60.3`, and the API trusts `X-Forwarded-For` only from `10.46.60.2` (`FORWARDED_ALLOW_IPS`). If that subnet is already in use on the host, Compose cannot create the network: change the subnet, both addresses and `FORWARDED_ALLOW_IPS` in `compose.yaml` together. [Architecture](../explanation/architecture.md#network) explains the layout.
