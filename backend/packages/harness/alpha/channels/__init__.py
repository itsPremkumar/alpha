"""Alpha channel primitives: mentions, transcript, ledger, timing, routing.

Each submodule is independently importable. This ``__init__`` exists so the
package is a regular package (it previously had no ``__init__`` at all and was
relying on namespace-package resolution), and so the public surface is stated in
one place.
"""

from __future__ import annotations

from alpha.channels.mentions import (
    ALL_SELECTOR,
    KIND_ALL,
    KIND_BOT,
    KIND_ROLE,
    MAX_TARGETS_PER_MESSAGE,
    MentionResolution,
    MentionResolutionError,
    MentionSpan,
    MentionTarget,
    normalise_handle,
    parse_mentions,
)
from alpha.channels.timing import (
    Clock,
    Deadline,
    IntakeMessage,
    RoomIntake,
    StageBudget,
    monotonic_clock,
    run_bounded,
)
from alpha.channels.transcript import (
    ChatMessage,
    Presence,
    PresenceReading,
    TranscriptError,
    TranscriptStore,
    console_safe,
    parse_iso,
    read_presence,
    utc_now_iso,
)

__all__ = [
    "ALL_SELECTOR",
    "KIND_ALL",
    "KIND_BOT",
    "KIND_ROLE",
    "MAX_TARGETS_PER_MESSAGE",
    "ChatMessage",
    "Clock",
    "Deadline",
    "IntakeMessage",
    "MentionResolution",
    "MentionResolutionError",
    "MentionSpan",
    "MentionTarget",
    "Presence",
    "PresenceReading",
    "RoomIntake",
    "StageBudget",
    "TranscriptError",
    "TranscriptStore",
    "console_safe",
    "monotonic_clock",
    "normalise_handle",
    "parse_iso",
    "parse_mentions",
    "read_presence",
    "run_bounded",
    "utc_now_iso",
]
