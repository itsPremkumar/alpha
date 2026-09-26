"""On-disk store format versions: detect, disclose, and refuse.

Every Alpha memory store writes a schema marker into its JSON document
(``schema``, ``schema_version``, or ``format_version``) but, before this
module, no reader ever compared it against the version its own code
implements.  The consequence is a specific, reproducible data-loss path:

1. A newer build writes ``{"schema": 2, ...}``.
2. An older build (or an older worker in a rolling deploy) reads it.
3. ``Extra("forbid")`` on every persisted model, or an explicit
   ``schema != _SCHEMA`` check, raises ``ValueError``.
4. The store's ``except`` handler treats that exactly like torn bytes: it
   renames the file to ``<name>.corrupt-<stamp>`` and continues with an empty
   document.
5. The next write publishes the *older* format at the live path.

The bytes survive only in an unindexed sidecar, the running process reports
``recovered``, and no code path anywhere surfaces the sidecar to an operator.
An old process therefore destroys a new process's live data, and a version
bump silently resets every store it touches.  That is the "no migration path"
gap, measured rather than asserted.

This module separates the two failure modes that every store had collapsed
into one:

``unreadable``
    The bytes do not parse, or the root is not an object.  This is genuine
    corruption.  Existing quarantine behaviour is correct for it and is
    left alone.

``legacy`` / ``future`` / ``absent``
    The document parsed perfectly and told us which version wrote it -- or
    that no version ever did.  This is a *version incompatibility*, not
    corruption, and it must never be quarantined: quarantining it is the data
    loss.  A store holding one of these keeps the file byte-for-byte, refuses
    to write over it, and publishes :data:`STORE_FORMAT_UNSUPPORTED` through
    the disclosure channel it already has.

The rule encoded in :func:`classify_store_format` is the policy
``alpha.persistence.storekit.documents`` already states for its own envelope
("an older process must not destroy a newer process's data") and that no
memory store had implemented.

:func:`audit_store_formats` is the preflight half.  It answers "which
documents on this disk would this build refuse?" without importing a single
store, so it is safe to run before a deploy and cheap enough to run in CI.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "DIRECTION_ABSENT",
    "DIRECTION_CURRENT",
    "DIRECTION_FUTURE",
    "DIRECTION_LEGACY",
    "DIRECTION_UNREADABLE",
    "SCHEMA_MARKER_KEYS",
    "STORE_FORMAT_UNSUPPORTED",
    "StoreFormatAudit",
    "StoreFormatFinding",
    "StoreFormatUnsupported",
    "StoreFormatVerdict",
    "audit_store_formats",
    "classify_store_format",
    "find_quarantine_paths",
    "format_disclosure",
    "is_quarantine_path",
    "read_schema_marker",
    "require_store_format",
    "quarantine_path_for",
]

#: Marker spellings actually written by the stores in this tree.  Ordered: the
#: first key present in a document wins, so a document that carries two
#: markers is classified on the one the writer used first.
SCHEMA_MARKER_KEYS: tuple[str, ...] = ("schema", "schema_version", "format_version")

#: The document declares the version this build implements.
DIRECTION_CURRENT = "current"
#: The document is older than this build and no migration is registered.
DIRECTION_LEGACY = "legacy"
#: The document is newer than this build.  A rolling deploy will see this.
DIRECTION_FUTURE = "future"
#: The document parses but carries no marker at all: a pre-versioning file.
DIRECTION_ABSENT = "absent"
#: The bytes do not parse, or the root is not a JSON object.  Real corruption.
DIRECTION_UNREADABLE = "unreadable"

#: Status a store publishes through its existing disclosure channel when it is
#: holding a document it refuses to touch.  Chosen to sit alongside the
#: already-published ``corrupt_document_preserved`` / ``read_error`` values so
#: no call site needs a new branch to render it.
STORE_FORMAT_UNSUPPORTED = "format_unsupported"

#: Suffix every store in this tree uses for a preserved corrupt document.
QUARANTINE_INFIX = ".corrupt-"

_REFUSAL_DIRECTIONS = frozenset({DIRECTION_LEGACY, DIRECTION_FUTURE, DIRECTION_ABSENT})


def is_quarantine_path(path: Path | str) -> bool:
    """Return whether ``path`` is itself a preserved-corrupt sidecar name."""

    return QUARANTINE_INFIX in Path(path).name


#: Alias kept so either spelling resolves; both answer the same predicate.
quarantine_path_for = is_quarantine_path


def find_quarantine_paths(root: Path | str, *, pattern: str = "**/*") -> tuple[Path, ...]:
    """Return every preserved-corrupt sidecar under ``root``.

    Sidecars are the only surviving copy of a document a store refused, so an
    operator needs to be able to enumerate them.  No existing code path does.
    """

    base = Path(root).expanduser()
    if not base.is_dir():
        return ()
    return tuple(sorted(path for path in base.glob(pattern) if path.is_file() and is_quarantine_path(path)))


def _coerce_marker(value: Any) -> int | None:
    """Return a usable positive marker integer, or ``None``.

    ``bool`` is rejected even though ``True == 1``: a document whose
    ``"schema": true`` is not a version 1 document.  A numeric string is
    accepted because hand-edited and externally-generated documents are
    common enough that rejecting them would turn a readable file into a
    quarantine, which is the failure mode this module exists to remove.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def read_schema_marker(
    raw: Any,
    marker_keys: Sequence[str] = SCHEMA_MARKER_KEYS,
) -> tuple[int | None, str | None]:
    """Return ``(value, key)`` for a document's version marker.

    ``value`` is ``None`` when no marker is present or the present marker is
    not a usable integer.  ``key`` distinguishes the two cases: ``key`` is
    ``None`` only when no marker key exists at all.
    """

    if not isinstance(raw, Mapping):
        return None, None
    for key in marker_keys:
        if key in raw:
            return _coerce_marker(raw[key]), key
    return None, None


@dataclass(frozen=True, slots=True)
class StoreFormatVerdict:
    """The version relationship between one document and one reader."""

    store: str
    path: str
    supported_version: int
    found_version: int | None = None
    direction: str = DIRECTION_CURRENT
    reason: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True when this build can read the document."""

        return self.direction == DIRECTION_CURRENT

    @property
    def refusal(self) -> bool:
        """True when the document must be left untouched and not overwritten.

        Corruption is deliberately *not* a refusal.  Quarantining unreadable
        bytes is the correct, already-implemented behaviour; quarantining a
        document that simply declares a different version is the data loss.
        """

        return self.direction in _REFUSAL_DIRECTIONS

    @property
    def status(self) -> str:
        """The store status string to publish; empty when there is nothing to say."""

        return STORE_FORMAT_UNSUPPORTED if self.refusal else ""

    @property
    def error(self) -> str:
        return self.detail or self.reason

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StoreFormatVerdict:
        """Rebuild a verdict from :meth:`to_dict`, ignoring the derived keys."""

        return cls(
            store=str(data.get("store", "")),
            path=str(data.get("path", "")),
            supported_version=int(data.get("supported_version", 1)),
            found_version=data.get("found_version"),
            direction=str(data.get("direction", DIRECTION_CURRENT)),
            reason=str(data.get("reason", "")),
            detail=str(data.get("detail", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "store": self.store,
            "path": self.path,
            "supported_version": self.supported_version,
            "found_version": self.found_version,
            "direction": self.direction,
            "reason": self.reason,
            "detail": self.detail,
            "status": self.status,
            "ok": self.ok,
            "refusal": self.refusal,
        }


def classify_store_format(
    *,
    store: str,
    path: Path | str,
    raw: Any,
    supported_version: int,
    marker_keys: Sequence[str] = SCHEMA_MARKER_KEYS,
) -> StoreFormatVerdict:
    """Classify one decoded document against the version ``store`` implements.

    This is the whole decision.  It reads nothing from disk and touches no
    store, so it is safe to unit-test exhaustively and to call from any load
    path.
    """

    supported = int(supported_version)
    if supported < 1:
        raise ValueError("supported_version must be a positive integer")
    located = str(path)

    if not isinstance(raw, Mapping):
        return StoreFormatVerdict(
            store=store,
            path=located,
            supported_version=supported,
            direction=DIRECTION_UNREADABLE,
            reason="document_root_not_object",
        )

    found, key = read_schema_marker(raw, marker_keys)
    if found is None:
        if key is not None:
            return StoreFormatVerdict(
                store=store,
                path=located,
                supported_version=supported,
                direction=DIRECTION_UNREADABLE,
                reason=f"schema_marker_not_an_integer:{key}",
            )
        return StoreFormatVerdict(
            store=store,
            path=located,
            supported_version=supported,
            direction=DIRECTION_ABSENT,
            reason="schema_marker_absent",
            detail=(
                f"{store} writes one of {'/'.join(marker_keys)}; {located} has none, "
                f"so it predates versioning and must be migrated, not discarded"
            ),
        )

    if found == supported:
        return StoreFormatVerdict(
            store=store,
            path=located,
            supported_version=supported,
            found_version=found,
            direction=DIRECTION_CURRENT,
            reason="ok",
        )
    if found > supported:
        return StoreFormatVerdict(
            store=store,
            path=located,
            supported_version=supported,
            found_version=found,
            direction=DIRECTION_FUTURE,
            reason="document_newer_than_reader",
            detail=(
                f"{store} document declares format {found} but this build implements {supported}; "
                f"a newer worker owns {located} and an older process must not quarantine or rewrite it"
            ),
        )
    return StoreFormatVerdict(
        store=store,
        path=located,
        supported_version=supported,
        found_version=found,
        direction=DIRECTION_LEGACY,
        reason="document_older_than_reader",
        detail=f"{store} document declares format {found} but this build implements {supported}; no migration is registered",
    )


class StoreFormatUnsupported(RuntimeError):
    """A store is holding a document whose format this build cannot read.

    The document is intact.  Callers must not quarantine, truncate, or
    overwrite the path named by :attr:`verdict`.
    """

    def __init__(self, verdict: StoreFormatVerdict) -> None:
        self.verdict = verdict
        super().__init__(format_disclosure(verdict))

    @property
    def store(self) -> str:
        return self.verdict.store

    @property
    def path(self) -> str:
        return self.verdict.path

    @property
    def direction(self) -> str:
        return self.verdict.direction

    @property
    def found_version(self) -> int | None:
        return self.verdict.found_version

    @property
    def supported_version(self) -> int:
        return self.verdict.supported_version


def format_disclosure(verdict: StoreFormatVerdict) -> str:
    """A stable one-line operator message for a refusal."""

    if verdict.ok:
        return f"{verdict.store}: format {verdict.supported_version} readable"
    return f"{STORE_FORMAT_UNSUPPORTED}: {verdict.detail or verdict.reason}"


def require_store_format(
    *,
    store: str,
    path: Path | str,
    raw: Any,
    supported_version: int,
    marker_keys: Sequence[str] = SCHEMA_MARKER_KEYS,
) -> StoreFormatVerdict:
    """Classify and raise :class:`StoreFormatUnsupported` on a refusal.

    Corruption is returned, not raised: the caller's existing quarantine path
    owns that case and this module must not take it over.
    """

    verdict = classify_store_format(
        store=store,
        path=path,
        raw=raw,
        supported_version=supported_version,
        marker_keys=marker_keys,
    )
    if verdict.refusal:
        raise StoreFormatUnsupported(verdict)
    return verdict


# ---------------------------------------------------------------------------
# Preflight audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoreFormatFinding:
    """One document the audit would refuse, or could not read at all."""

    store: str
    path: str
    direction: str
    supported_version: int
    found_version: int | None = None
    reason: str = ""
    detail: str = ""

    @property
    def refusal(self) -> bool:
        return self.direction in _REFUSAL_DIRECTIONS

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StoreFormatFinding:
        return cls(
            store=str(data.get("store", "")),
            path=str(data.get("path", "")),
            direction=str(data.get("direction", DIRECTION_UNREADABLE)),
            supported_version=int(data.get("supported_version", 1)),
            found_version=data.get("found_version"),
            reason=str(data.get("reason", "")),
            detail=str(data.get("detail", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "store": self.store,
            "path": self.path,
            "direction": self.direction,
            "supported_version": self.supported_version,
            "found_version": self.found_version,
            "reason": self.reason,
            "detail": self.detail,
            "refusal": self.refusal,
        }


@dataclass(frozen=True, slots=True)
class StoreFormatAudit:
    """What a build would find on a disk, before it touches anything."""

    root: str
    scanned: int = 0
    findings: tuple[StoreFormatFinding, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def refusals(self) -> tuple[StoreFormatFinding, ...]:
        return tuple(item for item in self.findings if item.refusal)

    @property
    def unreadable(self) -> tuple[StoreFormatFinding, ...]:
        return tuple(item for item in self.findings if item.direction == DIRECTION_UNREADABLE)

    @property
    def needs_migration(self) -> tuple[StoreFormatFinding, ...]:
        return tuple(item for item in self.refusals if item.direction == DIRECTION_LEGACY)

    @property
    def owned_by_a_newer_build(self) -> tuple[StoreFormatFinding, ...]:
        return tuple(item for item in self.refusals if item.direction == DIRECTION_FUTURE)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def stores_needing_migration(self) -> tuple[str, ...]:
        return tuple(sorted({item.store for item in self.needs_migration}))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StoreFormatAudit:
        """Rebuild an audit from :meth:`to_dict`; derived keys are recomputed."""

        return cls(
            root=str(data.get("root", "")),
            scanned=int(data.get("scanned", 0)),
            findings=tuple(
                StoreFormatFinding.from_dict(item) for item in data.get("findings", ()) if isinstance(item, Mapping)
            ),
            errors=tuple(str(item) for item in data.get("errors", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "scanned": self.scanned,
            "ok": self.ok,
            "refusals": len(self.refusals),
            "unreadable": len(self.unreadable),
            "needs_migration": list(self.stores_needing_migration()),
            "findings": [item.to_dict() for item in self.findings],
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class _DeclaredStore:
    name: str
    format_version: int
    document_glob: str
    marker_keys: tuple[str, ...] = SCHEMA_MARKER_KEYS


def _declared(entry: Any) -> _DeclaredStore | None:
    """Normalize a registration without importing the module that defines it.

    Accepts the :class:`alpha.persistence.storekit.registry.StoreRegistration`
    shape by attribute, or a plain mapping, so this module stays a stdlib leaf
    and the audit can run before any store is imported.
    """

    if isinstance(entry, Mapping):
        name = entry.get("name")
        version = entry.get("format_version")
        pattern = entry.get("document_glob")
        keys = entry.get("marker_keys") or SCHEMA_MARKER_KEYS
    else:
        name = getattr(entry, "name", None)
        version = getattr(entry, "format_version", None)
        pattern = getattr(entry, "document_glob", None)
        keys = getattr(entry, "marker_keys", None) or SCHEMA_MARKER_KEYS
    if not str(name or "").strip() or not str(pattern or "").strip():
        return None
    try:
        resolved_version = int(version)
    except (TypeError, ValueError):
        return None
    if resolved_version < 1:
        return None
    return _DeclaredStore(
        name=str(name).strip(),
        format_version=resolved_version,
        document_glob=str(pattern),
        marker_keys=tuple(str(key) for key in keys),
    )


def audit_store_formats(
    root: Path | str,
    entries: Iterable[Any],
    *,
    marker_keys: Sequence[str] = SCHEMA_MARKER_KEYS,
    limit_per_store: int = 2_000,
) -> StoreFormatAudit:
    """Report every document under ``root`` that the declared stores cannot read.

    ``entries`` is any iterable of store registrations exposing ``name``,
    ``format_version`` and ``document_glob`` -- the shape
    :class:`alpha.persistence.storekit.registry.StoreRegistration` already has.
    Nothing is imported, nothing is written, and sidecar quarantine files are
    skipped so a preserved copy is never reported as a live document.

    Run this before a deploy to answer "what would this build refuse?".  A
    non-empty :attr:`~StoreFormatAudit.refusals` means the deploy will strand
    those documents.
    """

    base = Path(root).expanduser()
    findings: list[StoreFormatFinding] = []
    errors: list[str] = []
    scanned = 0

    if not base.is_dir():
        return StoreFormatAudit(root=str(base))

    for entry in entries:
        declared = _declared(entry)
        if declared is None:
            errors.append(f"unusable store registration: {entry!r}")
            continue
        if Path(declared.document_glob).is_absolute() or ".." in Path(declared.document_glob).parts:
            errors.append(f"{declared.name}: refusing absolute or traversing glob {declared.document_glob!r}")
            continue
        keys = declared.marker_keys or tuple(marker_keys)
        seen = 0
        try:
            matched = sorted(path for path in base.glob(declared.document_glob) if path.is_file())
        except (OSError, ValueError) as exc:
            errors.append(f"{declared.name}: {exc}")
            continue
        for path in matched:
            if is_quarantine_path(path):
                continue
            if seen >= limit_per_store:
                errors.append(f"{declared.name}: stopped after {limit_per_store} documents (raise limit_per_store to finish)")
                break
            seen += 1
            scanned += 1
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                findings.append(
                    StoreFormatFinding(
                        store=declared.name,
                        path=str(path),
                        direction=DIRECTION_UNREADABLE,
                        supported_version=declared.format_version,
                        reason="document_unreadable",
                        detail=str(exc),
                    )
                )
                continue
            verdict = classify_store_format(
                store=declared.name,
                path=path,
                raw=raw,
                supported_version=declared.format_version,
                marker_keys=keys,
            )
            if verdict.ok:
                continue
            findings.append(
                StoreFormatFinding(
                    store=verdict.store,
                    path=verdict.path,
                    direction=verdict.direction,
                    supported_version=verdict.supported_version,
                    found_version=verdict.found_version,
                    reason=verdict.reason,
                    detail=verdict.detail,
                )
            )

    return StoreFormatAudit(root=str(base), scanned=scanned, findings=tuple(findings), errors=tuple(errors))
