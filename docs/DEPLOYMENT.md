# Deployment Guide

## Overview

Alpha supports multiple deployment models:
1. **Docker Compose** (Recommended for production servers)
2. **Kubernetes (Helm)** (For scalable cloud deployments)
3. **Windows Desktop App** (End-user distribution)
4. **Local Development** (Development only)

## Docker Compose Deployment

### Architecture

```
                    ┌─────────────────┐
                    │    Internet     │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │     Nginx       │
                    │   (Port 2026)   │
                    │  Reverse Proxy  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐
       │  Gateway    │ │ Frontend  │ │Provisioner│
       │  (Port 8001)│ │ (Port 3000)│ │ (Port 8002)│
       └──────┬──────┘ └───────────┘ └───────────┘
              │
       ┌──────┴──────┐
       │             │
  ┌────▼────┐   ┌────▼────┐
  │PostgreSQL│   │  Redis  │
  │ (Port    │   │ (Port   │
  │  5432)   │   │  6379)  │
  └─────────┘   └─────────┘
```

### Prerequisites

- Docker 24+ and Docker Compose 2.20+
- 4GB+ RAM available for containers
- 10GB+ disk space
- Ports 2026, 8001, 3000, 8002, 5432, 6379 available

### Quick Start

```bash
# 1. Clone repository
git clone https://github.com/itsPremkumar/alpha.git
cd alpha

# 2. Configure environment
cp .env.production.example .env
# Edit .env with your values (see Configuration section)

# 3. Generate config files
make config

# 4. Pre-flight check
python scripts/prod_check.py

# 5. Build and start
make up

# 6. Verify
curl http://localhost:2026/health
# Open http://localhost:2026 in browser
```

### Configuration

#### Required .env Variables
```bash
# Generate with: openssl rand -base64 32
BETTER_AUTH_SECRET="your-32-byte-base64-secret"

# Database (PostgreSQL for production)
DATABASE_URL="postgresql://alpha:secure-password@postgres:5432/alpha"

# Redis
REDIS_URL="redis://redis:6379/0"

# Model API Keys (at least one)
OPENAI_API_KEY="sk-..."
# OR
ANTHROPIC_API_KEY="sk-ant-..."
# OR
OPENROUTER_API_KEY="sk-or-..."

# Optional: Tracing
LANGSMITH_API_KEY="lsv2_..."
LANGFUSE_PUBLIC_KEY="pk-lf-..."
LANGFUSE_SECRET_KEY="sk-lf-..."

# Domain (for CORS, cookies)
NEXT_PUBLIC_APP_URL="https://your-domain.com"
```

#### Optional .env Variables
```bash
# Nginx
BIND_HOST="0.0.0.0"  # Or specific IP
PORT="2026"

# Gateway
GATEWAY_PORT="8001"
GATEWAY_WORKERS="4"

# Frontend
FRONTEND_PORT="3000"

# Provisioner (if using K8s sandbox)
PROVISIONER_ENABLED="false"
PROVISIONER_PORT="8002"

# Database
POSTGRES_PASSWORD="secure-password"
POSTGRES_USER="alpha"
POSTGRES_DB="alpha"

# Redis
REDIS_PASSWORD="secure-password"

# Sandbox
SANDBOX_MODE="docker"  # local, docker, provisioner
SANDBOX_DOCKER_IMAGE="ghcr.io/itsPremkumar/alpha-sandbox:latest"
SANDBOX_CPU_LIMIT="2.0"
SANDBOX_MEMORY_LIMIT="4g"

# Logging
LOG_LEVEL="INFO"

# Rate Limiting
RATE_LIMIT_ENABLED="true"
RATE_LIMIT_RPM="300"
```

### Docker Compose Files

#### Production (docker-compose.yaml)
```yaml
# Main production stack
# Services: nginx, gateway, frontend, postgres, redis
# Uses pre-built images, optimized for production
```

#### Development (docker-compose-dev.yaml)
```yaml
# Development stack with hot-reload
# Services: nginx, gateway (with reload), frontend (webpack), postgres, redis
# Mounts source code for live editing
```

#### CLI Auth (docker-compose.cli-auth.yaml)
```yaml
# For CLI-based authentication flows
# Adds auth service for device code flow
```

### Commands

```bash
# Start production stack
make up

# Start development stack
make docker-start

# Stop all
make down

# View logs
make docker-logs

# View specific service logs
docker compose -f docker/docker-compose.yaml logs -f gateway

# Restart service
docker compose -f docker/docker-compose.yaml restart gateway

# Update images
docker compose -f docker/docker-compose.yaml pull
make up

# Backup database
docker compose -f docker/docker-compose.yaml exec postgres pg_dump -U alpha alpha > backup.sql

# Restore database
docker compose -f docker/docker-compose.yaml exec -T postgres psql -U alpha alpha < backup.sql
```

### Health Checks

```bash
# Overall health
curl http://localhost:2026/health

# Gateway readiness
curl http://localhost:2026/api/health/ready

# Individual services
curl http://localhost:8001/health
curl http://localhost:8001/health/ready
curl http://localhost:3000
```

### Scaling

```bash
# Scale Gateway (stateless)
docker compose -f docker/docker-compose.yaml up -d --scale gateway=3

# Note: Requires sticky sessions for WebSocket/SSE
# Configure Nginx upstream with ip_hash or consistent hashing
```

### Updates

```bash
# Pull latest images
docker compose -f docker/docker-compose.yaml pull

# Rebuild frontend (if config changed)
docker compose -f docker/docker-compose.yaml build frontend

# Rolling update
docker compose -f docker/docker-compose.yaml up -d --no-deps gateway
```

## Kubernetes Deployment (Helm)

### Prerequisites

- Kubernetes 1.25+
- Helm 3.10+
- Ingress controller (nginx-ingress recommended)
- cert-manager for TLS (optional)
- PersistentVolume provisioner

### Install Chart

```bash
# Add repo (if published)
helm repo add alpha https://charts.alpha.dev
helm repo update

# Or install from local
cd deploy/helm/agent-workspace

# Create namespace
kubectl create namespace alpha

# Install with custom values
helm install alpha . \
  --namespace alpha \
  --values values.yaml \
  --set config.betterAuthSecret=$(openssl rand -base64 32) \
  --set config.databaseUrl=postgresql://... \
  --set config.redisUrl=redis://... \
  --set config.openaiApiKey=sk-...
```

### Values.yaml Configuration

```yaml
# deploy/helm/agent-workspace/values.yaml
global:
  imageRegistry: ghcr.io
  imagePullSecrets: [ghcr-secret]

nginx:
  enabled: true
  ingress:
    enabled: true
    className: nginx
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-prod
    hosts:
      - host: alpha.example.com
        paths:
          - path: /
            pathType: Prefix
    tls:
      - secretName: alpha-tls
        hosts: [alpha.example.com]

gateway:
  replicaCount: 2
  resources:
    limits:
      cpu: 2000m
      memory: 2Gi
    requests:
      cpu: 500m
      memory: 1Gi
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPUUtilization: 70

frontend:
  replicaCount: 2
  resources:
    limits:
      cpu: 1000m
      memory: 1Gi
    requests:
      cpu: 250m
      memory: 512Mi

provisioner:
  enabled: false  # Enable for K8s sandbox mode

postgresql:
  enabled: true
  auth:
    postgresPassword: "secure-password"
    username: alpha
    database: alpha
  primary:
    persistence:
      size: 20Gi
    resources:
      limits:
        cpu: 1000m
        memory: 2Gi

redis:
  enabled: true
  auth:
    enabled: true
    password: "secure-password"
  master:
    persistence:
      size: 5Gi
    resources:
      limits:
        cpu: 500m
        memory: 1Gi

config:
  betterAuthSecret: ""  # Set via --set or secret
  databaseUrl: ""       # Set via --set
  redisUrl: ""          # Set via --set
  openaiApiKey: ""      # Set via --set
  anthropicApiKey: ""   # Set via --set
  openrouterApiKey: ""  # Set via --set
  # ... other config options

# External secrets (recommended for production)
externalSecrets:
  enabled: false
  # Configure ExternalSecret resources
```

### Secrets Management

#### Option 1: Helm Secrets
```bash
# Create secret
kubectl create secret generic alpha-secrets \
  --namespace alpha \
  --from-literal=better-auth-secret=$(openssl rand -base64 32) \
  --from-literal=database-url=postgresql://... \
  --from-literal=redis-url=redis://... \
  --from-literal=openai-api-key=sk-...

# Reference in values.yaml
config:
  betterAuthSecret: ${SECRET:alpha-secrets:better-auth-secret}
```

#### Option 2: External Secrets Operator
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: alpha-secrets
  namespace: alpha
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secretsmanager
    kind: ClusterSecretStore
  target:
    name: alpha-secrets
  data:
    - secretKey: better-auth-secret
      remoteRef:
        key: alpha/prod/better-auth-secret
    - secretKey: database-url
      remoteRef:
        key: alpha/prod/database-url
```

### Ingress Configuration

```yaml
# Automatic with ingress.enabled=true
# Requires cert-manager for TLS
annotations:
  nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
  nginx.ingress.kubernetes.io/proxy-send-timeout: "300"
  nginx.ingress.kubernetes.io/proxy-body-size: "50m"
  nginx.ingress.kubernetes.io/ssl-redirect: "true"
```

### Scaling

```bash
# Manual scaling
kubectl scale deployment alpha-gateway --replicas=5 -n alpha

# HPA (configured in values.yaml)
# Automatically scales based on CPU/memory

# KEDA for event-driven scaling (optional)
# Scale based on queue depth, custom metrics
```

### Monitoring

```yaml
# Add to values.yaml
monitoring:
  enabled: true
  prometheus:
    enabled: true
    serviceMonitor:
      enabled: true
  grafana:
    enabled: true
    dashboards:
      enabled: true
```

### Backup & Restore

```bash
# Database backup (PostgreSQL)
kubectl exec -n alpha postgresql-0 -- pg_dump -U alpha alpha > backup.sql

# Restore
kubectl exec -i -n alpha postgresql-0 -- psql -U alpha alpha < backup.sql

# PVC backup (Velero)
velero backup create alpha-backup --include-namespaces alpha
```

### Upgrades

```bash
# Upgrade chart
helm upgrade alpha ./deploy/helm/agent-workspace \
  --namespace alpha \
  --values values.yaml \
  --set config.betterAuthSecret=... \
  --reuse-values

# Rollback
helm rollback alpha 1 -n alpha
```

## Windows Desktop App Deployment

### Building Installer

```powershell
cd electron
npm install
npm run dist
# Output: electron/dist/Agent-Workspace-Setup-2.1.0.exe
```

### Distribution

- **Unsigned**: SmartScreen warning, users must click "More info → Run anyway"
- **Signed**: Requires code signing certificate (EV recommended)
- **Auto-update**: The Electron installer remains manual-reinstall. The separate guarded source-checkout updater is documented in [AUTO_UPDATE.md](AUTO_UPDATE.md); it is not used to mutate packaged desktop binaries.

### Installation

1. Run `Agent-Workspace-Setup-2.1.0.exe`
2. Per-user install to `%LOCALAPPDATA%\Programs\Alpha`
3. Data stored in Electron's `userData` directory (`%APPDATA%\agent-workspace-desktop\`
   by default; the app's **User data** menu entry reveals the exact path)
4. Desktop Gateway runs on port 8201
5. Frontend on port 3000 (internal)

### Configuration

First launch auto-provisions Python environment. User must add API key to:
`<userData>\project\config.yaml`

## Production Checklist

### Pre-Deployment
- [ ] Run `make prod-check` (validates versions, config, secrets)
- [ ] Verify `config.yaml` has production settings
- [ ] Verify `.env` has all required secrets
- [ ] Verify `extensions_config.json` has required MCP/skills
- [ ] Run `make doctor` (system requirements)
- [ ] Run backend tests: `cd backend && make test`
- [ ] Run frontend tests: `cd frontend && pnpm test`
- [ ] Run lint: `cd backend && make lint && cd ../frontend && pnpm check`

### Security
- [ ] `BETTER_AUTH_SECRET` is 32+ bytes, randomly generated
- [ ] All API keys in `.env`, not in config files
- [ ] Database passwords are strong
- [ ] Redis has password
- [ ] CORS origins restricted to your domain
- [ ] Rate limiting enabled
- [ ] HSTS enabled (HTTPS only)
- [ ] Security headers configured
- [ ] TLS certificates valid (Let's Encrypt or custom)

### Infrastructure
- [ ] PostgreSQL: Backups configured, replication if needed
- [ ] Redis: Persistence configured, memory limits set
- [ ] Nginx: TLS termination, gzip compression, rate limiting
- [ ] Disk space: Monitoring alerts at 80%
- [ ] Memory: Monitoring alerts at 80%
- [ ] CPU: Monitoring alerts at 80%
- [ ] Log aggregation configured
- [ ] Health check endpoints accessible to load balancer

### Operations
- [ ] `make support-bundle` works for troubleshooting
- [ ] Runbook documented (PRODUCTION.md)
- [ ] On-call rotation configured
- [ ] Incident response plan
- [ ] Capacity planning done
- [ ] Disaster recovery tested

### Post-Deployment
- [ ] Smoke test: Create thread, send message, get response
- [ ] Verify streaming works
- [ ] Verify file upload works
- [ ] Verify scheduled tasks work
- [ ] Verify IM channels work (if configured)
- [ ] Monitor logs for errors
- [ ] Check metrics dashboard

## Rollback Procedures

### Docker Compose
```bash
# Quick rollback (previous images)
docker compose -f docker/docker-compose.yaml down
docker compose -f docker/docker-compose.yaml up -d --force-recreate

# Specific version
docker compose -f docker/docker-compose.yaml pull gateway:2.0.0 frontend:2.0.0
make up
```

### Kubernetes
```bash
# Helm rollback
helm rollback alpha <revision> -n alpha

# Or re-deploy previous image tag
helm upgrade alpha ./deploy/helm/agent-workspace \
  --set gateway.image.tag=2.0.0 \
  --set frontend.image.tag=2.0.0 \
  -n alpha
```

### Database
```bash
# If migration caused issues
# 1. Restore from backup
# 2. Rollback migration
cd backend && make migrate-downgrade
```

## Troubleshooting Deployment

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Gateway not ready | DB migration pending | Wait or run migrations manually |
| Frontend build fails | Node version mismatch | Check Node 22+, clear node_modules |
| Nginx 502 | Gateway not healthy | Check gateway logs, health endpoint |
| Database connection refused | Postgres not ready | Wait for healthcheck, check credentials |
| Redis connection refused | Redis not ready | Wait for healthcheck, check password |
| Port already in use | Conflict with host | Change ports in docker-compose |
| OOM killed | Memory limit too low | Increase container memory limits |
| Slow responses | No CPU limits | Set CPU limits, check for runaway processes |

### Debug Commands
```bash
# Container logs
docker compose -f docker/docker-compose.yaml logs -f gateway

# Container shell
docker compose -f docker/docker-compose.yaml exec gateway bash

# Database shell
docker compose -f docker/docker-compose.yaml exec postgres psql -U alpha alpha

# Redis shell
docker compose -f docker/docker-compose.yaml exec redis redis-cli

# Check config
docker compose -f docker/docker-compose.yaml exec gateway cat /app/config.yaml

# Network test
docker compose -f docker/docker-compose.yaml exec gateway curl -f http://postgres:5432
```

## Disaster Recovery

### RTO/RPO Targets
- **RTO** (Recovery Time Objective): < 30 minutes
- **RPO** (Recovery Point Objective): < 5 minutes (DB), < 1 hour (config)

### Backup Schedule
- Database: Every 5 minutes (WAL-G or pg_dump)
- Config: On every change (git)
- Redis: RDB snapshots every 15 minutes
- Volumes: Daily snapshots

### Recovery Steps
1. Provision new infrastructure
2. Restore database from latest backup
3. Restore config from git
4. Deploy application
5. Verify health checks
6. Update DNS