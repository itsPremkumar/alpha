---
sidebar_position: 1
title: Agents API
description: Manage agents and their profiles
slug: /api/agents
---

# Agents API

Manage agent profiles, health, and working status.

## List Agents

```bash
GET /api/bots
```

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 50 | Max results |
| `cursor` | string | - | Pagination cursor |
| `activity` | boolean | false | Include activity data |
| `status` | string | - | Filter by status |
| `model` | string | - | Filter by model |

### Response

```json
{
  "bots": [
    {
      "name": "researcher",
      "display_name": "Research Agent",
      "role": "researcher",
      "department": "research",
      "status": "online",
      "model": "gpt-4",
      "avatar": "https://...",
      "capabilities": ["web_search", "code_execution"],
      "last_active": "2026-10-10T10:30:00Z",
      "unread_count": 0
    }
  ],
  "count": 1,
  "has_more": false
}
```

## Get Agent

```bash
GET /api/bots/{name}
```

### Response

```json
{
  "name": "researcher",
  "display_name": "Research Agent",
  "role": "researcher",
  "department": "research",
  "status": "online",
  "model": "gpt-4",
  "avatar": "https://...",
  "capabilities": ["web_search", "code_execution"],
  "config": {
    "temperature": 0.7,
    "max_tokens": 4096
  },
  "created_at": "2026-01-15T10:00:00Z",
  "updated_at": "2026-10-10T10:30:00Z"
}
```

## Create Agent

```bash
POST /api/bots
```

### Request

```json
{
  "name": "analyst",
  "display_name": "Data Analyst",
  "role": "analyst",
  "department": "analytics",
  "model": "gpt-4",
  "capabilities": ["data_analysis", "visualization"],
  "config": {
    "temperature": 0.3,
    "max_tokens": 8192
  }
}
```

### Response

```json
{
  "name": "analyst",
  "display_name": "Data Analyst",
  "role": "analyst",
  "department": "analytics",
  "status": "idle",
  "model": "gpt-4",
  "capabilities": ["data_analysis", "visualization"],
  "created_at": "2026-10-10T10:30:00Z"
}
```

## Update Agent

```bash
PATCH /api/bots/{name}
```

### Request

```json
{
  "display_name": "Senior Data Analyst",
  "model": "gpt-4-turbo",
  "config": {
    "temperature": 0.2
  }
}
```

## Delete Agent

```bash
DELETE /api/bots/{name}
```

## Agent Health

```bash
GET /api/bots/health/overview
```

### Response

```json
{
  "total": 10,
  "healthy": 8,
  "degraded": 1,
  "unhealthy": 1,
  "bots": [
    {
      "name": "researcher",
      "status": "healthy",
      "last_heartbeat": "2026-10-10T10:30:00Z",
      "uptime": "7d 4h"
    }
  ]
}
```

## Working Status

```bash
GET /api/bots/working
```

### Response

```json
{
  "working": [
    {
      "bot": "researcher",
      "thread": "research-api-design",
      "run_id": "run_abc123",
      "elapsed_seconds": 45,
      "status": "running"
    }
  ],
  "count": 1
}
```

## Agent Model Config

```bash
GET /api/bots/{name}/model-config
PUT /api/bots/{name}/model-config
DELETE /api/bots/{name}/model-config
POST /api/bots/{name}/model-config/preview
```

### Config Schema

```json
{
  "enabled": true,
  "model": "gpt-4",
  "counsel": {
    "enabled": true,
    "model": "gpt-3.5-turbo",
    "rounds": 2
  },
  "mixture": {
    "enabled": true,
    "strategy": "parallel",
    "models": ["gpt-4", "claude-3"],
    "workers": 3
  },
  "sampling": {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 4096
  }
}
```