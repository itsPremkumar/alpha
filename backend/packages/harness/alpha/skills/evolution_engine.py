"""Skill self-evolution engine: evidence -> versioned candidate -> offline
validation -> policy gate -> promote or roll back.

This is the missing engine behind ``alpha.config.skill_evolution_config``.
It complements (does not replace) the surfaces that already exist:
``proposals.py`` (new-skill proposal queue), ``workshop.py`` (trace ->
draft synthesis), ``curator.py`` (active/stale/archived lifecycle with
``.archive/`` restore), and ``alpha/evolution/engine.py`` (generic
propose/benchmark/gate/promote for non-skill surfaces).

Pipeline and invariants:

- **propose** packages caller-supplied *evidence references* plus a
  *candidate* ``SKILL.md`` body as a versioned record. The active/canonical
  skill body is NEVER mutated in place before promotion; the candidate lives
  only in the store under ``runtime_home()/skill_evolution/`` (atomic writes:
  tmp + ``os.replace``).
- **evaluate** runs offline validation with no network access:
  structural/AST/consistency checks always run, and a skill test suite is
  executed via subprocess ONLY when the candidate frontmatter declares
  ``test-command``. Without a declared suite the record honestly says
  ``validation_kind="structural-only"`` and notes that runtime behavior was
  NOT verified. The configured moderation model is honored through the single
  module-level invoke seam :func:`moderation_invoke`; absence of a configured
  model records ``not_configured`` and NEVER fakes a moderation pass.
- **policy gate** (applied in ``evaluate`` and again in ``promote``) follows
  ``skill_evolution_config`` semantics: ``auto_promote`` defaults to off
  (promotion always requires recorded evaluation evidence plus an explicit
  approve flag), ``security_fail_closed=True`` rejects the proposal on any
  evaluation error, and with ``fail_closed=False`` an errored evaluation
  leaves the proposal ``proposed`` (never ``validated``) with the error
  recorded. Moderation-unavailable write blocking happens at promotion time,
  mirroring ``security_fail_closed``'s "block skill writes" contract.
- **promote** re-checks that the active body still matches the proposal's
  baseline hash (TOCTOU guard), stamps the computed candidate version into
  the frontmatter, atomically replaces ``SKILL.md``, and best-effort pins the
  skill in the curator so lifecycle maintenance cannot race a fresh
  promotion. The previous body is retained on the record for rollback.
- **rollback** restores the previous body atomically, and refuses to clobber
  an active file that was modified after promotion.

Honesty contract (pinned by ``tests/test_skill_evolution_engine.py``): every
record carries its evidence references, the validation kind actually
performed, the real status (proposed/validated/rejected/promoted/rolled_back),
and explicit notes when something was NOT verified. Any score is computed
from the checks that actually ran (``checks_passed / checks_total``) - never
invented. Rejection reasons carry the real failing detail.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from alpha.config.skill_evolution_config import SkillEvolutionConfig
from alpha.skills.types import SKILL_MD_FILE

logger = logging.getLogger(__name__)

# Record statuses (the honest lifecycle of one evolution proposal).
PROPOSED = "proposed"
VALIDATED = "validated"
REJECTED = "rejected"
PROMOTED = "promoted"
ROLLED_BACK = "rolled_back"
PROPOSAL_STATUSES = (PROPOSED, VALIDATED, REJECTED, PROMOTED, ROLLED_BACK)

# Validation kinds - always record what was ACTUALLY performed.
VALIDATION_STRUCTURAL_ONLY = "structural-only"
VALIDATION_STRUCTURAL_AND_SUITE = "structural+subprocess-suite"

# Moderation statuses the invoke seam may honestly return.
MODERATION_STATUSES = frozenset({"allow", "warn", "block", "error", "not_configured", "not_run"})

MAX_CANDIDATE_CHARS = 65536
MAX_EVIDENCE_REFS = 50
MAX_EVIDENCE_REF_CHARS = 2000
SUITE_TIMEOUT_SECONDS = 120
SUITE_OUTPUT_TAIL_CHARS = 2000

#: Frontmatter keys a candidate uses to declare its own test suite. The suite
#: runs ONLY when one of these is present - absence records structural-only.
TEST_COMMAND_KEYS = ("test-command", "test_command")

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_VERSION_LINE_RE = re.compile(r"^version\s*:")
_PY_FENCE_RE = re.compile(r"```(?:python|py|python3)[ \t]*\n(.*?)```", re.DOTALL)
_EXECUTABLE_FENCE_RE = re.compile(r"```\s*(bash|sh|shell|zsh|powershell|ps1|python|py|python3|console|cmd)\b", re.IGNORECASE)
_EXECUTABLE_TOOLS = frozenset({"bash", "shell", "execute_command", "exec"})


class UnknownProposalError(ValueError):
    """The proposal id does not resolve to a stored record (map to 404)."""


class SkillEvolutionDisabledError(ValueError):
    """``skill_evolution.enabled`` is False; no evolution writes are allowed (403)."""


class InvalidTransitionError(ValueError):
    """Illegal status transition or unmet promotion/rollback precondition (409)."""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tail(value: Any, limit: int = SUITE_OUTPUT_TAIL_CHARS) -> str:
    """Bounded tail of subprocess output (bytes or str); honest about absence."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text[-limit:]


def _split_frontmatter(md: str) -> tuple[str | None, str]:
    """Split a SKILL.md body into (frontmatter text | None, body)."""
    match = _FRONTMATTER_RE.match(md or "")
    if not match:
        return None, md or ""
    return match.group(1), md[match.end() :]


def _frontmatter_meta(md: str) -> dict[str, Any] | None:
    """Parsed frontmatter mapping, or None when absent/unparseable/not a dict."""
    text, _ = _split_frontmatter(md)
    if text is None:
        return None
    try:
        meta = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return meta if isinstance(meta, dict) else None


def _frontmatter_version(md: str) -> str | None:
    meta = _frontmatter_meta(md)
    if not meta:
        return None
    value = meta.get("version")
    if value is None:
        return None
    return str(value).strip() or None


def _set_version(md: str, version: str) -> str:
    """Return the candidate with its frontmatter ``version`` set to ``version``.

    Raises ValueError when there is no frontmatter block to stamp (the caller
    surfaces the real reason instead of silently writing an unversioned body).
    """
    text, body = _split_frontmatter(md)
    if text is None:
        raise ValueError("candidate has no YAML frontmatter to stamp a version into.")
    lines: list[str] = []
    replaced = False
    for line in text.splitlines():
        if _VERSION_LINE_RE.match(line):
            lines.append(f"version: {version}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append(f"version: {version}")
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def _bump_version(base: str | None) -> str:
    """Patch-bump the active version to derive the candidate version."""
    if not base:
        return "1.0.0"
    parts = str(base).split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        bumped = f"{base}.1"
        return bumped if bumped != base else "1.0.0"
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts)


def _content_fingerprint(md: str) -> str:
    """Hash of the candidate ignoring only ``version:`` lines (change detection)."""
    text, body = _split_frontmatter(md)
    if text is None:
        normalized = md
    else:
        kept = [line for line in text.splitlines() if not _VERSION_LINE_RE.match(line)]
        normalized = "\n".join(kept) + "\n" + body
    return _sha256(normalized)


def _is_executable(md: str) -> bool:
    """Does the candidate carry executable content (code fences / bash tools)?

    Drives the ``security_fail_closed=false`` rule that still blocks
    executable content when moderation is unavailable.
    """
    if _EXECUTABLE_FENCE_RE.search(md or ""):
        return True
    meta = _frontmatter_meta(md)
    if not meta:
        return False
    allowed = meta.get("allowed-tools")
    if isinstance(allowed, str):
        entries = re.split(r"[\s,]+", allowed.strip())
    elif isinstance(allowed, (list, tuple)):
        entries = [str(item) for item in allowed]
    else:
        entries = []
    return any(entry.strip().lower() in _EXECUTABLE_TOOLS for entry in entries)


def _normalize_evidence(evidence: Any) -> list[dict[str, Any]]:
    """Normalize caller evidence into bounded ``{"ref": ...}`` records."""
    if not isinstance(evidence, (list, tuple)) or not evidence:
        raise ValueError("An evolution proposal requires at least one evidence reference.")
    if len(evidence) > MAX_EVIDENCE_REFS:
        raise ValueError(f"Evolution proposals accept at most {MAX_EVIDENCE_REFS} evidence references.")
    normalized: list[dict[str, Any]] = []
    for item in evidence:
        if isinstance(item, str):
            payload: dict[str, Any] = {"ref": item.strip()}
        elif isinstance(item, dict):
            payload = {str(key): value for key, value in item.items()}
            payload["ref"] = str(item.get("ref") or "").strip()
        else:
            raise ValueError(f"Evidence references must be strings or mappings, got {type(item).__name__}.")
        ref = payload["ref"]
        if not ref:
            raise ValueError("Each evidence reference must be a non-empty string.")
        if len(ref) > MAX_EVIDENCE_REF_CHARS:
            raise ValueError(f"Evidence reference exceeds the {MAX_EVIDENCE_REF_CHARS}-char cap.")
        normalized.append(payload)
    return normalized


def moderation_invoke(content: str, *, executable: bool, model_name: str | None) -> dict[str, Any]:
    """Single module-level invoke seam for candidate moderation.

    This is the ONLY place the engine reaches for a moderation model, so tests
    stub exactly this one function. Behavior:

    - No configured ``moderation_model_name`` -> honest ``not_configured``
      result without any network call. This is recorded as "moderation was
      NOT performed" downstream - it is never reported as a pass.
    - Configured -> delegates to the existing production moderation path
      (``alpha.skills.security_scanner.scan_skill_content``, which reads the
      same ``skill_evolution.moderation_model_name`` from the app config) and
      maps its allow/warn/block decision. Exceptions propagate so the caller's
      ``security_fail_closed`` policy decides - an errored moderation call is
      an evaluation error, never a silent pass.

    Sync entry point: it drives the async scanner with ``asyncio.run`` and is
    intended to be called from worker threads (FastAPI sync handlers or
    ``asyncio.to_thread``), not from inside a running event loop.
    """
    if not model_name:
        return {
            "status": "not_configured",
            "model": None,
            "reason": "no moderation model configured; moderation was NOT performed",
        }
    from alpha.skills.security_scanner import scan_skill_content

    result = asyncio.run(scan_skill_content(content, executable=executable))
    decision = str(result.decision or "").lower()
    status = decision if decision in {"allow", "warn", "block"} else "error"
    return {"status": status, "model": model_name, "reason": str(result.reason or "")}


def _normalize_moderation(raw: Any, model_name: str | None) -> dict[str, Any]:
    """Validate the seam's return shape; unknown shapes are honest errors."""
    if not isinstance(raw, dict):
        return {"status": "error", "model": model_name, "reason": f"moderation seam returned {type(raw).__name__}, not a result mapping."}
    status = str(raw.get("status") or "").strip().lower()
    if status not in MODERATION_STATUSES:
        return {"status": "error", "model": model_name, "reason": f"moderation seam returned unknown status '{status}'."}
    reason = str(raw.get("reason") or "").strip() or "no reason recorded."
    model = raw.get("model")
    return {"status": status, "model": str(model) if model else model_name, "reason": reason}


@dataclass
class SkillEvolutionProposal:
    """One versioned evolution proposal with its honest evaluation record."""

    id: str
    skill_name: str
    candidate_md: str
    candidate_version: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    base_version: str | None = None
    active_sha256: str = ""
    status: str = PROPOSED
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    evaluation: dict[str, Any] | None = None
    moderation: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    reject_reason: str | None = None
    previous_md: str | None = None
    previous_sha256: str | None = None
    previous_version: str | None = None
    promoted_sha256: str | None = None
    promoted_at: str | None = None
    rolled_back_at: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillEvolutionProposal:
        known = set(cls.__dataclass_fields__)
        filtered = {key: value for key, value in data.items() if key in known}
        return cls(**filtered)


class SkillEvolutionStore:
    """File-backed proposal store under ``runtime_home()/skill_evolution/``.

    One JSON file per proposal; writes are atomic (tmp + ``os.replace``)
    behind a process lock, mirroring ``proposals.py``.
    """

    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def _record_path(self, proposal_id: str) -> Path:
        if not isinstance(proposal_id, str) or not _ID_RE.match(proposal_id):
            raise UnknownProposalError(f"Unknown skill-evolution proposal '{proposal_id}'.")
        return self._root / f"{proposal_id}.json"

    def save(self, record: SkillEvolutionProposal) -> None:
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            target = self._record_path(record.id)
            fd, tmp_name = tempfile.mkstemp(dir=str(self._root), prefix=".skill-evolution-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record.to_dict(), handle, ensure_ascii=False)
                os.replace(tmp_name, target)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

    def load(self, proposal_id: str) -> SkillEvolutionProposal | None:
        try:
            path = self._record_path(proposal_id)
        except UnknownProposalError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Unreadable skill-evolution record %s: %s", path.name, exc)
            return None
        try:
            return SkillEvolutionProposal.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Malformed skill-evolution record %s: %s", path.name, exc)
            return None

    def list_all(self) -> list[SkillEvolutionProposal]:
        if not self._root.is_dir():
            return []
        records: list[SkillEvolutionProposal] = []
        for path in sorted(self._root.glob("*.json")):
            record = self.load(path.stem)
            if record is not None:
                records.append(record)
        records.sort(key=lambda record: (record.created_at, record.id), reverse=True)
        return records


def _record_event(record: SkillEvolutionProposal, event: str, detail: str = "") -> None:
    record.history.append({"event": event, "at": _utcnow(), "detail": detail})


def _note(record: SkillEvolutionProposal, text: str) -> None:
    if text not in record.notes:
        record.notes.append(text)


class SkillEvolutionEngine:
    """Evidence -> candidate -> validate -> gate -> promote/rollback engine.

    ``store_dir`` defaults to ``runtime_home()/skill_evolution`` and
    ``skills_root`` defaults to ``runtime_home()/skills``; tests isolate by
    passing both explicitly (no global env fixtures).
    """

    def __init__(
        self,
        *,
        store_dir: str | Path | None = None,
        skills_root: str | Path | None = None,
        config: Any | None = None,
    ):
        from alpha.config.runtime_paths import runtime_home

        home = runtime_home()
        self._store = SkillEvolutionStore(Path(store_dir) if store_dir is not None else home / "skill_evolution")
        self._skills_root = Path(skills_root) if skills_root is not None else home / "skills"
        resolved = self._resolve_config(config)
        # Resolve policy up front with the MOST restrictive fallback for a
        # missing field: disabled, fail-closed, auto-promote off.
        self._enabled = bool(getattr(resolved, "enabled", False))
        self._fail_closed = bool(getattr(resolved, "security_fail_closed", True))
        self._auto_promote = bool(getattr(resolved, "auto_promote", False))
        model_name = getattr(resolved, "moderation_model_name", None)
        self._moderation_model_name = str(model_name).strip() or None if model_name is not None else None
        self._lock = threading.Lock()

    @staticmethod
    def _resolve_config(config: Any | None) -> Any:
        if config is not None:
            return config
        try:
            from alpha.config import get_app_config

            return get_app_config().skill_evolution
        except Exception:  # no usable config on disk -> restrictive defaults
            return SkillEvolutionConfig()

    # -- helpers ------------------------------------------------------------

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise SkillEvolutionDisabledError("skill evolution is disabled (config skill_evolution.enabled is false).")

    def _skill_dir(self, skill_name: str) -> Path:
        if not isinstance(skill_name, str) or not _NAME_RE.match(skill_name):
            raise ValueError(f"Invalid skill name '{skill_name}'; expected 1-64 chars of lowercase letters, digits, or hyphens.")
        return self._skills_root / skill_name

    def _active_path(self, skill_name: str) -> Path:
        return self._skill_dir(skill_name) / SKILL_MD_FILE

    def _require_record(self, proposal_id: str) -> SkillEvolutionProposal:
        record = self._store.load(proposal_id)
        if record is None:
            raise UnknownProposalError(f"Unknown skill-evolution proposal '{proposal_id}'.")
        return record

    # -- pipeline stages ----------------------------------------------------

    def propose(
        self,
        skill_name: str,
        candidate_markdown: str,
        evidence: Any,
        *,
        created_by: str = "",
    ) -> SkillEvolutionProposal:
        """Create a versioned candidate proposal from evidence references.

        The active skill body is read for baseline hashing only - it is never
        written here.
        """
        self._require_enabled()
        skill_dir = self._skill_dir(skill_name)
        active_path = skill_dir / SKILL_MD_FILE
        if not active_path.is_file():
            raise ValueError(f"Active skill '{skill_name}' not found at {active_path}; evolution proposals require an existing skill.")
        if not isinstance(candidate_markdown, str) or not candidate_markdown.strip():
            raise ValueError("Candidate SKILL.md markdown must be a non-empty string.")
        if len(candidate_markdown) > MAX_CANDIDATE_CHARS:
            raise ValueError(f"Candidate exceeds the {MAX_CANDIDATE_CHARS}-char cap.")
        normalized_evidence = _normalize_evidence(evidence)

        active_md = active_path.read_text(encoding="utf-8")
        base_version = _frontmatter_version(active_md)
        candidate_version = _bump_version(base_version)

        stamped = True
        try:
            candidate_md = _set_version(candidate_markdown, candidate_version)
        except ValueError:
            # Leave the body untouched; evaluation reports the real
            # frontmatter finding instead of silently skipping the stamp.
            stamped = False
            candidate_md = candidate_markdown

        now = _utcnow()
        record = SkillEvolutionProposal(
            id=uuid.uuid4().hex,
            skill_name=skill_name,
            candidate_md=candidate_md,
            candidate_version=candidate_version,
            evidence=normalized_evidence,
            base_version=base_version,
            active_sha256=_sha256(active_md),
            status=PROPOSED,
            created_by=str(created_by or ""),
            created_at=now,
            updated_at=now,
        )
        _record_event(
            record,
            "proposed",
            f"candidate v{candidate_version} from base v{base_version or 'unset'} with {len(normalized_evidence)} evidence reference(s)",
        )
        if not stamped:
            _note(record, "Candidate frontmatter was missing or unparseable at propose time; version stamping was deferred and evaluation reports the frontmatter finding.")
        with self._lock:
            self._store.save(record)
        return record

    def evaluate(self, proposal_id: str) -> SkillEvolutionProposal:
        """Run offline validation + moderation for a proposed candidate.

        Never raises for evaluation failures: errors are recorded on the
        record and the ``security_fail_closed`` policy decides whether the
        proposal is rejected or left unverified (``proposed``).
        """
        self._require_enabled()
        with self._lock:
            record = self._require_record(proposal_id)
            if record.status != PROPOSED:
                raise InvalidTransitionError(f"Proposal '{record.id}' is '{record.status}'; only a '{PROPOSED}' proposal can be evaluated.")
            now = _utcnow()
            try:
                active_path = self._active_path(record.skill_name)
                if not active_path.is_file():
                    raise RuntimeError(f"active {SKILL_MD_FILE} not found at {active_path}; cannot validate against a missing baseline")
                active_md = active_path.read_text(encoding="utf-8")
                evaluation = self._run_checks(record, active_md, now)
                executable = _is_executable(record.candidate_md)
                raw = moderation_invoke(record.candidate_md, executable=executable, model_name=self._moderation_model_name)
                moderation = _normalize_moderation(raw, self._moderation_model_name)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                record.evaluation = {
                    "ran_at": now,
                    "error": error,
                    "validation_kind": None,
                    "checks_total": 0,
                    "checks_passed": 0,
                    "score": None,
                    "checks": [],
                    "suite": None,
                }
                record.moderation = {
                    "status": "not_run",
                    "model": self._moderation_model_name,
                    "reason": "moderation did not run because the evaluation errored.",
                }
                if self._fail_closed:
                    record.status = REJECTED
                    record.reject_reason = f"evaluation error (security_fail_closed=true): {error}"
                    _record_event(record, "rejected", record.reject_reason)
                else:
                    _note(record, f"Evaluation error: {error}. The proposal was NOT verified and remains '{record.status}' because security_fail_closed=false.")
                    _record_event(record, "evaluation_error", error)
                record.updated_at = _utcnow()
                self._store.save(record)
                return record

            record.evaluation = evaluation
            record.moderation = moderation
            findings = list(evaluation["findings"])
            if findings:
                record.status = REJECTED
                record.reject_reason = "validation failed: " + " | ".join(findings)
                _record_event(record, "rejected", record.reject_reason)
            elif moderation["status"] == "block":
                record.status = REJECTED
                record.reject_reason = f"moderation blocked the candidate: {moderation['reason']}"
                _record_event(record, "rejected", record.reject_reason)
            elif moderation["status"] == "error" and self._fail_closed:
                record.status = REJECTED
                record.reject_reason = f"moderation error (security_fail_closed=true): {moderation['reason']}"
                _record_event(record, "rejected", record.reject_reason)
            else:
                record.status = VALIDATED
                _record_event(
                    record,
                    "validated",
                    f"validation_kind={evaluation['validation_kind']}, checks {evaluation['checks_passed']}/{evaluation['checks_total']}, moderation={moderation['status']}",
                )

            # Honesty notes: say what was NOT verified, always.
            if evaluation["validation_kind"] == VALIDATION_STRUCTURAL_ONLY:
                _note(record, "No test suite declared (candidate frontmatter has no 'test-command'); validation was structural-only and runtime behavior was NOT verified.")
            else:
                suite = evaluation.get("suite") or {}
                _note(
                    record,
                    f"Validation ran the declared test suite via subprocess (exit_code={suite.get('exit_code')}, timed_out={suite.get('timed_out')}); "
                    "only the declared command was exercised - broader runtime behavior was NOT verified.",
                )
            if moderation["status"] == "not_configured":
                _note(record, "No moderation model configured; moderation was NOT performed - this is not a moderation pass.")
            elif moderation["status"] == "warn":
                _note(record, f"Moderation returned 'warn' (not a clean pass): {moderation['reason']}")
            elif moderation["status"] == "error":
                _note(record, f"Moderation attempt failed: {moderation['reason']} - no moderation pass was recorded.")
            elif moderation["status"] == "not_run":
                _note(record, "Moderation did not run - no moderation result is recorded for this proposal.")
            _note(
                record,
                f"Score {evaluation['score']} = checks_passed {evaluation['checks_passed']} / checks_total {evaluation['checks_total']} (computed only from checks actually run).",
            )
            record.updated_at = _utcnow()
            self._store.save(record)
            return record

    def promote(self, proposal_id: str, *, approve: bool = False, reason: str = "", actor: str = "") -> SkillEvolutionProposal:
        """Policy gate + atomic promotion of a validated candidate.

        Refuses (raising :class:`InvalidTransitionError`) when there is no
        recorded evaluation evidence. With ``auto_promote`` off (the default)
        and no explicit ``approve``, the proposal stays ``validated`` with a
        hold note - nothing is written to the active skill.
        """
        self._require_enabled()
        with self._lock:
            record = self._require_record(proposal_id)
            if record.status == PROMOTED:
                raise InvalidTransitionError(f"Proposal '{record.id}' is already promoted.")
            if record.status != VALIDATED:
                raise InvalidTransitionError(f"Proposal '{record.id}' is '{record.status}', not '{VALIDATED}'; only an evaluated proposal with recorded evidence can be promoted.")
            evaluation = record.evaluation or {}
            if int(evaluation.get("checks_total") or 0) < 1 or evaluation.get("error"):
                raise InvalidTransitionError("No evaluation evidence recorded; refusing to promote without evaluation evidence.")
            now = _utcnow()

            # Moderation gate - honors skill_evolution_config's "block skill
            # writes when moderation is unavailable" fail-closed contract.
            moderation = record.moderation or {"status": "not_run", "model": self._moderation_model_name, "reason": "no moderation result recorded."}
            moderation_status = str(moderation.get("status") or "not_run")
            executable = _is_executable(record.candidate_md)
            if moderation_status == "block":
                record.status = REJECTED
                record.reject_reason = f"moderation blocked the candidate: {moderation.get('reason')}"
                _record_event(record, "rejected", record.reject_reason)
                record.updated_at = _utcnow()
                self._store.save(record)
                return record
            if moderation_status not in {"allow", "warn"}:
                why = f"moderation was not performed (status '{moderation_status}'): {moderation.get('reason')}"
                if self._fail_closed:
                    record.status = REJECTED
                    record.reject_reason = f"{why}; security_fail_closed=true blocks promotion without moderation."
                    _record_event(record, "rejected", record.reject_reason)
                    record.updated_at = _utcnow()
                    self._store.save(record)
                    return record
                if executable:
                    record.status = REJECTED
                    record.reject_reason = f"{why}; security_fail_closed=false still blocks executable candidates without moderation."
                    _record_event(record, "rejected", record.reject_reason)
                    record.updated_at = _utcnow()
                    self._store.save(record)
                    return record
                _note(record, f"{why}; permitted only because security_fail_closed=false and the candidate is non-executable - this is NOT a moderation pass.")
            elif moderation_status == "warn":
                _note(record, f"Moderation returned 'warn' (not a clean pass): {moderation.get('reason')}")

            # Approval gate - auto-promote defaults OFF.
            if not approve and not self._auto_promote:
                hold = "promotion held: auto_promote is disabled and no explicit approval was given"
                _note(record, hold + (f" ({reason})" if reason else "") + ".")
                _record_event(record, "promotion_held", hold)
                record.updated_at = now
                self._store.save(record)
                return record

            # TOCTOU guard: the active baseline must still match the proposal.
            active_path = self._active_path(record.skill_name)
            if not active_path.is_file():
                raise InvalidTransitionError(f"Active {SKILL_MD_FILE} disappeared at {active_path}; refusing to promote over a missing skill.")
            active_md = active_path.read_text(encoding="utf-8")
            if _sha256(active_md) != record.active_sha256:
                raise InvalidTransitionError("Active skill changed since the proposal was created; re-propose and re-evaluate against the current active version.")
            try:
                final_md = _set_version(record.candidate_md, record.candidate_version)
            except ValueError as exc:
                raise InvalidTransitionError(f"Cannot promote: {exc}")

            record.previous_md = active_md
            record.previous_sha256 = record.active_sha256
            record.previous_version = _frontmatter_version(active_md)
            record.promoted_sha256 = _sha256(final_md)
            fd, tmp_name = tempfile.mkstemp(dir=str(active_path.parent), prefix=".skill-evolution-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(final_md)
                os.replace(tmp_name, active_path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

            curator_detail = ""
            try:
                from alpha.skills.curator import SkillCurator

                SkillCurator(skills_root=self._skills_root).pin(record.skill_name)
                curator_detail = ", curator-pinned"
            except Exception as exc:
                _note(record, f"Curator pin was NOT applied ({exc}); lifecycle maintenance may still archive this skill.")
                curator_detail = f", curator pin NOT applied ({exc})"

            record.status = PROMOTED
            record.promoted_at = now
            record.reject_reason = None
            _record_event(
                record,
                "promoted",
                f"v{record.base_version or 'unset'} -> v{record.candidate_version} (validation_kind={evaluation.get('validation_kind')}, moderation={moderation_status}){curator_detail}"
                + (f" by {actor}" if actor else "")
                + (f": {reason}" if reason else ""),
            )
            _note(record, f"Promoted v{record.candidate_version} over v{record.base_version or 'unset'}; prior body retained on this record for rollback.")
            record.updated_at = _utcnow()
            self._store.save(record)
            return record

    def rollback(self, proposal_id: str, *, reason: str = "", actor: str = "") -> SkillEvolutionProposal:
        """Restore the pre-promotion body atomically; refuses to clobber edits."""
        self._require_enabled()
        with self._lock:
            record = self._require_record(proposal_id)
            if record.status != PROMOTED:
                raise InvalidTransitionError(f"Proposal '{record.id}' is '{record.status}'; only a '{PROMOTED}' proposal can be rolled back.")
            if record.previous_md is None:
                raise InvalidTransitionError("No previous version retained for this promotion; refusing to roll back blind.")
            active_path = self._active_path(record.skill_name)
            if not active_path.is_file():
                raise InvalidTransitionError(f"Active {SKILL_MD_FILE} disappeared at {active_path}; refusing to roll back a missing skill.")
            current_md = active_path.read_text(encoding="utf-8")
            if record.promoted_sha256 and _sha256(current_md) != record.promoted_sha256:
                raise InvalidTransitionError("Active skill was modified after promotion; refusing to overwrite the newer change - restore it manually if intended.")
            fd, tmp_name = tempfile.mkstemp(dir=str(active_path.parent), prefix=".skill-evolution-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(record.previous_md)
                os.replace(tmp_name, active_path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            record.status = ROLLED_BACK
            record.rolled_back_at = _utcnow()
            _record_event(
                record,
                "rolled_back",
                f"restored v{record.previous_version or 'unset'} (dropped v{record.candidate_version})"
                + (f" by {actor}" if actor else "")
                + (f": {reason}" if reason else ""),
            )
            _note(record, f"Rolled back to the retained previous body; the promoted v{record.candidate_version} candidate was withdrawn from the active skill.")
            record.updated_at = _utcnow()
            self._store.save(record)
            return record

    def get(self, proposal_id: str) -> SkillEvolutionProposal:
        return self._require_record(proposal_id)

    def list_proposals(self, *, status: str | None = None) -> list[SkillEvolutionProposal]:
        records = self._store.list_all()
        if status is None:
            return records
        if status not in PROPOSAL_STATUSES:
            raise ValueError(f"Unknown skill-evolution status '{status}'; expected one of {', '.join(PROPOSAL_STATUSES)}.")
        return [record for record in records if record.status == status]

    @property
    def store_root(self) -> Path:
        return self._store.root

    # -- validation ---------------------------------------------------------

    def _run_checks(self, record: SkillEvolutionProposal, active_md: str, ran_at: str) -> dict[str, Any]:
        """Structural/AST/consistency checks + optional declared suite (offline)."""
        checks: list[dict[str, Any]] = []

        def add(check_id: str, ok: bool, detail: str) -> None:
            checks.append({"id": check_id, "ok": bool(ok), "detail": detail})

        md = record.candidate_md
        frontmatter_text, body = _split_frontmatter(md)
        add("frontmatter_present", frontmatter_text is not None, "YAML frontmatter block present" if frontmatter_text is not None else "no YAML frontmatter block ('---' ... '---') found")

        meta: dict[str, Any] | None = None
        if frontmatter_text is not None:
            try:
                parsed = yaml.safe_load(frontmatter_text)
                parse_ok = isinstance(parsed, dict)
                if parse_ok:
                    meta = parsed
                    add("frontmatter_parseable", True, "frontmatter parses as a YAML mapping")
                else:
                    add("frontmatter_parseable", False, f"frontmatter parsed as {type(parsed).__name__}, not a YAML mapping")
            except yaml.YAMLError as exc:
                add("frontmatter_parseable", False, f"frontmatter is not valid YAML: {exc}")
        else:
            add("frontmatter_parseable", False, "frontmatter absent, so it cannot be parsed")

        name = meta.get("name") if meta else None
        add(
            "name_matches_target",
            bool(name) and str(name).strip() == record.skill_name,
            f"frontmatter name '{name}' matches target skill '{record.skill_name}'" if bool(name) and str(name).strip() == record.skill_name else f"frontmatter name '{name}' does not match target skill '{record.skill_name}'",
        )
        description = meta.get("description") if meta else None
        add(
            "description_present",
            bool(description) and isinstance(description, str) and bool(description.strip()),
            "description present" if bool(description) and isinstance(description, str) and description.strip() else "frontmatter description missing or empty",
        )
        add("body_nonempty", bool(body.strip()), "markdown body present after frontmatter" if body.strip() else "markdown body is empty after frontmatter")

        version = None
        if meta is not None and meta.get("version") is not None:
            version = str(meta.get("version")).strip()
        version_ok = version == record.candidate_version
        if version_ok:
            version_detail = f"frontmatter version '{version}' == candidate version '{record.candidate_version}'"
        else:
            version_detail = f"frontmatter version '{version}' != candidate version '{record.candidate_version}' (candidate is not stamped as the versioned candidate)"
        add("version_stamped", version_ok, version_detail)

        changed = _content_fingerprint(md) != _content_fingerprint(active_md)
        add(
            "changed_from_active",
            changed,
            "candidate changes content versus the active skill (ignoring version line)" if changed else "candidate is content-identical to the active skill (no change beyond the version line)",
        )
        add(
            "evidence_refs_present",
            bool(record.evidence),
            f"{len(record.evidence)} evidence reference(s) attached" if record.evidence else "no evidence references attached to the proposal",
        )
        within_cap = len(md) <= MAX_CANDIDATE_CHARS
        add(
            "size_within_cap",
            within_cap,
            f"candidate is {len(md)} chars (cap {MAX_CANDIDATE_CHARS})" if within_cap else f"candidate is {len(md)} chars, over the {MAX_CANDIDATE_CHARS} cap",
        )

        python_blocks = _PY_FENCE_RE.findall(md)
        if python_blocks:
            failures: list[str] = []
            for index, block in enumerate(python_blocks, start=1):
                try:
                    ast.parse(block)
                except SyntaxError as exc:
                    failures.append(f"python block {index}: {exc.msg} (line {exc.lineno})")
            add(
                "python_blocks_parse",
                not failures,
                f"{len(python_blocks)} embedded python block(s) parse cleanly" if not failures else f"{len(python_blocks)} embedded python block(s), {len(failures)} fail AST parse: " + "; ".join(failures),
            )
        else:
            add("python_blocks_parse", True, "no embedded python code blocks to parse")

        # Declared suite: run ONLY when the candidate frontmatter declares one.
        command = None
        if meta:
            for key in TEST_COMMAND_KEYS:
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    command = value.strip()
                    break
        suite: dict[str, Any] | None = None
        if command:
            suite = self._run_suite(record, command)
            suite_ok = (not suite["timed_out"]) and suite["exit_code"] == 0
            detail = f"declared test-command {suite['detail']}"
            if not suite_ok and suite.get("stderr_tail"):
                detail += f" | stderr tail: {suite['stderr_tail'][-300:]}"
            add("declared_test_suite", suite_ok, detail)
            validation_kind = VALIDATION_STRUCTURAL_AND_SUITE
        else:
            validation_kind = VALIDATION_STRUCTURAL_ONLY

        checks_total = len(checks)
        checks_passed = sum(1 for check in checks if check["ok"])
        findings = [check["detail"] for check in checks if not check["ok"]]
        return {
            "ran_at": ran_at,
            "error": None,
            "validation_kind": validation_kind,
            "checks": checks,
            "checks_total": checks_total,
            "checks_passed": checks_passed,
            "score": round(checks_passed / checks_total, 4) if checks_total else None,
            "findings": findings,
            "suite": suite,
        }

    def _run_suite(self, record: SkillEvolutionProposal, command: str) -> dict[str, Any]:
        """Execute the declared test suite against a staged candidate copy.

        The skill package is copied to a temp dir, the staged ``SKILL.md`` is
        replaced with the candidate, and the declared command runs there with
        a bounded timeout. ``shell=True`` executes the skill's own declared
        command (opt-in via frontmatter) - callers gate this behind admin.
        """
        skill_dir = self._skill_dir(record.skill_name)
        with tempfile.TemporaryDirectory(prefix="skill-evolution-suite-") as tmp:
            staging = Path(tmp) / record.skill_name
            shutil.copytree(skill_dir, staging, ignore=shutil.ignore_patterns(".archive", ".usage.json", ".curator.json"))
            (staging / SKILL_MD_FILE).write_text(record.candidate_md, encoding="utf-8")
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(staging),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=SUITE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                return {
                    "command": command,
                    "ran_against": "staged-candidate",
                    "timed_out": True,
                    "exit_code": None,
                    "stdout_tail": _tail(getattr(exc, "stdout", None)),
                    "stderr_tail": _tail(getattr(exc, "stderr", None)),
                    "detail": f"timed out after {SUITE_TIMEOUT_SECONDS}s",
                }
            return {
                "command": command,
                "ran_against": "staged-candidate",
                "timed_out": False,
                "exit_code": proc.returncode,
                "stdout_tail": _tail(proc.stdout),
                "stderr_tail": _tail(proc.stderr),
                "detail": f"exit code {proc.returncode}",
            }
