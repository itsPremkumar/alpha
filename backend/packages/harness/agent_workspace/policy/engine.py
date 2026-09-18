"""Risk/policy engine: allow | deny | approval for every sensitive action.

Fail-closed by default. Operators add approval policies (action patterns
that auto-approve); everything else risky requires a human decision.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Verdict = Literal["allow", "deny", "approval"]

# action glob -> (default verdict, reason)
BASE_RULES: tuple[tuple[str, Verdict, str], ...] = (
    ("read:*", "allow", "read-only observation"),
    ("search:*", "allow", "read-only research"),
    ("test:*", "allow", "verification is always safe"),
    ("lint:*", "allow", "static analysis is side-effect free"),
    ("git:commit", "approval", "code enters history"),
    ("git:push", "approval", "code leaves the machine"),
    ("git:merge", "approval", "integration needs review"),
    ("deploy:*", "approval", "production impact"),
    ("email:send", "approval", "external communication"),
    ("message:send", "approval", "external communication"),
    ("payment:*", "deny", "financial actions are out of scope without explicit policy"),
    ("secret:export", "deny", "credential exfiltration is never allowed"),
    ("db:drop", "deny", "irreversible destruction"),
    ("fs:delete:production", "deny", "irreversible destruction"),
    ("fs:delete:*", "approval", "deletion needs a second pair of eyes"),
    ("shell:rm -rf*", "deny", "mass deletion pattern"),
    ("shell:*", "approval", "arbitrary execution needs review"),
)


def _default_storage_path() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "policy" / "policies.json"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "policy" / "policies.json"


@dataclass
class ApprovalPolicy:
    policy_id: str
    action_pattern: str
    actor: str = "*"
    project_id: str = "*"
    auto: Verdict = "allow"
    note: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalPolicy:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class PolicyDecision:
    verdict: Verdict
    reason: str
    matched_rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PolicyEngine:
    """Evaluate actions against base rules plus operator approval policies."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._policies: list[ApprovalPolicy] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self._policies = [ApprovalPolicy.from_dict(p) for p in data.get("policies", [])]
        except Exception:
            logger.warning("Policy load failed; using base rules only", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "policies": [p.to_dict() for p in self._policies]}, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Policy save failed", exc_info=True)

    def evaluate(self, action: str, *, actor: str = "*", project_id: str = "*") -> PolicyDecision:
        with self._lock:
            policies = list(self._policies)
        for p in policies:
            if fnmatch.fnmatch(action, p.action_pattern) and (p.actor in ("*", actor)) and (p.project_id in ("*", project_id)):
                return PolicyDecision(verdict=p.auto, reason=p.note or f"operator policy {p.policy_id}", matched_rule=p.policy_id)
        for pattern, verdict, reason in BASE_RULES:
            if fnmatch.fnmatch(action, pattern):
                return PolicyDecision(verdict=verdict, reason=reason, matched_rule=f"base:{pattern}")
        return PolicyDecision(verdict="approval", reason="no rule matched; fail-closed to approval", matched_rule=None)

    def add_policy(self, action_pattern: str, *, actor: str = "*", project_id: str = "*", auto: Verdict = "allow", note: str = "") -> ApprovalPolicy:
        policy = ApprovalPolicy(policy_id=f"pol-{uuid.uuid4().hex[:10]}", action_pattern=action_pattern, actor=actor, project_id=project_id, auto=auto, note=note)
        with self._lock:
            self._policies.append(policy)
            self._save()
        return policy

    def remove_policy(self, policy_id: str) -> bool:
        with self._lock:
            before = len(self._policies)
            self._policies = [p for p in self._policies if p.policy_id != policy_id]
            if len(self._policies) == before:
                return False
            self._save()
            return True

    def list_policies(self) -> list[ApprovalPolicy]:
        with self._lock:
            return list(self._policies)


_engine: PolicyEngine | None = None
_engine_path: str | None = None
_engine_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    global _engine, _engine_path
    with _engine_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _engine is None or _engine_path != live:
            _engine = PolicyEngine()
            try:
                _engine_path = str(_engine.storage_path.resolve())
            except Exception:
                _engine_path = live
        return _engine
