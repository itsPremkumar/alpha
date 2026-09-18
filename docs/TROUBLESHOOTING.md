# Troubleshooting Guide

## Overview

This guide covers common issues, diagnostic procedures, and solutions for Alpha deployment and operation.

## Quick Diagnostics

### Health Checks
```bash
# Overall system health
curl http://localhost:2026/health

# Gateway readiness (checks DB, Redis, models)
curl http://localhost:8001/health/ready

# Individual service health
curl http://localhost:8001/health          # Gateway
curl http://localhost:3000                  # Frontend
curl http://localhost:8002/health           # Provisioner (if enabled)

# Detailed status
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/ops/status
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/ops/resources
```

### System Doctor
```bash
# Comprehensive system check
make doctor

# Output includes:
# - Python/Node/Docker versions
# - Port availability
# - Config file validity
# - Database connectivity
# - Redis connectivity
# - Model API reachability
# - Disk space
# - Memory availability
```

### Support Bundle
```bash
# Generate troubleshooting bundle
make support-bundle

# Includes:
# - System information
# - Configuration (redacted)
# - Recent logs
# - Database schema
# - Container status
# - AI-generated issue draft
```

## Common Issues

### Startup Issues

#### "config.yaml not found"
```bash
# Cause: Config files not generated
# Solution:
make config

# Verify
ls -la config.yaml extensions_config.json
```

#### "Port already in use"
```bash
# Check what's using ports
netstat -tulpn | grep -E '2026|8001|3000|8002|5432|6379'

# Kill conflicting processes
# Or change ports in docker-compose.yaml / .env
```

#### "Database connection failed"
```bash
# Check PostgreSQL is running
docker compose ps postgres

# Check logs
docker compose logs postgres

# Verify credentials
docker compose exec postgres psql -U alpha -c "SELECT 1"

# Check DATABASE_URL in .env matches docker-compose
```

#### "Redis connection failed"
```bash
# Check Redis is running
docker compose ps redis

# Check logs
docker compose logs redis

# Test connection
docker compose exec redis redis-cli ping

# Verify REDIS_URL in .env
```

#### "Frontend build failed"
```bash
# Check Node version (requires 22+)
node --version

# Clear cache and rebuild
cd frontend && rm -rf node_modules .next && pnpm install && pnpm build

# Check for TypeScript errors
cd frontend && pnpm typecheck
```

#### "Gateway not ready"
```bash
# Check gateway logs
docker compose logs gateway --tail=100

# Common causes:
# 1. Database migration pending
#    -> Wait or run: docker compose exec gateway python -m alembic upgrade head
# 2. Model API key invalid
#    -> Check .env and config.yaml
# 3. Configuration error
#    -> Run make doctor
```

### Runtime Issues

#### High Memory Usage
```bash
# Check container memory
docker stats --no-stream

# Gateway memory leak?
# - Check for runaway runs: curl /api/ops/status
# - Restart gateway: docker compose restart gateway

# Frontend memory?
# - Check for large bundle: cd frontend && pnpm build && npx @next/bundle-analyzer
# - Restart frontend: docker compose restart frontend

# Database memory?
# - Check work_mem, shared_buffers in postgresql.conf
# - Restart postgres: docker compose restart postgres
```

#### High CPU Usage
```bash
# Check which container
docker stats --no-stream

# Gateway CPU?
# - Check active runs: curl /api/ops/status
# - Check for runaway tool execution
# - Scale gateway: docker compose up -d --scale gateway=3

# Database CPU?
# - Check slow queries: pg_stat_statements
# - Add indexes
# - Scale read replicas
```

#### Slow Response Times
```bash
# Check latency
curl -w "@curl-format.txt" -o /dev/null -s http://localhost:2026/health

# Gateway latency?
# - Check model provider latency
# - Check tool execution time
# - Enable caching

# Database latency?
# - Check connection pool exhaustion
# - Check query performance
# - Add read replica
```

#### WebSocket/SSE Connection Issues
```bash
# Check Nginx config for WebSocket support
# Should have:
# proxy_http_version 1.1;
# proxy_set_header Upgrade $http_upgrade;
# proxy_set_header Connection "upgrade";
# proxy_read_timeout 3600;

# Check browser console for errors
# Check Nginx error logs
docker compose logs nginx | grep -i websocket
```

### Authentication Issues

#### "Unauthorized" on API calls
```bash
# Check session cookie (browser)
# Check Authorization header (API)
# Verify BETTER_AUTH_SECRET in .env
# Check cookie domain/settings for HTTPS

# Test with explicit token
curl -H "Authorization: Bearer <api-key>" http://localhost:8001/api/threads
```

#### Login not working
```bash
# Check Better Auth config
# Verify OAuth credentials (GitHub, Google, etc.)
# Check callback URLs match deployment
# Check CORS origins in config.yaml
```

### Model/Provider Issues

#### Model API errors
```bash
# Check model health
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/models/local/health

# Test provider directly
curl -H "Authorization: Bearer $OPENAI_API_KEY" https://api.openai.com/v1/models

# Check config.yaml model configuration
# Verify API keys in .env
# Check fallback models configured
```

#### Token budget exceeded
```bash
# Check token usage
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/ops/status

# Increase budgets in config.yaml
token_budget:
  per_run: 100000
  cumulative: 1000000

# Or disable for testing
token_budget:
  per_run: 0  # 0 = unlimited
```

### Scheduled Tasks Issues

#### Tasks not running
```bash
# Check scheduler enabled
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/config | jq '.scheduler.enabled'

# Check scheduler logs
docker compose logs gateway | grep -i scheduler

# Check wake gate
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/scheduled-tasks/blueprints

# Check incidents (auto-paused tasks)
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/scheduled-tasks/incidents
```

#### Tasks failing
```bash
# Check task occurrences
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/scheduled-tasks/task-id/occurrences

# Check run logs for specific occurrence
# Look at thread/run created by scheduler
```

### File Upload Issues

#### Upload fails
```bash
# Check file size limit (Nginx client_max_body_size)
# Default: 50M in nginx config

# Check disk space
df -h

# Check Gateway file upload config
# config.yaml -> file_upload.max_size
```

### WebSocket/Streaming Issues

#### Stream disconnects
```bash
# Check Nginx proxy_read_timeout
# Should be 300s+ for long streams

# Check Gateway keepalive
# Check for network intermediaries (load balancers, proxies)

# Test direct to gateway (bypass Nginx)
curl -N http://localhost:8001/api/threads/id/runs/id/stream
```

## Diagnostic Commands

### Container Inspection
```bash
# Container status
docker compose ps

# Container logs (follow)
docker compose logs -f gateway

# Container shell
docker compose exec gateway bash

# Container resource usage
docker stats --no-stream

# Container network
docker compose exec gateway netstat -tulpn
```

### Database Inspection
```bash
# PostgreSQL shell
docker compose exec postgres psql -U alpha alpha

# Common queries
# Active connections
SELECT count(*) FROM pg_stat_activity;

# Long running queries
SELECT pid, now() - pg_stat_activity.query_start AS duration, query 
FROM pg_stat_activity 
WHERE state = 'active' AND now() - pg_stat_activity.query_start > interval '5 minutes';

# Table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) 
FROM pg_tables 
WHERE schemaname = 'public' 
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

# Thread count
SELECT count(*) FROM threads;

# Run count by status
SELECT status, count(*) FROM runs GROUP BY status;
```

### Redis Inspection
```bash
# Redis shell
docker compose exec redis redis-cli

# Info
INFO memory
INFO clients
INFO stats

# Keys
KEYS *
DBSIZE

# Monitor (careful in production)
MONITOR
```

### Network Diagnostics
```bash
# Test connectivity between containers
docker compose exec gateway curl -f http://postgres:5432
docker compose exec gateway curl -f http://redis:6379
docker compose exec gateway curl -f http://frontend:3000

# DNS resolution
docker compose exec gateway nslookup postgres

# Port scanning
docker compose exec gateway nmap -p 5432,6379,3000 postgres redis frontend
```

## Log Analysis

### Log Locations
```bash
# Docker logs
docker compose logs gateway
docker compose logs frontend
docker compose logs nginx
docker compose logs postgres
docker compose logs redis

# Local development logs
# Each service logs to its terminal pane

# Structured logs (JSON)
# Use jq for parsing
docker compose logs gateway | jq 'select(.level=="ERROR")'
```

### Key Log Patterns

#### Gateway Startup
```
INFO  Starting Gateway API
INFO  Loading configuration from config.yaml
INFO  Database connected
INFO  Redis connected
INFO  Loading skills from skills/public
INFO  Loading extensions
INFO  Starting agent runtime
INFO  Gateway ready on 0.0.0.0:8001
```

#### Successful Run
```
INFO  Thread created thread_id=xxx
INFO  Run started run_id=yyy thread_id=xxx
INFO  Tool called tool=search_web thread_id=xxx run_id=yyy
INFO  Tool completed tool=search_web duration_ms=1200
INFO  Checkpoint created checkpoint_id=zzz step=5
INFO  Run completed run_id=yyy tokens=1500 cost=0.0015
```

#### Common Errors
```
ERROR  Model API error: Rate limit exceeded
ERROR  Sandbox execution failed: timeout
ERROR  Database connection pool exhausted
ERROR  Redis connection refused
ERROR  Skill load failed: missing dependency
ERROR  Extension load failed: import error
```

### Log Filtering
```bash
# Errors only
docker compose logs gateway | jq 'select(.level=="ERROR")'

# Specific thread
docker compose logs gateway | jq 'select(.thread_id=="thread-uuid")'

# Specific run
docker compose logs gateway | jq 'select(.run_id=="run-uuid")'

# Time range
docker compose logs gateway --since="2026-09-17T10:00:00Z" --until="2026-09-17T11:00:00Z"

# Follow with filter
docker compose logs -f gateway | jq 'select(.level=="ERROR" or .level=="WARN")'
```

## Performance Profiling

### Backend Profiling
```bash
# CPU profile
cd backend && python -m cProfile -o profile.stats -m pytest tests/test_perf.py

# Analyze
python -c "import pstats; p = pstats.Stats('profile.stats'); p.sort_stats('cumulative').print_stats(30)"

# Memory profile
cd backend && python scripts/sandbox_memory_profile.py

# Blocking I/O detection
cd backend && python scripts/detect_blocking_io_static.py
```

### Frontend Profiling
```bash
# Build analysis
cd frontend && pnpm build && npx @next/bundle-analyzer

# React DevTools Profiler
# 1. Open browser DevTools
# 2. Go to Profiler tab
# 3. Record interaction
# 4. Analyze render times

# Lighthouse audit
npx lighthouse http://localhost:3000 --view
```

### Database Profiling
```bash
# Enable pg_stat_statements
# In postgresql.conf: shared_preload_libraries = 'pg_stat_statements'

# Query analysis
SELECT query, calls, mean_time, total_time, rows 
FROM pg_stat_statements 
ORDER BY mean_time DESC 
LIMIT 20;

# Index usage
SELECT schemaname, tablename, indexname, idx_scan 
FROM pg_stat_user_indexes 
ORDER BY idx_scan;
```

## Recovery Procedures

### Gateway Restart
```bash
# Graceful restart
docker compose restart gateway

# Force restart
docker compose kill gateway && docker compose up -d gateway
```

### Database Recovery
```bash
# From backup
docker compose exec -T postgres psql -U alpha alpha < backup.sql

# Point-in-time recovery (if WAL archiving)
# Use wal-g or pgBackRest
```

### Full Stack Restart
```bash
# Stop all
make stop
# or
docker compose down

# Start all
make dev
# or
make up
```

### Config Rollback
```bash
# Restore previous config
git checkout HEAD~1 -- config.yaml extensions_config.json

# Restart services
docker compose restart gateway
```

## Getting Help

### Information to Collect
When reporting issues, include:
1. **Output of `make doctor`**
2. **Output of `make support-bundle`**
3. **Relevant logs** (filter for ERROR/WARN)
4. **Steps to reproduce**
5. **Environment**: OS, Docker version, deployment type
6. **Config** (redacted secrets)

### Channels
- **GitHub Issues**: Bug reports, feature requests
- **GitHub Discussions**: Questions, community help
- **Security**: security@alpha.dev (for vulnerabilities)

### Self-Service
- Check existing issues (open/closed)
- Search documentation
- Run `make doctor` first
- Generate `make support-bundle`