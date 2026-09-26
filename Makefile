# Alpha - Unified Development Environment

.PHONY: help config config-upgrade check check-agent-guidance install voice-setup voice-verify system-one-laya-setup system-one-laya-serve system-one-laya-status extension-install extension-upgrade extension-list extension-enable extension-disable extension-remove setup doctor prod-check support-bundle diagnostic-bundle logs-rotate update-status update-check update-apply update-recover update-skip detect-thread-boundaries detect-blocking-io dev dev-daemon start start-daemon nginx stop up down clean docker-init docker-start docker-stop docker-logs docker-logs-frontend docker-logs-gateway docker-logs-redis setup-sandbox verify checkpoint rollback guardrails context safe-exec

BASH ?= bash
BACKEND_UV_RUN = cd backend && uv run
# Laya setup device: auto selects CUDA when nvidia-smi is available, otherwise CPU.
DEVICE ?= auto

# Detect OS for Windows compatibility
ifeq ($(OS),Windows_NT)
    SHELL := cmd.exe
    PYTHON ?= python
    # Run repo shell scripts through Git Bash when Make is launched from cmd.exe / PowerShell.
    RUN_SHELL_SCRIPT = call scripts\run-with-git-bash.cmd
else
    PYTHON ?= python3
    # Invoke repo shell scripts through an explicit interpreter, so recipes keep
    # working in checkouts that lost the executable bit (zip download,
    # core.fileMode=false, non-POSIX filesystem).
    RUN_SHELL_SCRIPT = $(BASH)
endif

FRONTEND_PNPM = $(PYTHON) ../scripts/pnpm.py

help:
	@echo "Alpha Development Commands:"
	@echo "  make setup           - Interactive setup wizard (recommended for new users)"
	@echo "                           Unattended: make setup SETUP_ARGS=--non-interactive"
	@echo "  make doctor          - Check configuration and system requirements"
	@echo "  make prod-check      - Production readiness pre-flight (versions, config, secrets)"
	@echo "  make support-bundle  - One-command diagnostic bundle: redacted logs, run trace, config, versions, doctor"
	@echo "  make diagnostic-bundle RUN_ID=<id> - Same bundle, scoped to one run's trace records"
	@echo "  make logs-rotate      - Rotate oversized launcher logs (same 5 MiB x 3 budget as start.ps1)"
	@echo "  make update-status   - Read the guarded GitHub source-update state"
	@echo "  make update-check    - Check the configured GitHub release/branch"
	@echo "  make update-apply    - Apply the verified update after explicit confirmation"
	@echo "  make update-recover  - Recover an interrupted update transaction"
	@echo "  make update-skip VERSION=x.y.z - Skip one verified version"
	@echo "  make config          - Generate local config files (aborts if config already exists)"
	@echo "  make config-upgrade  - Merge new fields from config.example.yaml into config.yaml"
	@echo "  make check           - Check if all required tools are installed"
	@echo "  make check-agent-guidance - Validate scoped AGENTS.md file and chain budgets"
	@echo "  make detect-thread-boundaries - Inventory backend executor/thread/event-loop boundaries"
	@echo "  make detect-blocking-io        - Inventory blocking IO that may block the backend event loop"
	@echo "  make install         - Install all dependencies (frontend + backend + pre-commit hooks)"
	@echo "  make voice-setup     - Install free local Whisper + Piper models (no speech API key)"
	@echo "  make voice-verify    - Verify local speech dependencies and model assets"
	@echo "  make system-one-laya-setup  - Install Laya + download the selected local checkpoint (DEVICE=auto|cpu|cuda)"
	@echo "  make system-one-laya-serve  - Run the local Laya System One server"
	@echo "  make system-one-laya-status - Check the Laya runtime, cache, and server health"
	@echo "  make extension-install SOURCE=... - Install and enable a trusted Python extension"
	@echo "  make extension-upgrade SOURCE=... - Replace an installed extension and keep its config"
	@echo "  make extension-list              - List configured Python extensions"
	@echo "  make extension-enable NAME=...   - Enable an installed extension"
	@echo "  make extension-disable NAME=...  - Disable an extension without uninstalling it"
	@echo "  make extension-remove NAME=...   - Uninstall a managed extension"
	@echo "  make setup-sandbox   - Pre-pull sandbox container image (recommended)"
	@echo "  make dev             - Start all services in development mode (with hot-reloading)"
	@echo "  make dev-daemon      - Start dev services in background (daemon mode)"
	@echo "  make start           - Start all services in production mode (optimized, no hot-reloading)"
	@echo "  make start-daemon    - Start prod services in background (daemon mode)"
	@echo "  make nginx           - Start nginx alone in the foreground (local dev config)"
	@echo "  make stop            - Stop all running services"
	@echo "  make clean           - Clean up processes and temporary files"
	@echo ""
	@echo "Docker Production Commands:"
	@echo "  make up              - Build and start production Docker services (localhost:2026)"
	@echo "  make down            - Stop and remove production Docker containers"
	@echo ""
	@echo "Docker Development Commands:"
	@echo "  make docker-init     - Pull the sandbox image"
	@echo "  make docker-start    - Start Docker services (mode-aware from config.yaml, localhost:2026)"
	@echo "  make docker-stop     - Stop Docker development services"
	@echo "  make docker-logs     - View Docker development logs"
	@echo "  make docker-logs-frontend - View Docker frontend logs"
	@echo "  make docker-logs-gateway - View Docker gateway logs"
	@echo "  make docker-logs-redis - View Docker Redis logs"

## Setup & Diagnosis
setup:
	@$(BACKEND_UV_RUN) python ../scripts/setup_wizard.py $(SETUP_ARGS)

doctor:
	@$(BACKEND_UV_RUN) python ../scripts/doctor.py

prod-check:
	@$(PYTHON) ./scripts/prod_check.py

# The one diagnostic command: redacted launcher log tails, the rendered run
# trace, config/extensions summaries, git state, toolchain versions, and doctor
# output. Logs and the trace are on by default because a maintainer who has to
# ask for them separately has already lost the reporter; pass --no-logs/--no-trace
# to leave operational data out of the zip entirely.
support-bundle:
	@$(BACKEND_UV_RUN) python ../scripts/support_bundle.py --include-doctor

# Same bundle, scoped to one run's trace records. RUN_ID is the run id from
# `GET /api/threads/{id}/runs/{rid}` or the run metadata in the trace file.
diagnostic-bundle:
	$(if $(and $(filter command line,$(origin RUN_ID)),$(strip $(value RUN_ID))),,$(error usage: make diagnostic-bundle RUN_ID=<run-id>))
	@$(BACKEND_UV_RUN) python ../scripts/support_bundle.py --include-doctor --run-id "$(RUN_ID)"

# Bounded launcher logs, portably. `start.ps1` grew its own rotation; this is the
# same budget for `make dev` / `make start` and for any launcher that goes
# through the Makefile. Not run by start.sh/start.bat/watchdog.bat when they are
# invoked directly - see scripts/rotate_logs.py.
logs-rotate:
	@$(PYTHON) ./scripts/rotate_logs.py

# Guarded source updater. `update-check` is read-only; `update-apply` is an
# explicit operator command and refuses to run without the confirmation flag.
update-status:
	@$(BACKEND_UV_RUN) --no-sync python ../scripts/auto_update.py status --json

update-check:
	@$(BACKEND_UV_RUN) --no-sync python ../scripts/auto_update.py check --force --json

update-apply:
	@$(BACKEND_UV_RUN) --no-sync python ../scripts/auto_update.py apply --yes --force --json

update-recover:
	@$(BACKEND_UV_RUN) --no-sync python ../scripts/auto_update.py recover --json

update-skip:
	$(if $(strip $(VERSION)),,$(error usage: make update-skip VERSION=x.y.z))
	@$(BACKEND_UV_RUN) --no-sync python ../scripts/auto_update.py skip --version "$(VERSION)" --json

detect-thread-boundaries:
	@$(BACKEND_UV_RUN) python ../scripts/detect_thread_boundaries.py --json-output ../.agent-workspace/thread-boundary-inventory.json

detect-blocking-io:
	@$(MAKE) -C backend detect-blocking-io

config:
	@$(PYTHON) ./scripts/configure.py

config-upgrade:
	@$(RUN_SHELL_SCRIPT) ./scripts/config-upgrade.sh

# Check required tools
check:
	@$(PYTHON) ./scripts/check.py

check-agent-guidance:
	@$(PYTHON) ./scripts/check_agent_guidance.py

# Install all dependencies
install:
	@echo "Installing backend dependencies..."
	@cd backend && uv sync --locked
	@echo "Installing frontend dependencies..."
	@cd frontend && $(FRONTEND_PNPM) install
	@echo "Installing pre-commit hooks..."
	@uv tool install pre-commit
	@pre-commit install --overwrite
	@echo "✓ All dependencies installed"
	@echo ""
	@echo "=========================================="
	@echo "  Optional: Pre-pull Sandbox Image"
	@echo "=========================================="
	@echo ""
	@echo "If you plan to use Docker/Container-based sandbox, you can pre-pull the image:"
	@echo "  make setup-sandbox"
	@echo ""

# Free local speech: packages plus pinned model assets. Runtime never calls a
# paid speech API and never downloads model weights implicitly.
voice-setup:
	@cd backend && uv sync --locked --extra voice
	@cd backend && uv run --no-sync python scripts/setup_voice.py

voice-verify:
	@cd backend && uv run --no-sync python scripts/setup_voice.py --verify-only --skip-warmup

# Laya is intentionally installed in an ignored, project-local environment so its
# PyTorch/Transformers stack never becomes a mandatory Alpha dependency.
system-one-laya-setup:
	@cd backend && uv run --no-sync python scripts/system_one_laya_setup.py setup $(if $(MODEL),--model $(MODEL),) --device $(DEVICE)

system-one-laya-serve:
	@cd backend && uv run --no-sync python scripts/system_one_laya_setup.py serve

system-one-laya-status:
	@cd backend && uv run --no-sync python scripts/system_one_laya_setup.py status

extension-install: export AGENT_WORKSPACE_EXTENSION_SOURCE := $(value SOURCE)
extension-install:
	$(if $(and $(filter command line,$(origin SOURCE)),$(strip $(value SOURCE))),,$(error usage: make extension-install SOURCE=<package|git-url|dir>))
	@cd backend && uv run --frozen --no-group extensions agent-workspace extensions install --source-env __agent_workspace_extension_source__

extension-upgrade: export AGENT_WORKSPACE_EXTENSION_SOURCE := $(value SOURCE)
extension-upgrade:
	$(if $(and $(filter command line,$(origin SOURCE)),$(strip $(value SOURCE))),,$(error usage: make extension-upgrade SOURCE=<package|git-url|dir>))
	@cd backend && uv run --frozen --no-group extensions agent-workspace extensions upgrade --source-env __agent_workspace_extension_source__

extension-list:
	@cd backend && uv run --frozen --no-group extensions agent-workspace extensions list

extension-enable: export AGENT_WORKSPACE_EXTENSION_NAME := $(value NAME)
extension-enable:
	$(if $(and $(filter command line,$(origin NAME)),$(strip $(value NAME))),,$(error usage: make extension-enable NAME=<extension>))
	@cd backend && uv run --frozen --no-group extensions agent-workspace extensions enable --name-env __agent_workspace_extension_name__

extension-disable: export AGENT_WORKSPACE_EXTENSION_NAME := $(value NAME)
extension-disable:
	$(if $(and $(filter command line,$(origin NAME)),$(strip $(value NAME))),,$(error usage: make extension-disable NAME=<extension>))
	@cd backend && uv run --frozen --no-group extensions agent-workspace extensions disable --name-env __agent_workspace_extension_name__

extension-remove: export AGENT_WORKSPACE_EXTENSION_NAME := $(value NAME)
extension-remove:
	$(if $(and $(filter command line,$(origin NAME)),$(strip $(value NAME))),,$(error usage: make extension-remove NAME=<extension>))
	@cd backend && uv run --frozen --no-group extensions agent-workspace extensions remove --name-env __agent_workspace_extension_name__

# Pre-pull sandbox Docker image (optional but recommended)
setup-sandbox:
	@$(RUN_SHELL_SCRIPT) ./scripts/setup-sandbox.sh

# Start all services in development mode (with hot-reloading)
# Logs are rotated first: a long-lived `make dev` session would otherwise grow
# logs/*.log without bound, and this is the moment it is safe to rename them
# (no process holds the descriptor yet). See scripts/rotate_logs.py.
dev:
	@$(PYTHON) ./scripts/check.py
	@$(PYTHON) ./scripts/rotate_logs.py
	@$(RUN_SHELL_SCRIPT) ./scripts/serve.sh --dev

# Start all services in production mode (with optimizations).
# SKIP_FRONTEND_BUILD=1 reuses the existing frontend build instead of running
# `next build`; see scripts/serve.sh --skip-frontend-build.
start:
	@$(PYTHON) ./scripts/check.py
	@$(PYTHON) ./scripts/rotate_logs.py
	@$(RUN_SHELL_SCRIPT) ./scripts/serve.sh --prod $(if $(filter 1,$(SKIP_FRONTEND_BUILD)),--skip-frontend-build)

# Start all services in daemon mode (background)
dev-daemon:
	@$(PYTHON) ./scripts/check.py
	@$(PYTHON) ./scripts/rotate_logs.py
	@$(RUN_SHELL_SCRIPT) ./scripts/serve.sh --dev --daemon

# Start prod services in daemon mode (background)
start-daemon:
	@$(PYTHON) ./scripts/check.py
	@$(PYTHON) ./scripts/rotate_logs.py
	@$(RUN_SHELL_SCRIPT) ./scripts/serve.sh --prod --daemon $(if $(filter 1,$(SKIP_FRONTEND_BUILD)),--skip-frontend-build)

# Start nginx alone in the foreground with the local dev config
nginx:
	@$(RUN_SHELL_SCRIPT) ./scripts/nginx.sh

# Stop all services
stop:
	@$(RUN_SHELL_SCRIPT) ./scripts/serve.sh --stop

# Clean up
clean: stop
	@echo "Cleaning up..."
	@-rm -rf backend/.agent-workspace 2>/dev/null || true
	@-rm -rf logs/*.log 2>/dev/null || true
	@echo "✓ Cleanup complete"

# ==========================================
# Docker Development Commands
# ==========================================

# Initialize Docker containers and install dependencies
docker-init:
	@$(RUN_SHELL_SCRIPT) ./scripts/docker.sh init

# Start Docker development environment
docker-start:
	@$(RUN_SHELL_SCRIPT) ./scripts/docker.sh start

# Stop Docker development environment
docker-stop:
	@$(RUN_SHELL_SCRIPT) ./scripts/docker.sh stop

# View Docker development logs
docker-logs:
	@$(RUN_SHELL_SCRIPT) ./scripts/docker.sh logs

# View Docker development logs
docker-logs-frontend:
	@$(RUN_SHELL_SCRIPT) ./scripts/docker.sh logs --frontend
docker-logs-gateway:
	@$(RUN_SHELL_SCRIPT) ./scripts/docker.sh logs --gateway
docker-logs-redis:
	@$(RUN_SHELL_SCRIPT) ./scripts/docker.sh logs --redis

# ==========================================
# Production Docker Commands
# ==========================================

# Build and start production services
up:
	@$(RUN_SHELL_SCRIPT) ./scripts/deploy.sh

# Stop and remove production containers
down:
	@$(RUN_SHELL_SCRIPT) ./scripts/deploy.sh down

# ==========================================
# Agent ergonomics (added: safe, additive)
# ==========================================

# Frontend typecheck + unit tests in one shot.
verify:
	@cd frontend && $(FRONTEND_PNPM) run verify

# Snapshot the working tree (non-destructive: git stash create).
checkpoint:
	@powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checkpoint.ps1

rollback:
	@powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checkpoint.ps1 -Restore

# Scan staged changes for secrets / destructive patterns (add -Strict to fail).
guardrails:
	@powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/guardrails.ps1

# Print AGENTS.md / CLAUDE.md / SOUL.md for pasting into a session.
context:
	@powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/print-context.ps1

# Allowlist-only command runner. Example: make safe-exec CMD="git status"
safe-exec:
	@powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/safe-exec.ps1 -Command "$(CMD)"
