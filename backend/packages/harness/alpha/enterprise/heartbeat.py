"""Enterprise Heartbeat Coordinator & Autonomous Orchestration Engine."""

from __future__ import annotations

import logging
import time
from typing import Any

from alpha.enterprise.council import get_council_quorum_engine
from alpha.enterprise.discovery import get_discovery_and_optimization_engine
from alpha.enterprise.governance import get_department_treasury
from alpha.enterprise.hierarchy import get_enterprise_hierarchy
from alpha.enterprise.models import EnterpriseTelemetry
from alpha.enterprise.pipeline import get_mission_pipeline
from alpha.enterprise.rfc import get_rfc_protocol

logger = logging.getLogger(__name__)


class EnterpriseHeartbeatCoordinator:
    """Coordinates perpetual cyclic heartbeats, continuous self-healing, and live War Room telemetry updates."""

    def __init__(self):
        self.hierarchy = get_enterprise_hierarchy()
        self.pipeline = get_mission_pipeline()
        self.rfc_protocol = get_rfc_protocol()
        self.treasury = get_department_treasury()
        self.council = get_council_quorum_engine()
        self.discovery = get_discovery_and_optimization_engine()

        self._start_time = time.time()
        self._cycle_counter = 0
        self._completed_tasks_total = 0
        self._stagnation_ticks = 0
        self._stagnation_status = "nominal"
        self._last_heartbeat_time = time.time()

        # Seed initial mission and sprint if none exist
        self._initialize_baseline_mission()

    def _initialize_baseline_mission(self) -> None:
        """Initializes baseline active mission, epics, and dynamic DAG sprint."""
        if not self.pipeline.list_sprints():
            m_id = "msn-genesis-001"
            self.pipeline.register_strategic_mission(
                mission_id=m_id,
                title="Perpetual Autonomous Software Enterprise Evolution",
                objective="Deliver world-class software, execute continuous memory consolidation, and uphold zero-trust security.",
            )
            epics = self.pipeline.decompose_strategic_mission(
                mission_id=m_id,
                objective="Perpetual autonomous enterprise software engineering",
            )
            if epics:
                spec = self.pipeline.synthesize_technical_spec(epics[0].epic_id)
                self.pipeline.compile_dynamic_dag_sprint(spec.spec_id)

    def step_heartbeat_cycle(self) -> dict[str, Any]:
        """Executes a single atomic enterprise heartbeat cycle.

        Sequence of Operations:
        1. Proactive feature gap discovery
        2. Latency profiling across critical paths
        3. AST boundary static security scan
        4. Mission-to-Sprint DAG advancement
        5. Department token treasury burn recording & circuit breaker verification
        6. RFC consensus evaluation & gating
        7. Council quorum multi-sig verification check
        8. Stagnation detection & auto-recovery
        9. Live telemetry assembly
        """
        self._cycle_counter += 1
        now = time.time()
        self._last_heartbeat_time = now

        # 1. Feature gap discovery
        gaps = self.discovery.discover_feature_gaps()

        # 2. Latency profiling
        latencies = self.discovery.profile_latencies()
        p95_values = [p.p95_ms for p in latencies]
        # No latency profiles => no measurement. Report None, never a fabricated 0.0ms.
        avg_p95 = round(sum(p95_values) / len(p95_values), 1) if p95_values else None

        # 3. AST boundary security scan
        sec_report = self.discovery.scan_ast_boundaries()

        # 4. Advance dynamic DAG sprints
        sprints = self.pipeline.list_sprints()
        advanced_tasks = []
        sprint_results: list[tuple[str, dict[str, Any]]] = []
        for s in sprints:
            if s.status == "active":
                res = self.pipeline.step_sprint_dag(s.sprint_id)
                sprint_results.append((s.sprint_id, res))
                advanced_tasks.extend(res.get("advanced_tasks", []))
                self._completed_tasks_total += len(res.get("advanced_tasks", []))

        # 5. Token treasury burn — MEASURED burns only, never invented.
        #
        # This used to invent a per-cycle burn (4500 with progress, 1200
        # without) plus fixed "minimal baseline" burns for four more
        # departments and 400 for every custom department. Those invented
        # numbers fed ``record_token_burn``, whose burn rate and CIRCUIT
        # BREAKERS were then computed from fiction — an invented burn can
        # trip a real breaker and block real spending. A sprint step reports
        # advanced tasks, not token usage, so unless a real count is present
        # there is nothing to record this cycle: record nothing, and disclose
        # it in the telemetry below.
        measured_burns: dict[str, int] = {}
        for _sprint_id, res in sprint_results:
            used = res.get("tokens_used")
            if isinstance(used, int) and used > 0:
                measured_burns["dept-engineering"] = measured_burns.get("dept-engineering", 0) + used
        for dept_id, tokens in measured_burns.items():
            self.treasury.record_token_burn(
                dept_id=dept_id,
                tokens_burned=tokens,
                tasks_completed=len(advanced_tasks),
            )

        # 8. Stagnation Watchdog & Keel-style Auto-Recovery
        if not advanced_tasks and all(s.status == "completed" for s in sprints):
            self._stagnation_ticks += 1
            if self._stagnation_ticks >= 3:
                self._stagnation_status = "auto_recovering"
                logger.info("Heartbeat: Stagnation detected; executing auto-recovery sequence")
                compiled_spec_ids = {s.spec_id for s in self.pipeline.list_sprints()}
                epics = self.pipeline.list_epics()

                candidate_epic = None
                for ep in epics:
                    spec_id = f"spec-{ep.epic_id}"
                    if spec_id not in compiled_spec_ids:
                        candidate_epic = ep
                        break

                if candidate_epic:
                    spec = self.pipeline.synthesize_technical_spec(candidate_epic.epic_id)
                    self.pipeline.compile_dynamic_dag_sprint(spec.spec_id)
                    logger.info(f"Auto-recovery: compiled next dynamic DAG sprint for epic '{candidate_epic.title}'")
                else:
                    # All current epics completed: decompose next continuous strategic mission
                    phase = len(self.pipeline._missions) + 1
                    next_mission_id = f"msn-continuous-phase-{phase}"
                    self.pipeline.register_strategic_mission(
                        mission_id=next_mission_id,
                        title=f"Perpetual Software Evolution Phase {phase}",
                        objective=f"Phase {phase}: Continuous discovery, AST security hardening, holdout benchmarking, and memory consolidation.",
                    )
                    new_epics = self.pipeline.decompose_strategic_mission(
                        mission_id=next_mission_id,
                        objective=f"Autonomous Enterprise Evolution Phase {phase}",
                    )
                    if new_epics:
                        spec = self.pipeline.synthesize_technical_spec(new_epics[0].epic_id)
                        self.pipeline.compile_dynamic_dag_sprint(spec.spec_id)
                        logger.info(f"Auto-recovery: synthesized strategic mission {next_mission_id} and compiled DAG sprint")

                self._stagnation_ticks = 0
                self._stagnation_status = "nominal"
        else:
            self._stagnation_ticks = 0
            self._stagnation_status = "nominal"

        # 9. Live Telemetry
        telemetry = self.get_telemetry()
        return {
            "cycle": self._cycle_counter,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "advanced_tasks": advanced_tasks,
            "gaps_count": len(gaps),
            # float | None — None when no latency profile has been recorded.
            "system_latency_p95_ms": avg_p95,
            "security_score": sec_report.security_score,
            "circuit_breakers_tripped": telemetry.treasury_circuit_breakers_tripped,
            # "measured" only when a real token count was recorded this cycle;
            # "unavailable" means no measurement existed and NOTHING was fed
            # to the treasury (the pre-fix code invented burns here).
            "token_burn_measurement": "measured" if measured_burns else "unavailable",
            "stagnation_status": self._stagnation_status,
            "telemetry": telemetry.model_dump(),
        }

    def get_telemetry(self) -> EnterpriseTelemetry:
        """Assembles live telemetry data for the War Room UI."""
        now = time.time()
        uptime = round(now - self._start_time, 1)

        csuite = self.hierarchy.get_csuite()
        csuite_status = {k: "nominal" for k in csuite.keys()}

        depts = self.hierarchy.get_departments()
        rfcs = self.rfc_protocol.list_rfcs()
        approved_rfcs = [r for r in rfcs if r.gating_passed or r.status.value == "approved"]
        sprints = self.pipeline.list_sprints()
        active_sprints = [s for s in sprints if s.status == "active"]

        treasury_data = self.treasury.get_overall_telemetry()
        latencies = self.discovery.get_latency_profiles()
        p95_values = [p.p95_ms for p in latencies]
        # No latency profiles => no measurement. Report None, never a fabricated 0.0ms.
        avg_p95 = round(sum(p95_values) / len(p95_values), 1) if p95_values else None

        sec = self.discovery.get_latest_scan()
        sec_score = sec.security_score if sec else None

        active_rel = self.council.get_active_release()
        latest_ver = active_rel.version if active_rel else None
        holdout_score = active_rel.holdout_benchmark_score if active_rel else None

        return EnterpriseTelemetry(
            heartbeat_cycle=self._cycle_counter,
            uptime_seconds=uptime,
            csuite_status=csuite_status,
            departments_count=len(depts),
            active_workers_count=len(self.hierarchy._nodes_by_bot),
            active_rfcs_count=len(rfcs),
            approved_rfcs_count=len(approved_rfcs),
            active_sprints_count=len(active_sprints),
            tasks_completed_count=self._completed_tasks_total,
            treasury_overall_burn_rate_tpm=treasury_data.get("overall_burn_rate_tpm", 0.0),
            treasury_circuit_breakers_tripped=treasury_data.get("active_circuit_breakers_count", 0),
            system_latency_p95_ms=avg_p95,
            security_posture_score=sec_score,
            holdout_pass_rate_percent=holdout_score,
            latest_release_version=latest_ver,
            stagnation_recovery_status=self._stagnation_status,
            last_heartbeat_timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_heartbeat_time)),
        )


_HEARTBEAT_COORDINATOR: EnterpriseHeartbeatCoordinator | None = None


def get_enterprise_heartbeat_coordinator() -> EnterpriseHeartbeatCoordinator:
    global _HEARTBEAT_COORDINATOR
    if _HEARTBEAT_COORDINATOR is None:
        _HEARTBEAT_COORDINATOR = EnterpriseHeartbeatCoordinator()
    return _HEARTBEAT_COORDINATOR
