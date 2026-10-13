# Metis developer entry points. Everything here must work in local fixture
# mode with no cloud account and no third-party AI provider key.

SHELL := /bin/bash
UV ?= uv
PNPM ?= pnpm
export METIS_MODE ?= local

.DEFAULT_GOAL := help

.PHONY: help
help: ## list targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: bootstrap
bootstrap: ## install python + node dependencies
	$(UV) sync --all-packages
	$(PNPM) install --frozen-lockfile || $(PNPM) install

.PHONY: contracts
contracts: ## regenerate JSON Schema + TypeScript from the Pydantic contracts
	cd packages/contracts && PYTHONPATH=src python3 -m metis_contracts.generate
	@cp packages/contracts/generated/metis.ts apps/web/src/contracts/metis.ts 2>/dev/null \
	  && echo "synced apps/web/src/contracts/metis.ts" || true

.PHONY: contracts-check
contracts-check: ## fail if the generated contract artifacts are stale
	cd packages/contracts && PYTHONPATH=src python3 -m metis_contracts.generate --check

.PHONY: spec
spec: ## re-extract docs/spec/* from the canonical prompt
	python3 scripts/extract_spec.py

.PHONY: dev
dev: ## API + local worker loop + web, all on fixture adapters
	./scripts/dev.sh

.PHONY: api
api: ## run only the API (fixture adapters, SQLite state)
	$(UV) run uvicorn metis_api.main:app --reload --port 8000

.PHONY: worker
worker: ## run the local outbox/job worker loop
	$(UV) run python -m metis_pipeline.local_worker

.PHONY: web
web: ## run only the web client
	$(PNPM) --filter @metis/web dev

.PHONY: test
test: ## python unit tests + web unit tests
	$(UV) run pytest -m "not integration and not cloud"
	@# No-op while apps/web has no package.json (pnpm exits 0 on no match).
	@# The web package's own `test` script must be non-watching.
	$(PNPM) --filter @metis/web test

.PHONY: test-integration
test-integration: ## tests that need a container or real audio decoding
	$(UV) run pytest -m integration

.PHONY: check
check: contracts-check ## lint, format check and typecheck
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy packages services
	$(PNPM) -r typecheck

.PHONY: fmt
fmt: ## autoformat python and web sources
	$(UV) run ruff check --fix .
	$(UV) run ruff format .
	$(PNPM) -r lint --fix || true

.PHONY: synth
synth: ## cdk synth every stack (no AWS calls beyond lookups, no deploy)
	$(PNPM) --filter @metis/infra synth

.PHONY: desktop-test
desktop-test: ## cargo + sidecar backend tests for the Tauri shell (needs rust toolchain)
	$(PNPM) --filter @metis/desktop test

.PHONY: speech-image
speech-image: ## build the owned ASR worker container
	docker build -t metis-speech:dev services/speech

.PHONY: speech-bench
speech-bench: ## WER + name/date/negation fidelity on the evaluation fixtures
	$(UV) run python -m metis_speech.evaluate --fixtures services/speech/fixtures

.PHONY: clean
clean: ## remove local build and runtime artifacts (keeps git-tracked files)
	rm -rf .pytest_cache .ruff_cache .mypy_cache infra/cdk.out apps/web/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
