"""Reusable bot role templates (inventory #23).

Each template supplies the display name, role, and avatar for one
well-understood team role. The SOUL text is generated from the role via
:func:`generate_default_soul` so personality stays in one place. Users can
always create fully custom roles — templates are shortcuts, not a closed set.
"""

from __future__ import annotations

from typing import Any

BOT_TEMPLATES: dict[str, dict[str, Any]] = {
    "ceo": {
        "display": "CEO",
        "role": "Chief Executive Officer & Final Decision Maker",
        "avatar": "👑",
        "department": "executive",
        "reports_to": None,
        "responsibilities": ["Strategic direction", "Final approvals", "Resource allocation", "Company vision"],
        "capabilities": ["executive_planning", "risk_evaluation", "high_level_synthesis"],
    },
    "cto": {
        "display": "CTO",
        "role": "Chief Technology Officer & Architecture Owner",
        "avatar": "🧭",
        "department": "engineering",
        "reports_to": "ceo",
        "responsibilities": ["Technical architecture", "Engineering roadmap", "System integrity", "Technology selection"],
        "capabilities": ["architecture_design", "tech_stack_selection", "code_review"],
    },
    "architect": {
        "display": "Architect",
        "role": "System Architect & Technical Lead",
        "avatar": "🏛️",
        "department": "engineering",
        "reports_to": "cto",
        "responsibilities": ["System design", "Interface boundaries", "Scalability", "Design documentation"],
        "capabilities": ["system_design", "refactoring", "boundary_enforcement"],
    },
    "developer": {
        "display": "Developer",
        "role": "Software Engineer & Backend Developer",
        "avatar": "💻",
        "department": "engineering",
        "reports_to": "architect",
        "responsibilities": ["Backend services", "Data models", "Core logic", "Unit tests"],
        "capabilities": ["python", "fastapi", "sql", "testing"],
    },
    "coder": {
        "display": "Coder",
        "role": "Software Engineer & Backend Developer",
        "avatar": "💻",
        "department": "engineering",
        "reports_to": "architect",
        "responsibilities": ["Feature implementation", "Bug fixing", "Algorithm development"],
        "capabilities": ["python", "typescript", "backend", "code_generation"],
    },
    "frontend": {
        "display": "Frontend",
        "role": "Frontend & Design Specialist",
        "avatar": "🎨",
        "department": "engineering",
        "reports_to": "architect",
        "responsibilities": ["UI components", "User experience", "Client state", "Responsive design"],
        "capabilities": ["react", "nextjs", "tailwind", "ui_design"],
    },
    "researcher": {
        "display": "Researcher",
        "role": "Deep Researcher & Synthesis Specialist",
        "avatar": "🔍",
        "department": "product",
        "reports_to": "product-manager",
        "responsibilities": ["Information discovery", "Fact verification", "Market & tech research", "Synthesis"],
        "capabilities": ["web_search", "document_synthesis", "fact_checking"],
    },
    "data-analyst": {
        "display": "Data Analyst",
        "role": "Data Engineer & Database Specialist",
        "avatar": "📊",
        "department": "engineering",
        "reports_to": "architect",
        "responsibilities": ["Data pipelines", "Schema migration", "Analytics queries", "Database tuning"],
        "capabilities": ["sql", "data_modeling", "performance_tuning"],
    },
    "qa": {
        "display": "QA",
        "role": "QA & Automated Verification Specialist",
        "avatar": "🧪",
        "department": "qa",
        "reports_to": "cto",
        "responsibilities": ["Automated test suites", "Regression testing", "Acceptance verification", "Bug reporting"],
        "capabilities": ["pytest", "e2e_testing", "boundary_testing"],
    },
    "tester": {
        "display": "Tester",
        "role": "QA & Automated Verification Specialist",
        "avatar": "🧪",
        "department": "qa",
        "reports_to": "qa",
        "responsibilities": ["Unit test execution", "Edge case validation", "Quality gate evaluation"],
        "capabilities": ["pytest", "test_automation", "failure_triage"],
    },
    "reviewer": {
        "display": "Reviewer",
        "role": "Code & Quality Reviewer",
        "avatar": "🧹",
        "department": "qa",
        "reports_to": "architect",
        "responsibilities": ["Code review", "Standards enforcement", "Security auditing", "Refactor guidance"],
        "capabilities": ["code_audit", "style_enforcement", "security_review"],
    },
    "security": {
        "display": "Security",
        "role": "Security & Vulnerability Analyst",
        "avatar": "🛡️",
        "department": "security",
        "reports_to": "cto",
        "responsibilities": ["Vulnerability scanning", "Permission boundaries", "Credential safety", "Audit logs"],
        "capabilities": ["vulnerability_analysis", "credential_auditing", "risk_mitigation"],
    },
    "sre": {
        "display": "SRE",
        "role": "Site Reliability Engineer & On-Call Responder",
        "avatar": "🚨",
        "department": "operations",
        "reports_to": "cto",
        "responsibilities": ["System uptime", "Incident triage", "Resource monitoring", "Crash recovery"],
        "capabilities": ["incident_response", "health_monitoring", "recovery_automation"],
    },
    "devops": {
        "display": "DevOps",
        "role": "DevOps & Infrastructure Engineer",
        "avatar": "⚙️",
        "department": "operations",
        "reports_to": "sre",
        "responsibilities": ["CI/CD pipelines", "Build tooling", "Environment configuration", "Docker / packaging"],
        "capabilities": ["docker", "makefiles", "environment_provisioning"],
    },
    "product-manager": {
        "display": "Product Manager",
        "role": "Product Manager & Requirements Owner",
        "avatar": "📋",
        "department": "product",
        "reports_to": "ceo",
        "responsibilities": ["User stories", "Acceptance criteria", "Backlog prioritization", "Roadmap planning"],
        "capabilities": ["requirements_definition", "prioritization", "spec_writing"],
    },
    "technical-writer": {
        "display": "Writer",
        "role": "Technical Writer & Documentation Specialist",
        "avatar": "✍️",
        "department": "product",
        "reports_to": "product-manager",
        "responsibilities": ["User documentation", "API documentation", "Release notes", "Architecture walkthroughs"],
        "capabilities": ["technical_writing", "markdown", "api_docs"],
    },
    "marketing": {
        "display": "Marketing",
        "role": "Marketing Manager & Growth Specialist",
        "avatar": "📣",
        "department": "growth",
        "reports_to": "ceo",
        "responsibilities": ["Messaging", "Outreach", "Positioning", "User engagement"],
        "capabilities": ["copywriting", "campaign_strategy", "audience_research"],
    },
    "sales": {
        "display": "Sales",
        "role": "Sales Specialist & Deal Coordinator",
        "avatar": "🤝",
        "department": "growth",
        "reports_to": "ceo",
        "responsibilities": ["Client outreach", "Proposal drafting", "Value proposition alignment"],
        "capabilities": ["negotiation", "client_discovery", "proposal_generation"],
    },
    "support": {
        "display": "Support",
        "role": "Support Specialist & Customer Advocate",
        "avatar": "🎧",
        "department": "support",
        "reports_to": "product-manager",
        "responsibilities": ["Issue triage", "User guidance", "Bug reproduction", "Feedback loop to engineering"],
        "capabilities": ["troubleshooting", "customer_communication", "issue_escalation"],
    },
}

# Lifecycle states for permanent bots (inventory #25). Bots are data, not
# processes: states describe availability for team runs and messaging.
# - active: fully available
# - sleeping: idle, wakes on demand (auto-provisioned mentions still work)
# - suspended: temporarily out of rotation (skipped by team runs)
# - archived: retired, hidden from defaults (skipped by team runs)
BOT_STATUSES: tuple[str, ...] = ("active", "sleeping", "suspended", "archived")

#: Standard organization departments (Master Inventory #20)
DEPARTMENTS: tuple[str, ...] = (
    "executive",
    "engineering",
    "product",
    "qa",
    "operations",
    "security",
    "growth",
    "support",
)

#: States excluded from autonomous team execution.
INACTIVE_STATUSES: frozenset[str] = frozenset({"suspended", "archived"})


def get_template(name: str) -> dict[str, str] | None:
    """Return the template for a role slug, or None when unknown."""
    return BOT_TEMPLATES.get(name.lower().strip())


def list_templates() -> list[dict[str, str]]:
    """Return the full template catalog with slugs, sorted by name."""
    return [{"slug": slug, **spec} for slug, spec in sorted(BOT_TEMPLATES.items())]
