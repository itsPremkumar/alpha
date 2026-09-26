"""Machine-readable run output.

A stream-JSON output mode exists so tooling can drive and assert on a run
instead of scraping human-formatted text.  The mode's only real risk is
**silent field loss**: a renamed or dropped key is a consumer break that no test
notices until a downstream job quietly reads ``undefined``.

So the emitter here is *accounted for*:

* :data:`TERMINAL_FIELDS` is the closed list of keys a consumer may rely on.
* :func:`audit_frame` returns the exact set of missing and unexpected keys for
  a frame, so a test can fail on drift.
* :func:`accounted_fields` is the union of everything the emitter can emit, and
  the emitter refuses to write a frame containing a key outside the registry.

That is the difference between "we emit JSON" and "the JSON is a contract".
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = 1

StreamFrameType = Literal["run.started", "message.delta", "tool.call", "tool.result", "error", "run.finished"]

#: Every key a consumer may depend on, by frame type.  Additive changes must add
#: a key here in the same change that emits it.
TERMINAL_FIELDS: dict[str, frozenset[str]] = {
    "run.started": frozenset({"v", "type", "run_id", "thread_id", "model", "started_at", "tools"}),
    "message.delta": frozenset({"v", "type", "run_id", "message_id", "delta"}),
    "tool.call": frozenset({"v", "type", "run_id", "tool_call_id", "name", "arguments"}),
    "tool.result": frozenset({"v", "type", "run_id", "tool_call_id", "name", "ok", "duration_ms", "summary"}),
    "error": frozenset({"v", "type", "run_id", "code", "message", "correlation_id"}),
    "run.finished": frozenset({"v", "type", "run_id", "finished_at", "status", "usage", "stop_reason"}),
}

#: Fields required on every frame, whatever its type.  ``seq`` is here rather
#: than added later so a frame is never emitted before the contract declares it.
ENVELOPE_FIELDS: frozenset[str] = frozenset({"v", "type", "seq"})

for _frame_type in TERMINAL_FIELDS:
    TERMINAL_FIELDS[_frame_type] = frozenset(TERMINAL_FIELDS[_frame_type] | ENVELOPE_FIELDS)
del _frame_type

#: Keys that must never appear, because they would carry data a consumer has no
#: contract for (and, historically, a secret).
FORBIDDEN_FIELDS: frozenset[str] = frozenset({"api_key", "token", "secret", "password", "credential", "authorization", "auth_token"})


class UnknownFrameField(KeyError):
    """A frame carried a key that is not in the accounted registry."""


def accounted_fields() -> dict[str, frozenset[str]]:
    """The full field contract, as a copy."""
    return {frame: set(fields) for frame, fields in TERMINAL_FIELDS.items()}


def audit_frame(frame: Mapping[str, Any]) -> dict[str, list[str]]:
    """Report exactly which contract fields a frame misses or invents.

    ``missing`` is the consumer-breaking direction: a required key the producer
    stopped emitting.  ``unexpected`` is the producer-side direction: a key that
    no consumer has agreed to.
    """
    frame_type = str(frame.get("type", ""))
    expected = TERMINAL_FIELDS.get(frame_type)
    if expected is None:
        return {"unknown_type": [frame_type], "missing": [], "unexpected": []}
    present = set(frame)
    return {
        "unknown_type": [],
        "missing": sorted(expected - present),
        "unexpected": sorted(present - expected),
    }


def is_accounted(frame: Mapping[str, Any]) -> bool:
    report = audit_frame(frame)
    return not report["unknown_type"] and not report["missing"] and not report["unexpected"]


class UnaccountedField(ValueError):
    """A frame tried to carry a field the contract does not declare."""


@dataclass
class StreamJsonEmitter:
    """Emits newline-delimited JSON frames, one per line, UTF-8, unbuffered.

    ``frames`` is retained so a test (or a supervisor) can assert on the whole
    run without re-parsing text.
    """

    run_id: str
    thread_id: str
    model: str = ""
    sink: Any = None
    strict: bool = True
    frames: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0

    def _emit(self, frame_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if frame_type not in TERMINAL_FIELDS:
            raise UnknownFrameField(f"undeclared frame type {frame_type!r}")
        forbidden = FORBIDDEN_FIELDS & set(payload)
        if forbidden:
            raise UnaccountedField(f"frame {frame_type!r} tried to emit contract-excluded fields: {sorted(forbidden)}")
        frame = {"v": SCHEMA_VERSION, "type": frame_type, "seq": 0, **dict(payload)}
        report = audit_frame(frame)
        if report["unexpected"] and self.strict:
            raise UnaccountedField(f"frame {frame_type!r} carries undeclared fields {report['unexpected']}; add them to TERMINAL_FIELDS or stop emitting them")
        if report["missing"] and self.strict:
            raise UnaccountedField(f"frame {frame_type!r} is missing contract fields {report['missing']}")
        self._seq += 1
        frame["seq"] = self._seq
        self.frames.append(frame)
        if self.sink is not None:
            self.sink.write(json.dumps(frame, ensure_ascii=False, default=str) + "\n")
            flush = getattr(self.sink, "flush", None)
            if callable(flush):
                flush()
        return frame

    # -- frame constructors ------------------------------------------------
    def run_started(self, *, tools: Iterable[str] = (), started_at: float = 0.0) -> dict[str, Any]:
        return self._emit(
            "run.started",
            {
                "run_id": self.run_id,
                "thread_id": self.thread_id,
                "model": self.model,
                "started_at": started_at,
                "tools": sorted(tools),
            },
        )

    def message_delta(self, *, message_id: str, delta: str) -> dict[str, Any]:
        return self._emit("message.delta", {"run_id": self.run_id, "message_id": message_id, "delta": delta})

    def tool_call(self, *, tool_call_id: str, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._emit(
            "tool.call",
            {
                "run_id": self.run_id,
                "tool_call_id": tool_call_id,
                "name": name,
                "arguments": dict(arguments),
            },
        )

    def tool_result(self, *, tool_call_id: str, name: str, ok: bool, duration_ms: float, summary: str) -> dict[str, Any]:
        return self._emit(
            "tool.result",
            {
                "run_id": self.run_id,
                "tool_call_id": tool_call_id,
                "name": name,
                "ok": bool(ok),
                "duration_ms": round(float(duration_ms), 3),
                "summary": summary[:2000],
            },
        )

    def error(self, *, code: str, message: str, correlation_id: str = "") -> dict[str, Any]:
        return self._emit(
            "error",
            {
                "run_id": self.run_id,
                "code": code,
                "message": message[:2000],
                "correlation_id": correlation_id,
            },
        )

    def run_finished(
        self,
        *,
        status: str,
        usage: Mapping[str, int] | None = None,
        stop_reason: str = "",
        finished_at: float = 0.0,
    ) -> dict[str, Any]:
        return self._emit(
            "run.finished",
            {
                "run_id": self.run_id,
                "finished_at": finished_at,
                "status": status,
                "usage": dict(usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
                "stop_reason": stop_reason,
            },
        )


def parse_ndjson(text: str) -> Iterator[dict[str, Any]]:
    """Parse an NDJSON stream, raising on a malformed line.

    A parse error is an error, not a skipped line: a consumer that silently drops
    a malformed frame will act on an incomplete run.
    """
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {number} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"line {number} is not a JSON object")
        yield payload


def audit_stream(text: str) -> list[dict[str, list[str]]]:
    """Audit every frame in an NDJSON stream.  One report per frame."""
    return [audit_frame(frame) for frame in parse_ndjson(text)]
