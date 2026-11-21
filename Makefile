# Local quality gates. `make verify-fast` (during work) and `make verify` (before merging large
# changes, before every push and release) run their steps in order and stop at the first failure.
SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
.NOTPARALLEL:

UV  := uv run --locked
NPM := npm --prefix web
T   := timeout -k 10
# Scanner image pins are updated by hand (Dependabot does not read the Makefile).
GITLEAKS := zricethezav/gitleaks:v8.30.0@sha256:691af3c7c5a48b16f187ce3446d5f194838f91238f27270ed36eef6359a574d9

.PHONY: help install deps verify-fast verify style secrets secrets-history test-fast prod-check loadtest prod-down audit lint typecheck test openapi openapi-check build docs e2e smoke clean

help: ## List targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Install Python and frontend dependencies from the lockfiles
	uv sync --locked
	$(NPM) ci --no-fund
	sha256sum web/package-lock.json > web/node_modules/.lock.sha256

# Detects a missing or outdated install. Packages added by hand outside the lockfiles
# (uv pip install, npm install --no-save) are not detected.
deps: ## Fail early if the locked environments are missing or out of date
	@test -d web/node_modules || { echo "web/node_modules missing: run make install"; exit 1; }
	@sha256sum --status -c web/node_modules/.lock.sha256 2>/dev/null || { echo "web/node_modules was not installed from the current package-lock.json: run make install"; exit 1; }
	@$(T) 120 uv sync --quiet --locked --check || { echo "Python environment differs from uv.lock: run make install"; exit 1; }

verify-fast: deps lint typecheck style secrets test-fast ## Fast gate: lint, types, style, secrets, tests
	@echo "verify-fast: all steps passed"

verify: deps lint typecheck style secrets audit secrets-history test openapi-check build docs ## Full gate
	@echo "verify: all steps passed"

style: ## No emojis or em/en dashes in versioned text files
	$(T) 60 $(UV) python scripts/check_style.py

# Tracked and new (not ignored) files; the copy is removed even when a secret is found.
secrets: ## Secret scan of the working tree
	@trap 'rm -rf .gitleaks-scan' EXIT; rm -rf .gitleaks-scan; mkdir .gitleaks-scan; \
	git ls-files -z --cached --others --exclude-standard \
	  | while IFS= read -r -d '' f; do [ -f "$$f" ] && printf '%s\0' "$$f"; done \
	  | xargs -0r cp --parents -t .gitleaks-scan; \
	$(T) 120 docker run --rm -v "$(CURDIR)/.gitleaks-scan:/scan:ro" $(GITLEAKS) dir /scan --no-banner --redact

# Mounts the shared git directory too, so the scan also works inside a git worktree,
# and fails if no commit was scanned.
secrets-history: ## Secret scan over the full git history
	@g=$$(git rev-parse --path-format=absolute --git-common-dir); \
	out=$$($(T) 300 docker run --rm -v "$(CURDIR):$(CURDIR):ro" -v "$$g:$$g:ro" $(GITLEAKS) \
	  git "$(CURDIR)" --no-banner --redact 2>&1) || { echo "$$out"; exit 1; }; \
	echo "$$out" | tail -2; \
	! echo "$$out" | grep -q " 0 commits scanned" || { echo "secrets-history: no commits scanned"; exit 1; }

test-fast: ## Tests without coverage
	$(T) 600 $(UV) pytest -q -p no:cacheprovider
	$(T) 300 $(NPM) run --silent test

audit: ## Dependency vulnerability audit (needs network to the package indexes)
	$(T) 300 uv export --quiet --locked --no-emit-project --format requirements.txt -o .audit-requirements.txt
	$(T) 300 $(UV) pip-audit --strict --disable-pip -r .audit-requirements.txt || { rm -f .audit-requirements.txt; exit 1; }
	rm -f .audit-requirements.txt
	$(T) 120 $(NPM) audit --audit-level=low

lint: ## ruff, ESLint, Prettier
	$(T) 120 $(UV) ruff check .
	$(T) 120 $(UV) ruff format --check .
	$(T) 180 $(NPM) run --silent lint
	$(T) 120 $(NPM) run --silent format:check

typecheck: ## mypy strict and tsc
	$(T) 300 $(UV) mypy
	$(T) 180 $(NPM) run --silent typecheck

test: ## pytest and Vitest with coverage thresholds
	$(T) 600 $(UV) pytest --cov --cov-report=term --cov-report=json
	$(T) 60 $(UV) python scripts/check_coverage.py
	$(T) 300 $(NPM) run --silent test:coverage

openapi: ## Regenerate openapi.json and the TypeScript API types
	$(T) 60 $(UV) python scripts/export_openapi.py openapi.json
	cd web && $(T) 120 npx --no-install openapi-typescript ../openapi.json -o src/api/schema.d.ts

openapi-check: ## Fail if openapi.json or the TypeScript API types are out of date
	tmp=$$(mktemp -d); trap 'rm -rf -- "$$tmp"' EXIT; \
	$(T) 60 $(UV) python scripts/export_openapi.py "$$tmp/openapi.json" > /dev/null \
		|| { echo "openapi-check: exporting the OpenAPI document failed"; exit 1; }; \
	(cd web && $(T) 120 npx --no-install openapi-typescript "$$tmp/openapi.json" -o "$$tmp/schema.d.ts" > /dev/null) \
		|| { echo "openapi-check: generating the TypeScript types failed"; exit 1; }; \
	if ! diff -u openapi.json "$$tmp/openapi.json" || ! diff -u web/src/api/schema.d.ts "$$tmp/schema.d.ts"; then \
		echo "openapi-check: generated API files are out of date, run make openapi"; exit 1; \
	fi

build: ## Frontend production build with size budget
	$(T) 300 $(NPM) run --silent build
	$(T) 60 $(UV) python scripts/check_bundle_size.py

docs: ## MkDocs strict build
	$(T) 180 $(UV) mkdocs build --strict --site-dir site

smoke: ## Live request against SMARD (not part of verify)
	$(T) 120 $(UV) python scripts/smoke.py

# Uses Playwright's Chromium (npx --prefix web playwright install chromium) and port 8471.
e2e: build ## Browser checks of the production build with fixture data (part of prod-check)
	$(T) 300 npx --no-install --prefix web playwright test --config web/playwright.config.ts

prod-check: e2e ## Fixture browser checks, then a production-like build on port 8461 with a copy of the local data
	$(T) 900 ./scripts/prod_check.sh

loadtest: ## Parallel dashboard requests against the prod-check stack
	$(T) 300 $(UV) python scripts/loadtest.py --base http://127.0.0.1:8461 --project grid-load-forecast-check

prod-down: ## Stop and remove the prod-check stack
	docker compose -p grid-load-forecast-check -f compose.yaml -f compose.check.yaml down -v

clean: ## Remove build artefacts
	rm -rf site web/dist web/coverage .coverage coverage.json .pytest_cache .mypy_cache .ruff_cache
