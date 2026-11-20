# Contributing

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 24 and Docker with Compose.

```bash
make install
```

## Quality gate

Every change must pass the local gate before it is merged:

```bash
make verify
```

The gate runs the dependency audit, linters, strict type checks, tests with coverage thresholds, the frontend build with its size budget and the strict documentation build. Steps run in order and the gate stops at the first failure. Tests never reach the real data source: they use recorded fixtures, and a guard in `tests/conftest.py` blocks network access. `make smoke` checks the live source separately.

## Branches and commits

- One branch per topic, merged into `master` with `git merge --no-ff` after a green gate.
- Commit messages in English, short and factual.
- Every bug fix comes with a regression test that fails before the fix.
