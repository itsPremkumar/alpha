---
sidebar_position: 2
title: Docker Sandbox
description: Docker-based sandbox execution
slug: /guides/sandbox/docker
---

# Docker Sandbox

Docker-based sandbox execution with container-level isolation.

## Quick Start

```yaml
sandbox:
  provider: "docker"
  docker:
    image: "alpha/sandbox:latest"
    cpu_limit: "2"
    memory_limit: "4g"
    network_mode: "bridge"
```

## Image

Default image: `alpha/sandbox:latest`

Custom image:
```yaml
docker:
  image: "myorg/my-sandbox:latest"
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

## Network Modes

```yaml
docker:
  network_mode: "bridge"  # bridge, host, none
```

| Mode | Description |
|------|-------------|
| `bridge` | Isolated network (default) |
| `host` | Host network stack |
| `none` | No network |

## Volume Mounts

```yaml
docker:
  volumes:
    - "/host/path:/workspace:ro"
    - "./data:/data:rw"
```

## Security Hardening

```yaml
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

## Custom Image

```dockerfile
# Dockerfile
FROM alpha/sandbox:latest
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libpq-dev
```

```yaml
docker:
  image: "myorg/custom-sandbox:latest"
  build:
    context: .
    dockerfile: Dockerfile.sandbox
```