# Getting Started with Alpha

## The short version (Windows)

Two commands, in this order. Everything else on this page is a variation.

```bat
install.bat      :: provisions everything, then starts Alpha and verifies it
start.bat        :: start later (double-click this any time)
stop.bat         :: stop cleanly
```

Then open **<http://localhost:2026>**.

`install.bat` does all of this for you, with no administrator rights and nothing
installed outside the project folder:

| Step | What it does |
|------|--------------|
| uv | Downloads a pinned uv into `.tools/` (never your user profile) and uses it to install CPython 3.12 |
| Node.js | Resolves your existing install, or reports exactly what is missing |
| nginx | Downloads the pinned Windows build into `.tools/` — this is the `:2026` entry point |
| Dependencies | `uv sync --locked` for the backend, `pnpm install` for the frontend |
| Config | Creates `config.yaml` (which carries every model setting), `.env`, `frontend/.env`, `extensions_config.json` from the tracked `*.example` templates, generating a secure `BETTER_AUTH_SECRET` |
| Autostart | Offers to register the three Windows scheduled tasks (start at logon, health check every 5 min, tray status) |
| Verification | Starts Alpha, waits for the Gateway and frontend, checks the health endpoint, the launcher process, and the watchdog chain — and **fails loudly rather than claiming success** if any of it is not healthy |

To uninstall later: `uninstall.ps1`. To remove just the autostart tasks:
`scripts\unregister_autostart.ps1`.

## Requirements

### System
- **OS**: Windows 10/11 (one-click path), macOS 12+ or Linux for the manual paths
- **CPU**: 4+ cores recommended
- **RAM**: 8GB minimum, 16GB+ recommended
- **Disk**: 10GB free
- **Network**: internet access for model APIs and the initial downloads

### What you must already have
| Tool | Why | Provided by `install.bat`? |
|------|-----|----------------------------|
| Git | you had to clone the repo | no — install before cloning |
| PowerShell 5.1+ | runs the installer | built into Windows |
| Git Bash | `make`/shell scripts | ships with Git for Windows |

### What the installer provides for you
| Tool | Version | Provisioned |
|------|---------|-------------|
| Python | 3.12 | yes, via uv into `.tools/` |
| uv | 0.11.1 (pinned) | yes, into `.tools/` |
| Node.js | 22+ | resolved; you install it yourself if absent |
| pnpm | 10.26.2 (pinned) | yes, via Corepack |
| nginx | 1.27.x (pinned series) | yes, into `.tools/` |

Nothing is written to `C:\Program Files` or any shared location, so a checkout
can be deleted to remove Alpha completely, and two checkouts never collide.

## Installation Methods

### Method 1: One-click installer (recommended on Windows)

Run `install.bat`, or from PowerShell:

```powershell
.\install.ps1                 # interactive, with verification
.\install.ps1 -SkipFrontend   # backend only
.\install.ps1 -SkipVerification  # install, do not start
```

Then `start.bat`, or `make dev`, and open <http://localhost:2026>.

If nginx could not be downloaded, Alpha still runs: the Gateway is on
`http://localhost:8001` and the web UI on `http://localhost:3000`. The installer
and the launcher both say so explicitly rather than advertising a port that is
not listening.

### Method 2: Docker Deployment (Recommended for Servers)

#### Quick Start
```bash
# Clone repository
git clone https://github.com/itsPremkumar/alpha.git
cd alpha

# Configure environment
cp .env.production.example .env
# Edit .env with your API keys and secrets

# Generate config files
make config

# Install dependencies
make install

# Pre-flight check
python scripts/prod_check.py

# Start production stack
make up

# Access at http://localhost:2026
```

#### Docker Commands
```bash
make up          # Build and start all services
make down        # Stop and remove containers
make docker-logs # View logs (follow mode)
make docker-start # Start existing containers
make docker-stop  # Stop containers
```

### Method 3: Local Development (Full Hot-Reload)

#### Setup
```bash
# Clone and enter
git clone https://github.com/itsPremkumar/alpha.git
cd alpha

# Generate local config (REQUIRED before first boot)
make config

# Install all dependencies
make install

# Start development stack
make dev
```

#### What `make dev` Starts
- **Gateway API**: `http://localhost:8001` (hot-reload)
- **Frontend**: `http://localhost:3000` (Webpack dev server, HMR)
- **Nginx**: `http://localhost:2026` (proxies to above)

#### Stop Development
```bash
make stop
```

## Configuration

### Required: Model API Key
At minimum, you need one model provider configured. Run the interactive wizard:

```bash
make setup
```

Or configure manually by editing `config.yaml`:

```yaml
models:
  - name: "primary"
    provider: "openrouter"
    model: "unbiased/pareto"
    api_key: "${OPENROUTER_API_KEY}"
    # Or for other providers:
    # provider: "openai"
    # model: "gpt-4o"
    # api_key: "${OPENAI_API_KEY}"
```

### Supported Model Providers
| Provider | Models | Credential |
|----------|--------|------------|
| OpenAI | GPT-4o, GPT-4, GPT-3.5 | `OPENAI_API_KEY` |
| Anthropic | Claude 3.5 Sonnet, Opus, Haiku | `ANTHROPIC_API_KEY` |
| Google | Gemini 1.5 Pro, Flash | `GEMINI_API_KEY` |
| DeepSeek | DeepSeek-V3, R1 | `DEEPSEEK_API_KEY` |
| Moonshot | Kimi K2 | `MOONSHOT_API_KEY` |
| MiniMax | MiniMax-01 | `MINIMAX_API_KEY` |
| Ollama | Local models | none - set `use: langchain_ollama:ChatOllama` + `base_url:` per model in `config.yaml` |
| vLLM | Self-hosted | none - set `base_url:` per model in `config.yaml` |
| OpenRouter | 100+ models | `OPENROUTER_API_KEY` |
| Codex CLI | Codex login | `CODEX_AUTH_PATH` |
| Claude CLI | Claude login | `CLAUDE_CODE_OAUTH_TOKEN` (or `CLAUDE_CODE_CREDENTIALS_PATH`, `ANTHROPIC_AUTH_TOKEN`) |

### Optional: External Integrations
Edit `extensions_config.json` for:
- **MCP Servers**: Custom tool servers
- **Skills**: Additional agent capabilities
- **IM Channels**: Telegram, Slack, Discord, etc.

### Environment Variables (`.env`)
```bash
# Required for production
BETTER_AUTH_SECRET="generate-with-openssl-rand-base64-32"
DATABASE_URL="postgresql://user:pass@localhost:5432/alpha"
REDIS_URL="redis://localhost:6379"

# Model API Keys (alternative to config.yaml)
OPENAI_API_KEY="sk-..."
ANTHROPIC_API_KEY="sk-ant-..."
OPENROUTER_API_KEY="sk-or-..."

# Optional: Tracing (each provider also needs its own *_TRACING=true flag)
LANGSMITH_TRACING="true"
LANGSMITH_API_KEY="lsv2_..."
LANGFUSE_TRACING="true"
LANGFUSE_PUBLIC_KEY="pk-lf-..."
LANGFUSE_SECRET_KEY="sk-lf-..."

# Optional: IM Channels
TELEGRAM_BOT_TOKEN="..."
SLACK_BOT_TOKEN="xoxb-..."
DISCORD_BOT_TOKEN="..."
```

## First Run Verification

### Check Services
```bash
# Health checks
curl http://localhost:2026/health        # Nginx + Gateway
curl http://localhost:8001/health        # Gateway direct
curl http://localhost:8001/health/ready  # Readiness (DB, models)

# Operator endpoints (authenticated)
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/ops/version
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/ops/status
```

### Verify Frontend
Open `http://localhost:2026` (Docker) or `http://localhost:3000` (dev) in browser:
1. Should see chat interface
2. Create a new thread
3. Send a test message
4. Verify agent responds

### Run Tests
```bash
# Backend tests
cd backend && make test

# Frontend tests
cd frontend && pnpm test

# Lint/format
cd backend && make lint
cd backend && make format
cd frontend && pnpm check
```

## Common Issues & Solutions

### "config.yaml not found"
```bash
make config
```
Must run before `make dev` or `make up`.

### Port Conflicts
Default ports: 2026 (nginx), 8001 (gateway), 3000 (frontend), 8002 (provisioner)
```bash
# Check what's using ports
netstat -tulpn | grep -E '2026|8001|3000|8002'

# Change ports in docker-compose.yaml or .env
```

### Python/uv Issues
```bash
# Reinstall uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clear caches
uv cache clean
rm -rf backend/.venv
make install
```

### Node/pnpm Issues
```bash
# Use Corepack (recommended)
corepack enable
corepack prepare pnpm@latest --activate

# Or reinstall
npm install -g pnpm
cd frontend && pnpm install
```

### Docker Issues
```bash
# Clean start
make down
docker system prune -f
make up

# Check container logs
make docker-logs
```

### Windows Path Issues
- Use Git Bash for `make` commands
- Ensure `ALPHA_ROOT` resolves correctly
- Use forward slashes in paths

## Next Steps

### Explore the UI
1. **Chat** - Main conversation interface
2. **Workspace** - Threads, Overview, Team, Scheduled Tasks
3. **Agents** - Bot gallery, fleet management
4. **Projects** - Collaborative workspaces
5. **Memory** - Knowledge graph, session search
6. **Skills** - Skill library, authoring, curation
7. **System** - Logs, configuration, health

### Learn Key Workflows
- **Single Agent**: Send prompt → agent runs tools to completion
- **Resume**: Model-only interruptions continue automatically from the durable checkpoint. Runs paused for an ambiguous external action appear on Overview with a Resume action for review.
- **Team**: Team page → Objective → Run team → Bots work in parallel
- **Scheduled**: Scheduled Tasks → Create → Background execution

### Advanced Configuration
- Read `CONFIGURATION.md` for all config options
- Read `ARCHITECTURE.md` for system design
- Read `PRODUCTION.md` for production deployment
- Read `DEVELOPMENT.md` for contributing

## Getting Help

- **Documentation**: This `docs/` folder
- **Issues**: GitHub Issues
- **Discussions**: GitHub Discussions
- **Support Bundle**: `make support-bundle` for troubleshooting
- **Doctor**: `make doctor` for system checks