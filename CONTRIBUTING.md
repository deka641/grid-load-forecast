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

The gate runs the dependency audit, linters, strict type checks, tests with coverage thresholds, the frontend build with its size budget and the strict documentation build. Steps run in order and the gate stops at the first failure. Tests never reach the network: a guard in `tests/conftest.py` blocks DNS lookups and outbound sockets and fails any test that tries, and the frontend test setup replaces `fetch` with a function that throws. Tests of the data source will use recorded fixtures. `make smoke` is reserved for a separate, non-blocking check against the live source.

## Branches and commits

- One branch per topic, merged into `master` with `git merge --no-ff` after a green gate.
- Commit messages in English, short and factual.
- Every bug fix comes with a regression test that fails before the fix.
