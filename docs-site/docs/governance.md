---
sidebar_position: 1
title: Governance
description: Alpha project governance model and decision making
slug: /governance
---

# Governance

Alpha follows an open governance model inspired by successful open source projects like Kubernetes, Rust, and Node.js.

## Governance Model

### Decision Making

Decisions are made through a **consensus-seeking** process:

1. **Proposal** - Anyone can propose changes via GitHub Discussions or RFC
2. **Discussion** - Open discussion period (minimum 7 days for major changes)
3. **Consensus** - Rough consensus among maintainers
4. **Implementation** - Approved changes merged by maintainers

### Roles

| Role | Responsibilities | Selection |
|------|------------------|-----------|
| **BDFL** (Prem Kumar) | Final authority on project direction | Project creator |
| **Maintainers** | Code review, merge decisions, releases | Demonstrated contribution + peer nomination |
| **Contributors** | Code, docs, tests, issues, reviews | Open to all |
| **Community** | Feedback, testing, advocacy | Open to all |

### Maintainer Criteria

To become a maintainer:
- 5+ merged PRs of significant complexity
- Active for 6+ months
- Demonstrated understanding of codebase
- Nominated by 2+ existing maintainers
- Approved by BDFL

### Decision Categories

| Category | Authority | Process |
|----------|-----------|---------|
| **Strategic** (roadmap, architecture) | BDFL + Maintainers | RFC + Consensus |
| **Architectural** (core changes) | Maintainers | RFC + Consensus |
| **Feature** (new features) | Maintainers | PR + Review |
| **Bug Fix** | Any Maintainer | PR + Review |
| **Documentation** | Any Contributor | PR + Review |
| **Dependencies** | Maintainers | Automated + Review |

---

## Release Process

### Versioning

Follows [Semantic Versioning](https://semver.org/):

| Version | When | Example |
|---------|------|---------|
| Major | Breaking changes, major features | 1.0.0 → 2.0.0 |
| Minor | New features, non-breaking | 1.0.0 → 1.1.0 |
| Patch | Bug fixes, security | 1.0.0 → 1.0.1 |

### Release Cycle

| Phase | Duration | Activities |
|-------|----------|------------|
| Development | 6-8 weeks | Feature development |
| Feature Freeze | 1 week | Bug fixes only |
| Release Candidate | 1-2 weeks | Testing, bug fixes |
| Release | 1 day | Tag, publish, announce |

### Release Checklist

- [ ] All CI checks pass
- [ ] Changelog updated
- [ ] Documentation updated
- [ ] Migration guide (if breaking)
- [ ] Security scan passed
- [ ] Performance benchmarks met
- [ ] Docker images built and tested
- [ ] Changelog published
- [ ] GitHub Release created
- [ ] Docker images pushed
- [ ] Announcement posted

---

## Code of Conduct

All participants must follow the [Code of Conduct](CODE_OF_CONDUCT.md).

### Enforcement

- **Reports**: conduct@itsPremkumar.com
- **Process**: Investigation → Decision → Action
- **Appeals**: governance@itsPremkumar.com

---

## Security

### Vulnerability Reporting

Report to: security@itsPremkumar.com

See [SECURITY.md](SECURITY.md) for details.

### Disclosure Policy

- **Coordinated disclosure** - Fix before public disclosure
- **Timeline**: 90 days for critical, 180 for others
- **Credit**: Researchers credited (unless anonymous requested)

---

## Intellectual Property

### License

Alpha is licensed under the **MIT License**.

### Contributions

All contributions are licensed under the MIT License.

### Trademarks

"Alpha" and the Alpha logo are trademarks of Prem Kumar.

---

## Communication Channels

| Channel | Purpose | Link |
|---------|---------|------|
| GitHub Discussions | Design discussions, RFCs | [github.com/itsPremkumar/alpha/discussions](https://github.com/itsPremkumar/alpha/discussions) |
| Discord | Real-time chat, support | [discord.gg/alpha](https://discord.gg/alpha) |
| GitHub Issues | Bug reports, feature requests | [github.com/itsPremkumar/alpha/issues](https://github.com/itsPremkumar/alpha/issues) |
| Email | Security, governance | security@itsPremkumar.com / governance@itsPremkumar.com |

---

## Amendment Process

Changes to governance require:

1. **Proposal** - GitHub Discussion with `governance` label
2. **Discussion** - Minimum 14 days
3. **Consensus** - Rough consensus among maintainers
4. **Ratification** - BDFL approval
5. **Documentation** - Update this file

---

*Last updated: 2026-10-10*
*Version: 1.0*