ALPHA_HOME ?= $(CURDIR)/.alpha
ALPHA_HOME := $(abspath $(ALPHA_HOME))
ALPHA_HOME ?= $(ALPHA_HOME)
BACKEND_SANDBOX_HOME := $(abspath $(CURDIR)/sandbox)

# Cross-platform environment prefix for recipes.
#
# `PYTHONUTF8=1 some-command` is a POSIX shell prefix that cmd.exe cannot parse:
# it tries to run a program literally named "PYTHONUTF8=1" and fails with
# "'PYTHONUTF8=1' is not recognized as an internal or external command". Because
# this Makefile sets `SHELL := cmd.exe` on Windows, every recipe written that way
# was broken there - `test`, `test-live`, `test-blocking-io`, `test-shard`,
# `gateway` and `dev`, including the backend test command the developer guide
# tells you to run. It only worked in CI, which is Linux.
#
# cmd.exe needs `set "VAR=value"&& ` chained instead. The trailing `&& ` is
# deliberate and must have no space before it.
ifeq ($(OS),Windows_NT)
    RUN_ENV = set "PYTHONPATH=."&& set "PYTHONIOENCODING=utf-8"&& set "PYTHONUTF8=1"&&
    RUN_ENV_ALPHA_HOME = set "PYTHONPATH=."&& set "PYTHONIOENCODING=utf-8"&& set "PYTHONUTF8=1"&& set "ALPHA_HOME=$(ALPHA_HOME)"&&
    RUN_ENV_LIVE = set "PYTHONPATH=."&& set "PYTHONIOENCODING=utf-8"&& set "PYTHONUTF8=1"&& set "ALPHA_RUN_LIVE_TESTS=1"&&
else
    RUN_ENV = PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1
    RUN_ENV_ALPHA_HOME = PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ALPHA_HOME="$(ALPHA_HOME)"
    RUN_ENV_LIVE = PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ALPHA_RUN_LIVE_TESTS=1
endif

install:
	uv sync --locked

dev:
	mkdir -p "$(ALPHA_HOME)" "$(BACKEND_SANDBOX_HOME)"
	$(RUN_ENV_ALPHA_HOME) uv run --locked uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 \
		--reload \
		--reload-include='*.yaml' \
		--reload-include='.env' \
		--reload-exclude='*.pyc' \
		--reload-exclude='__pycache__' \
		--reload-exclude="$(BACKEND_SANDBOX_HOME)" \
		--reload-exclude="$(ALPHA_HOME)"

gateway:
	$(RUN_ENV) uv run --locked uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001

test:
	$(RUN_ENV) uv run pytest -m "not live" --ignore=tests/blocking_io tests/ -v

test-live:
	$(RUN_ENV_LIVE) uv run pytest -m live tests/ -v -s

test-blocking-io:
	$(RUN_ENV) uv run pytest tests/blocking_io -q --tb=short

# Run the backend unit-test suite as one shard of a parallel CI split.
#   SPLITS = total number of shards; GROUP = 1-based shard index.
# Duration-aware: `least_duration` balances the shards by real wall-clock cost
# read from .test_durations. Shards only READ that file (no --store-durations),
# so concurrent CI jobs cannot race writes on it. Refresh it with
# `make test-shard-durations` after a meaningful change to the test set.
# `make test-shard SPLITS=4 GROUP=2` runs shard 2 of 4; a bare `make test-shard`
# runs the whole (non-live) suite as a single split, matching `make test`.
SPLITS ?= 1
GROUP ?= 1
DURATIONS_FILE ?= .test_durations

# Duration-aware balancing needs the committed baseline. When that file is
# absent, fall back to pytest-split's file-count balancing instead of failing.
#
# It used to hard-error, and the baseline is not in the tree, so
# `make test-shard SPLITS=4 GROUP=N` - which is exactly what every job in
# .github/workflows/backend-unit-tests.yml runs - died immediately on
# "error: .test_durations is missing". The whole backend unit-test suite was
# therefore not running in CI at all, and the failure had nothing to do with any
# test. A missing performance baseline must never decide whether tests run.
ifneq ($(wildcard $(DURATIONS_FILE)),)
SPLIT_BALANCE = --splitting-algorithm least_duration --durations-path=$(DURATIONS_FILE)
else
SPLIT_BALANCE =
endif

test-shard:
ifeq ($(SPLIT_BALANCE),)
	@echo warning: $(DURATIONS_FILE) is missing - balancing shards by file count. 1>&2
	@echo "         Every test still runs; only the per-shard balance degrades." 1>&2
	@echo "         Run 'make test-shard-durations' and commit the file to restore it." 1>&2
endif
	$(RUN_ENV) uv run pytest -m "not live" --ignore=tests/blocking_io --splits $(SPLITS) --group $(GROUP) $(SPLIT_BALANCE) tests/ -q

# Regenerate .test_durations from the full offline (non-live, non-blocking-I/O)
# suite so duration-aware sharding reflects real wall-clock cost. Run locally or
# in a periodic job, then commit the refreshed file.
test-shard-durations:
	$(RUN_ENV) uv run pytest -m "not live" --ignore=tests/blocking_io --store-durations --clean-durations --durations-path=$(DURATIONS_FILE) tests/ -q

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check . --fix && uv run ruff format .

detect-blocking-io:
	@PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python ../scripts/detect_blocking_io_static.py --output ../.alpha/blocking-io-findings.json

# Generate a new alembic revision by autogenerating the diff against the live
# ORM models. Usage: make migrate-rev MSG="add foo column to runs"
# The Gateway runs `alembic upgrade head` automatically at startup via
# `bootstrap_schema`, so there is intentionally no `migrate` / `migrate-stamp`
# target -- the single execution path keeps ops mistakes off the table.
# The script builds a fresh temp SQLite at head, then diffs the live models
# against it, so a clean checkout does NOT need a pre-existing ./data/alpha.db.
migrate-rev:
ifeq ($(strip $(MSG)),)
	@echo usage: make migrate-rev MSG="describe the change"
	@exit 1
endif
	$(RUN_ENV) uv run python scripts/_autogen_revision.py "$(MSG)"
