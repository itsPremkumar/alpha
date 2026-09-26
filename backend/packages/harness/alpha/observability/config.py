"""Settings for the run-correlation spine. Default-OFF, and a no-op when off.

Every key here is read by exactly one documented consumer, and every consumer
is reachable from :data:`READERS`. That mapping is the point of this module:
a configuration key nobody reads is a lie told in YAML, and this repo's health
subsystem solves the same problem with ``read_all_keys()`` (see
``alpha.memory.health.config``). :data:`READERS` goes one step further and
names the *function* that consumes each key, so a reader test can prove the
key is live rather than merely declared.

The gate
--------
``enabled`` defaults to :data:`DEFAULT_ENABLED` (false) and nothing else turns
it on. When it is false:

* :meth:`TraceRecorder.record` returns ``False`` at its first statement and
  touches no sink -- the test asserts ``sink.calls == 0``, not merely that no
  record appeared;
* :meth:`TraceRecorder.span` yields a span that holds no clock read and no id;
* the file sink is never constructed, so a disabled recorder creates no file
  and no parent directory.

There is no partially-on state: a disabled recorder is inert, not quiet.

The two sampling knobs
---------------------
``sample_rate`` decides **per run**, not per event, because a half-sampled run
is worse than no run -- you cannot correlate a turn you have but not the tool
call that preceded it. The decision is a hash of the ``run_id``, so it is
deterministic and reproducible from the trace file alone, and it is disclosed
through the recorder's ``sampled_out_runs`` counter.

``max_spans_per_run`` / ``max_events_per_run`` bound one run; the sinks bound
the process. Both refusals are counted, and both surface in the recorder's
disclosure and in the metrics bridge, so a cap is something an operator can see
rather than something that silently happens.

The remote sink
---------------
``remote_sink_enabled`` exists so the *absence* of a network export is a
declared, testable decision rather than an oversight. There is no remote sink
implementation in this package at all: the shipped default is ``false`` and the
key exists to be asserted. Turning it on without writing a sink is a
configuration error, refused at recorder construction, not a silent no-op.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from .redaction import REDACTION_POLICIES, STRICT

__all__ = [
    "DEFAULT_ENABLED",
    "READERS",
    "TRACE_SINK_NAMES",
    "NullSinkName",
    "ObservabilityConfig",
    "TraceSinkName",
    "read_all_keys",
    "resolve_all",
]

DEFAULT_ENABLED: Final[bool] = False

#: The closed set of sink names. ``memory`` is the in-memory ring,
#: ``file`` the bounded JSONL writer, ``null`` the no-op. A remote sink is
#: deliberately absent: see the module docstring.
TraceSinkName = Literal["memory", "file", "null"]
NullSinkName: Final[str] = "null"
TRACE_SINK_NAMES: Final[frozenset[str]] = frozenset({"memory", "file", "null"})


class ObservabilityConfig(BaseModel):
    """Settings for the run-correlation spine."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(default=DEFAULT_ENABLED, description="Master gate; false makes the recorder an inert no-op that touches no sink.")
    sinks: list[TraceSinkName] = Field(
        default_factory=lambda: ["memory"],
        description="Which sinks are constructed. Ignored entirely when enabled is false.",
    )
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0, description="Per-run sampling probability; the decision is a hash of run_id, so it is reproducible.")
    max_spans_per_run: int = Field(default=2_000, ge=1, description="Spans one run may record before the recorder starts disclosing refusals.")
    max_events_per_run: int = Field(default=20_000, ge=1, description="Events one run may record before the recorder starts disclosing refusals.")
    max_tracked_runs: int = Field(default=256, ge=1, description="How many runs' per-run counters are retained at once; the least recently ended is evicted and disclosed.")
    max_span_depth: int = Field(default=12, ge=1, description="Nesting depth past which a span is created at the cap and stamped depth_capped.")
    max_attributes_per_span: int = Field(default=64, ge=1, description="Attributes one span may hold before it starts disclosing refusals.")
    max_attribute_value_chars: int = Field(default=1024, ge=16, description="Longest attribute value stored; a longer one is truncated and the truncation disclosed.")
    max_attribute_items: int = Field(default=64, ge=1, description="Items kept from a list-valued or mapping-valued attribute; the rest are replaced by a disclosed marker.")
    max_attribute_depth: int = Field(default=6, ge=1, description="Nesting depth an attribute payload may reach before it is replaced by a disclosed marker.")
    file_sink_path: str | None = Field(
        default=None,
        description="JSONL trace file for the `file` sink. None leaves the sink unconstructed, so a config that lists `file` without a path is an error rather than a silent no-op.",
    )
    file_sink_max_events: int = Field(default=100_000, ge=1, description="Events the file sink writes before it discloses an overflow count and stops growing.")
    file_sink_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, description="Byte budget for the trace file; the sink stops writing at the cap and discloses the shortfall.")
    redaction_policy: Literal["standard", "strict"] = Field(
        default=STRICT,
        validation_alias=AliasChoices("redaction_policy", "redaction"),
        description="Which scrubber policy applies. strict additionally hides credential-shaped key names and bare high-entropy blobs.",
    )
    remote_sink_enabled: bool = Field(default=False, description="Opt-in for a network export. No remote sink ships; true is refused at recorder construction until one exists.")
    metrics_bridge_enabled: bool = Field(default=False, description="Whether the recorder's derived counters are published into the existing metrics registries.")

    @field_validator("redaction_policy", mode="before")
    @classmethod
    def _reject_unknown_policy(cls, value: object) -> object:
        if value is None:
            return STRICT
        if isinstance(value, str) and value not in REDACTION_POLICIES:
            raise ValueError(f"unknown redaction policy {value!r}; expected one of {sorted(REDACTION_POLICIES)}")
        return value

    @field_validator("sinks", mode="before")
    @classmethod
    def _normalize_sinks(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("file_sink_path", mode="before")
    @classmethod
    def _normalize_path(cls, value: object) -> object:
        if isinstance(value, Path):
            return str(value)
        return value

    @property
    def file_sink_enabled(self) -> bool:
        """Whether the file sink can be constructed.

        Derived, not a key: a config listing ``file`` with no path is a
        configuration error and the recorder raises rather than quietly
        dropping the sink.
        """
        return "file" in self.sinks and bool(self.file_sink_path)

    def described_readers(self) -> dict[str, str]:
        """Return ``key -> consumer`` for every key, from :data:`READERS`."""
        return {key: reader.consumer for key, reader in READERS.items()}


class _Reader:
    """One key's reader: how to read it, and which code depends on it."""

    __slots__ = ("consumer", "read")

    def __init__(self, read: Callable[[ObservabilityConfig], Any], consumer: str) -> None:
        self.read = read
        self.consumer = consumer


#: The reader table. This is the audit surface: ``test_observability_trace.py``
#: asserts it covers exactly ``read_all_keys()`` with no extra and no missing
#: key, and that flipping a key changes an observable behaviour.
READERS: Final[Mapping[str, _Reader]] = {
    "enabled": _Reader(lambda c: c.enabled, "TraceRecorder.record early-return; TraceRecorder.span no-op; TraceRecorder construction"),
    "sinks": _Reader(lambda c: list(c.sinks), "TraceRecorder.build_sinks sink construction"),
    "sample_rate": _Reader(lambda c: c.sample_rate, "TraceRecorder._sampled per-run decision"),
    "max_spans_per_run": _Reader(lambda c: c.max_spans_per_run, "TraceRecorder.record_span per-run span cap"),
    "max_events_per_run": _Reader(lambda c: c.max_events_per_run, "TraceRecorder.record per-run event cap"),
    "max_tracked_runs": _Reader(lambda c: c.max_tracked_runs, "TraceRecorder._run_event_counts LRU bound"),
    "max_span_depth": _Reader(lambda c: c.max_span_depth, "Tracer.start_span depth cap"),
    "max_attributes_per_span": _Reader(lambda c: c.max_attributes_per_span, "Span.set_attribute per-span cap"),
    "max_attribute_value_chars": _Reader(lambda c: c.max_attribute_value_chars, "Redactor.max_value_chars"),
    "max_attribute_items": _Reader(lambda c: c.max_attribute_items, "Redactor.max_items"),
    "max_attribute_depth": _Reader(lambda c: c.max_attribute_depth, "Redactor.max_depth"),
    "file_sink_path": _Reader(lambda c: c.file_sink_path, "TraceRecorder.build_sinks file sink target"),
    "file_sink_max_events": _Reader(lambda c: c.file_sink_max_events, "JsonlFileSink event cap"),
    "file_sink_max_bytes": _Reader(lambda c: c.file_sink_max_bytes, "JsonlFileSink byte budget"),
    "redaction_policy": _Reader(lambda c: c.redaction_policy, "TraceRecorder redactor construction"),
    "remote_sink_enabled": _Reader(lambda c: c.remote_sink_enabled, "TraceRecorder construction refusal when no remote sink exists"),
    "metrics_bridge_enabled": _Reader(lambda c: c.metrics_bridge_enabled, "TraceRecorder.derived_metrics opt-in"),
}


def read_all_keys(config: ObservabilityConfig | None = None) -> frozenset[str]:
    """Return the canonical schema keys (or *config*'s own keys)."""
    if config is None:
        return frozenset(ObservabilityConfig.model_fields)
    return frozenset(type(config).model_fields)


def resolve_all(config: ObservabilityConfig | None = None) -> dict[str, Any]:
    """Read every key through :data:`READERS` and return the resolved map.

    This is the one function a coverage test calls instead of touching fields
    directly, so a key that stops being consumed fails the table rather than
    quietly drifting.
    """
    target = config if config is not None else ObservabilityConfig()
    return {key: reader.read(target) for key, reader in READERS.items()}
