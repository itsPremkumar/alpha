"""Task Contracts and Definition of Done (DoD) Gatekeeper.

Inspired by multi-agent workforce and autonomous quality assurance principles.
Enforces that tasks cannot be marked 'done' without satisfying explicit proof obligations
(evidence receipts, automated test reports, lint results, and verifier bot sign-offs).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from alpha.config.runtime_paths import runtime_home
from alpha.projects.events import get_event_bus
from alpha.projects.evidence import Evidence

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _contracts_path(project_id: str) -> Path:
    return runtime_home() / "projects" / project_id / "contracts" / "contracts.json"


@dataclass
class EvidenceReceipt:
    """Proof receipt verifying that a contract requirement has been met."""

    kind: str  # e.g., 'tests_passed', 'lint', 'commit', 'security', 'verifier_signoff', 'manual'
    reference: str  # Commit SHA, test report ID, or artifact path
    verified_by: str = "system"  # Bot name or system process that certified this
    detail: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceReceipt:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class DefinitionOfDone:
    """Formal completion criteria required before a task can transition to DONE."""

    required_evidence: list[str] = field(default_factory=lambda: ["tests_passed", "lint"])
    require_verifier_signoff: bool = True
    min_test_pass_rate: float = 1.0
    custom_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DefinitionOfDone:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class TaskContract:
    """Legally binding execution contract between orchestrator and agent."""

    task_id: str
    project_id: str
    title: str
    assignee_bot: str
    verifier_bot: str | None = "reviewer"
    inputs: dict[str, Any] = field(default_factory=dict)
    expected_outputs: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=lambda: ["all"])
    definition_of_done: DefinitionOfDone = field(default_factory=DefinitionOfDone)
    evidence_receipts: list[EvidenceReceipt] = field(default_factory=list)
    status: Literal["draft", "in_progress", "under_review", "done", "rejected"] = "draft"
    rejection_reasons: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["definition_of_done"] = self.definition_of_done.to_dict()
        data["evidence_receipts"] = [e.to_dict() for e in self.evidence_receipts]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskContract:
        d = dict(data)
        if "definition_of_done" in d and isinstance(d["definition_of_done"], dict):
            d["definition_of_done"] = DefinitionOfDone.from_dict(d["definition_of_done"])
        if "evidence_receipts" in d and isinstance(d["evidence_receipts"], list):
            d["evidence_receipts"] = [
                EvidenceReceipt.from_dict(e) if isinstance(e, dict) else e for e in d["evidence_receipts"]
            ]
        filtered = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class ContractGatekeeper:
    """Durable file-backed task contract store and verification gatekeeper."""

    def __init__(self, project_id: str, storage_path: Path | None = None):
        self.project_id = project_id
        self._path = storage_path or _contracts_path(project_id)
        self._lock = threading.Lock()
        self._contracts: dict[str, TaskContract] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("contracts", []):
                contract = TaskContract.from_dict(item)
                self._contracts[contract.task_id] = contract
        except Exception:
            logger.warning("Failed to load contracts for project %s", self.project_id, exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            payload = {
                "version": 1,
                "project_id": self.project_id,
                "contracts": [c.to_dict() for c in self._contracts.values()],
                "updated_at": _now(),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self._path)
        except Exception:
            logger.warning("Failed to save contracts for project %s", self.project_id, exc_info=True)

    def create_contract(
        self,
        task_id: str,
        title: str,
        assignee_bot: str,
        verifier_bot: str | None = "reviewer",
        *,
        inputs: dict[str, Any] | None = None,
        expected_outputs: list[str] | None = None,
        allowed_tools: list[str] | None = None,
        definition_of_done: DefinitionOfDone | dict[str, Any] | None = None,
    ) -> TaskContract:
        dod = (
            definition_of_done
            if isinstance(definition_of_done, DefinitionOfDone)
            else (DefinitionOfDone.from_dict(definition_of_done) if isinstance(definition_of_done, dict) else DefinitionOfDone())
        )
        with self._lock:
            contract = TaskContract(
                task_id=task_id,
                project_id=self.project_id,
                title=title,
                assignee_bot=assignee_bot,
                verifier_bot=verifier_bot,
                inputs=inputs or {},
                expected_outputs=expected_outputs or [],
                allowed_tools=allowed_tools or ["all"],
                definition_of_done=dod,
                status="in_progress",
            )
            self._contracts[task_id] = contract
            self._save()

        get_event_bus(self.project_id).emit(
            "contract_created",
            assignee_bot,
            {"task_id": task_id, "title": title, "verifier_bot": verifier_bot},
        )
        return contract

    def get_contract(self, task_id: str) -> TaskContract | None:
        with self._lock:
            return self._contracts.get(task_id)

    def add_evidence(
        self,
        task_id: str,
        receipt: EvidenceReceipt | dict[str, Any],
    ) -> TaskContract:
        """Attach an evidence receipt to the contract."""
        r = receipt if isinstance(receipt, EvidenceReceipt) else EvidenceReceipt.from_dict(receipt)
        with self._lock:
            contract = self._contracts.get(task_id)
            if not contract:
                raise KeyError(f"TaskContract '{task_id}' not found in project '{self.project_id}'.")
            contract.evidence_receipts.append(r)
            contract.updated_at = _now()
            self._save()

        get_event_bus(self.project_id).emit(
            "evidence_attached",
            r.verified_by,
            {"task_id": task_id, "kind": r.kind, "reference": r.reference},
        )
        return contract

    def verify_and_complete(
        self,
        task_id: str,
        *,
        verifier_bot: str | None = None,
        verification_detail: str = "Automated verification passed.",
    ) -> tuple[bool, list[str]]:
        """Verify all DoD criteria against attached receipts.

        Returns (True, []) and moves status to 'done' on success.
        Returns (False, [missing_reasons]) and leaves status as 'in_progress' or 'rejected' on failure.
        """
        with self._lock:
            contract = self._contracts.get(task_id)
            if not contract:
                return False, [f"Contract {task_id} does not exist."]

            missing: list[str] = []
            present_kinds = {e.kind for e in contract.evidence_receipts}

            # Check required evidence kinds
            for req in contract.definition_of_done.required_evidence:
                if req not in present_kinds:
                    missing.append(f"Missing required evidence: '{req}'")

            # Check verifier bot signoff if required
            if contract.definition_of_done.require_verifier_signoff:
                has_signoff = any(
                    e.kind == "verifier_signoff"
                    and (not contract.verifier_bot or e.verified_by.lower() == contract.verifier_bot.lower())
                    for e in contract.evidence_receipts
                )
                if not has_signoff and verifier_bot:
                    # If verifier is actively performing signoff now:
                    if not contract.verifier_bot or verifier_bot.lower() == contract.verifier_bot.lower():
                        signoff_receipt = EvidenceReceipt(
                            kind="verifier_signoff",
                            reference=f"signoff-{task_id}",
                            verified_by=verifier_bot,
                            detail=verification_detail,
                        )
                        contract.evidence_receipts.append(signoff_receipt)
                        has_signoff = True

                if not has_signoff:
                    missing.append(
                        f"Requires sign-off from designated verifier bot: '{contract.verifier_bot or 'any'}'"
                    )

            if missing:
                contract.status = "rejected"
                contract.rejection_reasons = missing
                contract.updated_at = _now()
                self._save()
                get_event_bus(self.project_id).emit(
                    "contract_rejected",
                    verifier_bot or "gatekeeper",
                    {"task_id": task_id, "reasons": missing},
                )
                return False, missing

            # All DoD obligations satisfied
            contract.status = "done"
            contract.rejection_reasons = []
            contract.updated_at = _now()
            self._save()

        get_event_bus(self.project_id).emit(
            "contract_completed",
            verifier_bot or "gatekeeper",
            {"task_id": task_id, "evidence_count": len(contract.evidence_receipts)},
        )
        return True, []

    def list_contracts(self, *, status: str | None = None) -> list[TaskContract]:
        with self._lock:
            contracts = list(self._contracts.values())
        if status:
            contracts = [c for c in contracts if c.status == status]
        return contracts


_gatekeepers: dict[str, ContractGatekeeper] = {}
_gatekeepers_lock = threading.Lock()


def get_contract_gatekeeper(project_id: str) -> ContractGatekeeper:
    with _gatekeepers_lock:
        gk = _gatekeepers.get(project_id)
        if gk is None:
            gk = ContractGatekeeper(project_id)
            _gatekeepers[project_id] = gk
        return gk
