# Command line

The `gridload` command is installed with the package (`uv run gridload ...` locally, `docker compose exec pipeline gridload ...` in the stack). It exits with 0 on success, 1 if the command failed (a failed or aborted run, an unhealthy pipeline, or no backtest stored, including `--days` below 7) and 2 for invalid arguments or unknown series.

| Command | What it does |
|---|---|
| `gridload run` | The scheduled job: load data, issue the forecast when due, refresh a stale backtest, publish the snapshot |
| `gridload ingest [--series KEY ...] [--refetch-weeks N] [--accept-withdrawals]` | Load missing chunks, chunks stored while still open, and the newest N weeks again (default 4); optionally accept values SMARD removed, in completed weeks and in the open week beyond a short tail at its end ([procedure](../how-to/operate.md#accept-values-that-smard-removed)) |
| `gridload backfill [--since YYYY-MM-DD] [--series KEY ...]` | Load the history, optionally from a start day |
| `gridload backtest [--days N]` | Run and store the backtest over the last N days (default 365) and republish the snapshot |
| `gridload status` | Print the state of the published snapshot as JSON: recent runs, coverage per series, errors of the last run |
| `gridload healthcheck` | Exit 0 if the pipeline is healthy (used by the container healthcheck) |

All commands that write need exclusive access to the warehouse. While the hourly job runs, a second writing command fails with a DuckDB lock error; run it again afterwards. `gridload status` reads the snapshot and always works.

## Scripts

| Script | Purpose |
|---|---|
| `scripts/backtest.py` | Backtest on the local warehouse, writes `reports/backtest.json` and `reports/backtest.md` |
| `scripts/crosscheck_metrics.py` | Recomputes every reported metric with scikit-learn in a separate environment |
| `scripts/record_fixtures.py` | Records the SMARD responses used as test fixtures |
| `scripts/smoke.py` | Live check against SMARD (`make smoke`) |
| `scripts/loadtest.py` | Load test against an isolated stack (`make loadtest`) |
| `scripts/export_openapi.py` | Writes `openapi.json` (`make openapi`) |
| `scripts/prod_check.sh` | Production-like build and checks on an isolated stack on port 8461 (`make prod-check`) |
| `scripts/loadtest_abuse.py` | Abusive clients against the prod-check stack, run by `scripts/prod_check.sh` from containers on its network; modes `halfopen` (unfinished request headers held open), `flood` (keep-alive connections requesting without pause) and `probe` (what an ordinary visitor sees meanwhile) |
| `scripts/check_style.py`, `scripts/check_coverage.py`, `scripts/check_bundle_size.py` | Gate helpers called by `make verify`: no emojis or em and en dashes in text files, per-directory coverage, gzip size budget of the frontend |
