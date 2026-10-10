---
sidebar_position: 3
title: Configuration
description: Configure Alpha for your environment
slug: /getting-started/configuration
---

# Configuration

Complete configuration reference for Alpha.

## Configuration File

Alpha uses a single `config.yaml` file for all settings. Copy the example and customize:

```bash
cp config.example.yaml config.yaml
```

## Configuration Structure

```yaml
# config.yaml
app:
  name: "Alpha"
  version: "1.0.0"
  environment: "development"  # development, staging, production
  debug: true

# Database configuration
database:
  backend: "sqlite"  # sqlite, postgresql
  sqlite:
    path: ".alpha/data/alpha.db"
  postgresql:
    host: "localhost"
    port: 5432
    database: "alpha"
    username: "alpha"
    password: "${POSTGRES_PASSWORD}"

# Redis configuration
redis:
  host: "localhost"
  port: 6379
  db: 0
  password: "${REDIS_PASSWORD}"

# Model configuration
models:
  - name: "gpt-4"
    provider: "openai"
    model: "gpt-4-turbo-preview"
    api_key: "${OPENAI_API_KEY}"
    context_window: 128000
    max_tokens: 4096
    temperature: 0.7
    is_default: true

# Sandbox configuration
sandbox:
  provider: "docker"  # docker, aio, e2b, local
  docker:
    image: "alpha/sandbox:latest"
    cpu_limit: "2"
    memory_limit: "4g"
    network_mode: "bridge"
  aio:
    enabled: true
  e2b:
    api_key: "${E2B_API_KEY}"

# Memory configuration
memory:
  provider: "deermem"  # deermem, simple
  deermem:
    path: ".alpha/memory"
    embedding_model: "text-embedding-3-small"
    chunk_size: 1000
    chunk_overlap: 200

# Tool configuration
tools:
  enabled: true
  builtin: true
  mcp:
    enabled: true
    servers: []

# Skill configuration
skills:
  enabled: true
  public_path: "skills/public"
  custom_path: "skills/custom"

# Autonomy configuration
autonomy:
  enabled: false
  loops:
    sentinel:
      enabled: true
      interval: 600
    perpetual:
      enabled: false
      interval: 3600

# Network configuration
network:
  host: "0.0.0.0"
  port: 8001
  cors:
    enabled: true
    origins: ["http://localhost:3000", "http://localhost:2026"]

# Logging
logging:
  level: "INFO"
  format: "json"
  file: ".alpha/logs/alpha.log"
  max_size: "100MB"
  backup_count: 5