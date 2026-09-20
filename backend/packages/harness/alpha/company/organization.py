"""Autonomous Organization OS, Dynamic Archetypes & Perpetual Collectives."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from alpha.company.archetypes import (
    detect_archetype_from_prompt,
    get_open_source_archetype,
    get_research_lab_archetype,
    get_security_soc_archetype,
    synthesize_custom_archetype,
)
from alpha.company.attendance import AttendanceLedgerEngine, AttendanceStatus, BotHeartbeat
from alpha.company.bot_medic import BotMedicEngine
from alpha.company.discovery import ContinuousWorkDiscoveryEngine
from alpha.company.executive import ExecutiveDigest, ExecutiveIntelligenceLayer
from alpha.company.enterprise_kanban import EnterpriseKanbanAdapter, HermesKanbanAdapter
from alpha.company.group_chat import GroupChatEngine
from alpha.company.swarm_bridge import HermesLocalBridge, SwarmLocalBridge
from alpha.company.kanban import CompanyKanbanEngine
from alpha.company.kpi import KPIEngine
from alpha.company.models import (
    CompanyCharter,
    CompanyProject,
    CompanyState,
    DepartmentSpec,
    DepartmentType,
    EvolutionRecord,
    OrgArchetype,
    OrgState,
    StrategicObjective,
)
from alpha.company.production_line import ProductionLineEngine
from alpha.company.responsibility import ResponsibilityEngine
from alpha.company.self_improvement import ContinuousSelfImprovementEngine
from alpha.company.strategy import StrategicPlanningEngine, StrategyReplanReport

logger = logging.getLogger(__name__)


class AutonomousCompanyEngine:
    """Master controller instantiating and operating persistent autonomous organizations."""

    def __init__(self):
        # org_id -> CompanyState
        self._organizations: dict[str, CompanyState] = {}
        # Subsystem engines per organization
        self._responsibility_engines: dict[str, ResponsibilityEngine] = {}
        self._kpi_engines: dict[str, KPIEngine] = {}
        self._discovery_engines: dict[str, ContinuousWorkDiscoveryEngine] = {}
        self._chat_engines: dict[str, GroupChatEngine] = {}
        self._attendance_engines: dict[str, AttendanceLedgerEngine] = {}
        self._bot_medics: dict[str, BotMedicEngine] = {}
        self._kanban_engines: dict[str, CompanyKanbanEngine] = {}
        # Local Swarm & Enterprise Kanban integrations
        self._swarm_bridge = SwarmLocalBridge()
        self._hermes_bridge = self._swarm_bridge
        self._production_line = ProductionLineEngine()
        self._enterprise_kanban = EnterpriseKanbanAdapter()
        self._hermes_kanban = self._enterprise_kanban

    def list_archetypes(self) -> list[dict[str, str]]:
        """Returns catalogue of supported organization archetypes."""
        return [
            {
                "archetype": OrgArchetype.COMPANY.value,
                "display_name": "Autonomous AI Company & Startup",
                "description": "Full enterprise company with Executive, Engineering, Security, and Growth departments.",
            },
            {
                "archetype": OrgArchetype.OPEN_SOURCE.value,
                "display_name": "Open Source Maintainer Collective",
                "description": "Continuous repository maintenance, PR review, bug reproduction, SemVer releases, and docs.",
            },
            {
                "archetype": OrgArchetype.SECURITY_SOC.value,
                "display_name": "24/7 Security Operations Center (SOC) & Red/Blue Swarm",
                "description": "Continuous telemetry monitoring, CVE hunting, automated patch synthesis, and pentesting.",
            },
            {
                "archetype": OrgArchetype.RESEARCH_LAB.value,
                "display_name": "Autonomous Scientific & Discovery Lab",
                "description": "Preprint literature crawling, hypothesis generation, simulation execution, and paper synthesis.",
            },
            {
                "archetype": OrgArchetype.CUSTOM.value,
                "display_name": "Dynamic Custom Collective",
                "description": "Dynamically synthesized perpetual organization for ANY user prompt (game studio, data pipeline, newsroom, etc.).",
            },
        ]

    def bootstrap_company(
        self,
        prompt: str,
        archetype: OrgArchetype | str | None = None,
        owner: str = "human-owner",
        duration_years: float = 5.0,
    ) -> CompanyState:
        """Transforms a user goal into a living autonomous organization with workforce and departments."""
        org_id = f"org-{uuid.uuid4().hex[:8]}"

        # Resolve archetype
        target_archetype: OrgArchetype
        if archetype:
            if isinstance(archetype, str):
                try:
                    target_archetype = OrgArchetype(archetype.lower())
                except ValueError:
                    target_archetype = OrgArchetype.CUSTOM
            else:
                target_archetype = archetype
        else:
            target_archetype = detect_archetype_from_prompt(prompt)

        # Initialize engines
        resp_engine = ResponsibilityEngine()

        # Build archetype specifics
        if target_archetype == OrgArchetype.OPEN_SOURCE:
            defn = get_open_source_archetype()
            name = defn.default_name
            departments = defn.departments
            objectives = defn.objectives
            projects = defn.projects
            kpis = defn.kpis
            for r in defn.responsibilities:
                resp_engine.register_responsibility(
                    name=r["name"],
                    department=r["department"],
                    primary_bot_name=r["primary"],
                    backup_bot_name=r["backup"],
                    recovery_bot_name=r["recovery"],
                )
        elif target_archetype == OrgArchetype.SECURITY_SOC:
            defn = get_security_soc_archetype()
            name = defn.default_name
            departments = defn.departments
            objectives = defn.objectives
            projects = defn.projects
            kpis = defn.kpis
            for r in defn.responsibilities:
                resp_engine.register_responsibility(
                    name=r["name"],
                    department=r["department"],
                    primary_bot_name=r["primary"],
                    backup_bot_name=r["backup"],
                    recovery_bot_name=r["recovery"],
                )
        elif target_archetype == OrgArchetype.RESEARCH_LAB:
            defn = get_research_lab_archetype()
            name = defn.default_name
            departments = defn.departments
            objectives = defn.objectives
            projects = defn.projects
            kpis = defn.kpis
            for r in defn.responsibilities:
                resp_engine.register_responsibility(
                    name=r["name"],
                    department=r["department"],
                    primary_bot_name=r["primary"],
                    backup_bot_name=r["backup"],
                    recovery_bot_name=r["recovery"],
                )
        elif target_archetype == OrgArchetype.CUSTOM:
            defn = synthesize_custom_archetype(prompt)
            name = defn.default_name
            departments = defn.departments
            objectives = defn.objectives
            projects = defn.projects
            kpis = defn.kpis
            for r in defn.responsibilities:
                resp_engine.register_responsibility(
                    name=r["name"],
                    department=r["department"],
                    primary_bot_name=r["primary"],
                    backup_bot_name=r["backup"],
                    recovery_bot_name=r["recovery"],
                )
        else:
            # Default Enterprise Company Archetype
            target_archetype = OrgArchetype.COMPANY
            name = "Apex Autonomous Systems Inc."
            departments = [
                DepartmentSpec(
                    department_id="dept-exec",
                    name="Executive & Operations",
                    department_type=DepartmentType.EXECUTIVE,
                    lead_bot_name="bot-ceo",
                    member_bot_names=["bot-ceo", "bot-coo"],
                    responsibilities=["Strategic Planning", "Resource Allocation", "Governance"],
                ),
                DepartmentSpec(
                    department_id="dept-eng",
                    name="Product & Engineering",
                    department_type=DepartmentType.ENGINEERING,
                    lead_bot_name="bot-cto",
                    member_bot_names=[
                        "bot-cto",
                        "bot-product-manager",
                        "bot-backend-lead",
                        "bot-frontend-lead",
                        "bot-ai-engineer",
                    ],
                    responsibilities=["Architecture", "Feature Delivery", "Core API", "UI/UX"],
                ),
                DepartmentSpec(
                    department_id="dept-sec",
                    name="Security & SRE",
                    department_type=DepartmentType.SECURITY,
                    lead_bot_name="bot-security-lead",
                    member_bot_names=["bot-security-lead", "bot-qa-lead", "bot-sre-lead"],
                    responsibilities=["Zero-Trust Security", "Vulnerability Auditing", "Infrastructure SLA"],
                ),
                DepartmentSpec(
                    department_id="dept-growth",
                    name="Research & Growth",
                    department_type=DepartmentType.RESEARCH,
                    lead_bot_name="bot-research-lead",
                    member_bot_names=["bot-research-lead", "bot-marketing-lead", "bot-customer-support"],
                    responsibilities=["Market Intelligence", "Customer Triage", "Growth Channels"],
                ),
            ]

            resp_engine.register_responsibility(
                name="Architecture & Systems Scalability",
                department="engineering",
                primary_bot_name="bot-cto",
                backup_bot_name="bot-ai-engineer",
                recovery_bot_name="bot-ceo",
            )
            resp_engine.register_responsibility(
                name="Core Backend API & Database Reliability",
                department="engineering",
                primary_bot_name="bot-backend-lead",
                backup_bot_name="bot-sre-lead",
                recovery_bot_name="bot-cto",
            )
            resp_engine.register_responsibility(
                name="Vulnerability Scanning & Compliance",
                department="security",
                primary_bot_name="bot-security-lead",
                backup_bot_name="bot-qa-lead",
                recovery_bot_name="bot-sre-lead",
            )
            resp_engine.register_responsibility(
                name="Customer Inbound Triage & Support",
                department="growth",
                primary_bot_name="bot-customer-support",
                backup_bot_name="bot-product-manager",
                recovery_bot_name="bot-marketing-lead",
            )

            obj1 = StrategicObjective(
                title="Launch High-Throughput Core Platform & Developer Gateway",
                target_kpi="kpi-deployment-freq",
                target_metric_value=8.0,
            )
            obj2 = StrategicObjective(
                title="Maintain 99.95% Availability & Zero Unpatched CVEs",
                target_kpi="kpi-availability",
                target_metric_value=99.95,
            )
            objectives = [obj1, obj2]

            proj1 = CompanyProject(
                name="Project Genesis: Core Infrastructure & API Engine",
                department="engineering",
                objective_id=obj1.objective_id,
                lead_bot_name="bot-cto",
                kanban_tasks_count=12,
            )
            proj2 = CompanyProject(
                name="Project Sentinel: Autonomous Security & Reliability Guard",
                department="security",
                objective_id=obj2.objective_id,
                lead_bot_name="bot-security-lead",
                kanban_tasks_count=8,
            )
            projects = [proj1, proj2]
            kpis = None  # Use DEFAULT_ORGANIZATION_KPIS in KPIEngine

        # Formulate Charter
        charter = CompanyCharter(
            org_id=org_id,
            name=name,
            archetype=target_archetype,
            mission_statement=prompt,
            vision="Sustained autonomous product delivery, engineering excellence, and customer growth.",
            duration_years=duration_years,
            autonomy_level="L4",
            owner=owner,
        )

        kpi_engine = KPIEngine(initial_kpis=kpis)
        disc_engine = ContinuousWorkDiscoveryEngine()
        chat_engine = GroupChatEngine(org_id=org_id)
        attendance_engine = AttendanceLedgerEngine(org_id=org_id)
        bot_medic = BotMedicEngine(attendance_ledger=attendance_engine, responsibility_engine=resp_engine)

        # Collect all active bot identities across departments
        all_bots: set[str] = set()
        for d in departments:
            all_bots.update(d.member_bot_names)

        # Ingest local Hermes bots if present into company workforce
        local_hermes = self._hermes_bridge.discover_local_bots()
        all_bots.update(local_hermes)

        # 1. Create Default All-Hands Room (No 7-bot limit! Supports all company + Hermes bots)
        chat_engine.create_channel(
            channel_id="all-hands",
            name="#Company-All-Hands",
            member_bot_names=list(all_bots),
            description="Company-wide main communication room for all active bots and executives.",
            is_default=True,
            created_by="system",
        )

        # 2. Create Departmental Sub-Groups
        for d in departments:
            clean_dept_name = d.name.lower().replace(" ", "-").replace("&", "and")
            chat_engine.create_channel(
                channel_id=f"dept-{clean_dept_name}",
                name=f"#{d.name}",
                member_bot_names=d.member_bot_names,
                description=f"Department channel for {d.name} specialists and leads.",
                is_default=False,
                created_by="system",
            )

        # 3. Create Strategic Cognition Council Sub-Group (inspired by Hermes Bot Mode AGI)
        executive_leads = [d.lead_bot_name for d in departments]
        chat_engine.create_channel(
            channel_id="cognition-council",
            name="#Cognition-Council",
            member_bot_names=list(set(executive_leads)),
            description="High-level deliberation and multi-agent reasoning room for leadership leads.",
            is_default=False,
            created_by="system",
        )

        # 4. Register all bots in Silent Attendance Ledger
        for bot_name in all_bots:
            # Find department if assigned
            dept_name = "General"
            for d in departments:
                if bot_name in d.member_bot_names:
                    dept_name = d.name
                    break
            attendance_engine.register_bot(
                bot_name=bot_name,
                role="Specialist",
                department=dept_name,
                initial_status=AttendanceStatus.PRESENT,
            )

        # 5. Initialize Native Autonomous Kanban Board
        kanban_engine = CompanyKanbanEngine(org_id=org_id)
        kanban_engine.sync_projects_to_kanban(projects)

        self._responsibility_engines[org_id] = resp_engine
        self._kpi_engines[org_id] = kpi_engine
        self._discovery_engines[org_id] = disc_engine
        self._chat_engines[org_id] = chat_engine
        self._attendance_engines[org_id] = attendance_engine
        self._bot_medics[org_id] = bot_medic
        self._kanban_engines[org_id] = kanban_engine

        total_bots = len(all_bots) if all_bots else sum(len(d.member_bot_names) for d in departments)
        responsibilities = resp_engine.list_responsibilities()
        running_tasks = sum(p.kanban_tasks_count for p in projects)

        state = CompanyState(
            org_id=org_id,
            name=charter.name,
            archetype=target_archetype,
            state=OrgState.ACTIVE,
            charter=charter,
            departments=departments,
            responsibilities=responsibilities,
            objectives=objectives,
            projects=projects,
            kpis=kpi_engine.list_kpis(),
            active_bots_count=max(1, total_bots - 3),
            sleeping_bots_count=3,
            running_tasks_count=running_tasks,
            overall_health_percent=98.5,
        )

        self._organizations[org_id] = state
        logger.info(f"Successfully bootstrapped autonomous [{target_archetype.value}] organization {org_id}: '{state.name}'")
        return state

    def get_company(self, org_id: str) -> CompanyState | None:
        return self._organizations.get(org_id)

    def list_companies(self) -> list[CompanyState]:
        return list(self._organizations.values())

    def pause_company(self, org_id: str) -> CompanyState:
        state = self._organizations.get(org_id)
        if not state:
            raise KeyError(f"Organization '{org_id}' not found.")
        state.state = OrgState.PAUSED
        state.updated_at = time.time()
        return state

    def resume_company(self, org_id: str) -> CompanyState:
        state = self._organizations.get(org_id)
        if not state:
            raise KeyError(f"Organization '{org_id}' not found.")
        state.state = OrgState.ACTIVE
        state.updated_at = time.time()
        return state

    def get_responsibility_engine(self, org_id: str) -> ResponsibilityEngine:
        if org_id not in self._responsibility_engines:
            self._responsibility_engines[org_id] = ResponsibilityEngine()
        return self._responsibility_engines[org_id]

    def get_kpi_engine(self, org_id: str) -> KPIEngine:
        if org_id not in self._kpi_engines:
            self._kpi_engines[org_id] = KPIEngine()
        return self._kpi_engines[org_id]

    def get_discovery_engine(self, org_id: str) -> ContinuousWorkDiscoveryEngine:
        if org_id not in self._discovery_engines:
            self._discovery_engines[org_id] = ContinuousWorkDiscoveryEngine()
        return self._discovery_engines[org_id]

    def get_executive_digest(self, org_id: str) -> ExecutiveDigest:
        state = self._organizations.get(org_id)
        if not state:
            raise KeyError(f"Organization '{org_id}' not found.")
        return ExecutiveIntelligenceLayer.generate_digest(state=state)

    def replan_strategy(self, org_id: str, directive: str) -> StrategyReplanReport:
        state = self._organizations.get(org_id)
        if not state:
            raise KeyError(f"Organization '{org_id}' not found.")
        report = StrategicPlanningEngine.replan_strategy(
            current_projects=state.projects,
            new_directive=directive,
        )
        # Update organization state with new initiatives
        state.projects = [p for p in state.projects if p.name not in report.cancelled_projects]
        state.projects.extend(report.new_projects)
        state.objectives.extend(report.new_objectives)
        state.updated_at = time.time()
        return report

    def run_retrospective(self, org_id: str) -> EvolutionRecord:
        """Runs an autonomous retrospective and writes to the evolution journal."""
        state = self._organizations.get(org_id)
        if not state:
            raise KeyError(f"Organization '{org_id}' not found.")
        return ContinuousSelfImprovementEngine.run_retrospective(state=state)

    def get_swarm_bridge(self) -> SwarmLocalBridge:
        return self._swarm_bridge

    def get_hermes_bridge(self) -> HermesLocalBridge:
        return self._swarm_bridge

    def get_production_line(self) -> ProductionLineEngine:
        return self._production_line

    def get_enterprise_kanban(self) -> EnterpriseKanbanAdapter:
        return self._enterprise_kanban

    def get_hermes_kanban(self) -> HermesKanbanAdapter:
        return self._enterprise_kanban

    def sync_swarm_bots(self, org_id: str) -> dict[str, Any]:
        """Discovers local agent bots and enriches company departments with real local profiles."""
        state = self._organizations.get(org_id)
        if not state:
            raise KeyError(f"Organization '{org_id}' not found.")
        local_bots = self._swarm_bridge.discover_local_bots()
        metadata_map = self._swarm_bridge.get_all_local_profiles()
        return {
            "org_id": org_id,
            "swarm_installed": self._swarm_bridge.is_swarm_installed,
            "hermes_installed": self._swarm_bridge.is_swarm_installed,
            "discovered_bots_count": len(local_bots),
            "bot_names": local_bots,
            "sample_profiles": {k: v.model_dump() for k, v in list(metadata_map.items())[:10]},
        }

    def sync_hermes_bots(self, org_id: str) -> dict[str, Any]:
        return self.sync_swarm_bots(org_id)

    def sync_company_to_enterprise_kanban(self, org_id: str) -> dict[str, Any]:
        """Syncs company projects into local SQLite kanban board."""
        state = self._organizations.get(org_id)
        if not state:
            raise KeyError(f"Organization '{org_id}' not found.")
        return self._enterprise_kanban.sync_projects_to_kanban(state.projects)

    def sync_company_to_hermes_kanban(self, org_id: str) -> dict[str, Any]:
        return self.sync_company_to_enterprise_kanban(org_id)

    def get_chat_engine(self, org_id: str) -> GroupChatEngine:
        if org_id not in self._chat_engines:
            self._chat_engines[org_id] = GroupChatEngine(org_id=org_id)
        return self._chat_engines[org_id]

    def get_attendance_engine(self, org_id: str) -> AttendanceLedgerEngine:
        if org_id not in self._attendance_engines:
            self._attendance_engines[org_id] = AttendanceLedgerEngine(org_id=org_id)
        return self._attendance_engines[org_id]

    def get_bot_medic(self, org_id: str) -> BotMedicEngine:
        if org_id not in self._bot_medics:
            att = self.get_attendance_engine(org_id)
            resp = self._responsibility_engines.get(org_id)
            self._bot_medics[org_id] = BotMedicEngine(attendance_ledger=att, responsibility_engine=resp)
        return self._bot_medics[org_id]

    def post_group_message(
        self,
        org_id: str,
        channel_id: str,
        sender_bot: str,
        content: str,
    ) -> GroupMessage:
        chat = self.get_chat_engine(org_id)
        return chat.post_message(
            channel_id=channel_id,
            sender_bot=sender_bot,
            content=content,
        )

    def create_subgroup(
        self,
        org_id: str,
        channel_id: str,
        name: str,
        member_bot_names: list[str] | None = None,
        description: str = "",
        created_by: str = "system",
    ) -> GroupChannel:
        chat = self.get_chat_engine(org_id)
        return chat.create_channel(
            channel_id=channel_id,
            name=name,
            member_bot_names=member_bot_names,
            description=description,
            is_default=False,
            created_by=created_by,
        )

    def record_bot_pulse(
        self,
        org_id: str,
        bot_name: str,
        status: str = "present",
        active_task_id: str | None = None,
    ) -> BotHeartbeat:
        att = self.get_attendance_engine(org_id)
        return att.record_pulse(
            bot_name=bot_name,
            status=status,
            active_task_id=active_task_id,
        )

    def check_attendance_and_heal(
        self,
        org_id: str,
        timeout_seconds: float = 120.0,
        stuck_task_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Evaluates attendance and automatically dispatches Bot Medic to heal any absent or stuck agents."""
        state = self._organizations.get(org_id)
        org_name = state.name if state else "Autonomous Organization"
        att = self.get_attendance_engine(org_id)
        medic = self.get_bot_medic(org_id)

        eval_res = att.evaluate_attendance(
            timeout_seconds=timeout_seconds,
            stuck_task_seconds=stuck_task_seconds,
        )

        healing_reports = medic.auto_heal_all_unresponsive(
            timeout_seconds=timeout_seconds,
            stuck_task_seconds=stuck_task_seconds,
        )

        digest = att.generate_roll_call_digest(company_name=org_name)

        return {
            "org_id": org_id,
            "status": "healthy" if not healing_reports else "healed_unresponsive_bots",
            "absent_count": len(eval_res.get("absent", [])),
            "stuck_count": len(eval_res.get("stuck", [])),
            "healing_reports": [r.model_dump() for r in healing_reports],
            "roll_call_digest": digest,
        }

    def get_roll_call_digest(self, org_id: str) -> str:
        state = self._organizations.get(org_id)
        org_name = state.name if state else "Autonomous Enterprise"
        att = self.get_attendance_engine(org_id)
        return att.generate_roll_call_digest(company_name=org_name)

    def get_kanban_engine(self, org_id: str) -> CompanyKanbanEngine:
        if org_id not in self._kanban_engines:
            self._kanban_engines[org_id] = CompanyKanbanEngine(org_id=org_id)
        return self._kanban_engines[org_id]

    def list_kanban_tasks(
        self,
        org_id: str,
        status: str | None = None,
        assignee: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Lists tasks from the native autonomous Kanban board, with optional status and bot filters."""
        kanban = self.get_kanban_engine(org_id)
        tasks = kanban.list_tasks(status=status, assignee=assignee, limit=limit)
        return [t.model_dump() for t in tasks]

    def update_kanban_task(
        self,
        org_id: str,
        task_id: str,
        new_status: str,
        bot_name: str = "system",
        log_message: str = "",
        result: str = "",
    ) -> dict[str, Any]:
        """Updates a task status on the kanban board and records an audit log event."""
        kanban = self.get_kanban_engine(org_id)
        res = kanban.update_task_status(
            task_id=task_id,
            new_status=new_status,
            bot_name=bot_name,
            log_message=log_message,
            result=result,
        )
        # Also silently record bot pulse to keep attendance fresh
        if org_id in self._attendance_engines and bot_name != "system":
            self.record_bot_pulse(
                org_id=org_id,
                bot_name=bot_name,
                status="busy" if new_status == "in_progress" else "present",
                active_task_id=task_id if new_status == "in_progress" else None,
            )
        return res

    def add_kanban_log(
        self,
        org_id: str,
        task_id: str,
        bot_name: str,
        message: str,
        kind: str = "progress_log",
        payload_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Records an operational activity log for a specific task."""
        kanban = self.get_kanban_engine(org_id)
        log = kanban.add_activity_log(
            task_id=task_id,
            bot_name=bot_name,
            message=message,
            kind=kind,
            payload=payload_data,
        )
        return log.model_dump()

    def list_kanban_logs(
        self,
        org_id: str,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieves recent audit logs and task progress events."""
        kanban = self.get_kanban_engine(org_id)
        logs = kanban.list_activity_logs(task_id=task_id, limit=limit)
        return [entry.model_dump() for entry in logs]

    def agent_kanban_check_in(
        self,
        org_id: str,
        bot_name: str,
        current_task_id: str | None = None,
        progress_notes: str = "",
        new_status: str | None = None,
    ) -> dict[str, Any]:
        """Allows an agent to regularly check in on its queue, log progress, and claim work."""
        kanban = self.get_kanban_engine(org_id)
        res = kanban.agent_check_in(
            bot_name=bot_name,
            current_task_id=current_task_id,
            progress_notes=progress_notes,
            new_status=new_status,
        )
        # Silently record heartbeat
        if org_id in self._attendance_engines:
            self.record_bot_pulse(
                org_id=org_id,
                bot_name=bot_name,
                status="busy" if (new_status == "in_progress" or current_task_id) else "present",
                active_task_id=current_task_id,
            )
        return res


_GLOBAL_COMPANY_ENGINE: AutonomousCompanyEngine | None = None


def get_autonomous_company_engine() -> AutonomousCompanyEngine:
    global _GLOBAL_COMPANY_ENGINE
    if _GLOBAL_COMPANY_ENGINE is None:
        _GLOBAL_COMPANY_ENGINE = AutonomousCompanyEngine()
    return _GLOBAL_COMPANY_ENGINE
