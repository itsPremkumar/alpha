# Security Policy

## Supported Versions

We release security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| main    | ✅ Yes             |
| v1.x    | ✅ Yes             |
| < v1.0  | ❌ No              |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security vulnerability, please report it responsibly.

### How to Report

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to **security@itsPremkumar.com**.

Include the following information:
- Type of vulnerability (e.g., RCE, SQLi, XSS, authentication bypass, etc.)
- Full description of the vulnerability
- Steps to reproduce
- Potential impact
- Any proof-of-concept or exploit code (if available)
- Affected versions (if known)

### What to Expect

1. **Acknowledgment**: We will acknowledge receipt within 48 hours
2. **Triage**: We will assess the severity and impact within 5 business days
3. **Updates**: We will keep you informed of progress at least weekly
4. **Resolution**: We aim to release a fix within 90 days for critical issues
5. **Disclosure**: Coordinated disclosure after a fix is available

### Response Timeline

| Severity | Acknowledgment | Triage | Fix Target |
|----------|---------------|--------|------------|
| Critical | 24 hours      | 48 hours | 30 days   |
| High     | 48 hours      | 5 days   | 60 days   |
| Medium   | 5 days        | 10 days  | 90 days   |
| Low      | 10 days       | 30 days  | Next release |

## Security Architecture

### Sandbox Isolation

Alpha uses multiple layers of sandboxing:

- **Process Isolation**: Each agent runs in its own process
- **Container Isolation**: Docker/AIO/E2B sandboxes with restricted capabilities
- **Network Isolation**: Configurable network policies per sandbox
- **Filesystem Isolation**: Per-thread isolated filesystems with quota enforcement
- **Resource Limits**: CPU, memory, disk, and network quotas per sandbox

### Authentication & Authorization

- **Gateway Authentication**: JWT-based with configurable providers (OIDC, API keys, PATs)
- **Role-Based Access Control**: Fine-grained permissions per resource
- **Thread Ownership**: Threads scoped to owners, cross-thread access controlled
- **Admin Operations**: Separate admin scope, separate authentication

### Data Protection

- **Encryption at Rest**: SQLite/PostgreSQL with encryption options
- **Encryption in Transit**: TLS 1.3 for all external communication
- **Secrets Management**: Environment variables, Vault integration, no secrets in code
- **Data Retention**: Configurable retention policies with automated cleanup

### Supply Chain Security

- **Dependency Scanning**: Automated via Dependabot and pip-audit
- **SBOM Generation**: SBOM generated for each release
- **Signature Verification**: Release artifacts signed with cosign
- **Provenance**: SLSA Level 2+ build provenance

## Vulnerability Disclosure

We follow coordinated vulnerability disclosure:

1. **Private Report**: Reporter sends details to security@itsPremkumar.com
2. **Verification**: We reproduce and assess impact
3. **Fix Development**: We develop and test a fix
4. **Coordinated Release**: Fix released with security advisory
5. **Public Disclosure**: CVE requested, details published after fix available

## Security Best Practices for Users

### Configuration
- Use strong, unique API keys
- Enable authentication in production
- Configure sandbox resource limits
- Enable audit logging
- Regularly rotate API keys

### Deployment
- Use HTTPS/TLS in production
- Run behind a reverse proxy (nginx included)
- Configure firewall rules
- Enable firewall on sandbox hosts
- Monitor resource usage

### Monitoring
- Enable audit logging
- Set up alerts for anomalous activity
- Monitor sandbox resource usage
- Review audit logs regularly

## Security Contacts

- **Security Team**: security@itsPremkumar.com
- **Maintainer**: Prem Kumar (@itsPremkumar)
- **GPG Key**: Available on request

## Hall of Fame

We thank the following researchers for responsibly disclosing vulnerabilities:

| Researcher | Vulnerability | Date |
|------------|---------------|------|
| *Your name here* | *First vulnerability* | *Date* |

---

**Last Updated**: 2026-10-10
**Version**: 1.0