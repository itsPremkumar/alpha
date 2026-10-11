"""Evidence: what a check actually observed, and whether it still speaks.

Evidence is first class — a structured record with a digest of the bytes that
were matched — because a chat line cannot be re-checked and a worker's summary
is a *claim*, not an observation.

Two independent digests carry the freshness model:

* **Definition digest** — gate kind, ``check_ref``, check implementation
  version, canonical parameters, expectation, working directory and the policy
  id. Re-parameterising a gate, bumping the check implementation or changing
  policy invalidates prior evidence *and* prior approvals, because that
  evidence no longer answers the question now being asked.
* **Input fingerprints** — the declared inputs' SHA-256 at run time. A test
  that passed at 10:00 over code edited at 10:02 is **stale**, and this module
  is what notices.

What a definition digest deliberately does *not* claim: it does not prove that
every transitive dependency is unchanged. Hashing a command string is not proof
the code it executes is identical — that is why a high-assurance finalization
reruns mandatory gates rather than trusting the digest (``finalization.py``).

Staleness is never deleted: old evidence is retained with
``freshness=stale`` and an ``invalidation_reason``, so the history of what was
measured survives the invalidation.
"""

from __future__ import annotations

import hashlib
import time

from .contract import digest_of
from .gates import CHECK_VERSION, CheckResult
from .models import CheckOutcome, CompletionScope, Evidence, Freshness, Gate, GateStatus
from .policy import CompletionSettings

#: Evidence schema version. Bumping it makes older records ``unknown`` rather
#: than silently reinterpreted under new field semantics.
EVIDENCE_SCHEMA_VERSION = 1


def definition_digest(gate: Gate, settings: CompletionSettings, *, check_version: str = CHECK_VERSION) -> str:
    """Digest over everything that makes *this* gate's result mean *this*.

    Bindings, per the plan's freshness model: gate kind and check
    implementation version, canonical parameters and expectation, working
    directory, policy id, and the task/contract revision the gate was created
    from. Not bound: timestamps, status, evidence ids — reading a gate must
    not change its identity.
    """
    return digest_of(
        {
            "schema": EVIDENCE_SCHEMA_VERSION,
            "kind": gate.kind.value,
            "check_ref": gate.check_ref,
            "check_version": check_version,
            "parameters": gate.parameters,
            "expectation": gate.expectation.model_dump(mode="json"),
            "required": gate.required,
            "integration": gate.integration,
            "policy": settings.policy_id,
            "workspace": str(settings.workspace_root),
            "request_revision": gate.created_from_request_revision,
        }
    )


def input_fingerprints(gate: Gate, settings: CompletionSettings) -> dict[str, str]:
    """SHA-256 of each declared input file, at run time.

    Empty when the check declares no path inputs (a command's inputs are not
    knowable from its argv — hashing the command string would be a claim we
    cannot support). Empty means ``freshness_basis`` stays
    ``definition_only``, which is disclosed rather than assumed fresh.
    """
    fingerprints: dict[str, str] = {}
    relative = gate.parameters.get("path")
    candidates: list[str] = []
    if isinstance(relative, str) and relative:
        candidates.append(relative)
    for extra in gate.parameters.get("inputs", []) if isinstance(gate.parameters.get("inputs", []), list) else []:
        if isinstance(extra, str) and extra:
            candidates.append(extra)
    for candidate in candidates:
        try:
            resolved = settings.resolve(candidate)
        except Exception:  # noqa: BLE001 - an unresolvable input is simply not fingerprinted
            continue
        try:
            if resolved.is_file():
                fingerprints[candidate] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError:
            continue
    return fingerprints


def record_evidence(
    scope: CompletionScope,
    gate: Gate,
    result: CheckResult,
    settings: CompletionSettings,
    *,
    now: float | None = None,
    worker: str = "",
    check_version: str = CHECK_VERSION,
) -> Evidence:
    """Persist one check execution and link it to its gate.

    ``NOT_RUN``/``INVALID_CONFIGURATION`` results are recorded too: "the check
    never ran" is a fact the finalizer needs, and dropping it would leave the
    gate looking merely untouched.
    """
    moment = time.time() if now is None else now
    scope.counters["evidence"] = int(scope.counters.get("evidence", 0)) + 1
    evidence_id = f"ev-{gate.gate_id}-{scope.counters['evidence']}"
    fingerprints = input_fingerprints(gate, settings)
    preview = result.output or ""
    evidence = Evidence(
        evidence_id=evidence_id,
        scope_id=scope.scope_id,
        gate_id=gate.gate_id,
        task_id=gate.task_id,
        check_ref=gate.check_ref,
        check_version=check_version,
        outcome=result.outcome,
        reason=result.reason,
        summary=_summary(result),
        exit_code=result.exit_code,
        output_digest=hashlib.sha256((result.output or "").encode("utf-8")).hexdigest(),
        output_preview=_strip(preview),
        output_truncated="earlier bytes omitted" in (result.output or ""),
        artifacts=list(result.artifacts),
        input_fingerprints=fingerprints,
        freshness_basis="definition+inputs" if fingerprints else "definition_only",
        definition_digest=definition_digest(gate, settings, check_version=check_version),
        started_at=moment,
        ended_at=moment,
        duration_seconds=float(result.context.get("duration_seconds", 0.0) or 0.0),
        context=_safe_context(result.context),
        worker=worker,
        freshness=Freshness.CURRENT,
        created_at=moment,
    )
    scope.evidence[evidence.evidence_id] = evidence
    if evidence.evidence_id not in gate.evidence_ids:
        gate.evidence_ids.append(evidence.evidence_id)
    gate.attempts += 1
    gate.updated_at = moment
    gate.definition_digest = evidence.definition_digest
    return evidence


def _summary(result: CheckResult) -> str:
    text = result.reason or ("passed" if result.passed else "")
    return text[:4000]


def _strip(text: str) -> str:
    """Bounded, control-character-stripped preview of untrusted output."""
    from .gates import strip_control, tail

    return strip_control(tail(text))[:4000]


def _safe_context(context: dict) -> dict:
    """Keep only non-secret execution identity. Never the environment."""
    allowed = {"argv", "cwd", "duration_seconds", "python", "url", "method", "status_code", "path", "bytes", "format"}
    return {key: value for key, value in context.items() if key in allowed}


def latest_evidence(scope: CompletionScope, gate: Gate) -> Evidence | None:
    """The most recent evidence recorded for ``gate`` (newest id last)."""
    for evidence_id in reversed(gate.evidence_ids):
        evidence = scope.evidence.get(evidence_id)
        if evidence is not None:
            return evidence
    return None


def evaluate_freshness(evidence: Evidence, gate: Gate, settings: CompletionSettings, *, now: float) -> tuple[Freshness, str]:
    """Decide whether ``evidence`` may still answer for ``gate``.

    Returns ``(freshness, reason)``; ``CURRENT`` always carries an empty
    reason. A record whose *definition* no longer matches is ``STALE``; one
    whose inputs we cannot tie to it with adequate confidence is ``UNKNOWN``
    — "we do not know" is never folded into "still true".
    """
    current = definition_digest(gate, settings, check_version=evidence.check_version)
    if evidence.definition_digest and evidence.definition_digest != current:
        return Freshness.STALE, "the gate definition changed after this evidence was recorded (definition digest mismatch)"
    if evidence.check_version != CHECK_VERSION:
        return Freshness.STALE, f"check implementation changed ({evidence.check_version} -> {CHECK_VERSION})"
    if evidence.output_truncated:
        return Freshness.STALE, "the captured output exceeded the ceiling, so the result was never fully observed"
    if evidence.outcome in {CheckOutcome.NOT_RUN, CheckOutcome.INVALID_CONFIGURATION}:
        return Freshness.UNKNOWN, f"the check did not produce a result ({evidence.outcome.value})"
    if evidence.freshness_basis == "definition+inputs":
        live = input_fingerprints(gate, settings)
        for path, digest in evidence.input_fingerprints.items():
            current_digest = live.get(path)
            if current_digest is None:
                return Freshness.STALE, f"declared input {path!r} is gone, so this evidence covers nothing"
            if current_digest != digest:
                return Freshness.STALE, f"declared input {path!r} changed after this evidence was recorded"
        missing = [path for path in live if path not in evidence.input_fingerprints]
        if missing:
            return Freshness.UNKNOWN, f"inputs {missing[:3]} were not fingerprinted when the check ran"
    return Freshness.CURRENT, ""


def refresh_freshness(scope: CompletionScope, settings: CompletionSettings, *, now: float | None = None) -> list[str]:
    """Re-evaluate every evidence record and return the ids that went stale.

    Stale evidence never leaves the store: history is preserved with its
    invalidation reason, and the *gate* is what gets returned to
    ``pending``/``stale`` so it must be re-run.
    """
    moment = time.time() if now is None else now
    changed: list[str] = []
    for gate in scope.gates.values():
        for evidence_id in list(gate.evidence_ids):
            evidence = scope.evidence.get(evidence_id)
            if evidence is None:
                continue
            freshness, reason = evaluate_freshness(evidence, gate, settings, now=moment)
            if evidence.freshness is not freshness:
                evidence.freshness = freshness
                evidence.invalidation_reason = reason
                changed.append(evidence.evidence_id)
            elif freshness is Freshness.STALE and not evidence.invalidation_reason:
                evidence.invalidation_reason = reason
    # A gate with no current passing evidence cannot keep a green status.
    for gate in scope.gates.values():
        if gate.status in {GateStatus.PASSED, GateStatus.STALE} and not current_passing(scope, gate, settings):
            if gate.status is GateStatus.PASSED:
                gate.status = GateStatus.STALE
                gate.updated_at = moment
    return changed


def current_passing(scope: CompletionScope, gate: Gate, settings: CompletionSettings, *, now: float | None = None) -> Evidence | None:
    """The latest *current* passing evidence, or ``None``.

    This is the only function allowed to answer "is this gate proven right
    now?" — finalization asks it for every required gate.
    """
    moment = time.time() if now is None else now
    for evidence_id in reversed(gate.evidence_ids):
        evidence = scope.evidence.get(evidence_id)
        if evidence is None or evidence.outcome is not CheckOutcome.PASSED:
            continue
        freshness, _ = evaluate_freshness(evidence, gate, settings, now=moment)
        if freshness is Freshness.CURRENT:
            return evidence
    return None


__all__ = [
    "CHECK_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "current_passing",
    "definition_digest",
    "evaluate_freshness",
    "input_fingerprints",
    "latest_evidence",
    "record_evidence",
    "refresh_freshness",
]
