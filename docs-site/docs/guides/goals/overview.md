---
sidebar_position: 1
title: Overview
description: Goals and autonomous missions in Alpha
slug: /guides/goals
---

# Goals

Alpha's goal system enables autonomous mission execution with verification.

## What are Goals?

Goals are high-level objectives that Alpha can pursue autonomously:
- **Declarative**: Define *what* to achieve, not *how*
- **Verifiable**: Acceptance criteria for completion
- **Composable**: Goals can have subgoals
- **Auditable**: Full execution trace

## Creating Goals

```bash
# Via slash command
/goal create "Build a REST API with FastAPI"

# Via API
POST /api/goals
{
  "objective": "Build a REST API with FastAPI",
  "acceptance_criteria": [
    "API has CRUD endpoints",
    "Tests pass",
    "Documentation generated"
  ]
}
```

## Goal Structure

```json
{
  "id": "goal_abc123",
  "objective": "Build a REST API with FastAPI",
  "acceptance_criteria": [
    "API has CRUD endpoints",
    "Tests pass",
    "Documentation generated"
  ],
  "constraints": {
    "max_runtime_minutes": 60,
    "max_tokens": 100000
  },
  "subgoals": [
    "Design API schema",
    "Implement endpoints",
    "Write tests",
    "Generate docs"
  ]
}
```

## Goal Lifecycle

```
CREATED → PLANNING → EXECUTING → VERIFYING → COMPLETED
                    ↓
                BLOCKED (awaiting approval)
                    ↓
                FAILED (max retries exceeded)
```

## Goal Commands

| Command | Description |
|---------|-------------|
| `/goal create` | Create new goal |
| `/goal start` | Start goal execution |
| `/goal pause` | Pause execution |
| `/goal resume` | Resume execution |
| `/goal status` | Show goal status |
| `/goal verify` | Run verification |
| `/goal cancel` | Cancel goal |

## Acceptance Criteria

Define verifiable completion criteria:

```json
{
  "acceptance_criteria": [
    "API has GET /users endpoint",
    "GET /users returns 200 OK",
    "Response matches User schema",
    "Response time < 200ms"
  ]
}
```

## Goal Commands

```bash
# Create goal
/goal create "Build user API" --criteria "has GET /users" "has POST /users"

# Start goal
/goal start goal_abc123

# Check status
/goal status goal_abc123

# Verify completion
/goal verify goal_abc123

# Pause/Resume
/goal pause goal_abc123
/goal resume goal_abc123

# Cancel
/goal cancel goal_abc123
```

## Goal Verification

Automatic verification runs when criteria are met:

```bash
# Manual verification
/goal verify goal_abc123

# View verification report
/goal report goal_abc123
```

## Subgoals

Decompose complex goals:

```json
{
  "objective": "Build user management system",
  "subgoals": [
    "Design database schema",
    "Implement user model",
    "Create API endpoints",
    "Add authentication",
    "Write tests",
    "Deploy to staging"
  ]
}
```

## Goal Monitoring

```bash
# Real-time status
/goal watch goal_abc123

# View execution log
/goal log goal_abc123

# View resource usage
/goal usage goal_abc123
```

## Configuration

```yaml
goals:
  enabled: true
  max_concurrent: 5
  default_timeout_minutes: 60
  verification:
    enabled: true
    auto_verify: true
```

## Next Steps

- [Creating Goals](/guides/goals/creating-goals)
- [Goal Verification](/guides/goals/verification)
- [Subgoals](/guides/goals/subgoals)