"""Configuration for the default-OFF authority auditor.

Every knob is read by the census/CLI rather than being decorative:

``enabled``
    Read by :meth:`AuthorityAuditConfig.should_run`.  Runtime embedding must
    opt in; an explicit CLI invocation is itself an explicit request.
``scanned_roots``
    Read by :func:`alpha.safety.authority.census.iter_source_files`.
``excluded_paths``
    Read by the same enumerator and by :meth:`is_excluded`.
``rule_set_path``
    Read by :meth:`read_rule_set` and passed to the classifier.  The file is a
    small JSON object of keyword overrides, so the auditor has no new runtime
    dependency and a bad rule file fails loudly instead of silently changing
    policy.
``baseline_path``
    Read by the CLI when ``diff``/``baseline`` does not receive an explicit
    path.
``output_path``
    Read by the CLI as the sole permitted report destination when no
    ``--output`` is supplied.
``strictness``
    Read by the CLI when deciding whether an unknown is exit-code 1.

The design follows least privilege (small explicit scope) and zero trust
(verify the resource/path at the boundary) rather than inheriting ambient
process configuration.  See the references in :mod:`alpha.safety.authority.models`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Strictness = Literal["permissive", "strict"]

#: Deliberately small production scope.  Tests pass ``scanned_roots=[...]`` or
#: scan a synthetic directory explicitly; no unit test scans the real checkout.
DEFAULT_SCANNED_ROOTS: tuple[str, ...] = (
    "backend/app",
    "backend/packages/harness/alpha",
    "scripts",
)

DEFAULT_EXCLUDED_PATHS: tuple[str, ...] = (
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "backend/tests",
    "frontend",
    "references",
    "docs",
    "dist",
    "build",
    "coverage",
)


class AuthorityAuditConfig(BaseModel):
    """Typed, explicit configuration for the authority census."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = Field(default=False, description="Runtime opt-in; the auditor is default-OFF.")
    scanned_roots: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SCANNED_ROOTS),
        description="Repository-relative roots walked for Python source.",
    )
    excluded_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXCLUDED_PATHS),
        description="Repository-relative path prefixes excluded from the census.",
    )
    rule_set_path: str | None = Field(default=None, description="Optional JSON classification rule overrides.")
    baseline_path: str | None = Field(default=None, description="Default baseline JSON path for diff/write commands.")
    output_path: str | None = Field(default=None, description="Only report path the CLI may write when no flag is given.")
    strictness: Strictness = Field(default="permissive", description="Whether unknown findings fail the command.")

    @property
    def strict(self) -> bool:
        """Boolean view of :attr:`strictness` for small callers."""

        return self.strictness == "strict"

    def should_run(self, *, explicitly_requested: bool = False) -> bool:
        """Reader for ``enabled``.

        Embedded callers must set ``enabled``; the CLI passes
        ``explicitly_requested=True`` because running ``scan`` is the operator's
        explicit request and must not be silently turned into a no-op.
        """

        return self.enabled or explicitly_requested

    def resolve_scanned_roots(self, root: str | Path) -> list[Path]:
        """Reader for ``scanned_roots`` returning existing paths in stable order.

        If all configured roots are absent (the normal shape of a small
        synthetic fixture), the repository root itself is the conservative
        fallback.  A partially missing configuration is reported by the census
        as a gap rather than being silently discarded.
        """

        base = Path(root).resolve()
        resolved: list[Path] = []
        for raw in self.scanned_roots:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = base / candidate
            candidate = candidate.resolve()
            if candidate.exists() and candidate not in resolved:
                resolved.append(candidate)
        if not resolved:
            resolved.append(base)
        return resolved

    def _excluded_candidates(self, root: Path) -> list[Path]:
        candidates: list[Path] = []
        for raw in self.excluded_paths:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = root / candidate
            candidates.append(candidate.resolve())
        return candidates

    def is_excluded(self, path: str | Path, root: str | Path) -> bool:
        """Reader for ``excluded_paths`` with prefix-safe matching."""

        base = Path(root).resolve()
        target = Path(path)
        if not target.is_absolute():
            target = base / target
        target = target.resolve()
        for excluded in self._excluded_candidates(base):
            if target == excluded:
                return True
            try:
                target.relative_to(excluded)
            except ValueError:
                continue
            return True
        return False

    def read_rule_set(self, root: str | Path) -> dict[str, Any]:
        """Reader for ``rule_set_path``.

        The JSON shape is ``{"keyword_rules": {"token": {"rule_id": ...,
        "reversibility": ..., "externality": ...}}}`` or a flat mapping of the
        same keyword objects.  ``None`` means no overrides.  A missing or
        malformed explicit path raises so a CLI invocation can return its
        documented usage/parse exit code instead of auditing with a different
        policy than the operator selected.
        """

        if not self.rule_set_path:
            return {}
        base = Path(root).resolve()
        path = Path(self.rule_set_path)
        if not path.is_absolute():
            path = base / path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read authority rule set {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"authority rule set {path} must contain a JSON object")
        rules = raw.get("keyword_rules", raw)
        if not isinstance(rules, dict):
            raise ValueError(f"authority rule set {path}: keyword_rules must be an object")
        return dict(rules)

    def resolve_baseline_path(self, root: str | Path) -> Path | None:
        """Reader for ``baseline_path``."""

        return self._resolve_optional_path(root, self.baseline_path)

    def resolve_output_path(self, root: str | Path) -> Path | None:
        """Reader for ``output_path``."""

        return self._resolve_optional_path(root, self.output_path)

    def unknown_is_failure(self) -> bool:
        """Reader for ``strictness``."""

        return self.strictness == "strict"

    @staticmethod
    def _resolve_optional_path(root: str | Path, value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = Path(root).resolve() / path
        return path.resolve()

    @classmethod
    def from_file(cls, path: str | Path) -> AuthorityAuditConfig:
        """Load a JSON configuration object without importing Alpha config code."""

        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("authority audit config must contain a JSON object")
        return cls.model_validate(raw)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> AuthorityAuditConfig:
        """Validate a mapping supplied by a host application."""

        return cls.model_validate(data or {})


__all__ = [
    "AuthorityAuditConfig",
    "DEFAULT_EXCLUDED_PATHS",
    "DEFAULT_SCANNED_ROOTS",
    "Strictness",
]
