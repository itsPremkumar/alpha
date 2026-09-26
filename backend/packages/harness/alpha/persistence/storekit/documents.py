"""The versioned document envelope shared by StoreKit stores.

Every persisted record is wrapped in a small, self-describing envelope::

    {
      "format_version": 2,
      "schema_id": "narrative.events",
      "checksum": "sha256:...",
      "created_at": 1700000000.0,
      "updated_at": 1700000001.0,
      "migration_chain": ["v1->v2"],
      "payload": {...}
    }

The checksum covers the canonical JSON encoding of ``payload`` only.  It is
therefore stable across timestamp updates and can be recomputed by a second
process without knowing anything about the host's clock.  The checksum is a
corruption detector, not a signature: it does not protect against a malicious
writer that can rewrite the file.

A document that fails structural or checksum validation is *quarantined* by
moving it to a sibling named ``<name>.corrupt-<stamp>``.  Quarantine uses
``os.replace`` and never unlinks the original.  If preservation itself fails,
the original remains in place and the result says ``corrupt_preservation_failed``
so a caller can fail closed rather than overwrite unknown data.  This mirrors
the convention already used by Alpha's memory stores, but centralizes the
timestamp and checksum rules.

The ``clock`` argument is injectable everywhere a timestamp is created.  No
module-level clock is consulted except as an explicit default, which keeps
migration and crash tests deterministic.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = [
    "Document",
    "DocumentCorruptError",
    "DocumentEnvelope",
    "DocumentLoadResult",
    "DocumentValidation",
    "canonical_json",
    "checksum_for",
    "decode_document",
    "load_document",
    "make_document",
    "quarantine_document",
    "save_document",
    "validate_document",
    "write_document",
]

Clock = Callable[[], float]


class DocumentCorruptError(ValueError):
    """Raised by strict envelope construction for an invalid document."""


def _clock_value(clock: Clock | None) -> float:
    value = time.time() if clock is None else float(clock())
    if not (value == value) or value in {float("inf"), float("-inf")}:
        raise ValueError("document clock must return a finite value")
    return value


def _json_value(value: Any) -> Any:
    """Return a detached, JSON-compatible deep copy of ``value``."""

    return copy.deepcopy(value)


def canonical_json(payload: Any) -> str:
    """Encode a payload deterministically for checksums."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def checksum_for(payload: Any) -> str:
    """Return the versioned SHA-256 checksum of a JSON payload."""

    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True, slots=True)
class DocumentEnvelope:
    """Immutable, self-describing record envelope.

    ``checksum`` is populated by :func:`make_document` or
    :meth:`sealed`.  Constructing an envelope directly is useful for a caller
    assembling an already-known payload, but strict decoding will reject it
    when the checksum is absent or does not match.
    """

    payload: Any
    format_version: int = 1
    schema_id: str = "storekit.document"
    checksum: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    migration_chain: tuple[str, ...] = ()
    document_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.format_version, bool) or int(self.format_version) < 1:
            raise ValueError("format_version must be a positive integer")
        if not str(self.schema_id).strip():
            raise ValueError("schema_id must be non-empty")
        if self.migration_chain is not None and not isinstance(self.migration_chain, tuple):
            object.__setattr__(self, "migration_chain", tuple(str(item) for item in self.migration_chain))
        if not all(str(item).strip() for item in self.migration_chain):
            raise ValueError("migration_chain entries must be non-empty")
        object.__setattr__(self, "format_version", int(self.format_version))
        object.__setattr__(self, "schema_id", str(self.schema_id))
        object.__setattr__(self, "checksum", str(self.checksum or checksum_for(self.payload)))
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "updated_at", float(self.updated_at))
        object.__setattr__(self, "metadata", _json_value(dict(self.metadata)))

    @property
    def data(self) -> Any:
        """Compatibility alias for the stored payload."""

        return self.payload

    @property
    def version(self) -> int:
        """Short alias for :attr:`format_version`."""

        return self.format_version

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-serializable envelope."""

        return {
            "format_version": self.format_version,
            "schema_id": self.schema_id,
            "checksum": self.checksum,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "migration_chain": list(self.migration_chain),
            "document_id": self.document_id,
            "metadata": _json_value(dict(self.metadata)),
            "payload": _json_value(self.payload),
        }

    def sealed(self, *, clock: Clock | None = None, updated_at: float | None = None) -> DocumentEnvelope:
        """Return a copy with a recomputed checksum and injected timestamps."""

        now = _clock_value(clock)
        created = self.created_at if self.created_at > 0 else now
        return replace(
            self,
            checksum=checksum_for(self.payload),
            created_at=created,
            updated_at=now if updated_at is None else float(updated_at),
            metadata=_json_value(dict(self.metadata)),
        )

    def with_payload(self, payload: Any, *, migration: str | None = None, clock: Clock | None = None) -> DocumentEnvelope:
        """Return a sealed copy carrying new payload and migration provenance."""

        chain = self.migration_chain
        if migration:
            if migration in chain:
                chain = tuple(item for item in chain if item != migration) + (migration,)
            else:
                chain = (*chain, migration)
        return replace(
            self,
            payload=_json_value(payload),
            migration_chain=chain,
            metadata=_json_value(dict(self.metadata)),
        ).sealed(clock=clock)

    def with_format_version(self, version: int, *, migration: str | None = None, clock: Clock | None = None) -> DocumentEnvelope:
        """Return a sealed copy at another format version."""

        return replace(self, format_version=version, migration_chain=self.migration_chain + ((migration,) if migration else ())).sealed(clock=clock)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, verify_checksum: bool = True) -> DocumentEnvelope:
        """Strictly decode an envelope or raise :class:`DocumentCorruptError`."""

        validation = validate_document(data, verify_checksum=verify_checksum)
        if not validation.ok or validation.document is None:
            raise DocumentCorruptError(f"{validation.reason}: {validation.error}")
        return validation.document


#: Short alias used by callers that treat the envelope as a document.
Document = DocumentEnvelope


@dataclass(frozen=True, slots=True)
class DocumentValidation:
    """Structural/checksum result for one decoded envelope."""

    ok: bool
    document: DocumentEnvelope | None
    reason: str
    error: str = ""
    expected_checksum: str = ""
    actual_checksum: str = ""


@dataclass(frozen=True, slots=True)
class DocumentLoadResult:
    """Result of reading one document from disk.

    ``preserved_path`` is set only after a corrupt original was successfully
    moved aside.  A future format version is deliberately *not* quarantined:
    an older process must not destroy a newer process's data.
    """

    status: str
    path: Path
    document: DocumentEnvelope | None = None
    reason: str = ""
    error: str = ""
    preserved_path: Path | None = None
    raw: Any = None

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "empty"}

    @property
    def corrupt(self) -> bool:
        return self.status.startswith("corrupt")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "path": str(self.path),
            "reason": self.reason,
            "error": self.error,
            "preserved_path": str(self.preserved_path) if self.preserved_path else None,
        }


def _structural_error(data: Any) -> tuple[str, str] | None:
    if not isinstance(data, Mapping):
        return "malformed_document", "document root must be a JSON object"
    required = {"format_version", "schema_id", "checksum", "payload"}
    missing = sorted(required - set(data))
    if missing:
        return "malformed_document", f"missing envelope fields: {', '.join(missing)}"
    if isinstance(data.get("format_version"), bool):
        return "malformed_document", "format_version must be an integer"
    try:
        version = int(data["format_version"])
    except (TypeError, ValueError):
        return "malformed_document", "format_version must be an integer"
    if version < 1:
        return "malformed_document", "format_version must be positive"
    if not str(data.get("schema_id", "")).strip():
        return "malformed_document", "schema_id must be non-empty"
    if not isinstance(data.get("checksum"), str) or not data["checksum"]:
        return "malformed_document", "checksum must be a non-empty string"
    return None


def validate_document(
    data: Any,
    *,
    expected_schema_id: str | None = None,
    max_format_version: int | None = None,
    verify_checksum: bool = True,
) -> DocumentValidation:
    """Validate a decoded envelope without touching the filesystem."""

    structural = _structural_error(data)
    if structural is not None:
        return DocumentValidation(False, None, structural[0], structural[1])
    assert isinstance(data, Mapping)
    try:
        version = int(data["format_version"])
    except (TypeError, ValueError) as exc:  # pragma: no cover - guarded above
        return DocumentValidation(False, None, "malformed_document", str(exc))
    if max_format_version is not None and version > int(max_format_version):
        return DocumentValidation(
            False,
            None,
            "unsupported_future_version",
            f"document format_version {version} is newer than supported {int(max_format_version)}",
        )
    if expected_schema_id is not None and str(data["schema_id"]) != str(expected_schema_id):
        return DocumentValidation(
            False,
            None,
            "schema_mismatch",
            f"expected schema_id {expected_schema_id!r}, found {data['schema_id']!r}",
        )
    actual = str(data["checksum"])
    expected = checksum_for(data["payload"])
    if verify_checksum and actual != expected:
        return DocumentValidation(
            False,
            None,
            "checksum_mismatch",
            f"expected {expected}, found {actual}",
            expected,
            actual,
        )
    try:
        chain_raw = data.get("migration_chain", [])
        if not isinstance(chain_raw, (list, tuple)):
            raise ValueError("migration_chain must be an array")
        chain = tuple(str(item) for item in chain_raw)
        created = float(data.get("created_at", 0.0))
        updated = float(data.get("updated_at", created))
        metadata = data.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be an object")
        document = DocumentEnvelope(
            payload=_json_value(data["payload"]),
            format_version=version,
            schema_id=str(data["schema_id"]),
            checksum=actual,
            created_at=created,
            updated_at=updated,
            migration_chain=chain,
            document_id=str(data.get("document_id", "")),
            metadata=metadata,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return DocumentValidation(False, None, "malformed_document", str(exc))
    return DocumentValidation(True, document, "ok", expected_checksum=expected, actual_checksum=actual)


def decode_document(
    data: Any,
    *,
    expected_schema_id: str | None = None,
    max_format_version: int | None = None,
    verify_checksum: bool = True,
) -> DocumentValidation:
    """Alias for :func:`validate_document` with a decode-oriented name."""

    return validate_document(
        data,
        expected_schema_id=expected_schema_id,
        max_format_version=max_format_version,
        verify_checksum=verify_checksum,
    )


def _unique_corrupt_path(path: Path, stamp: str) -> Path:
    candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{suffix}")
        suffix += 1
    return candidate


def quarantine_document(
    path: Path | str,
    *,
    clock: Clock | None = None,
    stamp: str | None = None,
) -> tuple[Path | None, str]:
    """Move a corrupt document aside without deleting it.

    Returns ``(preserved_path, error)``.  A failed move is reported and the
    original stays in place; this function never attempts a destructive
    fallback such as truncation or unlink.
    """

    source = Path(path)
    if stamp is None:
        moment = _clock_value(clock)
        stamp = str(int(moment * 1_000_000))
    target = _unique_corrupt_path(source, stamp)
    try:
        os.replace(source, target)
    except OSError as exc:
        return None, f"corrupt document preservation failed: {exc}"
    return target, ""


def load_document(
    path: Path | str,
    *,
    expected_schema_id: str | None = None,
    max_format_version: int | None = None,
    verify_checksum: bool = True,
    clock: Clock | None = None,
    quarantine: bool = True,
) -> DocumentLoadResult:
    """Read and validate one envelope, quarantining corruption when possible."""

    target = Path(path)
    if not target.exists():
        return DocumentLoadResult(status="empty", path=target, reason="document_absent")
    try:
        raw_text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return DocumentLoadResult(status="read_error", path=target, reason="document_read_failed", error=str(exc))
    try:
        raw = json.loads(raw_text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if not quarantine:
            return DocumentLoadResult(status="corrupt_json", path=target, reason="malformed_json", error=str(exc), raw=raw_text)
        preserved, error = quarantine_document(target, clock=clock)
        return DocumentLoadResult(
            status="corrupt_preservation_failed" if error else "corrupt_preserved",
            path=target,
            reason="malformed_json",
            error=error or str(exc),
            preserved_path=preserved,
            raw=raw_text,
        )
    validation = validate_document(
        raw,
        expected_schema_id=expected_schema_id,
        max_format_version=max_format_version,
        verify_checksum=verify_checksum,
    )
    if validation.ok and validation.document is not None:
        return DocumentLoadResult(status="ok", path=target, document=validation.document, raw=raw)
    if validation.reason == "unsupported_future_version" or validation.reason == "schema_mismatch":
        return DocumentLoadResult(status=validation.reason, path=target, reason=validation.reason, error=validation.error, raw=raw)
    if not quarantine:
        return DocumentLoadResult(status=validation.reason, path=target, reason=validation.reason, error=validation.error, raw=raw)
    preserved, error = quarantine_document(target, clock=clock)
    return DocumentLoadResult(
        status="corrupt_preservation_failed" if error else "corrupt_preserved",
        path=target,
        reason=validation.reason,
        error=error or validation.error,
        preserved_path=preserved,
        raw=raw,
    )


def make_document(
    payload: Any,
    *,
    format_version: int,
    schema_id: str,
    clock: Clock | None = None,
    document_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    migration_chain: tuple[str, ...] = (),
) -> DocumentEnvelope:
    """Create a sealed envelope with timestamps from the injected clock."""

    now = _clock_value(clock)
    return DocumentEnvelope(
        payload=_json_value(payload),
        format_version=format_version,
        schema_id=schema_id,
        checksum=checksum_for(payload),
        created_at=now,
        updated_at=now,
        migration_chain=tuple(migration_chain),
        document_id=document_id,
        metadata=dict(metadata or {}),
    )


def save_document(
    path: Path | str,
    document: DocumentEnvelope,
    *,
    clock: Clock | None = None,
    atomic_writer: Callable[..., Any] | None = None,
    **write_kwargs: Any,
) -> Any:
    """Seal and atomically publish a document.

    ``atomic_writer`` is injectable for a host that wants to wrap the write in
    its own instrumentation; it must have the same call signature as
    :func:`alpha.persistence.storekit.atomic.atomic_write_json`.
    """

    sealed = document.sealed(clock=clock)
    if atomic_writer is None:
        from .atomic import atomic_write_json

        writer = atomic_write_json
    else:
        writer = atomic_writer
    return writer(path, sealed.to_dict(), **write_kwargs)


def write_document(
    path: Path | str,
    payload: Any,
    *,
    format_version: int,
    schema_id: str,
    clock: Clock | None = None,
    document_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    migration_chain: tuple[str, ...] = (),
    atomic_writer: Callable[..., Any] | None = None,
    **write_kwargs: Any,
) -> Any:
    """Create, seal, and atomically write an envelope in one call."""

    document = make_document(
        payload,
        format_version=format_version,
        schema_id=schema_id,
        clock=clock,
        document_id=document_id,
        metadata=metadata,
        migration_chain=migration_chain,
    )
    return save_document(path, document, clock=clock, atomic_writer=atomic_writer, **write_kwargs)
