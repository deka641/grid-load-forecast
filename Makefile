# Local quality gate. `make verify` runs every step in order and stops at the first failure.
SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
.NOTPARALLEL:

UV  := uv run --locked
NPM := npm --prefix web
T   := timeout -k 10

.PHONY: help install verify audit lint typecheck test openapi openapi-check build docs smoke clean

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Install Python and frontend dependencies from the lockfiles
	uv sync --locked
	$(NPM) ci --no-fund

verify: audit lint typecheck test openapi-check build docs ## Full local gate
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
	$(T) 60 $(UV) python scripts/export_openapi.py "$$tmp/openapi.json" > /dev/null; \
	(cd web && $(T) 120 npx --no-install openapi-typescript "$$tmp/openapi.json" -o "$$tmp/schema.d.ts" > /dev/null); \
	diff -u openapi.json "$$tmp/openapi.json" && diff -u web/src/api/schema.d.ts "$$tmp/schema.d.ts" \
		|| { echo "openapi-check: generated API files are out of date, run make openapi"; exit 1; }

build: ## Frontend production build with size budget
	$(T) 300 $(NPM) run --silent build
	$(T) 60 $(UV) python scripts/check_bundle_size.py

docs: ## MkDocs strict build
	$(T) 180 $(UV) mkdocs build --strict --site-dir site

smoke: ## Live request against SMARD (not part of verify)
	$(T) 120 $(UV) python scripts/smoke.py

clean: ## Remove build artefacts
	rm -rf site web/dist web/coverage .coverage coverage.json .pytest_cache .mypy_cache .ruff_cache
