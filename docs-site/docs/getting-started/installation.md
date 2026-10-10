---
sidebar_position: 2
title: Installation
description: Detailed installation guide for Alpha
slug: /getting-started/installation
---

# Installation

Detailed installation guide for Alpha across different environments.

## System Requirements

### Minimum Requirements

| Component | Requirement |
|-----------|-------------|
| CPU | 4 vCPU |
| RAM | 8 GB |
| Disk | 20 GB free space |
| OS | Linux (Ubuntu 22.04+), macOS 13+, Windows 11+ |

### Recommended for Production

| Component | Requirement |
|-----------|-------------|
| CPU | 8+ vCPU |
| RAM | 16 GB+ |
| Disk | 100 GB+ SSD |
| OS | Ubuntu 22.04 LTS |

## Installation Methods

### Method 1: Docker (Recommended)

```bash
# Clone repository
git clone https://github.com/itsPremkumar/alpha.git
cd alpha

# Initialize Docker environment
make docker-init

# Start services
make docker-start
```

### Method 2: Local Development

```bash
# Clone repository
git clone https://github.com/itsPremkumar/alpha.git
cd alpha

# Install dependencies
make install

# Start development server
make dev
```

### Method 3: Kubernetes (Production)

```bash
# Apply Kubernetes manifests
kubectl apply -f k8s/

# Or use Helm
helm install alpha ./helm/alpha
```

## Dependency Details

### Backend Dependencies (Python)

Managed by `uv`:

```bash
cd backend
uv sync --all-extras --dev
```

Key dependencies:
- `fastapi` — API framework
- `langgraph` — Agent orchestration
- `langchain` — LLM orchestration
- `uvicorn` — ASGI server
- `sqlalchemy` — Database ORM
- `redis` — Caching & pub/sub
- `pydantic` — Data validation
- `pydantic-settings` — Configuration

### Frontend Dependencies (Node.js)

Managed by `pnpm`:

```bash
cd frontend
pnpm install
```

Key dependencies:
- `next` — React framework
- `react` — UI library
- `typescript` — Type safety
- `tailwindcss` — Styling
- `react-markdown` — Markdown rendering
- `lucide-react` — Icons

### System Dependencies

| Tool | Version | Purpose |
|------|---------|---------|
| Docker | 24+ | Container runtime |
| Docker Compose | 2.24+ | Multi-container orchestration |
| nginx | 1.24+ | Reverse proxy |
| Node.js | 22+ | Frontend runtime |
| pnpm | 11+ | Package manager |
| Python | 3.12+ | Backend runtime |
| uv | 0.11+ | Python package manager |

## Verification

```bash
# Check all services are running
make docker-logs

# Health check
curl http://localhost:2026/health

# API health
curl http://localhost:8001/health
```

## Troubleshooting

### Port Conflicts

```bash
# Check what's using ports
netstat -tulpn | grep -E '2026|3000|8001'

# Kill conflicting processes
sudo kill -9 <PID>
```

### Docker Issues

```bash
# Clean up Docker
docker system prune -a

# Rebuild images
make docker-init
```

### Permission Issues (Linux)

```bash
# Fix Docker permissions
sudo usermod -aG docker $USER
newgrp docker
```

## Next Steps

- [Configuration](/getting-started/configuration) — Configure Alpha for your environment
- [Quick Start](/getting-started/quick-start) — Get running in 5 minutes
- [First Agent](/getting-started/first-agent) — Create your first agent