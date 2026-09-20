"""Pre-configured and dynamically synthesized archetypes for perpetual autonomous collectives."""

from __future__ import annotations

import logging

from pydantic import BaseModel

from alpha.company.models import (
    CompanyProject,
    DepartmentSpec,
    DepartmentType,
    KPISpec,
    OrgArchetype,
    StrategicObjective,
)

logger = logging.getLogger(__name__)


class ArchetypeDefinition(BaseModel):
    archetype: OrgArchetype
    display_name: str
    description: str
    default_name: str
    departments: list[DepartmentSpec]
    responsibilities: list[dict[str, str]]
    objectives: list[StrategicObjective]
    projects: list[CompanyProject]
    kpis: list[KPISpec]


def get_open_source_archetype() -> ArchetypeDefinition:
    """Archetype for continuous open-source repository maintenance, releases, and issue triage."""
    departments = [
        DepartmentSpec(
            department_id="dept-triage",
            name="Issue Triage & Reproduction",
            department_type=DepartmentType.OPERATIONS,
            lead_bot_name="bot-issue-triage",
            member_bot_names=["bot-issue-triage", "bot-labeler", "bot-repro-tester"],
            responsibilities=["Issue Ingestion", "Reproduction Verification", "Labeling & Routing"],
        ),
        DepartmentSpec(
            department_id="dept-core-dev",
            name="Core Maintenance & Bugfixing",
            department_type=DepartmentType.ENGINEERING,
            lead_bot_name="bot-core-maintainer",
            member_bot_names=["bot-core-maintainer", "bot-bugfixer", "bot-perf-optimizer"],
            responsibilities=["Core Feature PRs", "Minimal Diff Bug Fixes", "Benchmark Audits"],
        ),
        DepartmentSpec(
            department_id="dept-review-sec",
            name="PR Review & Code Security",
            department_type=DepartmentType.SECURITY,
            lead_bot_name="bot-pr-reviewer",
            member_bot_names=["bot-pr-reviewer", "bot-security-scanner", "bot-lint-enforcer"],
            responsibilities=["Automated PR Code Review", "Static Analysis", "Upstream CVE Auditing"],
        ),
        DepartmentSpec(
            department_id="dept-release-docs",
            name="Release Management & Docs",
            department_type=DepartmentType.PRODUCT,
            lead_bot_name="bot-release-manager",
            member_bot_names=["bot-release-manager", "bot-doc-writer", "bot-community-advocate"],
            responsibilities=["Semantic Version Bumps", "Changelog Synthesis", "Documentation Updates"],
        ),
    ]

    responsibilities = [
        {
            "name": "Incoming PR Review & Static Security Checks",
            "department": "security",
            "primary": "bot-pr-reviewer",
            "backup": "bot-security-scanner",
            "recovery": "bot-core-maintainer",
        },
        {
            "name": "Issue Triage & Bug Reproduction in Worktrees",
            "department": "operations",
            "primary": "bot-issue-triage",
            "backup": "bot-repro-tester",
            "recovery": "bot-bugfixer",
        },
        {
            "name": "Semantic Releases & Packaging Pipeline",
            "department": "product",
            "primary": "bot-release-manager",
            "backup": "bot-core-maintainer",
            "recovery": "bot-pr-reviewer",
        },
    ]

    obj1 = StrategicObjective(
        title="Zero Unreviewed Pull Requests Older than 24 Hours",
        target_kpi="kpi-pr-review-time",
        target_metric_value=12.0,
    )
    obj2 = StrategicObjective(
        title="Maintain 95% Test Coverage & Zero Open High-Severity CVEs",
        target_kpi="kpi-test-coverage",
        target_metric_value=95.0,
    )

    proj1 = CompanyProject(
        name="Project TriageGuard: Autonomous Issue & PR Pipeline",
        department="operations",
        objective_id=obj1.objective_id,
        lead_bot_name="bot-issue-triage",
        kanban_tasks_count=10,
    )
    proj2 = CompanyProject(
        name="Project ReleaseFlow: Automated SemVer & PyPI/NPM Publishing",
        department="product",
        objective_id=obj2.objective_id,
        lead_bot_name="bot-release-manager",
        kanban_tasks_count=6,
    )

    kpis = [
        KPISpec(
            kpi_id="kpi-pr-review-time",
            name="PR Review Turnaround",
            current_value=14.5,
            target_value=12.0,
            unit="h",
            threshold_critical=48.0,
        ),
        KPISpec(
            kpi_id="kpi-test-coverage",
            name="Test Suite Coverage",
            current_value=92.4,
            target_value=95.0,
            unit="%",
            threshold_critical=85.0,
        ),
        KPISpec(
            kpi_id="kpi-issue-repro-rate",
            name="Issue Reproduction Rate",
            current_value=88.0,
            target_value=90.0,
            unit="%",
            threshold_critical=70.0,
        ),
        KPISpec(
            kpi_id="kpi-cve-count",
            name="Unresolved Upstream CVEs",
            current_value=0.0,
            target_value=0.0,
            unit="cves",
            threshold_critical=1.0,
        ),
    ]

    return ArchetypeDefinition(
        archetype=OrgArchetype.OPEN_SOURCE,
        display_name="Open Source Maintainer Collective",
        description="Autonomous collective maintaining codebases, triaging issues, reviewing PRs, and publishing releases.",
        default_name="Open Forge Maintainer Collective",
        departments=departments,
        responsibilities=responsibilities,
        objectives=[obj1, obj2],
        projects=[proj1, proj2],
        kpis=kpis,
    )


def get_security_soc_archetype() -> ArchetypeDefinition:
    """Archetype for 24/7 Security Operations Center, Blue/Red teaming, and patch synthesis."""
    departments = [
        DepartmentSpec(
            department_id="dept-blue",
            name="Blue Team & Incident Detection",
            department_type=DepartmentType.SECURITY,
            lead_bot_name="bot-soc-lead",
            member_bot_names=["bot-soc-lead", "bot-telemetry-sentinel", "bot-log-analyzer"],
            responsibilities=["Telemetry Anomaly Detection", "Audit Log Auditing", "Threat Feed Monitoring"],
        ),
        DepartmentSpec(
            department_id="dept-red",
            name="Red Team & Threat Emulation",
            department_type=DepartmentType.SECURITY,
            lead_bot_name="bot-red-lead",
            member_bot_names=["bot-red-lead", "bot-pentester", "bot-fuzzing-engineer"],
            responsibilities=["Continuous Penetration Testing", "Fuzz Testing", "Exploit Surface Mapping"],
        ),
        DepartmentSpec(
            department_id="dept-patch",
            name="Vulnerability & Patch Engineering",
            department_type=DepartmentType.ENGINEERING,
            lead_bot_name="bot-cve-hunter",
            member_bot_names=["bot-cve-hunter", "bot-patch-synthesizer", "bot-sandbox-verifier"],
            responsibilities=["Zero-Day Vulnerability Scanning", "Minimal Diff Patching", "Regression Testing"],
        ),
        DepartmentSpec(
            department_id="dept-compliance",
            name="Compliance & Incident Command",
            department_type=DepartmentType.EXECUTIVE,
            lead_bot_name="bot-incident-commander",
            member_bot_names=["bot-incident-commander", "bot-compliance-officer"],
            responsibilities=["SOC2 / ISO 27001 Controls", "Crisis Escalation", "Forensic Reporting"],
        ),
    ]

    responsibilities = [
        {
            "name": "Continuous Telemetry Monitoring & Threat Ingestion",
            "department": "security",
            "primary": "bot-soc-lead",
            "backup": "bot-telemetry-sentinel",
            "recovery": "bot-incident-commander",
        },
        {
            "name": "Zero-Day CVE Patch Synthesis & Verification",
            "department": "engineering",
            "primary": "bot-cve-hunter",
            "backup": "bot-patch-synthesizer",
            "recovery": "bot-soc-lead",
        },
        {
            "name": "Non-Destructive Scheduled Pentesting",
            "department": "security",
            "primary": "bot-red-lead",
            "backup": "bot-pentester",
            "recovery": "bot-fuzzing-engineer",
        },
    ]

    obj1 = StrategicObjective(
        title="Zero-Day Mean Time to Detect (MTTD) Under 5 Minutes",
        target_kpi="kpi-mttd",
        target_metric_value=5.0,
    )
    obj2 = StrategicObjective(
        title="100% Automated Patch Verification in Isolated Sandboxes",
        target_kpi="kpi-patch-pass-rate",
        target_metric_value=100.0,
    )

    proj1 = CompanyProject(
        name="Project AegisWatch: Continuous Ingress & Egress Monitoring",
        department="security",
        objective_id=obj1.objective_id,
        lead_bot_name="bot-soc-lead",
        kanban_tasks_count=8,
    )
    proj2 = CompanyProject(
        name="Project AutoPatch: Zero-Human-Latency Vulnerability Remediator",
        department="engineering",
        objective_id=obj2.objective_id,
        lead_bot_name="bot-cve-hunter",
        kanban_tasks_count=12,
    )

    kpis = [
        KPISpec(
            kpi_id="kpi-mttd",
            name="Mean Time to Detect (MTTD)",
            current_value=4.2,
            target_value=5.0,
            unit="min",
            threshold_critical=15.0,
        ),
        KPISpec(
            kpi_id="kpi-mttr",
            name="Mean Time to Remediate (MTTR)",
            current_value=22.0,
            target_value=30.0,
            unit="min",
            threshold_critical=60.0,
        ),
        KPISpec(
            kpi_id="kpi-patch-pass-rate",
            name="Automated Patch Pass Rate",
            current_value=99.1,
            target_value=100.0,
            unit="%",
            threshold_critical=90.0,
        ),
        KPISpec(
            kpi_id="kpi-compliance-score",
            name="Zero-Trust Compliance Score",
            current_value=98.5,
            target_value=99.0,
            unit="%",
            threshold_critical=95.0,
        ),
    ]

    return ArchetypeDefinition(
        archetype=OrgArchetype.SECURITY_SOC,
        display_name="24/7 Security Operations Center (SOC) & Red/Blue Swarm",
        description="Autonomous cyber defense fleet running 24/7 monitoring, pentesting, CVE hunting, and auto-patching.",
        default_name="Sentinel Autonomous Cyber Defense Fleet",
        departments=departments,
        responsibilities=responsibilities,
        objectives=[obj1, obj2],
        projects=[proj1, proj2],
        kpis=kpis,
    )


def get_research_lab_archetype() -> ArchetypeDefinition:
    """Archetype for continuous scientific discovery, preprint ingestion, and hypothesis testing."""
    departments = [
        DepartmentSpec(
            department_id="dept-ingest",
            name="Literature & Intelligence Ingestion",
            department_type=DepartmentType.RESEARCH,
            lead_bot_name="bot-arxiv-crawler",
            member_bot_names=["bot-arxiv-crawler", "bot-citation-grapher", "bot-patent-analyzer"],
            responsibilities=["Continuous ArXiv Ingestion", "Citation Mapping", "State-of-the-Art Tracking"],
        ),
        DepartmentSpec(
            department_id="dept-theory",
            name="Hypothesis Generation & Theory",
            department_type=DepartmentType.RESEARCH,
            lead_bot_name="bot-chief-scientist",
            member_bot_names=["bot-chief-scientist", "bot-hypothesis-engine", "bot-math-prover"],
            responsibilities=["Hypothesis Formulation", "Mathematical Verification", "Experiment Design"],
        ),
        DepartmentSpec(
            department_id="dept-sim",
            name="Experimentation & Simulation",
            department_type=DepartmentType.ENGINEERING,
            lead_bot_name="bot-sim-runner",
            member_bot_names=["bot-sim-runner", "bot-benchmark-auditor", "bot-data-pipeline"],
            responsibilities=["Simulation Code Generation", "External Job Runs", "Empirical Evaluation"],
        ),
        DepartmentSpec(
            department_id="dept-publish",
            name="Synthesis & Peer Review",
            department_type=DepartmentType.PRODUCT,
            lead_bot_name="bot-paper-writer",
            member_bot_names=["bot-paper-writer", "bot-peer-reviewer", "bot-ethics-auditor"],
            responsibilities=["Research Paper Compilation", "Internal Blind Review", "Digest Publications"],
        ),
    ]

    responsibilities = [
        {
            "name": "Continuous Scientific Literature Crawling & Graphing",
            "department": "research",
            "primary": "bot-arxiv-crawler",
            "backup": "bot-citation-grapher",
            "recovery": "bot-chief-scientist",
        },
        {
            "name": "Autonomous Simulation Execution & Validation",
            "department": "engineering",
            "primary": "bot-sim-runner",
            "backup": "bot-benchmark-auditor",
            "recovery": "bot-chief-scientist",
        },
        {
            "name": "Peer Review & Publication Quality Gate",
            "department": "product",
            "primary": "bot-paper-writer",
            "backup": "bot-peer-reviewer",
            "recovery": "bot-chief-scientist",
        },
    ]

    obj1 = StrategicObjective(
        title="Synthesize and Empirically Validate 10 Novel Hypotheses Per Week",
        target_kpi="kpi-hypotheses-tested",
        target_metric_value=10.0,
    )
    obj2 = StrategicObjective(
        title="Maintain 100% Simulation Reproducibility on External Compute",
        target_kpi="kpi-reproducibility",
        target_metric_value=100.0,
    )

    proj1 = CompanyProject(
        name="Project DiscoveryNexus: Continuous Automated Literature Mining",
        department="research",
        objective_id=obj1.objective_id,
        lead_bot_name="bot-arxiv-crawler",
        kanban_tasks_count=14,
    )
    proj2 = CompanyProject(
        name="Project Veritas: Autonomous Hypothesis Simulation Harness",
        department="engineering",
        objective_id=obj2.objective_id,
        lead_bot_name="bot-sim-runner",
        kanban_tasks_count=9,
    )

    kpis = [
        KPISpec(
            kpi_id="kpi-hypotheses-tested",
            name="Weekly Hypotheses Validated",
            current_value=8.0,
            target_value=10.0,
            unit="hypotheses",
            threshold_critical=3.0,
        ),
        KPISpec(
            kpi_id="kpi-reproducibility",
            name="Simulation Reproducibility Rate",
            current_value=97.5,
            target_value=100.0,
            unit="%",
            threshold_critical=90.0,
        ),
        KPISpec(
            kpi_id="kpi-sota-delta",
            name="Benchmark Delta vs SOTA",
            current_value=3.8,
            target_value=5.0,
            unit="%",
            threshold_critical=0.0,
        ),
        KPISpec(
            kpi_id="kpi-peer-review-score",
            name="Internal Review Rigor Score",
            current_value=91.0,
            target_value=90.0,
            unit="%",
            threshold_critical=80.0,
        ),
    ]

    return ArchetypeDefinition(
        archetype=OrgArchetype.RESEARCH_LAB,
        display_name="Autonomous Scientific & Discovery Lab",
        description="Continuous research collective formulating hypotheses, running empirical simulations, and publishing papers.",
        default_name="Nova Autonomous Discovery Lab",
        departments=departments,
        responsibilities=responsibilities,
        objectives=[obj1, obj2],
        projects=[proj1, proj2],
        kpis=kpis,
    )


def synthesize_custom_archetype(prompt: str) -> ArchetypeDefinition:
    """Dynamically parses ANY arbitrary user prompt into a tailor-made perpetual organization."""
    p_lower = prompt.lower()
    clean_title = " ".join(w.capitalize() for w in prompt.split()[:4])
    org_name = f"{clean_title} Collective"

    # Identify primary domain keywords
    is_data = any(w in p_lower for w in ("data", "etl", "analytics", "pipeline", "streaming"))
    is_game = any(w in p_lower for w in ("game", "rpg", "graphics", "engine", "physics", "world"))
    is_finance = any(w in p_lower for w in ("trading", "finance", "crypto", "market", "arbitrage", "portfolio"))

    if is_game:
        dept1_name, d1_lead, d1_members = "Gameplay & Mechanics Engineering", "bot-gameplay-lead", ["bot-gameplay-lead", "bot-mechanics-coder"]
        dept2_name, d2_lead, d2_members = "World & Narrative Generation", "bot-world-builder", ["bot-world-builder", "bot-dialogue-writer"]
        dept3_name, d3_lead, d3_members = "Physics & Graphics Systems", "bot-engine-coder", ["bot-engine-coder", "bot-shader-tuner"]
        dept4_name, d4_lead, d4_members = "Playtesting & Economy Balancing", "bot-qa-tester", ["bot-qa-tester", "bot-economy-balancer"]
        primary_kpi_name, target_val, kpi_unit = "Frame Stability & Playtest Score", 95.0, "/100"
    elif is_finance:
        dept1_name, d1_lead, d1_members = "Market Data & Signal Ingestion", "bot-market-feeder", ["bot-market-feeder", "bot-signal-extractor"]
        dept2_name, d2_lead, d2_members = "Algorithmic Strategy & Models", "bot-quant-researcher", ["bot-quant-researcher", "bot-alpha-tester"]
        dept3_name, d3_lead, d3_members = "Execution & Latency Engine", "bot-execution-lead", ["bot-execution-lead", "bot-order-router"]
        dept4_name, d4_lead, d4_members = "Risk Management & Compliance", "bot-risk-officer", ["bot-risk-officer", "bot-circuit-breaker"]
        primary_kpi_name, target_val, kpi_unit = "Sharpe Ratio & Execution Latency", 2.5, "ratio"
    elif is_data:
        dept1_name, d1_lead, d1_members = "Ingestion & Stream Pipelines", "bot-pipeline-lead", ["bot-pipeline-lead", "bot-kafka-tuner"]
        dept2_name, d2_lead, d2_members = "Data Quality & Schema Hygiene", "bot-quality-enforcer", ["bot-quality-enforcer", "bot-schema-validator"]
        dept3_name, d3_lead, d3_members = "Transformations & Analytics", "bot-dbt-developer", ["bot-dbt-developer", "bot-query-optimizer"]
        dept4_name, d4_lead, d4_members = "SLA & Infrastructure Reliability", "bot-data-sre", ["bot-data-sre", "bot-cost-guard"]
        primary_kpi_name, target_val, kpi_unit = "Pipeline Latency & Freshness", 99.9, "%"
    else:
        # General dynamic synthesis
        dept1_name, d1_lead, d1_members = "Core Operations & Orchestration", "bot-chief-operator", ["bot-chief-operator", "bot-workflow-planner"]
        dept2_name, d2_lead, d2_members = "Domain Execution & Production", "bot-domain-specialist", ["bot-domain-specialist", "bot-craft-worker"]
        dept3_name, d3_lead, d3_members = "Quality Assurance & Evaluation", "bot-quality-lead", ["bot-quality-lead", "bot-verifier"]
        dept4_name, d4_lead, d4_members = "Evolution & Continuous Research", "bot-innovator", ["bot-innovator", "bot-trend-scout"]
        primary_kpi_name, target_val, kpi_unit = "System Operational Effectiveness", 98.0, "%"

    departments = [
        DepartmentSpec(
            department_id="dept-1",
            name=dept1_name,
            department_type=DepartmentType.ENGINEERING,
            lead_bot_name=d1_lead,
            member_bot_names=d1_members,
            responsibilities=[f"Lead {dept1_name}", "Pipeline Throughput"],
        ),
        DepartmentSpec(
            department_id="dept-2",
            name=dept2_name,
            department_type=DepartmentType.PRODUCT,
            lead_bot_name=d2_lead,
            member_bot_names=d2_members,
            responsibilities=[f"Manage {dept2_name}", "Deliverable Synthesis"],
        ),
        DepartmentSpec(
            department_id="dept-3",
            name=dept3_name,
            department_type=DepartmentType.SECURITY,
            lead_bot_name=d3_lead,
            member_bot_names=d3_members,
            responsibilities=[f"Audit {dept3_name}", "Quality Gates"],
        ),
        DepartmentSpec(
            department_id="dept-4",
            name=dept4_name,
            department_type=DepartmentType.OPERATIONS,
            lead_bot_name=d4_lead,
            member_bot_names=d4_members,
            responsibilities=[f"Govern {dept4_name}", "Self-Improvement"],
        ),
    ]

    responsibilities = [
        {
            "name": f"Core Continuous Delivery for {dept1_name}",
            "department": "engineering",
            "primary": d1_lead,
            "backup": d1_members[1] if len(d1_members) > 1 else d2_lead,
            "recovery": d3_lead,
        },
        {
            "name": f"Quality Gate & Integrity Auditing for {dept3_name}",
            "department": "security",
            "primary": d3_lead,
            "backup": d3_members[1] if len(d3_members) > 1 else d1_lead,
            "recovery": d4_lead,
        },
    ]

    obj = StrategicObjective(
        title=f"Sustain Continuous High-Performance Operations: {prompt[:60]}",
        target_kpi="kpi-custom-primary",
        target_metric_value=target_val,
    )
    proj = CompanyProject(
        name=f"Project Infinity: {clean_title}",
        department="engineering",
        objective_id=obj.objective_id,
        lead_bot_name=d1_lead,
        kanban_tasks_count=10,
    )

    kpis = [
        KPISpec(
            kpi_id="kpi-custom-primary",
            name=primary_kpi_name,
            current_value=target_val * 0.92,
            target_value=target_val,
            unit=kpi_unit,
            threshold_critical=target_val * 0.70,
        ),
        KPISpec(
            kpi_id="kpi-uptime",
            name="Continuous Operation Uptime",
            current_value=99.9,
            target_value=99.95,
            unit="%",
            threshold_critical=98.0,
        ),
        KPISpec(
            kpi_id="kpi-self-improvement-index",
            name="Autonomous Evolution Index",
            current_value=85.0,
            target_value=90.0,
            unit="pts",
            threshold_critical=60.0,
        ),
    ]

    return ArchetypeDefinition(
        archetype=OrgArchetype.CUSTOM,
        display_name=f"Custom Collective ({clean_title})",
        description=f"Dynamically generated perpetual organization configured to operate: '{prompt}'",
        default_name=org_name,
        departments=departments,
        responsibilities=responsibilities,
        objectives=[obj],
        projects=[proj],
        kpis=kpis,
    )


def detect_archetype_from_prompt(prompt: str) -> OrgArchetype:
    """Detects the most appropriate archetype from user prompt keywords."""
    p = prompt.lower()
    if any(w in p for w in ("open source", "github", "pull request", "pr review", "repo maintainer", "semver", "changelog", "foss")):
        return OrgArchetype.OPEN_SOURCE
    elif any(w in p for w in ("soc", "cyber", "security operations", "red team", "blue team", "pentest", "cve", "zero-day", "vulnerability")):
        return OrgArchetype.SECURITY_SOC
    elif any(w in p for w in ("research lab", "scientific", "hypothesis", "arxiv", "paper", "simulation", "patent")):
        return OrgArchetype.RESEARCH_LAB
    elif any(w in p for w in ("game", "rpg", "custom collective", "newsroom", "data pipeline")):
        return OrgArchetype.CUSTOM
    else:
        return OrgArchetype.COMPANY
