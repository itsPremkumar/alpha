# Configuration Reference

## Configuration Files

| File | Purpose | Location | Committed |
|------|---------|----------|-----------|
| `config.yaml` | Main app configuration | Repo root | No (gitignored) |
| `extensions_config.json` | MCP servers + skills | Repo root | No (gitignored) |
| `.env` | Secrets and API keys | Repo root | No (gitignored) |
| `config.example.yaml` | Template for config.yaml | Repo root | Yes |
| `extensions_config.example.json` | Template for extensions_config.json | Repo root | Yes |
| `.env.production.example` | Template for .env | Repo root | Yes |

## config.yaml Schema

### Root Structure
```yaml
# Model Configuration
models:
  - name: string           # Unique identifier
    provider: string       # openai, anthropic, google, deepseek, ollama, vllm, openrouter, codex, claude
    model: string          # Model identifier
    api_key: string        # Environment variable reference or literal
    base_url: string       # Optional custom endpoint
    max_tokens: integer    # Output token limit
    temperature: float     # Sampling temperature
    top_p: float           # Nucleus sampling
    reasoning: object      # Reasoning configuration (if supported)
    fallback: list         # Fallback model names

# Sandbox Configuration
sandbox:
  mode: string             # local, docker, provisioner
  docker:
    image: string          # Sandbox container image
    network: string        # Docker network name
    cpu_limit: string      # CPU quota (e.g., "2.0")
    memory_limit: string   # Memory limit (e.g., "4g")
    timeout: integer       # Execution timeout seconds
  provisioner:
    url: string            # Provisioner service URL
    namespace: string      # Kubernetes namespace
    service_account: string # K8s service account

# Database Configuration
database:
  backend: string          # sqlite, postgres
  sqlite:
    path: string           # Database file path
  postgres:
    host: string
    port: integer
    database: string
    user: string
    password: string       # Use env var reference
    pool_size: integer
    max_overflow: integer

# Redis Configuration
redis:
  url: string              # redis://host:port/db
  password: string         # Optional
  max_connections: integer

# Memory Configuration
memory:
  long_term:
    enabled: boolean
    provider: string       # sqlite, postgres, chroma, pgvector
    collection: string
  cognitive:
    enabled: boolean
    storage_dir: string    # Per-owner directory
  episodic:
    enabled: boolean
    max_episodes: integer

# Tool Configuration
tools:
  builtin:
    enabled: list          # List of enabled built-in tools
  mcp:
    servers: list          # MCP server configurations
  skills:
    auto_load: boolean     # Auto-load skills from directories
    directories: list      # Skill directories to scan

# Authentication
auth:
  enabled: boolean
  secret: string           # Better Auth secret (from env)
  providers:
    email_password:
      enabled: boolean
    oauth:
      github:
        enabled: boolean
        client_id: string
        client_secret: string
      google:
        enabled: boolean
        client_id: string
        client_secret: string

# IM Channels
channels:
  telegram:
    enabled: boolean
    bot_token: string
    webhook_url: string
  slack:
    enabled: boolean
    bot_token: string
    signing_secret: string
    app_token: string
  discord:
    enabled: boolean
    bot_token: string
    client_id: string
    client_secret: string
  feishu:
    enabled: boolean
    app_id: string
    app_secret: string
    verification_token: string
    encrypt_key: string
  dingtalk:
    enabled: boolean
    client_id: string
    client_secret: string

# Tracing
tracing:
  langsmith:
    enabled: boolean
    api_key: string
    project: string
  langfuse:
    enabled: boolean
    public_key: string
    secret_key: string
    host: string
  monocle:
    enabled: boolean
    api_key: string

# Token Budgets
token_budget:
  per_run: integer         # Max tokens per run
  cumulative: integer      # Max tokens per thread
  alert_threshold: float   # Alert at percentage (0.0-1.0)
  hard_limit: boolean      # Hard stop at limit

# Scheduler
scheduler:
  enabled: boolean
  max_concurrent_runs: integer
  queue_timeout_seconds: integer
  wake_gate:
    enabled: boolean
    preflight_checks: list

# Extensions
plugins:
  - string                 # Python import paths for extensions

# Logging
logging:
  level: string            # DEBUG, INFO, WARNING, ERROR
  format: string           # json, text
  file: string             # Optional log file path

# Security
security:
  cors_origins: list       # Allowed CORS origins
  rate_limit:
    enabled: boolean
    requests_per_minute: integer
  headers:
    hsts: boolean          # HTTPS only
    csp: string            # Content Security Policy
```

## extensions_config.json Schema

```json
{
  "mcpServers": {
    "server-name": {
      "command": "string",
      "args": ["string"],
      "env": {
        "KEY": "value"
      },
      "transport": "stdio|sse",
      "url": "string (for SSE)",
      "enabled": true
    }
  },
  "skills": {
    "enabled": ["skill-id"],
    "disabled": ["skill-id"],
    "directories": [
      "skills/public",
      "skills/custom",
      ".agent-workspace/integrations/skills"
    ]
  }
}
```

## Environment Variables (.env)

### Required for Production
```bash
# Authentication secret (generate: openssl rand -base64 32)
BETTER_AUTH_SECRET="your-secret-here"

# Database
DATABASE_URL="postgresql://user:pass@host:5432/dbname"
# Or for SQLite:
# DATABASE_URL="sqlite:///./data/app.db"

# Redis
REDIS_URL="redis://localhost:6379/0"

# Model API Keys (at least one required)
OPENAI_API_KEY="sk-..."
ANTHROPIC_API_KEY="sk-ant-..."
GOOGLE_API_KEY="..."
DEEPSEEK_API_KEY="..."
MOONSHOT_API_KEY="..."
MINIMAX_API_KEY="..."
OPENROUTER_API_KEY="sk-or-..."

# Optional: Local models
OLLAMA_BASE_URL="http://localhost:11434"
VLLM_BASE_URL="http://localhost:8000/v1"
```

### Optional: Tracing
```bash
LANGSMITH_API_KEY="lsv2_..."
LANGSMITH_PROJECT="alpha"
LANGFUSE_PUBLIC_KEY="pk-lf-..."
LANGFUSE_SECRET_KEY="sk-lf-..."
LANGFUSE_HOST="https://cloud.langfuse.com"
MONOCLE_API_KEY="..."
```

### Optional: IM Channels
```bash
TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
SLACK_BOT_TOKEN="xoxb-..."
SLACK_SIGNING_SECRET="..."
SLACK_APP_TOKEN="xapp-..."
DISCORD_BOT_TOKEN="..."
FEISHU_APP_ID="cli_..."
FEISHU_APP_SECRET="..."
FEISHU_VERIFICATION_TOKEN="..."
FEISHU_ENCRYPT_KEY="..."
DINGTALK_CLIENT_ID="..."
DINGTALK_CLIENT_SECRET="..."
```

### Optional: Feature Flags

- **Frontend dev bundler**: Webpack by default; run `pnpm dev --turbopack` for Turbopack.
- **Gateway log level**: `log_level:` in `config.yaml` (`DEBUG|INFO|WARNING|ERROR`,
  restart-required field in `AppConfig`).
- **Skip frontend build**: `SKIP_FRONTEND_BUILD=1 make prod` or
  `./scripts/serve.sh --prod --skip-frontend-build` — a make/shell variable,
  not an `.env` key (bash does not read `.env`). On Windows, `start.ps1`
  reuses an existing `.next` build automatically (no flag needed).

## Model Configuration Details

### OpenAI
```yaml
- name: "gpt-4o"
  provider: "openai"
  model: "gpt-4o"
  api_key: "${OPENAI_API_KEY}"
  max_tokens: 16384
  temperature: 0.7
```

### Anthropic
```yaml
- name: "claude-3.5-sonnet"
  provider: "anthropic"
  model: "claude-3-5-sonnet-20241022"
  api_key: "${ANTHROPIC_API_KEY}"
  max_tokens: 8192
  temperature: 0.7
```

### Google Gemini
```yaml
- name: "gemini-1.5-pro"
  provider: "google"
  model: "gemini-1.5-pro"
  api_key: "${GOOGLE_API_KEY}"
  max_tokens: 8192
  temperature: 0.7
```

### OpenRouter
```yaml
- name: "union-alpha"
  provider: "openrouter"
  model: "stealth/union-alpha"
  api_key: "${OPENROUTER_API_KEY}"
  base_url: "https://openrouter.ai/api/v1"
  max_tokens: 16384
  temperature: 0.7
```

### Ollama (Local)
```yaml
- name: "llama3.1"
  provider: "ollama"
  model: "llama3.1:70b"
  base_url: "${OLLAMA_BASE_URL}"
  max_tokens: 4096
  temperature: 0.7
```

### vLLM (Self-hosted)
```yaml
- name: "qwen2.5-72b"
  provider: "vllm"
  model: "Qwen/Qwen2.5-72B-Instruct"
  base_url: "${VLLM_BASE_URL}"
  api_key: "dummy"
  max_tokens: 8192
  temperature: 0.7
```

### Codex CLI
```yaml
- name: "codex"
  provider: "codex"
  model: "gpt-4o"
  # Uses CODEX_AUTH from environment
```

### Claude CLI
```yaml
- name: "claude-cli"
  provider: "claude"
  model: "claude-3-5-sonnet-20241022"
  # Uses CLAUDE_AUTH from environment
```

## Sandbox Modes

### Local (Default)
```yaml
sandbox:
  mode: "local"
```
- Runs tools directly on host
- Fastest execution
- Least isolation
- Good for development

### Docker
```yaml
sandbox:
  mode: "docker"
  docker:
    image: "ghcr.io/itsPremkumar/alpha-sandbox:latest"
    network: "alpha-sandbox"
    cpu_limit: "2.0"
    memory_limit: "4g"
    timeout: 300
```
- Container isolation
- Consistent environment
- Requires Docker daemon
- Good for CI/production

### Provisioner (Kubernetes)
```yaml
sandbox:
  mode: "provisioner"
  provisioner:
    url: "http://provisioner:8002"
    namespace: "alpha-sandbox"
    service_account: "alpha-sandbox-sa"
```
- Dynamic pod per execution
- Strong isolation
- Resource quotas
- Requires K8s cluster

## Database Backends

### SQLite (Development)
```yaml
database:
  backend: "sqlite"
  sqlite:
    path: "./data/app.db"
```
- Zero configuration
- Single file
- Not suitable for concurrent writers
- Good for local dev

### PostgreSQL (Production)
```yaml
database:
  backend: "postgres"
  postgres:
    host: "localhost"
    port: 5432
    database: "alpha"
    user: "alpha"
    password: "${POSTGRES_PASSWORD}"
    pool_size: 20
    max_overflow: 10
```
- Full ACID compliance
- Connection pooling
- Horizontal scaling ready
- Required for multi-user

## Memory Providers

### Long-term Memory
```yaml
memory:
  long_term:
    enabled: true
    provider: "pgvector"  # or "chroma", "sqlite"
    collection: "alpha_memory"
```

### Cognitive Memory
```yaml
memory:
  cognitive:
    enabled: true
    storage_dir: "./data/cognitive_memory"
```
- Per-owner isolation
- Atomic fsync snapshots
- Process cache with locks

### Episodic Memory
```yaml
memory:
  episodic:
    enabled: true
    max_episodes: 10000
```
- Experience replay
- Skill learning
- Automatic curation

## Subagent Workforce & Intent Presets

Subagents are configured in `config.yaml` with global timeout defaults, delegation caps, and intent presets (`categories`):

```yaml
subagents:
  timeout_seconds: 1800           # Global default timeout (30 minutes)
  max_total_per_run: 6            # Maximum subagent delegations per turn (1-50)
  token_budget:
    enabled: true
    max_tokens: 2000000           # 2M tokens per run ceiling
  categories:
    general:
      description: "Default intent preset (identity no-op)."
    research:
      description: "Thorough multi-source investigation with verified claims."
      max_turns: 100
      prompt_append: "Be thorough: consult multiple sources, verify load-bearing claims."
    quick:
      description: "Terse low-latency execution for small bounded tasks."
      max_turns: 30
      prompt_append: "Optimize for latency: be terse, skip exhaustive verification."
    deep-research:
      description: "Autonomous multi-hop deep research with 5-pass search and citation verification."
      tools: ["deep_research", "web_search", "web_fetch", "compile_five_pass_search"]
      max_turns: 150
      prompt_append: "Execute an exhaustive multi-hop deep research investigation."
```

## Runtime Configuration Updates

### Via Gateway API
```bash
# Update model config
curl -X PATCH http://localhost:8001/api/config/models \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"models": [...]}'

# Update extensions
curl -X PATCH http://localhost:8001/api/config/extensions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"mcpServers": {...}, "skills": {...}}'
```

### Via CLI
```bash
# List extensions
make extension-list

# Install extension
make extension-install SOURCE=git+https://github.com/user/ext.git

# Enable/disable
make extension-enable NAME=ext-name
make extension-disable NAME=ext-name
```

## Configuration Validation

### Check Config
```bash
# Validate config.yaml
cd backend && python -c "from app.config import load_config; load_config()"

# Check environment
make doctor

# Production pre-flight
make prod-check
```

### Common Errors
| Error | Cause | Fix |
|-------|-------|-----|
| `config.yaml not found` | Missing config | Run `make config` |
| `Invalid model provider` | Typo in provider name | Check supported providers |
| `API key not set` | Env var missing | Add to `.env` or config.yaml |
| `Database connection failed` | Wrong DATABASE_URL | Check DB running and credentials |
| `Redis connection failed` | Wrong REDIS_URL | Check Redis running |
| `Port already in use` | Port conflict | Change port or stop conflicting service |

## Migration Between Versions

### Config Upgrade
```bash
# Automatic migration
./scripts/config-upgrade.sh

# Manual: compare with config.example.yaml
diff config.example.yaml config.yaml
```

### Database Migrations
```bash
# Backend migrations
cd backend && make migrate-rev MESSAGE="description"
cd backend && make migrate-upgrade
```

## Security Best Practices

1. **Never commit secrets** - All config files with secrets are gitignored
2. **Use environment variables** - Reference `${VAR_NAME}` in config.yaml
3. **Rotate keys regularly** - Update `.env` and restart services
4. **Restrict CORS** - Set `security.cors_origins` to specific domains
5. **Enable rate limiting** - Prevent abuse
6. **Use HTTPS in production** - Enable HSTS, configure TLS termination at Nginx
7. **Audit extensions** - Only install trusted extensions
8. **Monitor token usage** - Set budgets and alerts