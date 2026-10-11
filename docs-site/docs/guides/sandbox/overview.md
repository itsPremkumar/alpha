---
sidebar_position: 1
title: Overview
description: Sandbox execution environments in Alpha
slug: /guides/sandbox
---

# Sandbox Execution

Alpha provides isolated execution environments for safe code execution.

## Sandbox Providers

| Provider | Isolation | Performance | Use Case |
|----------|-----------|-------------|----------|
| Docker | Container | Good | General purpose |
| AIO | Process | Excellent | Low latency |
| E2B | Cloud VM | Good | Heavy compute, browser |
| Local | Process | Best | Development only |

## Configuration

```yaml
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
  local:
    enabled: true
```

## Provider Details

### Docker
- **Isolation**: Container-level
- **Performance**: Good
- **Use case**: General purpose
- **Requirements**: Docker daemon

### AIO (All-In-One)
- **Isolation**: Process-level
- **Performance**: Excellent (low latency)
- **Use case**: Low latency, fast iteration
- **Requirements**: Linux

### E2B
- **Isolation**: Cloud VM
- **Performance**: Good
- **Use case**: Heavy compute, browser automation
- **Requirements**: E2B API key

### Local
- **Isolation**: Process-level
- **Performance**: Best
- **Use case**: Development only
- **No isolation guarantees**

## Sandbox Security

### Docker Hardening
```yaml
sandbox:
  docker:
    capabilities:
      drop: ["ALL"]
    security_opt:
      - "no-new-privileges:true"
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=100m
  seccomp:
    profile: "default"
  apparmor:
    profile: "alpha-sandbox"
```

### Resource Limits
```yaml
docker:
  cpu_limit: "2"
  memory_limit: "4g"
  pids_limit: 100
  ulimits:
    nofile:
      soft: 1024
      hard: 1024
```

## Sandbox Tools

The sandbox exposes tools for code execution:

```python
# Python execution
result = await sandbox.run_python("""
import pandas as pd
df = pd.read_csv('data.csv')
print(df.describe())
""")

# Shell commands
result = await sandbox.run_shell("ls -la /workspace")

# File operations
await sandbox.write_file("/workspace/output.txt", "Hello World")
content = await sandbox.read_file("/workspace/output.txt")
```

## File Operations

```python
# Write file
await sandbox.write_file("/workspace/output.txt", "content")

# Read file
content = await sandbox.read_file("/workspace/output.txt")

# List files
files = await sandbox.list_files("/workspace")

# Delete file
await sandbox.delete_file("/workspace/temp.txt")
```

## Network Access

```yaml
sandbox:
  docker:
    network_mode: "bridge"  # bridge, host, none
    # For host network access:
    # network_mode: "host"
```

## Resource Limits

```yaml
docker:
  cpu_limit: "2"        # CPU cores
  memory_limit: "4g"    # Memory limit
  pids_limit: 100       # Process limit
  ulimits:
    nofile:
      soft: 1024
      hard: 1024
```

## Provider Comparison

| Feature | Docker | AIO | E2B | Local |
|---------|--------|-----|-----|-------|
| Isolation | Container | Process | Cloud VM | Process |
| Performance | Good | Excellent | Good | Best |
| Network | Configurable | Host | Cloud | Host |
| Persistence | Volumes | None | Cloud | Local |
| Browser | No | No | Yes | No |
| GPU | Yes | No | Yes | Yes |

## Configuration

```yaml
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
  local:
    enabled: true
```

## Security Hardening

### Capabilities
```yaml
capabilities:
  drop: ["ALL"]
  add: []  # Add only required capabilities
```

### Seccomp
```yaml
seccomp:
  profile: "default"  # or custom profile path
```

### AppArmor
```yaml
apparmor:
  profile: "alpha-sandbox"
```

### Read-Only Root
```yaml
read_only: true
tmpfs:
  - /tmp:rw,noexec,nosuid,size=100m
```

## Resource Limits

```yaml
docker:
  cpu_limit: "2"
  memory_limit: "4g"
  pids_limit: 100
  ulimits:
    nofile:
      soft: 1024
      hard: 1024
```

## Network Access

```yaml
docker:
  network_mode: "bridge"  # bridge, host, none
  # For host network access:
  # network_mode: "host"
```

## Next Steps

- [Docker Sandbox](/guides/sandbox/docker)
- [AIO Sandbox](/guides/sandbox/aio)
- [E2B Sandbox](/guides/sandbox/e2b)
- [Security Hardening](/guides/sandbox/security)