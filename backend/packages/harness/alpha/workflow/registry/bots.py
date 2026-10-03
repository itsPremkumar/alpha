"""Bot-profile registry — read-only view of the ``alpha.bots`` Bot Registry.

Source of truth: ``alpha.bots.registry.get_bot_registry().list_bots()``. Bots were
the one registrable thing missing from the selection plane: they are peer workers
that hold roles, models and skills, so a planner that can see tools and skills but
not bots will invent one instead of reusing a hired specialist.

Archived bots are excluded, deliberately and with a reason. ``list_bots`` defaults
``include_archived=True``, which is right for a roster/org-chart read but wrong
here: an archived bot is soft-deleted by ``retire_bot``, so listing it as an
available capability advertises a worker that is gone. The exclusion is stated in
the module docstring rather than left as a default, because "the count changed and
nobody knows why" is the failure mode the shared descriptor shape exists to remove.

Honesty contract
----------------
* ``availability="available"`` = a **live** profile exists. Measured from the
  persisted roster, not inferred.
* ``availability="unavailable"`` = the profile is archived or carries a status that
  means it will not take work, with the real status in ``reason``.
* ``health`` stays ``unverified``: a roster row proves a profile was written, not
  that the Bot answers a message or passes a smoke test. ``alpha.bots.forge`` makes
  that distinction load-bearing too — "the files exist" and "the Bot works" must
  never be reported the same way.
* ``version`` stays ``None``; a bot profile declares no version.
* ``authority`` names the roster store, because a profile is runtime-authored
  (``bot_roster`` ``forge`` / ``import``) rather than operator config.
* An unreadable roster raises :class:`RegistryUnavailable` with the real exception
  text — a corrupt roster must read as ``unavailable``, never as "no bots exist".

The registry call touches disk, so Gateway callers run this through
``asyncio.to_thread``.
"""

from __future__ import annotations

from typing import Any

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
    RegistryUnavailable,
)

_AUTHORITY = "runtime (Bot Registry roster)"

#: Statuses that mean the profile exists on disk but will not accept work. Listed
#: explicitly so a new status added upstream fails closed as ``available`` only
#: after someone has decided here what it means.
_INACTIVE_STATUSES: frozenset[str] = frozenset({"retired", "archived", "disabled", "killed"})

#: Profile fields quoted in the descriptor address, in a fixed order.
_ADDRESS_FIELDS: tuple[str, ...] = ("role", "department", "model")


def _text(value: Any) -> str:
    return str(value or "").strip()


class BotProfileRegistry:
    """list/describe/health over the live Bot profiles."""

    name = "bots"

    def list(self) -> list[CapabilityDescriptor]:
        return [self._describe_bot(bot) for bot in self._load_bots()]

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        for bot in self._load_bots():
            if _text(getattr(bot, "name", "")) == entry_id:
                return self._describe_bot(bot)
        return None

    def health(self) -> RegistryHealth:
        try:
            descriptors = self.list()
        except Exception as exc:
            return RegistryHealth(
                registry=self.name,
                status="unavailable",
                count=None,
                error=f"{type(exc).__name__}: {exc}",
                evidence_kind="measured",
            )
        return RegistryHealth(
            registry=self.name,
            status="ok",
            count=len(descriptors),
            error=None,
            evidence_kind="measured",
        )

    @staticmethod
    def _load_bots() -> list[Any]:
        try:
            from alpha.bots.registry import get_bot_registry
        except Exception as exc:  # pragma: no cover - production-wired import
            raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
        try:
            # include_archived=False is the point of this registry: see the module
            # docstring. Passing it explicitly rather than relying on a default is
            # what keeps an upstream default change from silently re-advertising
            # retired Bots as capabilities.
            return list(get_bot_registry().list_bots(include_archived=False))
        except Exception as exc:
            raise RegistryUnavailable(f"Bot Registry could not be read: {type(exc).__name__}: {exc}") from exc

    def _describe_bot(self, bot: Any) -> CapabilityDescriptor:
        name = _text(getattr(bot, "name", ""))
        status = _text(getattr(bot, "status", "")) or "unknown"
        handle = _text(getattr(bot, "handle", "")) or name
        address = " / ".join(f"{field}={_text(getattr(bot, field, '')) or 'unset'}" for field in _ADDRESS_FIELDS)
        source = f"alpha.bots.registry:BotRegistry -> {handle or '(unnamed)'} ({address})"

        inactive_reason: str | None = None
        if not name:
            inactive_reason = "profile has no name; the persisted roster is malformed"
        elif status.lower() in _INACTIVE_STATUSES:
            inactive_reason = f"profile status is {status!r}; it will not accept work"

        return CapabilityDescriptor(
            id=name,
            kind="bot_profile",
            availability="unavailable" if inactive_reason else "available",
            source=source,
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
            reason=inactive_reason,
        )


__all__ = ["BotProfileRegistry"]
