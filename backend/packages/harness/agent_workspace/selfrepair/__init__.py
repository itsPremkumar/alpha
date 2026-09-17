"""Self-repair: diagnose, bounded-repair, verify — sandbox-confined."""

from agent_workspace.selfrepair.engine import Diagnosis, RepairRecord, attempt_repair, diagnose, disk_health, verify_repair

__all__ = ["Diagnosis", "RepairRecord", "attempt_repair", "diagnose", "disk_health", "verify_repair"]
