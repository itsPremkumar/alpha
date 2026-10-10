---
sidebar_position: 1
title: Security
description: Alpha security model and best practices
slug: /security
---

# Security

Alpha is designed with security as a foundational principle.

## Security Model

### Defense in Depth

```
┌─────────────────────────────────────────────┐
│              Network Perimeter               │
│  (nginx, WAF, DDoS protection)              │
├─────────────────────────────────────────────┤
│              Gateway Layer                   │
│  (Auth, Rate limiting, Input validation)    │
├─────────────────────────────────────────────┤
│              Application Layer               │
│  (RBAC, Thread isolation, Audit logging)    │
├─────────────────────────────────────────────┤
│              Runtime Layer                   │
│  (Sandbox, Capability drops, Seccomp)       │
├─────────────────────────────────────────────┤
│              Data Layer                      │
│  (Encryption, Access control, Auditing)     │
└─────────────────────────────────────────────┘
```

---

## Authentication

### Methods

| Method | Use Case | Security |
|--------|----------|----------|
| JWT | API access | RS256, 15min expiry |
| API Keys | Server-to-server | Scoped, rotatable |
| PATs | User CLI access | User-scoped, revocable |
| OIDC | Enterprise SSO | Standard OIDC |

### JWT Configuration

```yaml
jwt:
  algorithm: RS256
  access_token_ttl: 900      # 15 minutes
  refresh_token_ttl: 604800  # 7 days
  issuer: "alpha.itsPremkumar.com"
  audience: "alpha-api"
```

---

## Authorization

### RBAC Model

| Role | Permissions |
|------|-------------|
| Admin | Full access, user management, config |
| Operator | Deploy, monitor, debug |
| Developer | Threads, agents, tools |
| Viewer | Read-only access |

### Thread Isolation

- Each thread has isolated memory, tools, and sandbox
- Cross-thread access requires explicit sharing
- Owner-based access control enforced at gateway

---

## Sandbox Security

### Provider Comparison

| Provider | Isolation | Performance | Use Case |
|----------|-----------|-------------|----------|
| Docker | Container | Good | General purpose |
| AIO | Process | Excellent | Low latency |
| E2B | Cloud VM | Good | Heavy compute, browser |
| Local | Process | Best | Development only |

### Hardening

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

---

## Data Protection

### Encryption

| State | Algorithm | Key Management |
|-------|-----------|----------------|
| At Rest | AES-256-GCM | HashiCorp Vault / AWS KMS |
| In Transit | TLS 1.3 | mTLS for internal |
| Secrets | AES-256-GCM | Vault transit engine |

### Secrets Management

```yaml
secrets:
  backend: vault  # vault, env, k8s
  vault:
    address: https://vault:8200
    transit_path: transit/encrypt/alpha
  rotation:
    interval: 90d
    grace_period: 7d
```

---

## Network Security

### Network Policies

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gateway-egress
spec:
  podSelector:
    matchLabels:
      app: alpha-gateway
  policyTypes:
  - Egress
  egress:
  - to:
    - podSelector:
        matchLabels:
          app: postgres
    ports:
    - protocol: TCP
      port: 5432
  - to:
    - podSelector:
        matchLabels:
          app: redis
    ports:
    - protocol: TCP
      port: 6379
```

### TLS Configuration

```nginx
# nginx TLS config
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
ssl_prefer_server_ciphers off;
ssl_session_cache shared:SSL:10m;
ssl_session_timeout 10m;
```

---

## Application Security

### Input Validation

- All inputs validated via Pydantic models
- SQL injection prevention via SQLAlchemy ORM
- XSS prevention via React auto-escaping
- Path traversal prevention in file ops

### Rate Limiting

```python
# Per-client rate limiting
RATE_LIMITS = {
    "default": "60/minute",
    "auth": "10/minute",
    "runs": "30/minute",
    "tools": "100/minute",
}
```

### CORS Configuration

```python
CORS_CONFIG = {
    "allow_origins": ["https://alpha.itsPremkumar.com"],
    "allow_methods": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "allow_headers": ["Authorization", "Content-Type", "X-CSRF-Token"],
    "allow_credentials": True,
    "max_age": 3600,
}
```

---

## Compliance

### Standards

| Standard | Status | Evidence |
|----------|--------|----------|
| SOC 2 Type II | In Progress | Audit Q1 2027 |
| GDPR | Compliant | DPA, DPIA |
| CCPA | Compliant | Data subject rights |
| ISO 27001 | Planned | 2027 target |

### Data Subject Rights

| Right | Implementation |
|-------|----------------|
| Access | `/api/user/data` |
| Rectification | `/api/user/profile` |
| Erasure | `/api/user/delete` |
| Portability | `/api/user/export` |
| Restriction | `/api/user/restrict` |

---

## Incident Response

### Severity Levels

| Level | Response Time | Escalation |
|-------|---------------|------------|
| P0 (Critical) | 15 min | CTO + Security |
| P1 (High) | 1 hour | Security Lead |
| P2 (Medium) | 4 hours | On-call |
| P3 (Low) | 24 hours | Team |

### Runbooks

| Incident | Runbook |
|----------|---------|
| Data breach | `SECURITY-BREACH.md` |
| Compromise | `COMPROMISE.md` |
| DDoS | `DDOS.md` |
| Credential leak | `CREDENTIAL-LEAK.md` |

---

## Security Checklist

### Pre-Deployment

- [ ] Dependency scan passed
- [ ] Container scan passed
- [ ] SAST/DAST passed
- [ ] Secrets scan passed
- [ ] Penetration test (quarterly)

### Runtime

- [ ] WAF enabled
- [ ] Rate limiting active
- [ ] Audit logging enabled
- [ ] SIEM integration
- [ ] Vulnerability scanning (daily)

### Post-Incident

- [ ] Root cause analysis
- [ ] Post-mortem published
- [ ] Fixes deployed
- [ ] Monitoring enhanced

---

## Reporting Security Issues

**Email**: security@itsPremkumar.com  
**PGP**: [keys.openpgp.org](https://keys.openpgp.org/search?q=security%40itsPremkumar.com)  
**Response**: Within 48 hours  

See [SECURITY.md](../SECURITY.md) for full details.