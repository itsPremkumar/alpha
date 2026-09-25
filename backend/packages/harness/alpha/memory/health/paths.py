"""Atomic persistence helpers for health reports and metric snapshots.

The layout follows the runtime-home and safe-segment conventions used by the
L1 memory paths without importing that package:

``{storage_path}/reports/{safe_scope}.json``
``{storage_path}/metrics.json``

When ``storage_path`` is omitted, the root is
``runtime_home()/memory/health``.  Unreadable JSON is moved to a sibling whose
name contains ``.corrupt-`` before the caller receives an empty result.  The
original bytes are never silently overwritten or discarded.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import HealthReport

logger = logging.getLogger(__name__)

_UNSAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_SEGMENT = "__default__"


def safe_segment(value: str | None) -> str:
    """Sanitize one path segment using the L1-safe character convention."""

    if not value:
        return DEFAULT_SEGMENT
    cleaned = _UNSAFE_SEGMENT_RE.sub("_", value).strip("._") or "x"
    return cleaned[:100]


def health_root(storage_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the health persistence root.

    An explicit path is used as the root.  With no path, the shared runtime
    home is resolved lazily so importing this module has no filesystem or
    environment side effect.
    """

    if storage_path is not None:
        return Path(storage_path).expanduser().resolve()
    from alpha.config.runtime_paths import runtime_home

    return (Path(runtime_home()) / "memory" / "health").resolve()


def report_path(
    storage_path: str | os.PathLike[str] | None = None,
    scope: str = "default",
) -> Path:
    """Return the atomic JSON report path for one safe scope segment."""

    return health_root(storage_path) / "reports" / f"{safe_segment(scope)}.json"


def metrics_path(storage_path: str | os.PathLike[str] | None = None) -> Path:
    """Return the metric snapshot path."""

    return health_root(storage_path) / "metrics.json"


def metric_path(name: str, storage_path: str | os.PathLike[str] | None = None) -> Path:
    """Return a safe per-metric path for adapters that need one."""

    return health_root(storage_path) / "metrics" / f"{safe_segment(name)}.json"


def atomic_write_text(path: Path, text: str) -> None:
    """Write text through a sibling temporary file and ``os.replace``."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.warning("health persistence: could not remove temporary file %s", temporary)


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def write_json(path: Path, payload: Any) -> Path:
    """Atomically write a deterministic JSON document and return its path."""

    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default) + "\n"
    atomic_write_text(Path(path), text)
    return Path(path)


def preserve_corrupt(path: Path, *, token: str | int | None = None) -> Path | None:
    """Move an unreadable file to a ``.corrupt-*`` sibling.

    ``token`` is injectable for deterministic forensic tests.  If the move
    itself fails, the original remains in place and the error is logged; the
    caller still receives a disclosed ``None`` result from the read helper.
    """

    source = Path(path)
    marker = str(token) if token is not None else f"{os.getpid()}-{uuid4().hex}"
    safe_marker = safe_segment(marker)
    candidate = source.with_name(f"{source.name}.corrupt-{safe_marker}")
    suffix = 1
    while candidate.exists():
        candidate = source.with_name(f"{source.name}.corrupt-{safe_marker}-{suffix}")
        suffix += 1
    try:
        source.replace(candidate)
    except OSError as exc:
        logger.error("health persistence: corrupt file %s could not be preserved: %s", source, exc)
        return None
    logger.error("health persistence: corrupt file %s preserved as %s", source, candidate)
    return candidate


def _read_json_with_status(
    path: Path,
    *,
    corruption_token: str | int | None = None,
) -> tuple[Any | None, str | None, Path | None]:
    source = Path(path)
    if not source.exists():
        return None, None, None
    try:
        raw = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, f"could not read {source}: {type(exc).__name__}: {exc}", None
    try:
        return json.loads(raw), None, None
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        preserved = preserve_corrupt(source, token=corruption_token)
        return None, f"invalid JSON in {source}: {type(exc).__name__}: {exc}", preserved


def read_json(path: Path, *, corruption_token: str | int | None = None) -> Any | None:
    """Read JSON, preserving malformed content instead of discarding it."""

    value, _error, _preserved = _read_json_with_status(path, corruption_token=corruption_token)
    return value


def read_json_with_status(
    path: Path,
    *,
    corruption_token: str | int | None = None,
) -> tuple[Any | None, str | None, Path | None]:
    """Return ``(value, error, preserved_path)`` for forensic callers."""

    return _read_json_with_status(path, corruption_token=corruption_token)


def write_report(
    report: HealthReport | dict[str, Any],
    *,
    storage_path: str | os.PathLike[str] | None = None,
    scope: str | None = None,
) -> Path:
    """Persist a health report atomically."""

    payload = report.model_dump(mode="json") if isinstance(report, HealthReport) else dict(report)
    target_scope = scope if scope is not None else str(payload.get("scope", "default"))
    return write_json(report_path(storage_path, target_scope), payload)


def read_report(
    *,
    storage_path: str | os.PathLike[str] | None = None,
    scope: str = "default",
    corruption_token: str | int | None = None,
) -> HealthReport | None:
    """Read and validate a report, preserving schema-corrupt JSON as well."""

    target = report_path(storage_path, scope)
    value, error, _preserved = _read_json_with_status(target, corruption_token=corruption_token)
    if error is not None:
        logger.error("health persistence: %s", error)
        return None
    if value is None:
        return None
    try:
        return HealthReport.model_validate(value)
    except (TypeError, ValueError) as exc:
        preserve_corrupt(target, token=corruption_token)
        logger.error("health persistence: invalid report schema in %s: %s", target, exc)
        return None


def write_metrics(
    snapshot: dict[str, Any],
    *,
    storage_path: str | os.PathLike[str] | None = None,
) -> Path:
    """Persist a metric registry snapshot atomically."""

    if not isinstance(snapshot, dict):
        raise TypeError("metric snapshot must be a dictionary")
    return write_json(metrics_path(storage_path), snapshot)


def read_metrics(
    *,
    storage_path: str | os.PathLike[str] | None = None,
    corruption_token: str | int | None = None,
) -> dict[str, Any] | None:
    """Read a metric snapshot and preserve malformed/schema-invalid content."""

    target = metrics_path(storage_path)
    value, error, _preserved = _read_json_with_status(target, corruption_token=corruption_token)
    if error is not None:
        logger.error("health persistence: %s", error)
        return None
    if value is None:
        return None
    if not isinstance(value, dict):
        preserve_corrupt(target, token=corruption_token)
        logger.error("health persistence: metric snapshot root is not an object: %s", target)
        return None
    if value.get("schema") != 1 or not isinstance(value.get("metrics"), dict):
        preserve_corrupt(target, token=corruption_token)
        logger.error("health persistence: metric snapshot schema is invalid: %s", target)
        return None
    reservoir_size = value.get("reservoir_size")
    if isinstance(reservoir_size, bool) or not isinstance(reservoir_size, int) or reservoir_size < 1:
        preserve_corrupt(target, token=corruption_token)
        logger.error("health persistence: metric reservoir size is invalid: %s", target)
        return None
    return value


__all__ = [
    "DEFAULT_SEGMENT",
    "atomic_write_text",
    "health_root",
    "metric_path",
    "metrics_path",
    "preserve_corrupt",
    "read_json",
    "read_json_with_status",
    "read_metrics",
    "read_report",
    "report_path",
    "safe_segment",
    "write_json",
    "write_metrics",
    "write_report",
]
