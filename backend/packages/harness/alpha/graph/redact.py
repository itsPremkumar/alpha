"""Redaction policy for the graph plane's output surfaces.

Why this module exists
----------------------
The graph plane emits structured events (:class:`~alpha.graph.models.RunEvent`),
artifacts, and approval previews. All of them can carry secrets -- an API key in
a tool argument, a token in an artifact URI, a password in a claim locator.
:mod:`alpha.security.memory_redaction` is the canonical redaction engine for the
whole repository; this module is the **graph-plane policy layer** that decides
what each output surface gets, so a caller never decides "redact this but not
that" at the raise site.

The two levels, and what actually differs
------------------------------------------
``FULL``   -- the engine's whole set, **plus** the graph plane's own extras
             (private IPv4, database connection strings, bearer tokens). Used
             for traces, logs, and the event store that operators read.

``EDGE``   -- the engine's whole set only, with **no** graph-plane extras.
             Used for an approval preview and artifact reference shown to a
             user.

The engine always runs in both levels. That is a deliberate limit, not an
oversight: the engine's pattern registry (``alpha.security.memory_redaction``)
is private to that module, so a *subset* of it cannot be selected through the
public API. A graph-plane level that supposedly redacted less than the engine
would have to import a private tuple, and importing someone else's private
registry is exactly how a "weaker" filter silently becomes a *different*
filter that nobody reviewed -- plus it would break the moment that module
renamed its internals.

So the honest statement is: **EDGE is FULL without the graph-plane extras.**
An API key is redacted at EDGE too, because the engine's own ``sk-`` family
fires and a browser-facing preview is the *worst* place to omit it. What EDGE
does not do is apply the graph plane's aggressive extras, which are the ones
most likely to mangle operational data (a private IP in a hostname field, a
connection string that is really a fixture path). The consequence: the level
controls the extras, and both levels redact the base secret families. That
trade is documented rather than hidden behind a filter that cannot exist.

The level is a **required parameter**, never a default. That asymmetry is the
point: a trace that forgot to redact is a leak, and an approval preview that
over-redacted is a broken UI, and the first is strictly worse than the second.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha.security.memory_redaction import (
    MappingRedactionResult,
    RedactionResult,
    redact_for_memory,
    redact_mapping,
)

__all__ = [
    "GRAPH_EXTRA_PATTERNS",
    "RedactionLevel",
    "RedactionSummary",
    "redact_approval_payload",
    "redact_artifact_uri",
    "redact_claim_locator",
    "redact_event_attributes",
    "redact_event_message",
    "redact_structured",
]


class RedactionLevel(StrEnum):
    """How much redaction is appropriate for the output surface."""

    FULL = "full"
    EDGE = "edge"


# Graph-plane extras. These run ON TOP of the engine at ``FULL`` only. Each is
# a plain regex string, the ``extra_patterns`` shape the public API accepts.
#
# Private IPv4 is listed deliberately: it is a credential-shaped thing in an
# operator's trace but a legitimate value in a hostname or endpoint field, and
# mangling the latter is how a preview becomes useless. Connection strings and
# bearer tokens have no such excuse -- they are credentials wherever they appear.
GRAPH_EXTRA_PATTERNS: tuple[str, ...] = (
    r"(?<![0-9])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![0-9])",
    r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s\"'<>]+",
    r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}",
)


@dataclass(slots=True, frozen=True)
class RedactionSummary:
    """A concise report of what was redacted, for logging/audit.

    The level is included so a log line can distinguish "FULL redaction found 3"
    from "EDGE redaction found 0" without re-counting.
    """

    level: RedactionLevel
    count: int
    kinds: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level.value, "count": self.count, "kinds": list(self.kinds)}


def _extras(level: RedactionLevel) -> tuple[str, ...]:
    """The graph-plane extras for this level. The base engine always runs."""
    if level is RedactionLevel.FULL:
        return GRAPH_EXTRA_PATTERNS
    return ()


def redact_event_message(message: str, *, level: RedactionLevel) -> RedactionResult:
    """Redact a free-form event message at the requested level."""
    return redact_for_memory(message, extra_patterns=_extras(level))


def redact_artifact_uri(uri: str, *, level: RedactionLevel) -> RedactionResult:
    """Redact an artifact URI or path.

    A URI can carry a token in a query parameter or a credential in a path
    segment. At ``FULL`` the graph-plane extras fire too (a database URL is a
    connection string); at ``EDGE`` only the engine's families do.
    """
    return redact_for_memory(uri, extra_patterns=_extras(level))


def redact_approval_payload(payload: Mapping[str, Any]) -> MappingRedactionResult:
    """Redact an approval preview payload for the operator.

    This is an **edge** surface, so ``EDGE`` level is used: the operator sees
    what the operation *is* (command, target, side effects) without seeing the
    secret that authorizes it. ``redact_mapping`` walks the structure
    key-by-key rather than stringifying it, so a secret-named key is replaced
    wholesale and a non-secret value is left readable by the engine's own
    judgment.
    """
    return redact_mapping(payload, extra_patterns=_extras(RedactionLevel.EDGE))


def redact_claim_locator(locator: str, *, level: RedactionLevel) -> RedactionResult:
    """Redact a claim source locator.

    A locator is a file path with a line range, a URL with a query token, or a
    database connection string. ``FULL`` adds the graph-plane extras; ``EDGE``
    leaves the engine to decide.
    """
    return redact_for_memory(locator, extra_patterns=_extras(level))


def redact_event_attributes(attributes: Mapping[str, Any], *, level: RedactionLevel) -> MappingRedactionResult:
    """Redact the ``attributes`` bag on a ``RunEvent``.

    This bag is the only place an event carries unstructured key/value data, so
    it is the only place a secret can hide without a fixed schema field to
    target.
    """
    return redact_mapping(attributes, extra_patterns=_extras(level))


def redact_structured(payload: Any, *, level: RedactionLevel) -> Any:
    """Generic entry point: redacts whatever ``payload`` is.

    - ``str`` -> ``redact_event_message``
    - ``Mapping`` -> ``redact_mapping``
    - ``list``/``tuple`` -> recursed element-wise
    - anything else -> returned unchanged

    Used by the event sink when the exact shape is not known at the call site.
    The level is required, so the sink cannot default to the weaker one.
    """
    if isinstance(payload, str):
        return redact_event_message(payload, level=level).redacted_text
    if isinstance(payload, Mapping):
        return redact_mapping(payload, extra_patterns=_extras(level)).redacted_payload
    if isinstance(payload, (list, tuple)):
        return [redact_structured(item, level=level) for item in payload]
    return payload


def summarize(result: RedactionResult | MappingRedactionResult, *, level: RedactionLevel) -> RedactionSummary:
    """Collapse a redaction result into a loggable summary.

    ``kinds`` is the distinct pattern names that fired, sorted, so a metric
    label drawn from this is a closed set rather than a free-form string.
    """
    kinds = {redaction.pattern_name for redaction in result.redactions}
    return RedactionSummary(level=level, count=result.count, kinds=tuple(sorted(kinds)))


# Kept for symmetry with the other helpers and for a caller that wants a
# pre-compiled pattern check without running the whole engine.
def looks_like_connection_string(text: str) -> bool:
    """A cheap pre-check for the connection-string extra, without redacting."""
    return re.search(GRAPH_EXTRA_PATTERNS[1], text) is not None
