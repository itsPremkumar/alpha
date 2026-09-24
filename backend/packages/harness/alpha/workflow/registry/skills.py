"""Skill registry — read-only view of the installed skill storage.

Source of truth: ``alpha.skills.storage.get_or_new_skill_storage().load_skills()``
— the same discovery path the runtime uses (SKILL.md scan + enabled state from
``extensions_config.json``). Reads only: the registry never writes, enables or
disables a skill.

* ``availability="available"`` = the skill exists AND is enabled per config
  (measured by the storage read); disabled skills are honestly
  ``unavailable`` with a non-empty ``reason``.
* ``version`` stays ``None``: SKILL.md frontmatter declares no version field.
* ``health="unverified"``: no skill is executed by this registry.
* If the storage source cannot be read at all, ``list()`` raises
  :class:`RegistryUnavailable` with the real exception text (fail-closed).

``alpha.skills.storage`` is imported lazily so this module stays cheap to
import.
"""

from __future__ import annotations

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
    RegistryUnavailable,
)

_AUTHORITY = "runtime skill authoring + operator config (extensions_config.json skill state)"
_DISABLED_REASON = "skill disabled in extensions_config.json (skill state)"


class SkillRegistry:
    """list/describe/health over installed skills."""

    name = "skills"

    def list(self) -> list[CapabilityDescriptor]:
        return [self._describe_skill(skill) for skill in self._load_skills()]

    def describe(self, skill_name: str) -> CapabilityDescriptor | None:
        for skill in self._load_skills():
            if getattr(skill, "name", None) == skill_name:
                return self._describe_skill(skill)
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
    def _load_skills() -> list[object]:
        try:
            from alpha.skills.storage import get_or_new_skill_storage

            return list(get_or_new_skill_storage().load_skills())
        except Exception as exc:
            raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _describe_skill(skill: object) -> CapabilityDescriptor:
        enabled = bool(getattr(skill, "enabled", False))
        skill_dir = getattr(skill, "skill_dir", None)
        return CapabilityDescriptor(
            id=str(getattr(skill, "name", "")),
            kind="skill",
            availability="available" if enabled else "unavailable",
            source=str(skill_dir) if skill_dir is not None else "alpha.skills.storage",
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
            reason=None if enabled else _DISABLED_REASON,
        )
