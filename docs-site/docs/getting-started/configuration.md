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
    supports_vision: true
    supports_reasoning: true

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
  l1:
    enabled: true
    max_entries: 1000
    ttl_hours: 24

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
  allowed_skills: []

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
  profiles:
    off:
      enabled: false
    assist:
      enabled: true
    autonomous:
      enabled: true
    apex_max:
      enabled: true

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

# Token usage tracking
token_usage:
  enabled: true
  pricing_enabled: false

# Token budget
token_budget:
  enabled: false
  max_tokens_per_run: 100000
  warning_threshold: 0.8

# Context window management
context_window:
  enabled: true
  output_reserve_tokens: 4096
  next_turn_reserve_tokens: 1024
  elevated_fraction: 0.6
  critical_fraction: 0.8

# Loop detection
loop_detection:
  enabled: true
  max_turns: 50
  similarity_threshold: 0.9

# Token budget
token_budget:
  enabled: false
  max_tokens_per_run: 100000
  warning_threshold: 0.8

# Summarization
summarization:
  enabled: true
  model: "gpt-4"
  max_tokens: 4000
  trigger_threshold: 0.8
  preserve_recent: 10

# Verification
verification:
  completion_critics_enabled: true
  completion_critics_require_patch: false

# Safety
safety_finish_reason:
  enabled: true
  timeout_seconds: 30

# Security
security:
  finish_reason:
    enabled: true
    timeout_seconds: 30

# Reasoning effort
reasoning_effort:
  default: "medium"
  ladder: ["low", "medium", "high", "xhigh", "max"]
  labels:
    low: "Low"
    medium: "Medium"
    high: "High"
    xhigh: "Extra High"
    max: "Maximum"

# Model routing
model_routing:
  categories:
    deep: "gpt-4"
    coding: "gpt-4"
    fast: "gpt-4o-mini"
  tiers:
    fast: ["gpt-4o-mini", "gpt-3.5-turbo"]
    deep: ["gpt-4", "claude-3-opus"]

# Free gateways
free_gateways:
  enabled: true
  providers:
    - name: "openrouter"
      models: ["gratis"]

# Subagents
subagents:
  enabled: true
  max_total_per_run: 10
  max_concurrent: 3

# Skills
skills:
  enabled: true
  public_path: "skills/public"
  custom_path: "skills/custom"
  allowed_skills: []

# Autonomy
autonomy:
  enabled: false
  loops:
    sentinel:
      enabled: true
      interval: 600
    perpetual:
      enabled: false
      interval: 3600
  profiles:
    off:
      enabled: false
    assist:
      enabled: true
    autonomous:
      enabled: true
    apex_max:
      enabled: true