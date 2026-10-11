# Contributing to Alpha

> **Thank you for your interest in contributing to Alpha!** This guide will help you set up your development environment and understand our development workflow.

## 📋 Table of Contents

1. [Working with Git Worktrees](#working-with-git-worktrees)
2. [Development Environment Setup](#development-environment-setup)
3. [Code Style & Quality](#code-style--quality)
4. [Development Workflow](#development-workflow)
5. [Testing](#testing)
6. [Pull Request Process](#pull-request-process)
7. [AI Assistance Disclosure](#ai-assistance-disclosure)
8. [Need Help?](#need-help)

---

## 🌲 Working with Git Worktrees

This repository is large and, on Windows in particular, `git worktree add` can produce an **incomplete** working tree **with no error**: files present in the index/HEAD are missing on disk. Committing from such a tree is dangerous — a naive `git add <dir>` then stages **mass deletions** and can create a commit that removes thousands of files.

### Before You Stage or Commit in Any Worktree

```bash
make verify-checkout          # fails loudly on a short checkout
# or, directly:
python scripts/verify_checkout.py
git ls-files --deleted | wc -l   # must be 0
```

### Rules

- **Verify the checkout first** (`make verify-checkout`).
- **Never `git add <directory>`** — stage **explicit file paths** so a missing file is never silently staged as a deletion.
- If a worktree is short, repair it with `git checkout -- .` in that worktree (or recreate it) before doing anything else.
- On Windows, ensure `git config --global core.longpaths true`.

---

## 🛠️ Development Environment Setup

We offer two development environments. **Docker is recommended** for the most consistent and hassle-free experience.

### Option 1: Docker Development (Recommended)

Docker provides a consistent, isolated environment with all dependencies pre-configured.

#### Prerequisites

- Docker Desktop or Docker Engine
- Docker Compose **v2.24 or newer** (check with `docker compose version`)
- pnpm (for caching optimization)

#### Setup Steps

1. **Configure the application**:
   ```bash
   # Copy example configuration
   cp config.example.yaml config.yaml

   # Set your API keys
   export OPENAI_API_KEY="your-key-here"
   # or edit config.yaml directly
   ```

2. **Initialize Docker environment** (first time only):
   ```bash
   make docker-init
   ```
   This will:
   - Build Docker images
   - Install frontend dependencies (pnpm)
   - Install backend dependencies (uv)
   - Share pnpm cache with host for faster builds

3. **Start development services**:
   ```bash
   make docker-start
   ```
   `make docker-start` reads `config.yaml` and starts `provisioner` only for provisioner/Kubernetes sandbox mode.

   Prefer this wrapper over invoking Compose yourself: it checks your Compose version, creates the missing `.env` files, and exports `ALPHA_ROOT`.

   If you do run Compose directly, run it **from the repository root** and set `ALPHA_ROOT` to the absolute path of your checkout. Compose interpolates that variable into host-side paths (`ALPHA_HOST_BASE_DIR`, `THREADS_HOST_PATH`) that the AIO and provisioner sandbox modes bind-mount; leaving it unset renders them as `/backend/.alpha`, so those mounts silently miss your checkout instead of failing.

   ```bash
   # macOS / Linux
   ALPHA_ROOT="$PWD" docker compose -f docker/docker-compose-dev.yaml up --build

   # Windows PowerShell
   $env:ALPHA_ROOT = (Get-Location).Path
   docker compose -f docker/docker-compose-dev.yaml up --build
   ```

   Do not reuse that `-f` path from inside `docker/` — it resolves to `docker/docker/docker-compose-dev.yaml` and fails with a file-not-found error.

   All services will start with hot-reload enabled:
   - Frontend changes are automatically reloaded
   - Backend changes trigger automatic restart
   - Gateway-hosted LangGraph-compatible runtime supports hot-reload

4. **Access the application**:
   - Web Interface: http://localhost:2026
   - API Gateway: http://localhost:8001
   - LangGraph-compatible API: http://localhost:8001/api/langgraph/*

### Option 2: Local Development

If you prefer to run services directly on your machine:

#### Prerequisites

Check that you have all required tools installed:

```bash
make check
```

Required tools:
- Node.js 22+
- pnpm
- uv (Python package manager)
- nginx

#### Setup Steps

1. **Configure the application** (same as Docker setup above)

2. **Install dependencies** (this also sets up pre-commit hooks):
   ```bash
   make install
   ```

3. **Run development server** (starts all services with nginx):
   ```bash
   make dev
   ```

4. **Access the application**:
   - Web Interface: http://localhost:2026
   - All API requests are automatically proxied through nginx

#### Manual Service Control

If you need to start services individually:

1. **Start backend service**:
   ```bash
   # Terminal 1: Start Gateway API + embedded agent runtime (port 8001)
   cd backend
   make dev
   ```

2. **Start Frontend** (port 3000):
   ```bash
   cd frontend
   pnpm dev
   ```

3. **Start nginx** (run from the repo root):
   ```bash
   make nginx
   ```

   This runs `scripts/nginx.sh`, which launches nginx in the foreground the same way `scripts/serve.sh` (used by `make dev` / `make start`) does: it pre-creates the `logs/` and `temp/` directories and uses the local dev config at `docker/nginx/nginx.local.conf`.

### Nginx Configuration

The nginx configuration provides:
- Unified entry point on port 2026
- Rewrites `/api/langgraph/*` to Gateway's LangGraph-compatible API (8001)
- Routes other `/api/*` endpoints to Gateway API (8001)
- Routes non-API requests to Frontend (3000)
- Same-origin API routing; split-origin or port-forwarded browser clients should use the Gateway `GATEWAY_CORS_ORIGINS` allowlist
- SSE/streaming support for real-time agent responses
- Optimized timeouts for long-running operations

---

## 🎨 Code Style & Quality

### Backend (Python)
- **Formatter/Linter**: `ruff` (replaces black, isort, flake8)
- **Line length**: 240 characters
- **Python**: 3.12+ with type hints
- **Quotes**: Double quotes
- **Indentation**: 4 spaces

```bash
cd backend
make lint          # ruff check
make format        # ruff format --check
make typecheck     # mypy (if configured)
```

### Frontend (TypeScript)
- **Formatter**: Prettier
- **Linter**: ESLint (when configured)
- **TypeScript**: Strict mode
- **Tailwind**: Utility classes; match density of neighboring components

```bash
cd frontend
pnpm format:write   # Prettier format
pnpm lint           # ESLint (when configured)
pnpm typecheck      # tsc --noEmit
pnpm verify         # Typecheck + tests
```

---

## 🔄 Development Workflow

### 1. Create a Feature Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Your Changes

Follow the existing patterns and conventions in the codebase. Key principles:

- **No fabricated state** — Every number, status, count must come from a real API response
- **Failures surface the server's reason** — Never swallow errors into generic messages
- **Defaults that are off must look off** — Disabled features render as disabled with reason
- **Silence is not success** — Loading, empty, or unavailable states must be explicit

### 3. Format and Lint

```bash
# Backend
cd backend && make format && make lint

# Frontend
cd frontend && pnpm format:write && pnpm lint
```

### 4. Test Your Changes

```bash
# Backend
cd backend && make test

# Frontend
cd frontend && pnpm verify
```

### 5. Commit Your Changes

```bash
git add <explicit-file-paths>
git commit -m "feat: description of your changes"
```

**Commit Message Format**:
```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`

---

## 🧪 Testing

### Backend Tests

```bash
cd backend

# Default offline tests
make test

# Strict blocking-I/O tests (fails on blocking I/O in async code)
make test-blocking-io

# Live tests (real APIs, requires credentials)
ALPHA_RUN_LIVE_TESTS=1 make test-live

# Specific test file
PYTHONPATH=. uv run pytest tests/test_feature.py -v
```

**Test Requirements**:
- Write tests in `backend/tests/` following naming convention `test_<feature>.py`
- Run both offline targets before and after changes
- Tests must pass before a feature is considered complete
- For circular import issues in tests, add a `sys.modules` mock in `tests/conftest.py`
- Run a specific file with `PYTHONPATH=. uv run pytest tests/test_<feature>.py -v`

### Frontend Tests

```bash
cd frontend

# TypeScript typecheck
pnpm typecheck

# Unit tests (plain Node.js, no server required)
pnpm test

# All verification (typecheck + tests)
pnpm verify
```

**Client Tests** are plain `node --test` files (`*.test.mjs`) that transpile the TS module and run it against a stubbed HTTP layer. They pin real paths/verbs and honesty inversions.

---

## 📤 Pull Request Process

### Before Submitting

- [ ] Run `make format` (backend) and `pnpm format:write` (frontend)
- [ ] Run `make test` and `pnpm verify` — all tests pass
- [ ] Update documentation if needed
- [ ] Add tests for new functionality
- [ ] Ensure no breaking changes without migration path

### PR Requirements

1. **Title**: Clear, descriptive, follows conventional commits
2. **Description**: 
   - What changed and why
   - Screenshots for UI changes
   - Test plan / how to verify
   - Breaking changes noted
3. **AI Assistance Section**: Complete the "AI assistance" section of the [PR template](.github/pull_request_template.md):
   - Which tool(s) you used (or `none`)
   - How you used them
   - Confirmation that a human has read, understands, and takes responsibility for the change
4. **Linked Issues**: Reference related issues (`Fixes #123`, `Relates to #456`)

### Review Process

1. **Automated Checks**: CI runs lint, typecheck, tests
2. **Code Review**: At least one maintainer approval required
3. **Merge**: Squash and merge after approval

---

## 🤖 AI Assistance Disclosure

Alpha is an AI project and we welcome AI-assisted contributions. To help reviewers calibrate how closely to read a change, **every pull request must complete the "AI assistance" section of the [PR template](.github/pull_request_template.md)**:

- Which tool(s) you used (or `none`)
- How you used them
- A confirmation that a human has read, understands, and takes responsibility for the change

Please don't delete the section. PRs that ignore it may be asked to fill it in before review.

---

## 🆘 Need Help?

- **Documentation**: [docs/](docs/) — Architecture, Configuration, API, Deployment
- **Discord**: [discord.gg/alpha](https://discord.gg/alpha) — Real-time chat with maintainers
- **GitHub Discussions**: [github.com/itsPremkumar/alpha/discussions](https://github.com/itsPremkumar/alpha/discussions) — Questions, ideas, announcements
- **Issues**: [github.com/itsPremkumar/alpha/issues](https://github.com/itsPremkumar/alpha/issues) — Bug reports, feature requests

---

## 📜 License

By contributing to Alpha, you agree that your contributions will be licensed under the [MIT License](LICENSE).