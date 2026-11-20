# Contributing

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 24 and Docker with Compose.

```bash
make install
```

## Quality gate

Changes are checked by two local gates. `make verify-fast` (lint, strict typing, style and secret scans, tests) runs during work; `make verify` adds the dependency audit, a secret scan of the history, coverage thresholds, the API type check and the builds, and must pass before a larger change is merged and before every push. Steps run in order and each gate stops at the first failure. See [Develop and verify changes](docs/how-to/develop.md). Tests never reach the network: a guard in `tests/conftest.py` blocks DNS lookups and outbound sockets and fails any test that tries, and the frontend test setup replaces `fetch` with a function that throws. Tests of the data source use recorded SMARD responses in `tests/fixtures/smard/`. `make smoke` checks the live source separately and is not part of the gate.

## Generated API types

`make openapi` writes `openapi.json` and the TypeScript types in `web/src/api/schema.d.ts`, and `make openapi-check` (part of the gate) fails when either is out of date. The generator, `openapi-typescript` 7.13.0, declares a peer dependency on TypeScript 5, while the frontend uses TypeScript 6. The `overrides` entry in `web/package.json` resolves that peer to the project's TypeScript, so `npm ci` works without `--force` or `--legacy-peer-deps`. The generator therefore runs outside its declared peer range; any change in its output shows up in the check. Remove the override once a release supports TypeScript 6.

## Branches and commits

- One branch per topic, merged into `master` with `git merge --no-ff` after a green gate.
- Commit messages in English, short and factual.
- Every bug fix comes with a regression test that fails before the fix.
