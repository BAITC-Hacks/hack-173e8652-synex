.DEFAULT_GOAL := help

PYTHON ?= python3
HOST ?= 127.0.0.1
API_PORT ?= 8000
UI_PORT ?= 8501
DATA_DIR ?= ./data
OUT_DIR ?= ./out
ARTIFACTS_DIR ?= ./artifacts
DATABASE_URL ?= sqlite:///./moneygraph.db
EXPECTED_NODES ?= 2248

.PHONY: help install lint typecheck test analyze verify-outputs api ui dev smoke verify \
	docker-config docker-build docker-up
.NOTPARALLEL: verify

help: ## Show the available development commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Freedom MoneyGraph AML\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the package and development dependencies.
	$(PYTHON) -m pip install -e ".[dev,ai]"

lint: ## Run Ruff over source, tests, and operational scripts.
	$(PYTHON) -m ruff check .

typecheck: ## Run strict mypy checks for the application package.
	$(PYTHON) -m mypy src

test: ## Run the synthetic test suite with the coverage gate.
	$(PYTHON) -m pytest -m "not full_data" --cov=moneygraph --cov-report=term-missing --cov-fail-under=80

analyze: ## Run the deterministic pipeline on the local Parquet dataset.
	DATA_DIR="$(DATA_DIR)" OUT_DIR="$(OUT_DIR)" ARTIFACTS_DIR="$(ARTIFACTS_DIR)" \
		DATABASE_URL="$(DATABASE_URL)" PYTHON_BIN="$(PYTHON)" ./scripts/run_pipeline.sh

verify-outputs: ## Validate all mandatory CSV contracts and the runtime budget.
	$(PYTHON) scripts/verify_outputs.py --out "$(OUT_DIR)" --expected-nodes "$(EXPECTED_NODES)"

api: ## Start the FastAPI service on the local loopback interface.
	DATA_DIR="$(DATA_DIR)" OUT_DIR="$(OUT_DIR)" ARTIFACTS_DIR="$(ARTIFACTS_DIR)" \
		DATABASE_URL="$(DATABASE_URL)" \
		$(PYTHON) -m uvicorn moneygraph.api.main:app --host "$(HOST)" --port "$(API_PORT)"

ui: ## Start the Streamlit analyst workspace on the local loopback interface.
	MONEYGRAPH_API_URL="http://$(HOST):$(API_PORT)" \
		$(PYTHON) -m streamlit run src/moneygraph/ui/app.py \
		--server.address "$(HOST)" --server.port "$(UI_PORT)" \
		--server.headless true --browser.gatherUsageStats false

dev: ## Start API and UI together; Ctrl-C stops both processes.
	@set -eu; \
	DATA_DIR="$(DATA_DIR)" OUT_DIR="$(OUT_DIR)" ARTIFACTS_DIR="$(ARTIFACTS_DIR)" \
	DATABASE_URL="$(DATABASE_URL)" \
		$(PYTHON) -m uvicorn moneygraph.api.main:app --host "$(HOST)" --port "$(API_PORT)" & \
	api_pid=$$!; \
	trap 'kill "$$api_pid" 2>/dev/null || true; wait "$$api_pid" 2>/dev/null || true' EXIT INT TERM; \
	MONEYGRAPH_API_URL="http://$(HOST):$(API_PORT)" \
		$(PYTHON) -m streamlit run src/moneygraph/ui/app.py \
		--server.address "$(HOST)" --server.port "$(UI_PORT)" \
		--server.headless true --browser.gatherUsageStats false

smoke: ## Start API/UI temporarily and exercise their health and data endpoints.
	PYTHON_BIN="$(PYTHON)" API_PORT="$(API_PORT)" UI_PORT="$(UI_PORT)" \
		DATA_DIR="$(DATA_DIR)" OUT_DIR="$(OUT_DIR)" ARTIFACTS_DIR="$(ARTIFACTS_DIR)" \
		./scripts/smoke_test.sh

verify: ## Run lint, types, tests, full analysis, CSV verification, and live smoke checks.
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) analyze
	$(MAKE) verify-outputs
	$(MAKE) smoke

docker-config: ## Validate the resolved Docker Compose model.
	docker compose config --quiet

docker-build: docker-config ## Build the non-root application image.
	docker compose build

docker-up: ## Analyze the data, then start API and UI with health checks.
	docker compose up --build
