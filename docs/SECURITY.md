# Security Documentation

## Overview

Alpha implements defense-in-depth security across all layers: network, application, data, and operations.

## Security Architecture

```
┌─────────────────────────────────────────────────────────────────┐
                        Security Layers
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Network          │  Application      │  Data        │  Ops    │
│  ──────────────── │  ──────────────── │  ─────────── │  ────── │
│  TLS 1.3         │  Authentication  │  Encryption  │  Audit  │
│  WAF Rules       │  Authorization   │  At Rest     │  Logs   │
│  Rate Limiting   │  Input Validation│  In Transit  │  Alerts │
│  Network Policy  │  Sandbox Escape  │  Key Mgmt    │  Backup │
│  Private Networks│  Supply Chain    │  Retention   │  DR     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Authentication

### Better Auth Integration
- Email/password with bcrypt (cost factor 12)
- OAuth 2.0: GitHub, Google, Microsoft
- Session management with secure cookies
- CSRF protection via SameSite=Strict
- Password reset via email tokens

### API Authentication
```bash
# Session cookie (browser)
Cookie: better-auth.session=<token>

# Bearer token (API)
Authorization: Bearer <api-key>

# Internal (scheduler/bots)
X-Internal-Auth: <internal-token>
```

### Session Security
- HttpOnly, Secure, SameSite=Strict cookies
- Session timeout: 24 hours (configurable)
- Concurrent session limit: 10 per user
- Automatic logout on password change

## Authorization

### Role-Based Access Control (RBAC)

| Role | Threads | Projects | Bots | Skills | Admin |
|------|---------|----------|------|--------|-------|
| Owner | Full | Full | Full | Full | Yes |
| Admin | Full | Full | Full | Full | Yes |
| Member | Own | Assigned | Assigned | Use | No |
| Viewer | Read | Read | Read | Use | No |

### Resource Ownership
- Threads: Owned by creator, shared via project membership
- Projects: Owned by creator, members added explicitly
- Bots: Global, accessed via DM or team membership
- Skills: Public (read all), Custom (owner only)

### Thread Permissions
```python
# Thread access check
async def check_thread_access(user_id: str, thread_id: str, action: str) -> bool:
    thread = await get_thread(thread_id)
    if thread.owner_id == user_id:
        return True
    if action == "read" and thread.is_public:
        return True
    # Check project membership
    return await check_project_membership(user_id, thread.project_id, action)
```

## Data Protection

### Encryption at Rest

#### Database
- PostgreSQL: Transparent Data Encryption (TDE) via filesystem (LUKS) or cloud provider
- SQLite: SQLCipher for encrypted database files
- Backups: Encrypted with AES-256-GCM before storage

#### Redis
- TLS for data in transit
- RDB snapshots encrypted at rest (if persistence enabled)

#### File Storage
- Uploaded files: AES-256-GCM encrypted before storage
- Checkpoints: AES-256-GCM encrypted (per-thread keys)
- Cognitive memory: Per-owner encryption keys

### Encryption in Transit
- All external: TLS 1.3 (Nginx termination)
- Internal: mTLS between services (optional, configurable)
- Database: TLS required
- Redis: TLS required

### Key Management
```
Keys:
├── Master Key (KMIP/HSM or env)
│   ├── Data Encryption Keys (DEK) - per resource
│   │   ├── Thread checkpoint keys
│   │   ├── File encryption keys
│   │   └── Cognitive memory keys
│   └── Key Encryption Keys (KEK) - for DEK wrapping
├── TLS Certificates (Let's Encrypt or custom)
├── JWT Signing Keys (rotated weekly)
└── API Keys (hashed with bcrypt)
```

### Secret Handling
- **Never** in config files (gitignored)
- **Never** in logs (automatic redaction)
- **Never** in error messages
- Environment variables only
- Runtime injection via Docker secrets / K8s secrets
- Rotation via `make rotate-secrets` (custom script)

## Sandbox Security

### Execution Isolation

#### Local Mode
- Process isolation (separate user)
- Resource limits (CPU, memory, disk, time)
- No network access (configurable)
- Filesystem jail (chroot-like)

#### Docker Mode
- Container per execution
- Read-only root filesystem
- Dropped capabilities (ALL except needed)
- No new privileges
- Seccomp profile
- User namespace mapping

#### Kubernetes Mode (Provisioner)
- Pod per execution
- Dedicated namespace
- Network policies (deny all by default)
- Resource quotas
- Admission controller validation
- Runtime class (gVisor/Kata optional)

### Tool Execution Safety
- Input validation on all tool parameters
- Output sanitization
- Timeout enforcement (default 300s)
- Resource monitoring
- Audit logging of all tool calls

### Supply Chain Security
- Dependency scanning (pip-audit, npm audit)
- SBOM generation (Syft)
- Image signing (Cosign)
- Base image updates (Dependabot/Renovate)
- Lockfile verification

## Network Security

### Nginx Configuration
```nginx
# Security headers (all responses)
add_header X-Frame-Options "DENY" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' https:; frame-ancestors 'none';" always;

# HSTS (HTTPS only)
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;

# Rate limiting
limit_req_zone $binary_remote_addr zone=api:10m rate=300r/m;
limit_req zone=api burst=50 nodelay;

# Block common attacks
location ~* \.(git|env|sql|bak)$ { deny all; }
```

### Network Policies (Kubernetes)
```yaml
# Default deny all
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]

# Gateway ingress from nginx only
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gateway-ingress
spec:
  podSelector:
    matchLabels:
      app: gateway
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: nginx
      ports:
        - protocol: TCP
          port: 8001

# Gateway egress to database, redis, external APIs
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gateway-egress
spec:
  podSelector:
    matchLabels:
      app: gateway
  policyTypes: [Egress]
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
    - to: []  # External (model APIs)
      ports:
        - protocol: TCP
          port: 443
```

## Input Validation & Sanitization

### API Input Validation
- Pydantic models for all request bodies
- Strict type checking
- Custom validators for complex fields
- Size limits on all inputs
- Regex validation for identifiers

### Output Encoding
- JSON responses: Automatic escaping
- HTML: Not served (API-only)
- SSE: JSON-encoded events
- File downloads: Content-Disposition attachment

### SQL Injection Prevention
- SQLAlchemy ORM (parameterized queries)
- No raw SQL in application code
- Read-only replicas for analytics

### XSS Prevention
- No server-side HTML rendering
- CSP headers
- API-only backend

## Audit Logging

### Logged Events
| Event | Fields |
|-------|--------|
| Authentication | user_id, ip, method, success, timestamp |
| Authorization | user_id, resource, action, allowed, timestamp |
| Thread CRUD | user_id, thread_id, action, timestamp |
| Run execution | user_id, thread_id, run_id, model, tools, tokens, timestamp |
| File upload | user_id, thread_id, file_id, size, type, timestamp |
| Config change | user_id, config_key, old_value_hash, new_value_hash, timestamp |
| Skill install | user_id, skill_id, source, timestamp |
| Admin actions | admin_id, action, target, timestamp |

### Log Format (JSON)
```json
{
  "timestamp": "2026-09-17T10:00:00.123Z",
  "level": "INFO",
  "service": "gateway",
  "trace_id": "abc123",
  "span_id": "def456",
  "event": "thread_created",
  "user_id": "user-uuid",
  "thread_id": "thread-uuid",
  "ip": "192.168.1.1",
  "user_agent": "Mozilla/5.0..."
}
```

### Log Retention
- Hot: 7 days (Elasticsearch/ Loki)
- Warm: 90 days (S3/ GCS)
- Cold: 7 years (Glacier/ Archive)
- Audit logs: 7 years minimum (compliance)

## Vulnerability Management

### Scanning
```bash
# Backend dependencies
cd backend && pip-audit

# Frontend dependencies
cd frontend && npm audit

# Container images
docker scan alpha/gateway:latest
docker scan alpha/frontend:latest

# Code scanning
semgrep --config=auto backend/
semgrep --config=auto frontend/
```

### Patch Process
1. **Critical (CVSS >= 9.0)**: Patch within 24 hours
2. **High (CVSS >= 7.0)**: Patch within 72 hours
3. **Medium (CVSS >= 4.0)**: Patch within 2 weeks
4. **Low (CVSS < 4.0)**: Patch in next release

### Dependency Policy
- Pin exact versions in lockfiles
- Automated PRs for updates (Dependabot)
- Review and test before merge
- No unpinned dependencies

## Incident Response

### Security Incident Types
1. **Data breach** - Unauthorized data access
2. **Authentication bypass** - Broken auth
3. **Injection attack** - SQLi, command injection
4. **Denial of service** - Resource exhaustion
5. **Supply chain** - Compromised dependency
6. **Insider threat** - Malicious authorized user

### Response Playbook
```bash
# 1. Contain
docker compose scale gateway=0  # Stop API
# Revoke compromised credentials

# 2. Assess
make support-bundle  # Collect evidence
# Check audit logs
# Check access logs

# 3. Eradicate
# Rotate all secrets
# Patch vulnerability
# Remove malicious code

# 4. Recover
# Restore from clean backup
# Deploy patched version
# Verify integrity

# 5. Post-incident
# Postmortem
# Update defenses
# Notify affected parties (if required)
```

## Compliance

### Data Privacy
- **GDPR**: Right to access, rectify, erase, portability
- **CCPA**: Consumer rights, opt-out
- **Data processing agreement** for subprocessors

### Data Retention
| Data Type | Retention | Basis |
|-----------|-----------|-------|
| Threads/Runs | User-controlled | Consent |
| Audit logs | 7 years | Legal obligation |
| Metrics | 13 months | Legitimate interest |
| Backups | 30 days | Disaster recovery |
| Cognitive memory | Per-owner config | Consent |

### Data Subject Requests
```bash
# Export user data
python scripts/export_user_data.py --user-id <id> --output /tmp/export

## Enterprise Security Enclave & Governance Plane

### Astra Security Enclave
*Tools: `astra_security_manage`, `enterprise_security_manage`*
- **Hardware & Software Isolation**: Enforces tenant-level hardware and software isolation boundaries.
- **Scoped Credential Vault**: Subagents only receive explicitly scoped, short-lived tokens required for their delegated tasks.
- **Data Perimeter**: Blocks egress traffic to unauthorized IPs or unapproved domains.

### Smart Command Approval Gate
*Tool: `verify_command_approval`*
- **Static Blast Radius Analysis**: Terminal commands evaluated across file mutation, network egress, privilege escalation, and deletion risk tiers.
- **Human-in-the-Loop Interception**: High-risk actions (`rm -rf`, schema drops, git force-pushes) block until explicitly approved by the operator in the UI.

### Emergency Stop (Estop)
*Tool: `emergency_stop_manage`*
- **Global Halt**: Instantly cancels active agent runs, terminates child subprocesses, and closes network connections.
- **Fail-Safe Rollback**: Reverts uncommitted git checkpoints and releases acquired project locks.

### Trajectory Flight Recorder
*Tool: `trajectory_audit_tool`*
- **Cryptographic Audit Trail**: Records step-by-step reasoning tokens, tool invocations, inputs, outputs, and timestamps.
- **Tamper-Resistant Storage**: Stored alongside AES-GCM encrypted checkpoints for forensic post-incident reviews.

### Universal Artifact Lineage Tracing
*Tool: `trace_artifact_lineage`*
- **Provenance Graph**: Tracks every file, diff, and document back to the exact parent prompt, source data, and generating agent.
- **Integrity Hashes**: SHA-256 content hashes generated upon creation and validated prior to execution.

### Token Budget Ceilings & Real-Cost Telemetry
- **Deterministic Token Backstops**: Sets hard caps per run (e.g. 1M or 2M tokens) to prevent infinite hallucination loops and unexpected API billing.
- **Cache-Aware Telemetry**: Dynamically factors in provider prompt caching discounts (e.g. Anthropic/OpenAI prompt cache hits).

## Security Testing

### Penetration Testing
- Annual third-party pen test
- Scope: Full stack (API, frontend, infrastructure)
- Report shared with customers on request

### Bug Bounty
- Private program (HackerOne/Intigriti)
- Scope: *.alpha.dev, API endpoints
- Rewards based on severity

### Internal Testing
- SAST: Semgrep in CI
- DAST: OWASP ZAP in staging
- Dependency: Snyk/Dependabot
- Container: Trivy/Grype

## Secure Configuration

### Hardening Checklist
- [ ] TLS 1.3 only
- [ ] HSTS enabled
- [ ] CSP configured
- [ ] Rate limiting active
- [ ] WAF rules deployed
- [ ] Secrets in vault
- [ ] Debug endpoints disabled
- [ ] Default credentials changed
- [ ] Unused ports closed
- [ ] Logging configured
- [ ] Monitoring alerts active
- [ ] Backup encryption verified
- [ ] DR tested quarterly

### Environment-Specific

#### Development
- Relaxed CORS (localhost)
- Debug logging
- Self-signed certs
- No rate limiting

#### Staging
- Production-like config
- Test secrets
- Rate limiting enabled
- Full monitoring

#### Production
- Strict CORS (exact domains)
- Info-level logging
- Valid TLS certs
- Aggressive rate limiting
- Full audit logging
- Real-time alerting

## Reporting Security Issues

### Responsible Disclosure
- Email: security@alpha.dev
- PGP Key: [link]
- Response within 48 hours
- Fix timeline based on severity
- Credit in advisory (if desired)

### Scope
- In scope: All alpha.dev subdomains, API, desktop app
- Out of scope: Third-party services, social engineering, DoS

## Security Contacts

| Role | Contact |
|------|---------|
| Security Team | security@alpha.dev |
| Incident Response | incident@alpha.dev |
| Privacy Officer | privacy@alpha.dev |