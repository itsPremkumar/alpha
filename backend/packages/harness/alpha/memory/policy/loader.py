"""Strict, hot-reloadable YAML loading for the memory-admission policy.

PyYAML is a declared harness dependency, so this module uses ``safe_load``
semantics with an additional duplicate-key guard.  A valid reload is swapped
only after the whole document and every rule validate.  A bad reload keeps the
last known-good policy and returns a loud failure; an initial bad load creates
an explicit deny-all policy rather than falling back permissively.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from alpha.agents.memory.l1.paths import l1_root

from .engine import PolicySet
from .rules import RULE_ACTIONS, RULE_IDS, AdmissionRule
from .scoring import DEFAULT_SCORE_WEIGHTS, validate_score_weights

DEFAULT_POLICY_PATH = Path(__file__).with_name("defaults.yaml")
_MAX_POLICY_BYTES = 256 * 1024
_RUNTIME_POLICY_NAMES: tuple[str, ...] = (
    "memory-admission.yaml",
    "memory-admission.yml",
    "memory-policy.yaml",
    "memory/policy.yaml",
    "memory/policy/default.yaml",
    "config/memory-admission.yaml",
)


class PolicyValidationError(ValueError):
    """Raised when a policy document cannot be admitted safely."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: _UniqueKeySafeLoader, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConstructorError("while constructing a mapping", node.start_mark, "unhashable mapping key", key_node.start_mark) from exc
        if duplicate:
            raise ConstructorError("while constructing a mapping", node.start_mark, f"duplicate key: {key!r}", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class PolicyReloadResult:
    """Disclosed result of one stat/hash/parse/reload attempt."""

    status: Literal["loaded", "unchanged", "failed"]
    changed: bool
    policy: PolicySet
    reason: str
    error: str = ""
    observed_mtime_ns: int | None = None
    observed_sha256: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"loaded", "unchanged"}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_rule(rule_id: str, raw: Any) -> AdmissionRule:
    if not isinstance(raw, dict):
        raise PolicyValidationError(f"policy.{rule_id} must be a mapping")
    action = raw.get("action")
    if action != RULE_ACTIONS[rule_id]:
        raise PolicyValidationError(
            f"policy.{rule_id}.action must be {RULE_ACTIONS[rule_id]!r}, got {action!r}"
        )
    allowed = {"action"}
    kwargs: dict[str, Any] = {}
    if rule_id == "project_decision":
        allowed.add("require_provenance")
        require_provenance = raw.get("require_provenance")
        if type(require_provenance) is not bool or require_provenance is not True:
            raise PolicyValidationError("policy.project_decision.require_provenance must be true")
        kwargs["require_provenance"] = True
    elif rule_id == "user_preference":
        allowed.add("min_confidence")
        value = raw.get("min_confidence")
        if not _is_number(value) or not 0.0 <= float(value) <= 1.0:
            raise PolicyValidationError("policy.user_preference.min_confidence must be a number in [0, 1]")
        kwargs["min_confidence"] = float(value)
    elif rule_id == "successful_procedure":
        allowed.add("min_successes")
        value = raw.get("min_successes")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise PolicyValidationError("policy.successful_procedure.min_successes must be a positive integer")
        kwargs["min_successes"] = value
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PolicyValidationError(f"policy.{rule_id} has unknown fields: {unknown}")
    return AdmissionRule(rule_id, action, **kwargs)


def parse_policy_document(
    text: str,
    *,
    source: str = "<memory>",
    strict: bool = True,
) -> PolicySet:
    """Parse and validate the section-25 YAML shape plus optional score table."""

    if len(text.encode("utf-8")) > _MAX_POLICY_BYTES:
        raise PolicyValidationError(f"policy {source} exceeds {_MAX_POLICY_BYTES} bytes")
    try:
        payload = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise PolicyValidationError(f"invalid YAML in policy {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PolicyValidationError(f"policy {source} must contain a top-level mapping")

    allowed_top = {"policy", "score_weights", "min_score_to_admit"}
    if not strict:
        allowed_top.add("metadata")
    unknown_top = sorted(set(payload) - allowed_top)
    if unknown_top:
        raise PolicyValidationError(f"policy {source} has unknown top-level fields: {unknown_top}")
    if "metadata" in payload and not isinstance(payload["metadata"], dict):
        raise PolicyValidationError("policy metadata must be a mapping when strict=false")

    raw_policy = payload.get("policy")
    if not isinstance(raw_policy, dict):
        raise PolicyValidationError(f"policy {source} must contain a policy mapping")
    unknown_rules = sorted(set(raw_policy) - set(RULE_IDS))
    missing_rules = sorted(set(RULE_IDS) - set(raw_policy))
    if unknown_rules:
        raise PolicyValidationError(f"policy {source} has unknown rules: {unknown_rules}")
    if missing_rules:
        raise PolicyValidationError(f"policy {source} is missing required rules: {missing_rules}")

    rules = tuple(_parse_rule(rule_id, raw_policy[rule_id]) for rule_id in RULE_IDS)
    raw_weights = payload.get("score_weights", DEFAULT_SCORE_WEIGHTS)
    if not isinstance(raw_weights, dict):
        raise PolicyValidationError("score_weights must be a mapping")
    try:
        weights = validate_score_weights(raw_weights)
    except ValueError as exc:
        raise PolicyValidationError(f"invalid score_weights: {exc}") from exc

    min_score = payload.get("min_score_to_admit", 0.60)
    if not _is_number(min_score) or not 0.0 <= float(min_score) <= 1.0:
        raise PolicyValidationError("min_score_to_admit must be a number in [0, 1]")
    return PolicySet(rules=rules, score_weights=weights).with_overrides(
        min_score_to_admit=float(min_score)
    )


def load_policy_document(
    path: str | Path,
    *,
    strict: bool = True,
) -> PolicySet:
    """Read and validate one policy file, raising on every invalid input."""

    policy_path = Path(path).expanduser().resolve()
    try:
        raw = policy_path.read_bytes()
    except OSError as exc:
        raise PolicyValidationError(f"could not read policy {policy_path}: {exc}") from exc
    if len(raw) > _MAX_POLICY_BYTES:
        raise PolicyValidationError(f"policy {policy_path} exceeds {_MAX_POLICY_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyValidationError(f"policy {policy_path} is not UTF-8: {exc}") from exc
    return parse_policy_document(text, source=str(policy_path), strict=strict)


def resolve_policy_path(
    policy_path: str | Path | None = None,
    *,
    storage_path: str | Path | None = None,
) -> Path:
    """Resolve an explicit path, runtime-home override, or package default."""

    if policy_path is not None:
        candidate = Path(policy_path).expanduser()
        if candidate.is_dir() or not candidate.suffix:
            for name in _RUNTIME_POLICY_NAMES:
                option = candidate / name
                if option.is_file():
                    return option.resolve()
            return (candidate / _RUNTIME_POLICY_NAMES[0]).resolve()
        return candidate.resolve()

    root = l1_root(str(storage_path) if storage_path is not None else None)
    for name in _RUNTIME_POLICY_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate.resolve()
    return DEFAULT_POLICY_PATH.resolve()


class PolicyLoader:
    """Own one file's last-known-good policy and detect changes by mtime + hash."""

    def __init__(
        self,
        policy_path: str | Path | None = None,
        *,
        storage_path: str | Path | None = None,
        strict: bool = True,
    ) -> None:
        self.path = resolve_policy_path(policy_path, storage_path=storage_path)
        self.strict = bool(strict)
        self._mtime_ns: int | None = None
        self._sha256 = ""
        self._policy = PolicySet.fail_closed("policy_not_loaded")
        self._last_result = PolicyReloadResult(
            status="failed",
            changed=False,
            policy=self._policy,
            reason="policy_not_loaded",
            error="policy has not been loaded",
        )
        initial = self.reload_if_changed()
        if initial.status == "failed":
            self._policy = initial.policy
        self._last_result = initial

    @property
    def current_policy(self) -> PolicySet:
        return self._policy

    @property
    def loaded_mtime_ns(self) -> int | None:
        return self._mtime_ns

    @property
    def loaded_sha256(self) -> str:
        return self._sha256

    @property
    def last_result(self) -> PolicyReloadResult:
        return self._last_result

    def _failure(
        self,
        reason: str,
        error: str,
        *,
        mtime_ns: int | None = None,
        digest: str = "",
    ) -> PolicyReloadResult:
        failure_policy = self._policy
        if self._policy.fail_closed_reason in {"policy_not_loaded", "policy_load_failed"}:
            failure_policy = PolicySet.fail_closed(error or reason)
        result = PolicyReloadResult(
            status="failed",
            changed=False,
            policy=failure_policy,
            reason=reason,
            error=error[:2000],
            observed_mtime_ns=mtime_ns,
            observed_sha256=digest,
        )
        self._last_result = result
        return result

    def reload_if_changed(self) -> PolicyReloadResult:
        """Reload only after content hash or mtime changes; never fail open."""

        try:
            raw = self.path.read_bytes()
            stat = self.path.stat()
        except OSError as exc:
            return self._failure("policy_read_failed", str(exc))
        digest = hashlib.sha256(raw).hexdigest()
        if self._sha256 == digest:
            # Content is authoritative; refresh mtime even when a writer touched
            # the file without changing its bytes.
            self._mtime_ns = stat.st_mtime_ns
            result = PolicyReloadResult(
                status="unchanged",
                changed=False,
                policy=self._policy,
                reason="hash_unchanged",
                observed_mtime_ns=stat.st_mtime_ns,
                observed_sha256=digest,
            )
            self._last_result = result
            return result
        try:
            policy = load_policy_document(self.path, strict=self.strict)
        except PolicyValidationError as exc:
            return self._failure(
                "policy_validation_failed",
                str(exc),
                mtime_ns=stat.st_mtime_ns,
                digest=digest,
            )

        self._policy = policy
        self._mtime_ns = stat.st_mtime_ns
        self._sha256 = digest
        result = PolicyReloadResult(
            status="loaded",
            changed=True,
            policy=self._policy,
            reason="policy_loaded",
            observed_mtime_ns=self._mtime_ns,
            observed_sha256=self._sha256,
        )
        self._last_result = result
        return result


__all__ = [
    "DEFAULT_POLICY_PATH",
    "PolicyLoader",
    "PolicyReloadResult",
    "PolicyValidationError",
    "load_policy_document",
    "parse_policy_document",
    "resolve_policy_path",
]
