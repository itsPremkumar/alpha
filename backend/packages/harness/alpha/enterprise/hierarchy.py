"""Dynamic Enterprise Hierarchy & C-Suite Swarm Architecture."""

from __future__ import annotations

import logging
from typing import Any

from alpha.enterprise.models import (
    CSuiteRole,
    CapabilityContract,
    ClearanceLevel,
    DepartmentHierarchy,
    EnterpriseDepartment,
    OrgNode,
)

logger = logging.getLogger(__name__)


class EnterpriseHierarchyEngine:
    """Manages the dynamic enterprise leadership swarm, department synthesis, and capability contracts."""

    def __init__(self):
        self._csuite: dict[str, OrgNode] = {}
        self._departments: dict[str, DepartmentHierarchy] = {}
        self._nodes_by_bot: dict[str, OrgNode] = {}
        self._bootstrap_enterprise_org()

    def _bootstrap_enterprise_org(self) -> None:
        """Initializes the baseline 4 C-Suite roles and 5 specialized departments."""
        # 1. C-Suite Swarm Nodes
        ceo_contract = CapabilityContract(
            role_id="role-ceo",
            title="Executive Director (CEO)",
            department="executive",
            clearance=ClearanceLevel.L5_CSUITE,
            capabilities=["strategic_direction", "budget_veto", "mission_decomposition", "governance_override", "all_hands_broadcast"],
            max_concurrency=10,
            decision_authority=["budget_approval", "department_synthesis", "strategic_pivot", "emergency_halt"],
        )
        ceo_node = OrgNode(
            bot_name="bot-ceo",
            title="Executive Director (CEO)",
            role_type="c_suite",
            department="executive",
            reports_to=None,
            subordinates=["bot-cto", "bot-cpo", "bot-ciso"],
            contract=ceo_contract,
        )

        cto_contract = CapabilityContract(
            role_id="role-cto",
            title="Lead Architect (CTO)",
            department="architecture",
            clearance=ClearanceLevel.L5_CSUITE,
            capabilities=["system_architecture", "rfc_creation", "rfc_veto", "dag_compilation", "cryptographic_signing", "hot_swap_approval"],
            max_concurrency=8,
            decision_authority=["architecture_sign_off", "rfc_approval", "swe_promotion", "tech_stack_selection"],
        )
        cto_node = OrgNode(
            bot_name="bot-cto",
            title="Lead Architect (CTO)",
            role_type="c_suite",
            department="architecture",
            reports_to="bot-ceo",
            subordinates=["bot-eng-lead", "bot-system-architect", "bot-data-modeler", "bot-perf-lead"],
            contract=cto_contract,
        )

        cpo_contract = CapabilityContract(
            role_id="role-cpo",
            title="Product & Market Strategist (CPO)",
            department="product",
            clearance=ClearanceLevel.L5_CSUITE,
            capabilities=["feature_gap_discovery", "epic_synthesis", "roadmap_alignment", "user_need_modeling", "kpi_steering"],
            max_concurrency=8,
            decision_authority=["epic_prioritization", "feature_acceptance", "market_alignment"],
        )
        cpo_node = OrgNode(
            bot_name="bot-cpo",
            title="Product & Market Strategist (CPO)",
            role_type="c_suite",
            department="product",
            reports_to="bot-ceo",
            subordinates=["bot-docs-lead"],
            contract=cpo_contract,
        )

        ciso_contract = CapabilityContract(
            role_id="role-ciso",
            title="Quality & Security Director (CISO)",
            department="security",
            clearance=ClearanceLevel.L5_CSUITE,
            capabilities=["ast_boundary_scan", "threat_modeling", "security_veto", "cryptographic_signing", "zero_trust_enforcement"],
            max_concurrency=8,
            decision_authority=["security_sign_off", "circuit_breaker_override", "release_block", "quarantine_action"],
        )
        ciso_node = OrgNode(
            bot_name="bot-ciso",
            title="Quality & Security Director (CISO)",
            role_type="c_suite",
            department="security",
            reports_to="bot-ceo",
            subordinates=["bot-appsec-auditor", "bot-soc-redblue"],
            contract=ciso_contract,
        )

        for n in [ceo_node, cto_node, cpo_node, ciso_node]:
            self._csuite[n.bot_name] = n
            self._nodes_by_bot[n.bot_name] = n

        # 2. Synthesize Sub-Departments
        self._setup_engineering_dept(cto_node.bot_name)
        self._setup_architecture_dept(cto_node.bot_name)
        self._setup_security_dept(ciso_node.bot_name)
        self._setup_performance_dept(cto_node.bot_name)
        self._setup_documentation_dept(cpo_node.bot_name)

    def _setup_engineering_dept(self, supervisor_bot: str) -> None:
        lead_contract = CapabilityContract(
            role_id="role-eng-lead",
            title="Principal Engineering Lead",
            department="engineering",
            clearance=ClearanceLevel.L3_LEAD,
            capabilities=["dag_execution", "code_review", "dod_verification", "sprint_coordination"],
            decision_authority=["code_merge", "task_assignment"],
        )
        lead_node = OrgNode(
            bot_name="bot-eng-lead",
            title="Principal Engineering Lead",
            role_type="lead",
            department="engineering",
            reports_to=supervisor_bot,
            subordinates=["bot-backend-swe", "bot-frontend-swe", "bot-ai-swe"],
            contract=lead_contract,
        )
        workers = [lead_node]

        worker_specs = [
            ("bot-backend-swe", "Senior Backend SWE", ["fastapi", "asyncio", "sql_persistence", "dag_tasks"]),
            ("bot-frontend-swe", "Senior Frontend SWE", ["nextjs", "react", "tailwind", "war_room_telemetry"]),
            ("bot-ai-swe", "Autonomous Systems SWE", ["llm_routing", "memory_consolidation", "agent_meta_compiler"]),
        ]
        for bot_name, title, caps in worker_specs:
            w_contract = CapabilityContract(
                role_id=f"role-{bot_name}",
                title=title,
                department="engineering",
                clearance=ClearanceLevel.L1_WORKER,
                capabilities=caps,
            )
            w_node = OrgNode(
                bot_name=bot_name,
                title=title,
                role_type="worker",
                department="engineering",
                reports_to="bot-eng-lead",
                contract=w_contract,
            )
            workers.append(w_node)
            self._nodes_by_bot[bot_name] = w_node

        self._nodes_by_bot[lead_node.bot_name] = lead_node
        self._departments[EnterpriseDepartment.ENGINEERING.value] = DepartmentHierarchy(
            dept_id="dept-engineering",
            name="Core Systems & Software Engineering",
            department_type=EnterpriseDepartment.ENGINEERING,
            lead_bot_name="bot-eng-lead",
            lead_title=lead_node.title,
            workers=workers,
            capabilities=["code_implementation", "dag_task_execution", "backend_api", "frontend_ui", "continuous_integration"],
            token_budget=600000,
        )

    def _setup_architecture_dept(self, supervisor_bot: str) -> None:
        workers = []
        specs = [
            ("bot-system-architect", "Staff Systems Architect", ["system_design", "rfc_review", "distributed_patterns"]),
            ("bot-data-modeler", "Staff Data Modeler", ["schema_evolution", "epistemic_graphs", "memory_topology"]),
        ]
        for bot_name, title, caps in specs:
            contract = CapabilityContract(
                role_id=f"role-{bot_name}",
                title=title,
                department="architecture",
                clearance=ClearanceLevel.L2_SPECIALIST,
                capabilities=caps,
                decision_authority=["schema_review", "rfc_commentary"],
            )
            node = OrgNode(
                bot_name=bot_name,
                title=title,
                role_type="worker",
                department="architecture",
                reports_to=supervisor_bot,
                contract=contract,
            )
            workers.append(node)
            self._nodes_by_bot[bot_name] = node

        self._departments[EnterpriseDepartment.ARCHITECTURE.value] = DepartmentHierarchy(
            dept_id="dept-architecture",
            name="Systems Architecture & RFC Council",
            department_type=EnterpriseDepartment.ARCHITECTURE,
            lead_bot_name="bot-cto",
            lead_title="Lead Architect (CTO)",
            workers=workers,
            capabilities=["architecture_governance", "rfc_vetting", "decomposition_spec", "system_resilience"],
            token_budget=350000,
        )

    def _setup_security_dept(self, supervisor_bot: str) -> None:
        workers = []
        specs = [
            ("bot-appsec-auditor", "Principal AppSec Auditor", ["ast_boundary_scan", "cve_triage", "taint_tracking"]),
            ("bot-soc-redblue", "SOC Red/Blue Specialist", ["pentest_simulation", "sandbox_escape_testing", "threat_mitigation"]),
        ]
        for bot_name, title, caps in specs:
            contract = CapabilityContract(
                role_id=f"role-{bot_name}",
                title=title,
                department="security",
                clearance=ClearanceLevel.L2_SPECIALIST,
                capabilities=caps,
                decision_authority=["vulnerability_veto"],
            )
            node = OrgNode(
                bot_name=bot_name,
                title=title,
                role_type="worker",
                department="security",
                reports_to=supervisor_bot,
                contract=contract,
            )
            workers.append(node)
            self._nodes_by_bot[bot_name] = node

        self._departments[EnterpriseDepartment.SECURITY.value] = DepartmentHierarchy(
            dept_id="dept-security",
            name="Security Assurance & AST Sandboxing",
            department_type=EnterpriseDepartment.SECURITY,
            lead_bot_name="bot-ciso",
            lead_title="Quality & Security Director (CISO)",
            workers=workers,
            capabilities=["ast_verification", "security_signing", "cve_neutralization", "boundary_enforcement"],
            token_budget=300000,
        )

    def _setup_performance_dept(self, supervisor_bot: str) -> None:
        lead_contract = CapabilityContract(
            role_id="role-perf-lead",
            title="Lead Performance Engineer",
            department="performance",
            clearance=ClearanceLevel.L3_LEAD,
            capabilities=["latency_benchmarking", "holdout_testing", "swe_benchmark_signing"],
            decision_authority=["swe_benchmark_sign_off", "performance_veto"],
        )
        lead_node = OrgNode(
            bot_name="bot-perf-lead",
            title="Lead Performance Engineer",
            role_type="lead",
            department="performance",
            reports_to=supervisor_bot,
            subordinates=["bot-latency-profiler", "bot-sre-optimizer"],
            contract=lead_contract,
        )
        workers = [lead_node]

        specs = [
            ("bot-latency-profiler", "Senior Latency Profiler", ["p95_profiling", "bottleneck_detection", "trace_analysis"]),
            ("bot-sre-optimizer", "Site Reliability Optimizer", ["resource_tuning", "load_shedding", "auto_recovery"]),
        ]
        for bot_name, title, caps in specs:
            contract = CapabilityContract(
                role_id=f"role-{bot_name}",
                title=title,
                department="performance",
                clearance=ClearanceLevel.L1_WORKER,
                capabilities=caps,
            )
            node = OrgNode(
                bot_name=bot_name,
                title=title,
                role_type="worker",
                department="performance",
                reports_to="bot-perf-lead",
                contract=contract,
            )
            workers.append(node)
            self._nodes_by_bot[bot_name] = node

        self._nodes_by_bot[lead_node.bot_name] = lead_node
        self._departments[EnterpriseDepartment.PERFORMANCE.value] = DepartmentHierarchy(
            dept_id="dept-performance",
            name="Performance & SWE Holdout Benchmarking",
            department_type=EnterpriseDepartment.PERFORMANCE,
            lead_bot_name="bot-perf-lead",
            lead_title=lead_node.title,
            workers=workers,
            capabilities=["latency_profiling", "swe_benchmark_execution", "holdout_verification", "sre_optimization"],
            token_budget=250000,
        )

    def _setup_documentation_dept(self, supervisor_bot: str) -> None:
        lead_contract = CapabilityContract(
            role_id="role-docs-lead",
            title="Documentation & Knowledge Lead",
            department="documentation",
            clearance=ClearanceLevel.L3_LEAD,
            capabilities=["technical_writing", "api_spec_review", "rfc_documentation"],
            decision_authority=["docs_sign_off"],
        )
        lead_node = OrgNode(
            bot_name="bot-docs-lead",
            title="Documentation & Knowledge Lead",
            role_type="lead",
            department="documentation",
            reports_to=supervisor_bot,
            subordinates=["bot-api-specifier", "bot-rfc-scribe"],
            contract=lead_contract,
        )
        workers = [lead_node]

        specs = [
            ("bot-api-specifier", "API Specifier & OpenAPI Scribe", ["openapi_spec", "type_contracts", "markdown_docs"]),
            ("bot-rfc-scribe", "RFC Archivist & Knowledge Curator", ["rfc_cataloging", "consensus_logging", "journaling"]),
        ]
        for bot_name, title, caps in specs:
            contract = CapabilityContract(
                role_id=f"role-{bot_name}",
                title=title,
                department="documentation",
                clearance=ClearanceLevel.L1_WORKER,
                capabilities=caps,
            )
            node = OrgNode(
                bot_name=bot_name,
                title=title,
                role_type="worker",
                department="documentation",
                reports_to="bot-docs-lead",
                contract=contract,
            )
            workers.append(node)
            self._nodes_by_bot[bot_name] = node

        self._nodes_by_bot[lead_node.bot_name] = lead_node
        self._departments[EnterpriseDepartment.DOCUMENTATION.value] = DepartmentHierarchy(
            dept_id="dept-documentation",
            name="Documentation, OpenAPI & Knowledge Ops",
            department_type=EnterpriseDepartment.DOCUMENTATION,
            lead_bot_name="bot-docs-lead",
            lead_title=lead_node.title,
            workers=workers,
            capabilities=["api_documentation", "rfc_scribing", "developer_portal", "playbook_synthesis"],
            token_budget=200000,
        )

    def get_csuite(self) -> dict[str, OrgNode]:
        """Returns all 4 C-Suite leadership roles."""
        return self._csuite

    def get_departments(self) -> dict[str, DepartmentHierarchy]:
        """Returns all synthesized departments."""
        return self._departments

    def get_node(self, bot_name: str) -> OrgNode | None:
        return self._nodes_by_bot.get(bot_name)

    def verify_capability(self, bot_name: str, capability: str) -> bool:
        node = self.get_node(bot_name)
        if not node:
            return False
        return capability in node.contract.capabilities or node.contract.clearance == ClearanceLevel.L5_CSUITE

    def verify_authority(self, bot_name: str, authority: str) -> bool:
        node = self.get_node(bot_name)
        if not node:
            return False
        return authority in node.contract.decision_authority or node.contract.clearance == ClearanceLevel.L5_CSUITE

    def synthesize_custom_subdepartment(
        self,
        name: str,
        department_key: str,
        lead_bot_name: str,
        lead_title: str,
        capabilities: list[str],
        initial_budget: int = 250000,
        supervisor_bot: str = "bot-ceo",
    ) -> DepartmentHierarchy:
        """Dynamically creates and mounts a new autonomous sub-department."""
        dept_id = f"dept-{department_key.lower().replace(' ', '-')}"
        lead_contract = CapabilityContract(
            role_id=f"role-{dept_id}-lead",
            title=lead_title,
            department=department_key,
            clearance=ClearanceLevel.L3_LEAD,
            capabilities=capabilities + ["sprint_coordination", "task_assignment"],
            decision_authority=["task_assignment", "subdepartment_sign_off"],
        )
        lead_node = OrgNode(
            bot_name=lead_bot_name,
            title=lead_title,
            role_type="lead",
            department=department_key,
            reports_to=supervisor_bot,
            contract=lead_contract,
        )
        self._nodes_by_bot[lead_bot_name] = lead_node
        supervisor_node = self.get_node(supervisor_bot)
        if supervisor_node and lead_bot_name not in supervisor_node.subordinates:
            supervisor_node.subordinates.append(lead_bot_name)

        dept = DepartmentHierarchy(
            dept_id=dept_id,
            name=name,
            department_type=EnterpriseDepartment.ENGINEERING,  # default archetype
            lead_bot_name=lead_bot_name,
            lead_title=lead_title,
            workers=[lead_node],
            capabilities=capabilities,
            token_budget=initial_budget,
        )
        self._departments[dept_id] = dept
        logger.info(f"Synthesized dynamic sub-department: {dept_id} led by {lead_bot_name}")
        return dept

    def get_full_org_chart(self) -> dict[str, Any]:
        """Serializes complete enterprise hierarchy tree for War Room UI visualization."""
        csuite_list = [n.model_dump() for n in self._csuite.values()]
        dept_list = [d.model_dump() for d in self._departments.values()]
        total_workers = len(self._nodes_by_bot)
        return {
            "enterprise_name": "Alpha Autonomous AI Software Enterprise",
            "csuite": csuite_list,
            "departments": dept_list,
            "total_headcount": total_workers,
            "roles": [n.model_dump() for n in self._nodes_by_bot.values()],
        }


_HIERARCHY_ENGINE: EnterpriseHierarchyEngine | None = None


def get_enterprise_hierarchy() -> EnterpriseHierarchyEngine:
    global _HIERARCHY_ENGINE
    if _HIERARCHY_ENGINE is None:
        _HIERARCHY_ENGINE = EnterpriseHierarchyEngine()
    return _HIERARCHY_ENGINE
