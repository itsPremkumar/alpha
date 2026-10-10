---
sidebar_position: 1
title: Deployment
description: Deploy Alpha to production
slug: /deployment
---

# Deployment Guide

Complete guide to deploying Alpha in production.

## Deployment Options

| Method | Complexity | Scalability | Best For |
|--------|------------|-------------|----------|
| Docker Compose | Low | Single host | Development, small prod |
| Kubernetes | High | Horizontal | Enterprise, scale |
| Docker Swarm | Medium | Horizontal | Medium scale |
| Bare Metal | High | Vertical | Maximum control |

---

## Docker Compose (Recommended for Small-Medium)

### Prerequisites

- Docker 24+
- Docker Compose 2.24+
- 4+ GB RAM
- 20 GB disk

### Production Configuration

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  nginx:
    image: nginx:alpine
    ports:
      - "2026:2026"
    volumes:
      - ./docker/nginx/nginx.prod.conf:/etc/nginx/nginx.conf
      - ./logs:/var/log/nginx
    depends_on:
      - frontend
      - gateway
    restart: unless-stopped

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile.prod
    environment:
      - NEXT_PUBLIC_GATEWAY_URL=http://gateway:8001
    depends_on:
      - gateway
    restart: unless-stopped

  gateway:
    build:
      context: ./backend
      dockerfile: Dockerfile.prod
    environment:
      - ALPHA_CONFIG=/app/config.yaml
      - ALPHA_AUTH_DISABLED=0
      - DATABASE_URL=postgresql://alpha:${POSTGRES_PASSWORD}@postgres:5432/alpha
      - REDIS_URL=redis://redis:6379
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - .alpha:/app/.alpha
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      - POSTGRES_DB=alpha
      - POSTGRES_USER=alpha
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
```

### Environment Variables

Create `.env.production`:

```env
# Database
POSTGRES_PASSWORD=secure_random_password

# Redis
REDIS_PASSWORD=secure_random_password

# API Keys
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Auth
JWT_SECRET=super_secret_random_string
JWT_ALGORITHM=RS256

# Alpha Config
ALPHA_CONFIG=/app/config.yaml
ALPHA_AUTH_DISABLED=0
```

### Deploy

```bash
# Start production stack
docker compose -f docker-compose.prod.yml up -d

# View logs
docker compose -f docker-compose.prod.yml logs -f

# Scale gateway
docker compose -f docker-compose.prod.yml up -d --scale gateway=3
```