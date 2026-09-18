# Production Runbook

## Overview

This runbook covers production operations for Alpha, including monitoring, alerting, incident response, and routine maintenance.

## Service Level Objectives (SLOs)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Availability | 99.9% | Uptime over 30 days |
| Latency (p50) | < 500ms | API response time |
| Latency (p99) | < 5s | API response time |
| Error Rate | < 0.1% | 5xx errors / total requests |
| Run Success Rate | > 95% | Completed runs / total runs |

## Monitoring

### Key Metrics

#### Gateway Metrics
```promql
# Request rate
rate(http_requests_total[5m])

# Error rate
rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])

# Latency
histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))

# Active runs
gateway_active_runs

# Thread count
gateway_thread_count
```

#### System Metrics
```promql
# CPU usage
container_cpu_usage_seconds_total

# Memory usage
container_memory_usage_bytes / container_spec_memory_limit_bytes

# Disk usage
container_fs_usage_bytes / container_fs_limit_bytes

# Network
rate(container_network_receive_bytes_total[5m])
rate(container_network_transmit_bytes_total[5m])
```

#### Database Metrics
```promql
# Connections
pg_stat_database_numbackends / pg_settings_max_connections

# Query latency
histogram_quantile(0.99, rate(pg_stat_statements_duration_bucket[5m]))

# Replication lag
pg_replication_lag
```

#### Redis Metrics
```promql
# Memory usage
redis_memory_used_bytes / redis_memory_max_bytes

# Connected clients
redis_connected_clients

# Hit rate
redis_keyspace_hits_total / (redis_keyspace_hits_total + redis_keyspace_misses_total)
```

### Dashboards

Access Grafana at `https://monitoring.your-domain.com/dashboards`

Key dashboards:
- **Alpha Overview** - High-level service health
- **Gateway Deep Dive** - Request/response details
- **Database Performance** - Query analysis
- **Run Analytics** - Success rates, token usage, costs
- **Scheduler** - Scheduled task execution

### Alerting Rules

#### Critical (Page Immediately)
```yaml
- alert: GatewayDown
  expr: up{job="gateway"} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Gateway is down"
    description: "Gateway has been down for 1 minute"

- alert: DatabaseDown
  expr: up{job="postgres"} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "PostgreSQL is down"

- alert: HighErrorRate
  expr: rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.05
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "High 5xx error rate"

- alert: DiskSpaceCritical
  expr: (container_fs_usage_bytes / container_fs_limit_bytes) > 0.9
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Disk space critical"
```

#### Warning (Notify Within 15min)
```yaml
- alert: HighLatency
  expr: histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m])) > 10
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High p99 latency"

- alert: HighMemoryUsage
  expr: (container_memory_usage_bytes / container_spec_memory_limit_bytes) > 0.85
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "High memory usage"

- alert: DatabaseConnectionsHigh
  expr: pg_stat_database_numbackends / pg_settings_max_connections > 0.8
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Database connections near limit"

- alert: ScheduledTaskFailures
  expr: increase(scheduled_task_failures_total[1h]) > 5
  for: 0m
  labels:
    severity: warning
  annotations:
    summary: "Multiple scheduled task failures"
```

#### Info (Log Only)
```yaml
- alert: NewVersionDeployed
  expr: changes(alpha_version_info[1h]) > 0
  labels:
    severity: info
  annotations:
    summary: "New version deployed"
```

## Incident Response

### Severity Levels

| Level | Definition | Response Time | Escalation |
|-------|------------|---------------|------------|
| SEV-1 | Service down, data loss | 15 min | Page on-call, notify stakeholders |
| SEV-2 | Major feature broken | 1 hour | Page on-call |
| SEV-3 | Minor issue, workaround exists | 4 hours | Assign to team |
| SEV-4 | Cosmetic, no user impact | Next sprint | Track in backlog |

### Incident Process

1. **Detect** - Alert fires or user reports issue
2. **Acknowledge** - On-call responds within SLA
3. **Diagnose** - Run `make support-bundle`, check dashboards
4. **Mitigate** - Apply workaround or rollback
5. **Resolve** - Fix root cause
6. **Postmortem** - Write incident report within 48 hours

### Common Incidents & Runbooks

#### Gateway Not Ready
```bash
# 1. Check health
curl http://localhost:8001/health/ready

# 2. Check logs
docker compose logs gateway --tail=100

# 3. Common causes:
# - Database migration pending
# - Model provider unavailable
# - Configuration error

# 4. Fixes:
# - Wait for migration (check logs)
# - Check model API keys
# - Verify config.yaml
# - Restart gateway: docker compose restart gateway
```

#### Database Connection Exhausted
```bash
# 1. Check connections
docker compose exec postgres psql -U alpha -c "SELECT count(*) FROM pg_stat_activity;"

# 2. Kill idle connections
docker compose exec postgres psql -U alpha -c "
SELECT pg_terminate_backend(pid) 
FROM pg_stat_activity 
WHERE state = 'idle' AND state_change < now() - interval '10 minutes';
"

# 3. Increase pool size in config.yaml
# database.postgres.pool_size: 50

# 4. Scale gateway replicas
docker compose up -d --scale gateway=3
```

#### High Memory Usage
```bash
# 1. Check which container
docker stats --no-stream

# 2. Gateway memory leak?
# - Check for runaway runs
# - Check token budget enforcement
# - Restart gateway

# 3. Frontend memory?
# - Check for large bundle
# - Restart frontend

# 4. Database memory?
# - Check work_mem, shared_buffers
# - Restart postgres
```

#### Scheduled Tasks Not Running
```bash
# 1. Check scheduler enabled
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/config | jq '.scheduler.enabled'

# 2. Check scheduler logs
docker compose logs gateway | grep scheduler

# 3. Check wake gate
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/scheduled-tasks/blueprints

# 4. Check incidents
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/scheduled-tasks/incidents

# 5. Restart scheduler (restart gateway)
docker compose restart gateway
```

#### Model Provider Errors
```bash
# 1. Check model health
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/models/local/health

# 2. Check API keys valid
# - Verify in .env
# - Test with curl to provider

# 3. Check fallback models configured
# - config.yaml models[].fallback

# 4. Disable problematic model
# - Edit config.yaml, remove or disable model
# - Restart gateway
```

## Routine Maintenance

### Daily
- [ ] Check dashboards for anomalies
- [ ] Review error logs
- [ ] Verify scheduled tasks completed
- [ ] Check disk space

### Weekly
- [ ] Review token usage and costs
- [ ] Check database size and growth
- [ ] Review security scan results
- [ ] Update threat intelligence

### Monthly
- [ ] Rotate API keys and secrets
- [ ] Review and update dependencies
- [ ] Performance benchmark
- [ ] Capacity planning review
- [ ] Disaster recovery test

### Quarterly
- [ ] Full security audit
- [ ] Penetration test
- [ ] Review and update runbooks
- [ ] Team training exercise

## Backup & Restore

### Backup Schedule
| Data | Frequency | Retention |
|------|-----------|-----------|
| PostgreSQL | Every 5 min (WAL) + Daily (full) | 30 days |
| Redis | Every 15 min (RDB) | 7 days |
| Config | On change (git) | Forever |
| Volumes | Daily snapshot | 30 days |

### Restore Procedures

#### Database Restore
```bash
# 1. Stop writes (scale gateway to 0)
docker compose scale gateway=0

# 2. Restore from backup
# WAL-G restore (preferred)
wal-g backup-fetch LATEST /var/lib/postgresql/data

# OR pg_restore
pg_restore -d alpha backup.dump

# 3. Start gateway
docker compose scale gateway=2
```

#### Config Restore
```bash
# Config is in git
git checkout HEAD -- config.yaml extensions_config.json
# Restart services
docker compose restart gateway
```

#### Full Disaster Recovery
```bash
# 1. Provision new infrastructure (Terraform/Helm)
# 2. Restore database
# 3. Restore config from git
# 4. Deploy application
# 5. Verify health checks
# 6. Update DNS
```

## Capacity Planning

### Current Capacity
| Resource | Current | Limit | Headroom |
|----------|---------|-------|----------|
| Gateway CPU | 500m | 2000m | 75% |
| Gateway Memory | 1Gi | 2Gi | 50% |
| Database CPU | 500m | 2000m | 75% |
| Database Memory | 2Gi | 4Gi | 50% |
| Database Storage | 50Gi | 200Gi | 75% |
| Redis Memory | 500Mi | 1Gi | 50% |

### Scaling Triggers
- **Scale Up**: CPU > 70% for 10min, Memory > 80% for 10min
- **Scale Down**: CPU < 30% for 30min, Memory < 50% for 30min
- **Database**: Connections > 80%, Storage > 80%
- **Redis**: Memory > 80%

### Growth Projections
- Threads: ~1000/month growth
- Runs: ~5000/month growth
- Token usage: ~20% month-over-month
- Storage: ~5GiB/month

## Security Operations

### Daily
- [ ] Check failed login attempts
- [ ] Review audit logs
- [ ] Verify TLS certificates valid

### Weekly
- [ ] Rotate service account keys
- [ ] Review access control lists
- [ ] Scan for vulnerabilities

### Incident: Suspected Breach
1. Isolate affected systems
2. Preserve evidence
3. Notify security team
4. Follow incident response plan
5. Report to authorities if required

## Communication

### Channels
- **Slack**: #alpha-operations (alerts, discussion)
- **PagerDuty**: Critical alerts
- **Email**: Stakeholder notifications
- **StatusPage**: Public status (status.alpha.dev)

### Status Page Updates
- SEV-1: Update within 5 min, every 15 min
- SEV-2: Update within 15 min, every 30 min
- SEV-3: Update within 1 hour, as needed

### Stakeholder Notification
- SEV-1: Immediate (Slack + Email + Phone)
- SEV-2: Within 15 min (Slack + Email)
- SEV-3: Within 1 hour (Slack)

## Postmortem Template

```markdown
# Incident Postmortem: INC-YYYY-MM-DD-XXX

## Summary
- **Date**: YYYY-MM-DD
- **Duration**: XX minutes/hours
- **Severity**: SEV-X
- **Impact**: Description of user impact
- **Root Cause**: Brief root cause

## Timeline
- HH:MM - Detection (alert/user report)
- HH:MM - Acknowledgment
- HH:MM - Diagnosis started
- HH:MM - Mitigation applied
- HH:MM - Resolution
- HH:MM - All clear

## Root Cause Analysis
### What happened
Detailed description

### Why it happened
5 Whys analysis

### Contributing factors
- Factor 1
- Factor 2

## Action Items
| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| Fix root cause | @user | YYYY-MM-DD | Open |
| Add monitoring | @user | YYYY-MM-DD | Open |
| Update runbook | @user | YYYY-MM-DD | Open |

## Lessons Learned
- What went well
- What didn't go well
- What we learned
```

## Useful Commands Reference

```bash
# Support bundle (run during incidents)
make support-bundle

# Health checks
curl http://localhost:2026/health
curl http://localhost:8001/health/ready
curl http://localhost:8001/api/ops/status

# Logs
make docker-logs
docker compose logs gateway --tail=500 --follow

# Database
docker compose exec postgres psql -U alpha alpha

# Redis
docker compose exec redis redis-cli

# Config
docker compose exec gateway cat /app/config.yaml

# Restart services
docker compose restart gateway
docker compose restart frontend
docker compose restart nginx

# Scale
docker compose up -d --scale gateway=3

# Backup
docker compose exec postgres pg_dump -U alpha alpha > backup_$(date +%Y%m%d).sql

# Version info
curl http://localhost:8001/api/ops/version
```

## Contacts

| Role | Name | Contact |
|------|------|---------|
| Primary On-Call | | |
| Secondary On-Call | | |
| Engineering Lead | | |
| Security Team | | |
| Infrastructure Team | | |