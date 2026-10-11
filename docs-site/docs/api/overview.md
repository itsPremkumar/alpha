---
sidebar_position: 1
title: Overview
description: Alpha REST API overview and authentication
slug: /api
---

# API Overview

Alpha provides a comprehensive REST API for programmatic access to all platform features.

## Base URL

```
Production: https://api.alpha.itsPremkumar.com
Development: http://localhost:8001
```

## Authentication

### API Keys

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.alpha.itsPremkumar.com/api/threads
```

### JWT Tokens

```bash
# Get token
curl -X POST https://api.alpha.itsPremkumar.com/auth/token \
  -d '{"username": "user", "password": "pass"}'

# Use token
curl -H "Authorization: Bearer <token>" \
  https://api.alpha.itsPremkumar.com/api/threads
```

### Personal Access Tokens (PATs)

```bash
curl -H "Authorization: Bearer pat_..." \
  https://api.alpha.itsPremkumar.com/api/threads
```

## Rate Limiting

| Tier | Requests/Minute | Burst |
|------|-----------------|-------|
| Free | 60 | 10 |
| Pro | 300 | 50 |
| Enterprise | 1000 | 200 |

Headers:
- `X-RateLimit-Limit` - Request limit
- `X-RateLimit-Remaining` - Remaining requests
- `X-RateLimit-Reset` - Reset timestamp

## Error Handling

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid request",
    "details": [
      {"field": "name", "message": "Required"}
    ]
  }
}
```

### Common Error Codes

| Code | HTTP | Description |
|------|------|-------------|
| `VALIDATION_ERROR` | 422 | Invalid request body |
| `UNAUTHORIZED` | 401 | Invalid/missing auth |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `RATE_LIMITED` | 429 | Rate limit exceeded |
| `INTERNAL_ERROR` | 500 | Server error |

## Pagination

```bash
curl "https://api.alpha.itsPremkumar.com/api/threads?limit=20&cursor=abc123"
```

Response:
```json
{
  "data": [...],
  "pagination": {
    "has_more": true,
    "next_cursor": "def456",
    "total": 150
  }
}
```

## Webhooks

Register webhooks for real-time events:

```bash
curl -X POST https://api.alpha.itsPremkumar.com/api/webhooks \
  -H "Authorization: Bearer <token>" \
  -d '{"url": "https://your-app.com/webhook", "events": ["thread.created", "run.completed"]}'
```

### Supported Events

| Event | Description |
|-------|-------------|
| `thread.created` | New thread created |
| `thread.updated` | Thread updated |
| `thread.deleted` | Thread deleted |
| `run.created` | Run started |
| `run.completed` | Run completed |
| `run.failed` | Run failed |
| `message.created` | New message |
| `agent.spawned` | Subagent spawned |
| `tool.executed` | Tool executed |

## SDKs

| Language | Package | Status |
|----------|---------|--------|
| Python | `pip install alpha-client` | ✅ Stable |
| TypeScript | `npm install @alpha/client` | ✅ Stable |
| Go | `go get github.com/itsPremkumar/alpha-go` | 🚧 Beta |
| Rust | `cargo add alpha-client` | 📋 Planned |

## OpenAPI Spec

Download: [openapi.json](https://api.alpha.itsPremkumar.com/openapi.json)

View in Swagger UI: https://api.alpha.itsPremkumar.com/docs