# Local quality gate. `make verify` runs every step in order and stops at the first failure.
SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
.NOTPARALLEL:

UV  := uv run --locked
NPM := npm --prefix web
T   := timeout -k 10

.PHONY: help install deps verify prod-check loadtest prod-down audit lint typecheck test openapi openapi-check build docs smoke clean

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Install Python and frontend dependencies from the lockfiles
	uv sync --locked
	$(NPM) ci --no-fund

deps: ## Fail early if the locked environments are missing or out of date
	@test -d web/node_modules || { echo "web/node_modules missing: run make install"; exit 1; }
	@$(T) 60 $(NPM) ls --depth=0 >/dev/null || { echo "web dependencies differ from package-lock.json: run make install"; exit 1; }
	@$(T) 120 uv sync --locked --check >/dev/null 2>&1 || { echo "Python environment differs from uv.lock: run make install"; exit 1; }

verify: deps audit lint typecheck test openapi-check build docs ## Full local gate
	@echo "verify: all steps passed"

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

prod-check: ## Production-like build on port 8461 with a copy of the local data
	$(T) 900 ./scripts/prod_check.sh

loadtest: ## Parallel dashboard requests against the prod-check stack
	$(T) 300 $(UV) python scripts/loadtest.py --base http://127.0.0.1:8461 --project grid-load-forecast-check

prod-down: ## Stop and remove the prod-check stack
	docker compose -p grid-load-forecast-check -f docker-compose.yml -f docker-compose.check.yml down -v

clean: ## Remove build artefacts
	rm -rf site web/dist web/coverage .coverage coverage.json .pytest_cache .mypy_cache .ruff_cache
