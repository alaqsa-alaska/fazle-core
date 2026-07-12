# ──────────────────────────────────────────────────────────────────────────────
# fazle-core  —  Test Makefile
# Run from:  /home/azim/core/
# ──────────────────────────────────────────────────────────────────────────────

PYTHON      ?= python3
PIP         ?= pip3
PYTEST      ?= python -m pytest
TEST_DB_URL ?= postgresql://postgres:$(TEST_DB_PASSWORD)@172.20.0.3:5432/fazle_test

# Pytest base args (coverage always collected)
BASE_ARGS = \
  --cov=app --cov=modules \
  --cov-report=term-missing \
  --cov-report=html:tests/coverage_html \
  --cov-report=xml:tests/coverage.xml \
  --timeout=60 \
  -v

ENV_VARS = \
  ENVIRONMENT=test \
  TEST_DATABASE_URL=$(TEST_DB_URL) \
  INTERNAL_API_KEY=test-internal-key \
  ADMIN_NUMBERS=8801700000001 \
  META_VERIFY_TOKEN=test_verify_token \
  BRIDGE1_URL=http://mock-bridge1 \
  BRIDGE2_URL=http://mock-bridge2 \
  OLLAMA_BASE_URL=http://mock-ollama

# ─── Install ──────────────────────────────────────────────────────────────────
.PHONY: install
install:
	$(PIP) install -r requirements.txt -r tests/requirements-test.txt

.PHONY: install-playwright
install-playwright:
	playwright install chromium --with-deps

# ─── All tests (excludes e2e and load) ───────────────────────────────────────
.PHONY: test
test:
	$(ENV_VARS) $(PYTEST) tests/ \
	  -m "not e2e and not load" \
	  $(BASE_ARGS) \
	  --cov-fail-under=85

# ─── Unit only ────────────────────────────────────────────────────────────────
.PHONY: test-unit
test-unit:
	$(ENV_VARS) $(PYTEST) tests/unit/ \
	  -m unit \
	  --timeout=30 \
	  --cov=app --cov=modules \
	  --cov-report=term-missing \
	  --cov-report=xml:tests/coverage-unit.xml \
	  -v

# ─── Integration + DB + Workflow tests ───────────────────────────────────────
.PHONY: test-integration
test-integration:
	$(ENV_VARS) $(PYTEST) tests/integration/ tests/db/ tests/workflows/ \
	  -m "integration or db or workflow" \
	  $(BASE_ARGS)

# ─── Resilience tests ─────────────────────────────────────────────────────────
.PHONY: test-resilience
test-resilience:
	$(ENV_VARS) $(PYTEST) tests/resilience/ \
	  -m resilience \
	  --timeout=60 \
	  -v

# ─── E2E (Playwright) tests ───────────────────────────────────────────────────
.PHONY: test-e2e
test-e2e:
	TEST_APP_URL=$${TEST_APP_URL:-http://localhost:8200} \
	TEST_API_KEY=$${TEST_API_KEY:-test-internal-key} \
	$(PYTEST) tests/e2e/ -m e2e --timeout=60 -v \
	  --html=tests/e2e/report.html --self-contained-html \
	  --screenshot=only-on-failure \
	  --video=retain-on-failure \
	  --tracing=retain-on-failure

# Like test-e2e but saves screenshots for every tab (slower, used for baselines)
.PHONY: test-e2e-full
test-e2e-full:
	TEST_APP_URL=$${TEST_APP_URL:-http://localhost:8200} \
	TEST_API_KEY=$${TEST_API_KEY:-test-internal-key} \
	$(PYTEST) tests/e2e/ -m e2e --timeout=90 -v \
	  --html=tests/e2e/report.html --self-contained-html \
	  --screenshot=on \
	  --video=on \
	  --tracing=retain-on-failure

# ─── Load tests (Locust headless) ────────────────────────────────────────────
.PHONY: test-load
test-load:
	locust -f tests/load/locustfile.py \
	  --host http://localhost:8200 \
	  --headless \
	  -u 20 -r 5 \
	  --run-time 30s \
	  --html tests/load/load-report.html

# ─── Coverage html report ─────────────────────────────────────────────────────
.PHONY: coverage
coverage: test
	@echo "Coverage HTML report: tests/coverage_html/index.html"
	@python -c "import webbrowser; webbrowser.open('tests/coverage_html/index.html')"

# ─── Docker test stack ───────────────────────────────────────────────────────
.PHONY: test-docker-up
test-docker-up:
	docker compose -f docker-compose.test.yml up -d \
	  postgres-test redis-test mock-bridge1 mock-bridge2 mock-ollama

.PHONY: test-docker
test-docker:
	docker compose -f docker-compose.test.yml up \
	  --abort-on-container-exit \
	  --exit-code-from test-runner \
	  test-runner

.PHONY: test-docker-down
test-docker-down:
	docker compose -f docker-compose.test.yml down -v

# ─── Cleanup ──────────────────────────────────────────────────────────────────
.PHONY: clean-test
clean-test:
	rm -rf tests/coverage_html tests/coverage.xml tests/coverage-unit.xml \
	       tests/coverage-integration.xml .coverage .pytest_cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# ─── Quick smoke check (no coverage enforcement) ────────────────────────────
.PHONY: smoke
smoke:
	$(ENV_VARS) $(PYTEST) tests/unit/ tests/integration/test_api.py \
	  --timeout=30 -q \
	  --no-header \
	  --no-cov \
	  -x

# ─── Show coverage summary ───────────────────────────────────────────────────
.PHONY: cov-report
cov-report:
	@python -m coverage report --skip-covered --skip-empty

.DEFAULT_GOAL := test
