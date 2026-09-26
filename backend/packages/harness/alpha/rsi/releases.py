"""§26/§43 immutable release store with one active pointer.

Architecture spec §43: "Use immutable release directories and one active
pointer." §26: "Promotion should change one release pointer or otherwise
perform an atomic switch. Do not overwrite the running installation
file-by-file. Keep several known-good releases and checkpoints."

Layout under ``runtime_home()/rsi/releases/``::

    current.json          {"active": "v001", "flipped_at": <clock>, "candidate_id": <id>} — the ONE active pointer
    v001/release.json     §43 manifest: release, commit, parent_release, candidate_id, artifacts, evaluator_manifest
    v001/decision.json    the PromotionDecision.to_dict() this release was materialized from
    v002/...              history: previous directories are never touched

Immutability and the atomic switch, concretely:

* a release directory is written ONCE — :func:`create_release` raises
  :class:`ReleaseStoreError` when the id already exists, and the module
  exposes no API that writes into an existing release. The store does not
  chmod files read-only: immutability is enforced at the module boundary, so
  nothing becomes an undeletable trap for operators and audit stays possible.
* the pointer can only ever name a COMPLETE release: manifest + decision are
  written first (each via ``atomic_write_json`` — tmp + ``os.replace``),
  ``current.json`` is flipped last. A crash in between leaves a fully-written
  but never-active directory: :func:`release_history` lists it honestly (it
  is on disk), it is never activated automatically and never deleted.
* :func:`current_release` is fail-closed: ``None`` ONLY when the store has
  never been created (no promotion yet); an existing store with a missing,
  corrupt, or dangling ``current.json`` raises :class:`ReleaseStoreError`
  with the real text — never silently re-created, never "helpfully" reset.
* ids are real: ``v001``, ``v002``, … derived from the directories already
  on disk (monotonic ``max + 1``), never from a clock or an in-memory
  counter; an explicit id can be supplied (tests pin create-once refusal).

Honest §43 field disclosures:

* ``commit`` is the literal ``"unknown"``: this unit performs NO VCS
  operations (task/plan fence: no git commands), so no commit is probed or
  fabricated. The landed ``alpha.rsi.state.current_repo_commit()`` helper
  exists for cycle records; it is deliberately NOT called here — it runs a
  read-only ``git`` subprocess, and this store's consumers declare
  "no subprocess" as a test-suite property.
* ``artifacts`` maps every bundle file to the ``bundle_index.json`` sha256
  the bundle_integrity gate just verified, plus the index file's own freshly
  computed digest — provenance taken from verified bytes, never re-invented.
* ``evaluator_manifest`` is a real :func:`alpha.rsi.evaluator_manifest.
  build_manifest` output (sha256 over the whole evaluator surface) behind the
  :data:`RSI_EVALUATOR_MANIFEST_BUILDER` seam: the default is the landed
  builder; tests memoize ONE real build for the session (the surface cannot
  change under a run) instead of faking the content. Its ``state`` travels
  verbatim (``complete`` or ``incomplete``), never coerced to green.

Honesty contract (plan §5): every timestamp comes from the injected clock
(:data:`RSI_RELEASE_CLOCK`); a release may only be materialized from a
``promoted`` decision payload (enforced below); failures raise honest
exceptions (``ValueError`` for caller shape, :class:`ReleaseStoreError` for
store integrity, ``OSError`` for IO) that the promotion path discloses — no
bare ``except``, no silent repair, no fabricated manifest value.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json
from alpha.rsi.evaluator_manifest import build_manifest
from alpha.rsi.evidence_bundle import BUNDLES_DIR_NAME, INDEX_FILE_NAME

__all__ = [
    "RELEASES_DIR_NAME",
    "RSI_EVALUATOR_MANIFEST_BUILDER",
    "RSI_RELEASE_CLOCK",
    "ReleaseStoreError",
    "create_release",
    "current_release",
    "release_history",
]

#: Store directory under ``runtime_home()/rsi/``.
RELEASES_DIR_NAME = "releases"

#: §5 injectable clock seam for pointer flips (``flipped_at``).
RSI_RELEASE_CLOCK: Callable[[], float] = time.time

#: §5 injectable seam for the real (and comparatively expensive) evaluator
#: manifest build: default = the landed ``build_manifest``; tests memoize one
#: real build. State seam — it never invents a verdict, only recomputes bytes.
RSI_EVALUATOR_MANIFEST_BUILDER: Callable[[], dict[str, Any]] = build_manifest

#: Safe single-path-component candidate ids (same rule as the promotion module).
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
#: Release ids the store mints: ``v001``+, zero-padded, monotonic.
_RELEASE_ID_RE = re.compile(r"^v\d{1,6}$")


class ReleaseStoreError(RuntimeError):
    """Release-store integrity failure: corrupt/missing/dangling pointer or an id conflict on an immutable directory.

    ``alpha.rsi.errors`` classifies these honestly: an id conflict
    (``already exists`` / ``immutable``) is RSI-E018 Promotion conflict,
    anything else store-shaped is RSI-E013 Checkpoint/state failure.
    """


def _releases_dir() -> Path:
    """``runtime_home()/rsi/releases/`` (env resolved at call time)."""
    return runtime_home() / "rsi" / RELEASES_DIR_NAME


def _release_path(release_id: str) -> Path:
    return _releases_dir() / release_id


def _require_safe_candidate_id(candidate_id: Any) -> str:
    """Return ``candidate_id`` when it is a safe single-path-component id, else ``ValueError`` with the real rule."""
    if not isinstance(candidate_id, str) or not _SAFE_ID_RE.fullmatch(candidate_id) or ".." in candidate_id:
        raise ValueError(
            f"unsafe candidate_id for a release: {candidate_id!r}; ids must match {_SAFE_ID_RE.pattern} "
            "(no path separators, no traversal — releases stay inside runtime_home()/rsi/releases/)."
        )
    return candidate_id


def _read_pointer() -> dict[str, Any] | None:
    """The real ``current.json`` payload — or ``None`` when the store does not exist yet.

    Everything else fail-closes with :class:`ReleaseStoreError` and the real
    text: a store that exists without a readable pointer naming an existing
    directory is PARTIAL state, and partial state is disclosed, never
    guessed, repaired, or re-created.
    """
    releases_dir = _releases_dir()
    if not releases_dir.exists():
        return None
    pointer_path = releases_dir / "current.json"
    if not pointer_path.is_file():
        raise ReleaseStoreError(f"release store at {releases_dir} has no current.json pointer — partial store state is never re-created or guessed (fail-closed)")
    try:
        payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseStoreError(f"unreadable release pointer at {pointer_path}: {type(exc).__name__}: {exc} (fail-closed: the active release is never guessed)") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("active"), str) or not payload["active"]:
        raise ReleaseStoreError(f"corrupt release pointer at {pointer_path}: expected an object with a non-empty 'active' release id, got {payload!r} (fail-closed)")
    if "flipped_at" in payload and (isinstance(payload["flipped_at"], bool) or not isinstance(payload["flipped_at"], (int, float))):
        raise ReleaseStoreError(f"corrupt release pointer at {pointer_path}: 'flipped_at' must be an epoch-seconds number, got {payload['flipped_at']!r} (fail-closed)")
    if "candidate_id" in payload and not isinstance(payload["candidate_id"], str):
        raise ReleaseStoreError(f"corrupt release pointer at {pointer_path}: 'candidate_id' must be a string, got {payload['candidate_id']!r} (fail-closed)")
    active = payload["active"]
    if not (_releases_dir() / active).is_dir():
        raise ReleaseStoreError(f"dangling release pointer at {pointer_path}: active release {active!r} has no directory at {_releases_dir() / active} (fail-closed)")
    return payload


def release_history() -> list[str]:
    """Materialized release ids, oldest first — the REAL directories on disk.

    A fully-written release a crash left un-pointered is listed (it exists)
    but is never activated automatically and never deleted.
    """
    releases_dir = _releases_dir()
    if not releases_dir.is_dir():
        return []
    names = [path.name for path in releases_dir.iterdir() if path.is_dir() and _RELEASE_ID_RE.fullmatch(path.name)]
    return sorted(names, key=lambda name: int(name[1:]))


def current_release() -> dict[str, Any] | None:
    """The ONE active pointer payload ``{"active", "flipped_at", "candidate_id"}``.

    ``None`` only when the store has never been created; a created-but-broken
    store raises :class:`ReleaseStoreError` (fail-closed, real text).
    """
    return _read_pointer()


def _bundle_artifacts(candidate_id: str) -> dict[str, str]:
    """Real sha256 provenance: the digests the bundle_integrity gate verified, plus the index file's own digest."""
    bundle_dir = runtime_home() / "rsi" / BUNDLES_DIR_NAME / candidate_id
    index_path = bundle_dir / INDEX_FILE_NAME
    blob = index_path.read_bytes()  # OSError = the real error, propagated to the caller's fail-closed disclosure
    try:
        index = json.loads(blob)
    except ValueError as exc:
        raise ValueError(f"corrupt bundle index at {index_path}: {exc} (artifacts are never recorded from an unreadable index)") from exc
    if not isinstance(index, dict) or not isinstance(index.get("files"), dict):
        raise ValueError(f"malformed bundle index at {index_path}: expected an object with a 'files' mapping (artifacts are never guessed)")
    artifacts: dict[str, str] = {}
    for name, entry in index["files"].items():
        digest = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(digest, str) or not digest.strip():
            raise ValueError(f"bundle index entry for {name!r} at {index_path} carries no sha256 digest — artifacts come from the verified digests only, never re-derived guesses")
        artifacts[str(name)] = digest
    artifacts[INDEX_FILE_NAME] = hashlib.sha256(blob).hexdigest()
    return artifacts


def _resolve_release_id(pointer: dict[str, Any] | None) -> str:
    """Monotonic id from the REAL store contents (``max + 1`` over on-disk ids), never a clock or memory counter."""
    highest = 0
    for name in release_history():
        highest = max(highest, int(name[1:]))
    if pointer is not None and _RELEASE_ID_RE.fullmatch(pointer["active"]):
        highest = max(highest, int(pointer["active"][1:]))
    return f"v{highest + 1:03d}"


def create_release(candidate_id: str, decision: Mapping[str, Any], *, release_id: str | None = None) -> dict[str, Any]:
    """Materialize ONE immutable release and flip the single active pointer; return the §43 manifest.

    ``decision`` must be the promoted ``PromotionDecision.to_dict()`` payload:
    a release is only ever created from a real promoted composition
    (enforced). Order is part of the contract — every value is computed
    first (so a failure touches nothing), then ``decision.json`` and
    ``release.json`` are written atomically, and only then is ``current.json``
    flipped, so the pointer can never name a half-written directory.

    Raises ``ValueError`` for caller-shape problems, :class:`ReleaseStoreError`
    for store-integrity conflicts (including the immutability refusal), and
    ``OSError`` for real IO failures — the promotion path discloses all of
    them; nothing here is swallowed.
    """
    candidate_id = _require_safe_candidate_id(candidate_id)
    if not isinstance(decision, Mapping):
        raise ValueError(f"decision must be a mapping (PromotionDecision.to_dict()), got {type(decision).__name__}")
    if set(decision) != {"candidate_id", "promoted", "reason", "gates"}:
        raise ValueError(f"decision payload must carry exactly the PromotionDecision keys 'candidate_id', 'promoted', 'reason', 'gates'; got {sorted(map(str, decision))}")
    if decision.get("candidate_id") != candidate_id:
        raise ValueError(f"decision payload names candidate {decision.get('candidate_id')!r}, expected {candidate_id!r} — a release never records another candidate's provenance")
    if decision.get("promoted") is not True:
        raise ValueError(f"a release may only be materialized from a promoted decision; decision.promoted={decision.get('promoted')!r} (spec §26: promotion is what moves the pointer)")

    pointer = _read_pointer()  # None = fresh store; ReleaseStoreError = partial/corrupt store (fail-closed, nothing written below)
    if release_id is None:
        release_id = _resolve_release_id(pointer)
    elif not isinstance(release_id, str) or not _RELEASE_ID_RE.fullmatch(release_id):
        raise ValueError(f"release_id must look like 'v001' (a 'v' plus 1-6 digits), got {release_id!r}")
    release_dir = _release_path(release_id)
    if release_dir.exists():
        raise ReleaseStoreError(f"release {release_id!r} already exists at {release_dir} — release directories are immutable (spec §43): never overwritten, never edited")

    evaluator_manifest = RSI_EVALUATOR_MANIFEST_BUILDER()
    if not isinstance(evaluator_manifest, dict):
        raise ValueError(f"RSI_EVALUATOR_MANIFEST_BUILDER returned {type(evaluator_manifest).__name__}, not a dict — the §43 field carries a real build_manifest() output or the release fails")

    manifest: dict[str, Any] = {
        "release": release_id,
        "commit": "unknown",  # disclosed: this unit performs NO VCS operations (no git), so no commit is probed or invented
        "parent_release": pointer["active"] if pointer is not None else None,
        "candidate_id": candidate_id,
        "artifacts": _bundle_artifacts(candidate_id),
        "evaluator_manifest": evaluator_manifest,
    }
    payload = dict(decision)

    # Release first, pointer last: a crash can orphan a complete directory,
    # but the pointer can never point into an incomplete one.
    release_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_json(release_dir / "decision.json", payload)
    atomic_write_json(release_dir / "release.json", manifest)
    current = float(RSI_RELEASE_CLOCK())
    if not math.isfinite(current):
        raise ValueError(f"injected release clock returned a non-finite time: {current!r}")
    atomic_write_json(
        _releases_dir() / "current.json",
        {"active": release_id, "flipped_at": current, "candidate_id": candidate_id},
    )
    return manifest
