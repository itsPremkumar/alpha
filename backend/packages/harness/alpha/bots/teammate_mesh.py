"""Autonomous Teammate Mesh & Loop Guard.

Provides asynchronous, fire-and-forget direct messaging (message_agent)
with server-side anti-spoof attribution prefixing, teammate roster validation,
and recursion loop detection (BotLoopGuard).
"""

from __future__ import annotations

import collections
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

MESSAGE_MAX_CHARS = 16_000
_ATTRIBUTION_RE = re.compile(r"^\[DM from [^\]]+\]\s*")


class LoopDetectedError(RuntimeError):
    """Raised when recursive cross-agent messaging loop is detected."""
    pass


@dataclass
class DMReceipt:
    receipt_id: str
    sender: str
    target: str
    body: str
    delivered_at: float
    status: str = "delivered"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BotLoopGuard:
    """Guards against runaway conversational ping-pong loops between autonomous agents."""

    def __init__(self, max_exchanges: int = 5, window_seconds: float = 30.0):
        self.max_exchanges = max_exchanges
        self.window_seconds = window_seconds
        self._history: dict[tuple[str, str], list[float]] = collections.defaultdict(list)

    def check_and_record(self, sender: str, target: str) -> None:
        now = time.time()
        pair_key = (sender, target)
        reverse_key = (target, sender)

        self._history[pair_key] = [t for t in self._history[pair_key] if (now - t) <= self.window_seconds]
        self._history[reverse_key] = [t for t in self._history[reverse_key] if (now - t) <= self.window_seconds]

        total_exchange = len(self._history[pair_key]) + len(self._history[reverse_key])
        if (total_exchange + 1) >= self.max_exchanges:
            raise LoopDetectedError(
                f"Recursive bot messaging loop detected between '{sender}' and '{target}' "
                f"({total_exchange + 1} messages in {self.window_seconds}s). Halting exchange."
            )

        self._history[pair_key].append(now)


class AutonomousTeammateMesh:
    """Mesh routing direct messages between autonomous agents with server attribution."""

    def __init__(self):
        self.loop_guard = BotLoopGuard()
        self.roster: dict[str, dict[str, Any]] = {}
        self.inboxes: dict[str, list[DMReceipt]] = collections.defaultdict(list)

    def register_teammate(self, name: str, role: str, description: str = "") -> None:
        self.roster[name] = {"name": name, "role": role, "description": description}

    def format_roster_prompt(self) -> str:
        lines = ["Available Teammates (message with message_agent):"]
        for name, meta in self.roster.items():
            lines.append(f"- {name} ({meta['role']}): {meta.get('description', 'Specialist')}")
        return "\n".join(lines)

    def send_dm(self, sender: str, target: str, message: str) -> DMReceipt:
        clean_target = target.strip()
        if clean_target not in self.roster and "/" not in clean_target:
            raise ValueError(f"Unknown recipient '{clean_target}'. Available teammates: {list(self.roster.keys())}")

        if len(message) > MESSAGE_MAX_CHARS:
            raise ValueError(f"Message exceeds maximum allowed length of {MESSAGE_MAX_CHARS} characters.")

        self.loop_guard.check_and_record(sender, clean_target)

        unprefixed = _ATTRIBUTION_RE.sub("", message.strip())
        attributed_body = f"[DM from {sender}] {unprefixed}"

        receipt = DMReceipt(
            receipt_id=f"dm-{int(time.time() * 1000)}",
            sender=sender,
            target=clean_target,
            body=attributed_body,
            delivered_at=time.time(),
        )

        self.inboxes[clean_target].append(receipt)
        logger.info("Delivered DM from %s to %s", sender, clean_target)
        return receipt

    def get_inbox(self, agent_name: str) -> list[DMReceipt]:
        return list(self.inboxes.get(agent_name, []))

    def clear_inbox(self, agent_name: str) -> int:
        count = len(self.inboxes.get(agent_name, []))
        self.inboxes[agent_name] = []
        return count
