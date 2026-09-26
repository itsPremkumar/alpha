"""RSI promotion evidence bundle (plan WP-C2/C2a, features #10/#11/#15/#4 family).

Implements the *evidence bundle* component of plan
``references/ALPHA_RSI_IMPLEMENTATION_PLAN.md`` §3 WP-C2 (design bullet for
``backend/packages/harness/alpha/rsi/evidence_bundle.py``) and nothing else
in that work package: review verdicts are WP-C2b, the promotion decision and
router flip are WP-C2c, shadow comparison records are WP-C1.

Purpose: a durable, hash-provenanced directory per candidate that stores the
nine plan-named evidence files **verbatim** and can prove later — byte for
byte — that what is on disk today is what was recorded at finalization
(spec §31–§32 provenance: sha256 per file in ``bundle_index.json``).

Honesty contract (plan §5.6, binding):

- ``evidence_kind`` comes only from the §5.6 whitelist
  ``{measured, simulated, heuristic, unverified}``, imported from
  :mod:`alpha.rsi.lineage` (one whitelist, validated at write) and asserted
  against the literal set in ``tests/test_rsi_evidence_bundle.py``.
- ``add()`` refuses ``None`` payloads and non-object/array payloads. A
  payload that does not declare an ``evidence_kind`` is itemized as
  ``unverified`` (``kind_source="absent_default"``) — never ``measured``.
  Payload bytes are written verbatim: the bundle adds no keys and invents
  no numbers (no score, no confidence, no pass rate, ever).
- :meth:`Bundle.meets_evidence_standard` gates the item *kind*: only
  ``measured`` evidence can satisfy a pass condition; ``simulated`` (never
  gates, §5.6), ``heuristic``, and ``unverified`` items — and items missing
  from the bundle — fail with the real reason. Kind eligibility is
  necessary, not sufficient: pass/fail verdicts belong to the source
  module's own gate (e.g. :func:`alpha.rsi.holdout.holdout_gate`),
  composed later by WP-C2c — this bundle never evaluates candidates.
- Missing/failed upstream sources travel as :func:`unavailable_source` /
  :func:`failed_source` markers carrying the real error text — never a
  default success, never silence.
- Shadow evidence (WP-C1, landing concurrently) is integrated duck-typed and
  optional: when ``alpha.rsi.shadow`` is not importable, or no on-disk
  record names this candidate, the result is an honest ``unavailable``
  marker with the real reason; an import/scan/label error is ``failed``
  with the real exception text (workspace ``_protected_paths_api`` seam
  precedent). These paths never raise for an upstream gap.
- Persistence is atomic (``alpha.evolution.identity.atomic_write_json``:
  unique tmp + ``os.replace``); finalization appends exactly ONE ledger
  event to the append-only ``runtime_home()/rsi/bundles/bundles_ledger.jsonl``
  (lineage JSONL precedent) and degrades to ``persistence="degraded"`` with
  the real logged error if only that append fails — while a failed index
  write raises the real OS error (the bundle never returns a path it did
  not write).
- Finalization is terminal: a populated bundle directory is immutable —
  re-beginning over existing evidence is refused, never overwritten.

Scope fences (plan §5): no router, no ``app.py``, no auth, no feature
manifest, no config, no gate constants; only landed Wave-1/2 modules are
imported (:func:`alpha.config.runtime_paths.runtime_home`,
:func:`alpha.evolution.identity.atomic_write_json`,
:mod:`alpha.rsi.lineage`) plus the optional duck-typed shadow seam.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json
from alpha.rsi.lineage import EVIDENCE_KINDS, _normalize_evidence_kind

logger = logging.getLogger(__name__)

#: The plan's nine evidence files (§3 WP-C2) — the ONLY names ``add()``
#: accepts, so no stray file can enter the provenance index.
BUNDLE_FILES: tuple[str, ...] = (
    "manifest.json",
    "hypothesis.json",
    "baseline_metrics.json",
    "candidate_metrics.json",
    "holdout.json",
    "shadow.json",
    "reviews.json",
    "canary.json",
    "promotion_decision.json",
)

INDEX_FILE_NAME = "bundle_index.json"
LEDGER_FILE_NAME = "bundles_ledger.jsonl"
BUNDLES_DIR_NAME = "bundles"
BUNDLE_INDEX_VERSION = 1

#: Where an item's ``evidence_kind`` label came from (disclosed, never
#: guessed): declared by the caller, read from the payload, or the honest
#: ``unverified`` default applied when no label exists at all.
KIND_SOURCES: frozenset[str] = frozenset({"declared", "payload", "absent_default"})

#: Vocabulary for upstream source markers (:func:`unavailable_source` /
#: :func:`failed_source` / :func:`shadow_evidence`) — a marker outside this
#: set would be a fabricated state, so tests assert membership literally.
SOURCE_STATES: frozenset[str] = frozenset({"recorded", "unavailable", "failed"})

# Module-owned safe-id pattern (archive/workspace each own an equivalent
# copy): a bundle directory name must be traversal-proof, so the id is
# validated against this code literal — never loaded from anywhere.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_candidate_id(candidate_id: Any) -> str:
    """Return ``candidate_id`` when it is a safe single-path-component id, else ``ValueError``."""
    if not isinstance(candidate_id, str) or not _SAFE_ID_RE.fullmatch(candidate_id) or ".." in candidate_id:
        raise ValueError(
            f"unsafe candidate_id for an evidence bundle: {candidate_id!r}; ids must match {_SAFE_ID_RE.pattern} "
            "(no path separators, no traversal — a bundle directory can never escape runtime_home()/rsi/bundles/)."
        )
    return candidate_id


def _bundle_dir(candidate_id: str) -> Path:
    """``runtime_home()/rsi/bundles/<candidate_id>/`` (env resolved at call time)."""
    return runtime_home() / "rsi" / BUNDLES_DIR_NAME / candidate_id


def _require_known_name(name: Any) -> str:
    """Allow only the plan's nine file names (unknown names cannot enter provenance)."""
    if not isinstance(name, str) or name not in BUNDLE_FILES:
        raise ValueError(
            f"unknown evidence bundle file {name!r}; the bundle accepts exactly the plan's nine files: "
            f"{', '.join(BUNDLE_FILES)} (unknown names are refused — no stray files enter the provenance index)."
        )
    return name


def _resolve_evidence_kind(name: str, payload: Any, declared: str | None) -> tuple[str, str]:
    """Resolve one item's whitelist label from caller declaration and/or payload.

    Returns ``(evidence_kind, kind_source)``. Both labels are normalized
    through the lineage whitelist validator (legacy ``"unknown"`` maps to
    ``"unverified"``); an invalid label raises with the real whitelist text,
    and two *conflicting* labels raise instead of picking a winner. No label
    at all resolves to the honest ``unverified`` default — never
    ``measured``, never a fabricated neutral.
    """
    declared_norm: str | None = None
    if declared is not None:
        try:
            declared_norm = _normalize_evidence_kind(declared)
        except ValueError as exc:
            raise ValueError(f"evidence item {name!r}: {exc}") from exc
    payload_norm: str | None = None
    if isinstance(payload, dict) and "evidence_kind" in payload:
        try:
            payload_norm = _normalize_evidence_kind(payload["evidence_kind"])
        except ValueError as exc:
            raise ValueError(f"evidence payload for {name!r}: {exc}") from exc
    if declared_norm is not None and payload_norm is not None and declared_norm != payload_norm:
        raise ValueError(
            f"conflicting evidence_kind claims for {name!r}: declared {declared_norm!r} vs payload "
            f"{payload_norm!r}; refusing to record two evidence labels for one item."
        )
    if declared_norm is not None:
        return declared_norm, "declared"
    if payload_norm is not None:
        return payload_norm, "payload"
    return "unverified", "absent_default"


def unavailable_source(source: str, reason: str) -> dict[str, Any]:
    """Canonical missing-upstream marker: honest ``unavailable`` + the real reason.

    Never success, never silence: an empty/non-string reason raises, because
    an undisclosed gap would be the first step toward a fabricated pass.
    The ``unverified`` kind makes the marker structurally unable to satisfy
    :meth:`Bundle.meets_evidence_standard`.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValueError("an unavailable source marker must name its source (non-empty string).")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("an unavailable source must disclose a non-empty real reason (silence would hide the gap).")
    return {"source": source, "state": "unavailable", "evidence_kind": "unverified", "reason": reason, "record": None}


def failed_source(source: str, reason: str) -> dict[str, Any]:
    """Canonical failed-upstream marker: ``failed`` state carrying the real error text."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("a failed source marker must name its source (non-empty string).")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a failed source must disclose a non-empty real reason (silence would hide the gap).")
    return {"source": source, "state": "failed", "evidence_kind": "unverified", "reason": reason, "record": None}


def _shadow_module_api() -> tuple[Any | None, str]:
    """Optional-import seam for WP-C1's ``alpha.rsi.shadow`` (workspace ``_protected_paths_api`` precedent).

    Returns ``(module, "")`` when importable, else ``(None, "<real
    ImportError text>")``. Only ``ImportError`` is absorbed here; any other
    import failure (e.g. a syntax error in a half-landed module) propagates
    to :func:`shadow_evidence`, which records it as ``failed`` with the real
    text.
    """
    try:
        return importlib.import_module("alpha.rsi.shadow"), ""
    except ImportError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _shadow_dir() -> Path:
    """``runtime_home()/rsi/shadow/`` — WP-C1's documented record directory (plan §3)."""
    return runtime_home() / "rsi" / "shadow"


def _shadow_record_for(candidate_id: str) -> tuple[dict[str, Any] | None, str]:
    """First on-disk comparison record naming this candidate, else ``(None, real reason)``.

    Duck-typed against the plan's documented persistence layout only
    (``runtime_home()/rsi/shadow/<run_id>.json``, plan WP-C1) — not against
    a C1 API that may not have landed: a record matches exactly when its
    top-level ``candidate_id`` equals this candidate, nothing more is
    assumed. Unreadable files are counted with the first real error and
    never guessed at; no match yields the honest count-and-location reason.
    """
    shadow_dir = _shadow_dir()
    if not shadow_dir.is_dir():
        return None, f"no shadow comparison record names candidate {candidate_id!r}: directory {shadow_dir} does not exist"
    scanned = 0
    unreadable = 0
    first_error = ""
    for path in sorted(shadow_dir.glob("*.json")):
        scanned += 1
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            unreadable += 1
            if not first_error:
                first_error = f"{type(exc).__name__}: {exc}"
            continue
        if isinstance(record, dict) and record.get("candidate_id") == candidate_id:
            return record, ""
    note = f"no shadow comparison record names candidate {candidate_id!r}: {scanned} record file(s) scanned under {shadow_dir}"
    if unreadable:
        note += f"; {unreadable} unreadable (first error: {first_error})"
    return None, note


def shadow_evidence(candidate_id: str) -> dict[str, Any]:
    """WP-C1 shadow evidence, duck-typed and optional — honest in every availability state.

    States (:data:`SOURCE_STATES`): ``recorded`` (a matching record was read
    and its label validated), ``unavailable`` (module or record absent — the
    real reason travels verbatim), ``failed`` (import/scan/label error — the
    real exception text travels verbatim). Never raises for an upstream gap,
    never claims improvement, never invents a delta or score.
    """
    candidate_id = _validate_candidate_id(candidate_id)
    try:
        module, absence = _shadow_module_api()
    except Exception as exc:  # noqa: BLE001 — non-ImportError import failures fail closed with the real text
        return failed_source("shadow", f"alpha.rsi.shadow import failed: {type(exc).__name__}: {exc}")
    if module is None:
        return unavailable_source("shadow", f"alpha.rsi.shadow is not importable ({absence}); no shadow evidence exists for candidate {candidate_id!r}")
    try:
        record, reason = _shadow_record_for(candidate_id)
    except Exception as exc:  # noqa: BLE001 — scan failures fail closed with the real error
        return failed_source("shadow", f"shadow record scan failed: {type(exc).__name__}: {exc}")
    if record is None:
        return unavailable_source("shadow", reason)
    try:
        # An absent label on a real record is the honest "unverified", same
        # rule as add(); an invalid label is a fabricated one and fails closed.
        evidence_kind = _normalize_evidence_kind(record.get("evidence_kind", "unverified"))
    except ValueError as exc:
        return failed_source("shadow", f"shadow record for candidate {candidate_id!r} carries an invalid evidence label: {exc}")
    return {"source": "shadow", "state": "recorded", "evidence_kind": evidence_kind, "reason": "", "record": record}


class Bundle:
    """One candidate's evidence bundle: verbatim payloads + per-file provenance.

    Instances are created by :func:`begin_bundle` (which owns directory
    validation and the overwrite refusal). ``items`` maps each of the nine
    file names to its disclosed record ``{name, evidence_kind, kind_source,
    added_at}``; payloads themselves live on disk only, byte-for-byte as
    supplied. ``persistence`` flips to ``"degraded"`` (sticky, lineage
    precedent) only if the finalization ledger append fails — the real error
    is then disclosed in the finalize report, never swallowed.
    """

    def __init__(self, candidate_id: str, *, bundle_dir: Path, clock: Callable[[], float]) -> None:
        self.candidate_id = candidate_id
        self.bundle_dir = bundle_dir
        self.items: dict[str, dict[str, Any]] = {}
        self.finalized = False
        self.persistence = "ok"
        self._clock = clock

    def add(self, name: str, payload: Any, *, evidence_kind: str | None = None) -> dict[str, Any]:
        """Store one evidence file atomically and return its disclosed item record.

        Refusals (all ``ValueError`` with the real reason): adding after
        ``finalize()``; a name outside :data:`BUNDLE_FILES`; a ``None`` or
        non-object/array payload; a duplicate add (items are recorded
        exactly once — a second add would silently replace provenance); a
        non-JSON-serializable payload; an ``evidence_kind`` outside the §5.6
        whitelist; conflicting declared vs payload labels. Unknown payload
        fields are preserved verbatim — this method adds no keys, back-fills
        no defaults, and computes no metrics.
        """
        if self.finalized:
            raise ValueError(
                f"bundle for {self.candidate_id!r} is already finalized; refusing to add {name!r} after "
                f"{INDEX_FILE_NAME} was written (a finalized bundle is immutable provenance)."
            )
        _require_known_name(name)
        if payload is None:
            raise ValueError(
                f"refusing to add {name!r} to the bundle: payload is None (the evidence bundle never stores empty "
                "evidence; an absent source must travel as an unavailable/failed marker with its real reason)."
            )
        if not isinstance(payload, (dict, list)):
            raise ValueError(
                f"evidence payload for {name!r} must be a JSON object or array, got {type(payload).__name__} "
                "(fail-closed: no lossy coercion of evidence)."
            )
        if name in self.items:
            raise ValueError(
                f"evidence item {name!r} was already added to the bundle for {self.candidate_id!r}; "
                "items are recorded exactly once (a second add would silently replace provenance)."
            )
        try:
            json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"evidence payload for {name!r} is not JSON-serializable: {exc}") from exc
        kind, kind_source = _resolve_evidence_kind(name, payload, evidence_kind)
        atomic_write_json(self.bundle_dir / name, payload)  # OSError propagates: the real write error, never a fake path
        item = {"name": name, "evidence_kind": kind, "kind_source": kind_source, "added_at": float(self._clock())}
        self.items[name] = item
        return dict(item)

    def meets_evidence_standard(self, name: str) -> tuple[bool, str]:
        """``(ok, real reason)``: can this item's KIND satisfy a pass condition?

        Evidence-*kind* eligibility only (plan §5.6): only ``measured``
        qualifies; ``simulated`` can never gate, ``heuristic`` and
        ``unverified`` are disclosed but are not passes, and an item missing
        from the bundle is not evidence at all. A ``measured`` kind is
        necessary, never sufficient: the pass/fail verdict itself comes from
        the source module's own gate composed by WP-C2c — this bundle does
        not evaluate candidate quality and never will.
        """
        item = self.items.get(name)
        if item is None:
            return False, f"evidence {name!r} is missing from the bundle for {self.candidate_id!r} — an absent source is not a pass"
        kind = item["evidence_kind"]
        if kind == "measured":
            return True, f"{name}: evidence_kind='measured' meets the evidence standard (pass/fail verdict still comes from the source module's own gate, never from the bundle)"
        if kind == "simulated":
            return False, f"{name}: evidence_kind='simulated' can never satisfy a pass condition (plan §5.6: simulated evidence may exist but never gates)"
        if kind == "heuristic":
            return False, f"{name}: evidence_kind='heuristic' is a disclosed heuristic, not a measurement — it cannot satisfy a pass condition"
        return False, f"{name}: evidence_kind='unverified' is a 0.5-neutral disclosed marker, not a pass (plan §5.6)"

    def finalize(self) -> dict[str, Any]:
        """Write ``bundle_index.json`` (sha256 per real file) + append ONE ledger event.

        Runs exactly once: re-finalizing raises, because the index is
        immutable provenance. Hashes are computed from the bytes on disk
        (not from memory), so the index vouches for what a later
        :func:`verify_bundle` will read. The index write raises the real OS
        error on failure (no unwritten path is ever returned); only the
        ledger append degrades — disclosed as ``ledger.error`` with
        ``persistence == "degraded"``. The returned report is the on-disk
        index plus ``index_path`` and the ledger disclosure.
        """
        if self.finalized:
            raise ValueError(
                f"bundle for {self.candidate_id!r} is already finalized; finalize() runs exactly once "
                f"({INDEX_FILE_NAME} is immutable provenance)."
            )
        files: dict[str, Any] = {}
        for name in sorted(self.items):
            path = self.bundle_dir / name
            try:
                blob = path.read_bytes()
            except OSError as exc:
                raise OSError(f"cannot hash bundle file {path}: {exc}") from exc
            item = self.items[name]
            files[name] = {
                "evidence_kind": item["evidence_kind"],
                "kind_source": item["kind_source"],
                "added_at": item["added_at"],
                "sha256": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
            }
        finalized_at = float(self._clock())
        index = {
            "version": BUNDLE_INDEX_VERSION,
            "candidate_id": self.candidate_id,
            "bundle_dir": str(self.bundle_dir),
            "finalized_at": finalized_at,
            "files": files,
        }
        index_path = self.bundle_dir / INDEX_FILE_NAME
        atomic_write_json(index_path, index)  # raises the real error: the bundle never returns an unwritten path
        self.finalized = True
        ledger_path = runtime_home() / "rsi" / BUNDLES_DIR_NAME / LEDGER_FILE_NAME
        event = {
            "event": "bundle_finalized",
            "candidate_id": self.candidate_id,
            "bundle_dir": str(self.bundle_dir),
            "at": finalized_at,
            "files": {name: entry["sha256"] for name, entry in files.items()},
        }
        ledger_error: str | None = None
        try:
            line = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            # Ledger-style degradation (lineage precedent): the index is
            # already durable; the append failure is disclosed, never
            # swallowed and never claimed as success.
            ledger_error = f"{type(exc).__name__}: {exc}"
            self.persistence = "degraded"
            logger.warning("could not append evidence bundle ledger event to %s: %s", ledger_path, ledger_error)
        return {
            **index,
            "index_path": str(index_path),
            "ledger": {"appended": ledger_error is None, "path": str(ledger_path), "error": ledger_error},
        }


def begin_bundle(candidate_id: str, *, clock: Callable[[], float] = time.time) -> Bundle:
    """Start the evidence bundle directory for ``candidate_id`` (plan §3 WP-C2).

    Directory: ``runtime_home()/rsi/bundles/<candidate_id>/``, resolved at
    call time so a test-set ``AGENT_WORKSPACE_HOME`` is honored. A directory
    that already contains evidence (a prior finalized or partial bundle) is
    refused with the real location instead of being overwritten — existing
    provenance is never silently replaced. ``clock`` is the injectable time
    source (production default ``time.time``): every recorded timestamp
    flows through it, so tests are deterministic.
    """
    candidate_id = _validate_candidate_id(candidate_id)
    bundle_dir = _bundle_dir(candidate_id)
    if bundle_dir.exists() and any(bundle_dir.iterdir()):
        raise ValueError(
            f"bundle directory {bundle_dir} already contains evidence; refusing to overwrite an existing bundle "
            f"for {candidate_id!r} (remove it deliberately to rebuild — provenance is never replaced silently)."
        )
    bundle_dir.mkdir(parents=True, exist_ok=True)
    return Bundle(candidate_id, bundle_dir=bundle_dir, clock=clock)


def verify_bundle(candidate_id: str) -> tuple[bool, str]:
    """Re-hash a finalized bundle against ``bundle_index.json``; ``(ok, real reason)``.

    Detects every provenance break with the real text: a missing bundle
    directory, a missing/corrupt/malformed index, an index entry whose
    ``evidence_kind`` leaves the §5.6 whitelist or whose file name leaves
    the plan's nine (fail-closed — a tampered index cannot pull arbitrary
    paths into verification), a deleted or unreadable file, a sha256 or
    size mismatch, and any stray file not recorded in the index.
    """
    candidate_id = _validate_candidate_id(candidate_id)
    bundle_dir = _bundle_dir(candidate_id)
    if not bundle_dir.is_dir():
        return False, f"no bundle directory at {bundle_dir}"
    index_path = bundle_dir / INDEX_FILE_NAME
    try:
        raw = index_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"unreadable bundle index at {index_path}: {exc}"
    try:
        index = json.loads(raw)
    except ValueError as exc:
        return False, f"corrupt bundle index at {index_path}: {exc}"
    if not isinstance(index, dict) or not isinstance(index.get("files"), dict):
        return False, f"malformed bundle index at {index_path}: expected an object with a 'files' mapping, got {type(index).__name__}"
    if index.get("version") != BUNDLE_INDEX_VERSION:
        return False, f"unsupported bundle index version {index.get('version')!r} at {index_path} (expected {BUNDLE_INDEX_VERSION})"
    if index.get("candidate_id") != candidate_id:
        return False, f"bundle index at {index_path} names candidate {index.get('candidate_id')!r}, expected {candidate_id!r}"
    indexed: dict[str, Any] = index["files"]
    for name in sorted(indexed):
        entry = indexed[name]
        if not isinstance(name, str) or name not in BUNDLE_FILES:
            return False, f"bundle index {index_path} names {name!r}, which is not one of the plan's nine evidence files (fail-closed: an index cannot pull arbitrary paths into verification)"
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            return False, f"malformed index entry for {name!r} in {index_path}: missing sha256 digest"
        if entry.get("evidence_kind") not in EVIDENCE_KINDS:
            return False, f"index entry for {name!r} carries evidence_kind {entry.get('evidence_kind')!r} outside the §5.6 whitelist {sorted(EVIDENCE_KINDS)} (fail-closed)"
        if entry.get("kind_source") not in KIND_SOURCES:
            return False, f"index entry for {name!r} carries kind_source {entry.get('kind_source')!r} outside {sorted(KIND_SOURCES)} (fail-closed)"
        path = bundle_dir / name
        try:
            blob = path.read_bytes()
        except OSError as exc:
            return False, f"bundle file missing or unreadable: {path}: {exc}"
        actual = hashlib.sha256(blob).hexdigest()
        if actual != entry["sha256"]:
            return False, (
                f"sha256 mismatch for {name!r} in {bundle_dir}: index records {entry['sha256']}, "
                f"file on disk is {actual} (bundle corrupted or modified after finalize)"
            )
        if entry.get("bytes") != len(blob):
            return False, f"size mismatch for {name!r} in {bundle_dir}: index records {entry.get('bytes')!r} bytes, file on disk is {len(blob)} bytes"
    allowed = set(indexed) | {INDEX_FILE_NAME}
    try:
        present = sorted(entry.name for entry in bundle_dir.iterdir())
    except OSError as exc:
        return False, f"unreadable bundle directory {bundle_dir}: {exc}"
    strays = [found for found in present if found not in allowed]
    if strays:
        return False, f"unexpected file(s) in bundle {bundle_dir}: {', '.join(strays)} (not recorded in {INDEX_FILE_NAME} — provenance is incomplete)"
    return True, f"bundle for {candidate_id!r} verified: {len(indexed)} file(s) match {INDEX_FILE_NAME} sha256 digests"
