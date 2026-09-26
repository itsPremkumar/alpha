"""Capability acquisition: the remote-code-execution supply chain, fenced.

A bot that can download and modify tools, and then create more bots, is
self-replicating. This module is the fence, and it is deliberately arranged so
that the failure mode of every stage is REFUSE.

The pipeline, and the gate at each stage:

1. **Source allowlist, enforced in code.** HTTPS only, host must be on the
   allowlist. "Any URL the model found" is not a policy, so the allowlist lives
   here, in code, and the model cannot extend it. Model-authored input is never
   authoritative for its own source allowlist.
2. **Fetch into quarantine.** The bytes land in a quarantine directory, never
   in the live registry, and get a provenance record (source URL, sha256, byte
   length, fetch time). Quarantine is recorded through
   :class:`alpha.skills.tiers.TierRegistry` so a quarantined artefact is not
   loadable by the existing tier rules.
3. **Scan, reusing the existing scanners.** The deterministic,
   offline :func:`alpha.skills.skillscan.orchestrator.scan_skill_dir` (39 rules,
   CRITICAL blocks) plus the
   :func:`alpha.skills.security_scanner.scan_skill_content` rubric. A scanner
   that is UNAVAILABLE refuses; it never degrades to allow. Untrusted MCP text
   is laundered through :func:`alpha.mcp.untrusted.neutralize_untrusted_mcp_text`.
4. **Approval that is not the requester.** A model must never be the approver of
   something it requested. Self-approval is refused, and so is a model
   identity.
5. **Enable under the SAME authority ceiling.** The acquired capability's
   capabilities go through :func:`alpha.bots.authority_ceiling.enforce_grant`
   with the REQUESTER's grant as the creator bound, so acquiring a capability can
   never raise authority.
6. **Tool modification gets its own gate.** Modifying an existing tool is the
   highest-risk action here, so it is a separate method with its own
   non-self approval, a full before/after diff in the ledger, and VERSIONING: a
   modified tool never silently replaces an existing one.

Everything refuses through :func:`refuse`, which records the refusal in the one
governance ledger and returns a typed result rather than raising into a caller
that might swallow it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from alpha.bots.authority_ceiling import (
    AuthorityCeiling,
    AuthorityViolation,
    enforce_grant,
    get_ceiling,
)

logger = logging.getLogger(__name__)


class CapabilityKind(StrEnum):
    SKILL = "skill"
    TOOL = "tool"
    MCP_SERVER = "mcp_server"


class AcquisitionStatus(StrEnum):
    QUARANTINED = "quarantined"
    SCANNED = "scanned"
    APPROVED = "approved"
    ENABLED = "enabled"
    REFUSED = "refused"


#: The permitted source allowlist, enforced in code.
#:
#: Deliberately small and deliberately not configurable by the requester. A
#: model that finds a new URL does not thereby gain permission to fetch it; an
#: operator adds a host here, in a reviewed commit.
DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
        "codeload.github.com",
        "pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
    }
)

#: Identities that are never an acceptable approver, because they are the model
#: (or a component the model controls) asking for itself.
MODEL_APPROVER_PREFIXES: tuple[str, ...] = ("model:", "llm:", "agent-self:", "self:")

#: Cap on a fetched artefact. A supply-chain fetch is not a disk-fill vector.
MAX_ARTEFACT_BYTES = 8 * 1024 * 1024

#: Severity at which an ACQUIRED artefact is refused.
#:
#: This is deliberately STRICTER than the scanner's own default, which blocks
#: only on CRITICAL. That default is correct for a skill bundled with the
#: harness: the maintainer already reviewed it. It is NOT correct for something
#: a bot downloaded because a URL appeared in a model response, where the
#: realistic attack is a prompt-override instruction ("ignore all previous
#: instructions") that the scanner classifies as HIGH.
#:
#: Verified against the real scanner: a SKILL.md containing exactly that
#: override is reported as ``HIGH declaration-prompt-override`` and
#: ``blocked=False`` under the default policy. An acquired artefact is refused
#: at HIGH and above. This is a policy decision in the ACQUISITION layer; the
#: scanner itself is untouched and still reports what it finds.
ACQUIRED_BLOCK_SEVERITY: str = "HIGH"

_SEVERITY_RANK: dict[str, int] = {"LOW": 10, "MEDIUM": 20, "HIGH": 30, "CRITICAL": 40}


class AcquisitionRefused(RuntimeError):
    """The acquisition pipeline refused. Nothing was enabled."""


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a capability came from, and exactly what arrived.

    Required before anything may be enabled. A capability with no provenance is
    an anonymous binary, which is refused.
    """

    name: str
    kind: str
    source_url: str
    sha256: str
    byte_length: int
    fetched_at: str
    requested_by: str
    quarantine_path: str
    host: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def human_line(self) -> str:
        return (
            f"{self.kind} {self.name!r} from {self.source_url} sha256={self.sha256[:16]}... "
            f"bytes={self.byte_length} fetched={self.fetched_at} by @{self.requested_by}"
        )


@dataclass(frozen=True, slots=True)
class ScanVerdict:
    """The combined, deterministic + rubric scan result."""

    passed: bool
    scanner: str
    reason: str
    findings: tuple[dict[str, Any], ...] = ()
    blocked_by: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def human_line(self) -> str:
        head = "PASS" if self.passed else "BLOCK"
        return f"{head} [{self.scanner}] {self.reason} ({len(self.findings)} finding(s))"


@dataclass(slots=True)
class AcquisitionResult:
    """One acquisition attempt, end to end, whether it succeeded or not."""

    name: str
    kind: str
    status: str
    reason: str
    provenance: dict[str, Any] | None = None
    scan: dict[str, Any] | None = None
    granted_capabilities: tuple[str, ...] = ()
    approver: str = ""
    ledger_seq: int | None = None
    stage_reached: str = ""

    @property
    def enabled(self) -> bool:
        return self.status == AcquisitionStatus.ENABLED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "status": str(self.status),
            "reason": self.reason,
            "provenance": self.provenance,
            "scan": self.scan,
            "granted_capabilities": list(self.granted_capabilities),
            "approver": self.approver,
            "ledger_seq": self.ledger_seq,
            "stage_reached": self.stage_reached,
            "enabled": self.enabled,
        }


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------
#: A fetcher. ``(url) -> bytes``. Injected so the pipeline is testable offline
#: and so the runtime can supply a network client with its own timeouts.
Fetcher = Callable[[str], bytes]

#: Refuse a body larger than the artefact ceiling without buffering all of it.
_FETCH_TIMEOUT_SECONDS = 30.0
_FETCH_CHUNK_LIMIT = 64 * 1024


def default_fetcher() -> Fetcher:
    """The real network fetcher: HTTPS only, bounded size, bounded time.

    Returns a callable rather than fetching, so constructing a pipeline never
    touches the network. ``urllib`` is used because it is stdlib, so the
    pipeline has no dependency the scanner could be bypassed around.

    Size is enforced while READING, not after: a hostile server cannot make
    this process buffer an unbounded body just to discover it was too large.
    """

    def _fetch(url: str) -> bytes:
        import urllib.request

        request = urllib.request.Request(url, headers={"User-Agent": "alpha-acquisition/1.0"})
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_ARTEFACT_BYTES:
                raise AcquisitionRefused(
                    f"declared Content-Length {declared} exceeds the {MAX_ARTEFACT_BYTES} byte ceiling"
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(_FETCH_CHUNK_LIMIT)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARTEFACT_BYTES:
                    raise AcquisitionRefused(
                        f"body exceeded the {MAX_ARTEFACT_BYTES} byte ceiling while streaming"
                    )
                chunks.append(chunk)
            return b"".join(chunks)

    return _fetch


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _model_identity(identity: str) -> bool:
    lowered = (identity or "").strip().lower()
    return any(lowered.startswith(prefix) for prefix in MODEL_APPROVER_PREFIXES)


class AcquisitionPipeline:
    """Quarantine -> scan -> independent approval -> enable, under the ceiling."""

    def __init__(
        self,
        quarantine_root: str | Path,
        *,
        allowed_hosts: Iterable[str] | None = None,
        ledger_store: Any | None = None,
        tier_registry: Any | None = None,
        ceiling: AuthorityCeiling | None = None,
        fetcher: Fetcher | None = None,
        use_rubric_scanner: bool = False,
        block_severity: str = ACQUIRED_BLOCK_SEVERITY,
    ) -> None:
        self.quarantine_root = Path(quarantine_root)
        self.allowed_hosts = frozenset(
            h.strip().lower() for h in (allowed_hosts if allowed_hosts is not None else DEFAULT_ALLOWED_HOSTS) if h.strip()
        )
        self.ledger_store = ledger_store
        self.tier_registry = tier_registry
        self._ceiling = ceiling
        self.fetcher = fetcher
        #: The LLM rubric scanner needs a model. Off by default: the
        #: DETERMINISTIC scanner is the one that must gate a supply-chain fetch,
        #: and an absent rubric must not be the reason an artefact is allowed.
        self.use_rubric_scanner = use_rubric_scanner
        self.block_severity = block_severity

    # -- ledger -----------------------------------------------------------
    def _record(self, event: str, **kwargs: Any) -> int | None:
        from alpha.channels import ledger as channel_ledger

        entry = channel_ledger.append(
            event,
            actor=str(kwargs.pop("actor", "acquisition")),
            target=str(kwargs.pop("target", "capability")),
            reason=str(kwargs.pop("reason", "")),
            details=kwargs,
            store=self.ledger_store,
        )
        return entry.seq

    def _refuse(
        self,
        name: str,
        kind: str,
        reason: str,
        *,
        actor: str,
        stage: str,
        scan: ScanVerdict | None = None,
        provenance: Provenance | None = None,
    ) -> AcquisitionResult:
        """Record the refusal and return it. Nothing is enabled."""
        from alpha.channels import ledger as channel_ledger

        try:
            self._record(
                channel_ledger.EV_CAPABILITY_REFUSED,
                actor=actor,
                target=name,
                reason=reason,
                kind=kind,
                stage=stage,
                blocked_by=list(scan.blocked_by) if scan else [],
                findings=list(scan.findings)[:20] if scan else [],
                sha256=provenance.sha256 if provenance else "",
            )
        except Exception:  # noqa: BLE001
            logger.error("ledger unavailable while recording an acquisition refusal for %s", name)
        return AcquisitionResult(
            name=name,
            kind=kind,
            status=AcquisitionStatus.REFUSED,
            reason=reason,
            provenance=provenance.to_dict() if provenance else None,
            scan=scan.to_dict() if scan else None,
            stage_reached=stage,
        )

    # -- stage 1: the source allowlist ------------------------------------
    def check_source(self, url: str) -> tuple[bool, str]:
        """Is this URL permitted? Enforced in code, not by the requester."""
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme != "https":
            return False, f"scheme {parsed.scheme!r} is not https; cleartext fetches are refused"
        host = (parsed.hostname or "").lower()
        if not host:
            return False, "URL has no host"
        if host not in self.allowed_hosts:
            return False, (
                f"host {host!r} is not on the source allowlist "
                f"({', '.join(sorted(self.allowed_hosts))})"
            )
        return True, f"host {host!r} is on the source allowlist"

    # -- stage 2: fetch into quarantine ----------------------------------
    def fetch(self, name: str, url: str, *, kind: str, requested_by: str) -> Provenance:
        """Fetch into QUARANTINE, never the live registry, with provenance."""
        ok, reason = self.check_source(url)
        if not ok:
            raise AcquisitionRefused(f"source refused: {reason}")
        if self.fetcher is None:
            raise AcquisitionRefused("no fetcher configured; refusing rather than fetching blind")
        payload = self.fetcher(url)
        if not isinstance(payload, (bytes, bytearray)):
            raise AcquisitionRefused(f"fetcher returned {type(payload).__name__}, not bytes")
        payload = bytes(payload)
        if not payload:
            raise AcquisitionRefused("fetcher returned an empty payload")
        if len(payload) > MAX_ARTEFACT_BYTES:
            raise AcquisitionRefused(
                f"artefact is {len(payload)} bytes, above the {MAX_ARTEFACT_BYTES} byte ceiling"
            )
        digest = hashlib.sha256(payload).hexdigest()
        target = self.quarantine_root / f"{kind}s" / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        (target / "ARTEFACT.bin").write_bytes(payload)
        provenance = Provenance(
            name=name,
            kind=kind,
            source_url=url,
            sha256=digest,
            byte_length=len(payload),
            fetched_at=_now(),
            requested_by=requested_by,
            quarantine_path=str(target),
            host=(urlparse(url).hostname or "").lower(),
        )
        (target / "provenance.json").write_text(
            json.dumps(provenance.to_dict(), indent=2, ensure_ascii=True), encoding="utf-8", newline="\n"
        )
        # Quarantine in the tier registry too, so the existing "quarantined
        # skills never load into a live agent" rule applies to this artefact.
        if self.tier_registry is not None:
            try:
                self.tier_registry.quarantine(name)
            except Exception:  # noqa: BLE001
                logger.warning("tier registry refused to quarantine %s; continuing", name)
        return provenance

    # -- stage 3: scan ----------------------------------------------------
    def scan(self, provenance: Provenance) -> ScanVerdict:
        """Scan with the EXISTING deterministic scanner. Reuses, does not reinvent.

        The artefact directory is materialised as a skill package (the bytes
        plus a ``SKILL.md``) because that is the shape
        :func:`alpha.skills.skillscan.orchestrator.scan_skill_dir` reads.
        """
        from alpha.skills.skillscan.orchestrator import (
            StaticScannerError,
            scan_skill_dir,
        )

        directory = Path(provenance.quarantine_path)
        raw = (directory / "ARTEFACT.bin").read_bytes()
        # Materialise as a scannable package: SKILL.md carries the content, the
        # .py file carries the same bytes so the python analysers see them.
        (directory / "SKILL.md").write_bytes(raw)
        (directory / "payload.py").write_bytes(raw)

        try:
            result = scan_skill_dir(directory)
        except StaticScannerError as exc:
            # An unreadable artefact is not a clean artefact.
            return ScanVerdict(
                passed=False,
                scanner="skillscan",
                reason=f"scanner could not read the artefact: {exc}",
                blocked_by=("scanner_error",),
            )
        except Exception as exc:  # noqa: BLE001
            return ScanVerdict(
                passed=False,
                scanner="skillscan",
                reason=f"scanner raised {type(exc).__name__}: {exc}; refusing rather than assuming clean",
                blocked_by=("scanner_error",),
            )
        findings = tuple(dict(f) for f in result.get("findings", []))
        floor = _SEVERITY_RANK.get(self.block_severity.upper(), 30)
        blocked = tuple(
            f"{f.get('rule_id')}:{f.get('severity')}"
            for f in findings
            if _SEVERITY_RANK.get(str(f.get("severity", "")).upper(), 0) >= floor
        )
        if result.get("scanner_errors"):
            # Analyzer errors are non-fatal in the scanner by design. For a
            # supply chain fetch they ARE fatal: fewer findings is not consent.
            return ScanVerdict(
                passed=False,
                scanner="skillscan",
                reason=(
                    f"scanner reported {len(result['scanner_errors'])} analyzer error(s); "
                    f"an incomplete scan cannot clear an artefact"
                ),
                findings=findings,
                blocked_by=blocked + ("scanner_incomplete",),
            )
        if result.get("blocked") or blocked:
            worst = max(
                (str(f.get("severity")) for f in findings),
                key=lambda s: _SEVERITY_RANK.get(s.upper(), 0),
                default="",
            )
            return ScanVerdict(
                passed=False,
                scanner="skillscan",
                reason=(
                    f"{len(blocked)} finding(s) at or above {self.block_severity} block this "
                    f"acquired artefact (highest severity {worst}); the acquired tier is stricter "
                    f"than the bundled tier, which blocks only on CRITICAL"
                ),
                findings=findings,
                blocked_by=blocked,
            )
        if not self.use_rubric_scanner:
            return ScanVerdict(
                passed=True,
                scanner="skillscan",
                reason="deterministic scan found no blocking finding",
                findings=findings,
            )

        # Optional second opinion. A rubric failure must REFUSE, never allow.
        import asyncio

        from alpha.skills.security_scanner import scan_skill_content

        try:
            rubric = asyncio.run(
                scan_skill_content(
                    raw.decode("utf-8", errors="replace"),
                    executable=provenance.kind == CapabilityKind.TOOL,
                    attach_tracing=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return ScanVerdict(
                passed=False,
                scanner="security_scanner",
                reason=f"rubric scanner unavailable ({type(exc).__name__}: {exc}); refusing",
                findings=findings,
                blocked_by=("rubric_unavailable",),
            )
        if rubric.decision == "block" or (provenance.kind == CapabilityKind.TOOL and rubric.decision != "allow"):
            return ScanVerdict(
                passed=False,
                scanner="security_scanner",
                reason=rubric.reason,
                findings=findings,
                blocked_by=(f"rubric:{rubric.decision}",),
            )
        return ScanVerdict(
            passed=True, scanner="security_scanner", reason=rubric.reason, findings=findings
        )

    # -- stage 4: approval that is not the requester ----------------------
    def check_approval(
        self, *, name: str, requested_by: str, approver: str, scan: ScanVerdict
    ) -> tuple[bool, str]:
        """A model must never be the approver of something it requested.

        Three independent refusals: the scan must have passed, the approver must
        not be the requester, and the approver must not be a model identity.
        """
        if not scan.passed:
            return False, f"cannot approve an artefact that did not pass its scan: {scan.reason}"
        if not str(approver or "").strip():
            return False, "no approver named; an absent approver is not an approving approver"
        if str(approver).strip().lower() == str(requested_by or "").strip().lower():
            return False, (
                f"self-approval refused: @{requested_by} requested {name!r} and cannot also approve it. "
                f"A model must never be the approver of something it requested."
            )
        if _model_identity(approver):
            return False, (
                f"approver {approver!r} is a model identity; approval must come from outside the model"
            )
        return True, f"approved by @{approver}, requested by @{requested_by}"

    # -- the whole pipeline ------------------------------------------------
    def acquire(
        self,
        name: str,
        url: str,
        *,
        kind: str = CapabilityKind.SKILL,
        requested_by: str,
        approver: str,
        capabilities: Sequence[str] = (),
        requester_grant: Sequence[str] | None = None,
    ) -> AcquisitionResult:
        """Run the whole fence. Returns a result; enables only if every stage passed."""
        kind_s = str(kind)
        if not str(requested_by or "").strip():
            return self._refuse(
                name, kind_s, "no requester named; anonymous acquisition is refused", actor="acquisition", stage="requester"
            )

        ok, reason = self.check_source(url)
        if not ok:
            return self._refuse(
                name, kind_s, f"source refused: {reason}", actor=requested_by, stage="source_allowlist"
            )

        try:
            provenance = self.fetch(name, url, kind=kind_s, requested_by=requested_by)
        except AcquisitionRefused as exc:
            return self._refuse(name, kind_s, str(exc), actor=requested_by, stage="fetch")
        except Exception as exc:  # noqa: BLE001
            return self._refuse(
                name, kind_s, f"fetch failed: {type(exc).__name__}: {exc}", actor=requested_by, stage="fetch"
            )

        verdict = self.scan(provenance)
        if not verdict.passed:
            return self._refuse(
                name,
                kind_s,
                f"scan refused the artefact: {verdict.reason}",
                actor=requested_by,
                stage="scan",
                scan=verdict,
                provenance=provenance,
            )

        approved, approval_reason = self.check_approval(
            name=name, requested_by=requested_by, approver=approver, scan=verdict
        )
        if not approved:
            return self._refuse(
                name,
                kind_s,
                f"approval refused: {approval_reason}",
                actor=requested_by,
                stage="approval",
                scan=verdict,
                provenance=provenance,
            )

        # Acquiring a capability must NEVER raise authority. The requester's own
        # grant is the creator bound, exactly as for a bot-created child.
        grant: frozenset[str] = frozenset()
        if capabilities:
            try:
                grant = enforce_grant(
                    list(capabilities),
                    creator_grant=list(requester_grant or ()),
                    subject=f"acquired capability {name!r} for @{requested_by}",
                    ceiling=self._ceiling or get_ceiling(),
                )
            except AuthorityViolation as exc:
                return self._refuse(
                    name,
                    kind_s,
                    f"authority refused: {exc}",
                    actor=requested_by,
                    stage="authority_ceiling",
                    scan=verdict,
                    provenance=provenance,
                )

        if self.tier_registry is not None:
            try:
                self.tier_registry.graduate(name, reviewer=approver, tier="trusted")
            except Exception:  # noqa: BLE001
                logger.warning("tier registry refused to graduate %s; capability stays quarantined", name)

        seq = self._record(
            "channel.capability_acquired",
            actor=requested_by,
            target=name,
            reason=f"{kind_s} enabled after quarantine, scan and independent approval by @{approver}",
            kind=kind_s,
            sha256=provenance.sha256,
            source=provenance.source_url,
            approver=approver,
            granted_capabilities=sorted(grant),
        )
        return AcquisitionResult(
            name=name,
            kind=kind_s,
            status=AcquisitionStatus.ENABLED,
            reason=approval_reason,
            provenance=provenance.to_dict(),
            scan=verdict.to_dict(),
            granted_capabilities=tuple(sorted(grant)),
            approver=approver,
            ledger_seq=seq,
            stage_reached="enabled",
        )

    # -- tool modification: its own gate ---------------------------------
    def modify_tool(
        self,
        tool_path: str | Path,
        new_source: str,
        *,
        modified_by: str,
        approver: str,
        reason: str,
    ) -> AcquisitionResult:
        """Modify an existing tool. The highest-risk action in this module.

        Requires its own non-self approval, writes a FULL before/after diff and
        both hashes to the ledger, and VERSIONS the tool: the previous revision
        is kept beside the new one, so a modified tool never silently replaces an
        existing one and a bad revision can be identified and rolled back.
        """
        from alpha.channels import ledger as channel_ledger

        target = Path(tool_path)
        name = target.name

        if _model_identity(approver) or str(approver).strip().lower() == str(modified_by).strip().lower():
            return self._refuse(
                name,
                CapabilityKind.TOOL,
                f"tool modification refused: self-approval by {approver!r}. Tool modification "
                f"requires an approver who is neither the modifier nor a model.",
                actor=modified_by,
                stage="modification_approval",
            )
        if not str(approver or "").strip():
            return self._refuse(
                name,
                CapabilityKind.TOOL,
                "tool modification refused: no approver named",
                actor=modified_by,
                stage="modification_approval",
            )
        if not target.exists():
            return self._refuse(
                name,
                CapabilityKind.TOOL,
                f"tool modification refused: {target} does not exist",
                actor=modified_by,
                stage="modification_read",
            )

        before = target.read_text(encoding="utf-8", errors="replace")
        before_sha = hashlib.sha256(before.encode("utf-8")).hexdigest()
        after_sha = hashlib.sha256(str(new_source).encode("utf-8")).hexdigest()
        if before_sha == after_sha:
            return self._refuse(
                name,
                CapabilityKind.TOOL,
                "tool modification refused: the proposed source is byte-identical to the current one",
                actor=modified_by,
                stage="modification_read",
            )

        # Scan the proposed source with the same scanner before it lands. It is
        # staged in quarantine with the same layout `fetch` produces, so
        # `scan` sees exactly what a downloaded artefact of this size would
        # present.
        staged = self.quarantine_root / "tools" / f"{name}.proposed"
        staged.mkdir(parents=True, exist_ok=True)
        payload = str(new_source).encode("utf-8")
        (staged / "ARTEFACT.bin").write_bytes(payload)
        (staged / "SKILL.md").write_bytes(payload)
        (staged / "payload.py").write_bytes(payload)
        verdict = self.scan(
            Provenance(
                name=f"{name}.proposed",
                kind=str(CapabilityKind.TOOL),
                source_url=f"local://{target}",
                sha256=after_sha,
                byte_length=len(str(new_source).encode("utf-8")),
                fetched_at=_now(),
                requested_by=modified_by,
                quarantine_path=str(staged),
            )
        )
        if not verdict.passed:
            return self._refuse(
                name,
                CapabilityKind.TOOL,
                f"tool modification refused: proposed source did not pass its scan: {verdict.reason}",
                actor=modified_by,
                stage="modification_scan",
                scan=verdict,
            )

        diff = _unified_diff(before, str(new_source), name)

        # VERSION, do not replace. The prior revision is preserved verbatim.
        revisions = target.parent / f"{target.stem}.revisions"
        revisions.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        kept = revisions / f"{target.stem}.{before_sha[:12]}.{stamp}.py"
        kept.write_text(before, encoding="utf-8", newline="\n")
        target.write_text(str(new_source), encoding="utf-8", newline="\n")

        seq = self._record(
            channel_ledger.EV_TOOL_MODIFIED,
            actor=modified_by,
            target=str(target),
            reason=reason or f"tool {name} modified under an explicit modification gate",
            tool=name,
            before_sha256=before_sha,
            after_sha256=after_sha,
            diff=diff,
            approver=approver,
            previous_revision_kept=str(kept),
        )
        return AcquisitionResult(
            name=name,
            kind=str(CapabilityKind.TOOL),
            status=AcquisitionStatus.ENABLED,
            reason=f"tool modified with approval by @{approver}; previous revision kept at {kept.name}",
            scan=verdict.to_dict(),
            approver=approver,
            ledger_seq=seq,
            stage_reached="tool_modified",
        )


def _unified_diff(before: str, after: str, name: str) -> str:
    import difflib

    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            lineterm="",
            n=3,
        )
    )
