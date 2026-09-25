"""Pre-write validation for self-configuration proposals.

The order is security-relevant: effective enablement, protected/undeclared
refusals, product-decision authorization, optimistic concurrency, the real
owning Pydantic model, explicit policy bounds, and finally the complete
``AppConfig``.  This mirrors the validate-before-deploy gate in Google SRE's
*Release Engineering* and the deny-by-default feature-flag model described by
Martin Fowler in *Feature Toggles (Kill Switches)*.

No function in this module writes a config file.  A failed full-config load
reports Pydantic's exact field location and leaves the caller's document
untouched.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from .config import SelfTuningConfig
from .models import (
    BlastRadiusClass,
    ChangeSet,
    ConfigChange,
    Refusal,
    RefusalCode,
    ValidationErrorCode,
    ValidationIssue,
    ValidationResult,
    ValidationWarning,
    ValidationWarningCode,
)
from .targets import (
    ConfigTarget,
    PathResolutionStatus,
    TargetRegistry,
    coerce_target_value,
    default_off_subsystem_paths,
)

FullConfigValidator = Callable[[Mapping[str, Any]], object]
"""Injected full-document validator; production default is the real AppConfig."""


@runtime_checkable
class OperatorAuthorizer(Protocol):
    """External authority that verifies an opaque, operator-issued token."""

    def authorize(self, *, token: str, path: str, proposed_value: Any, change_set: ChangeSet) -> bool:
        """Return true only after independently verifying operator authority."""
        ...


def _default_full_config_validator(document: Mapping[str, Any]) -> object:
    """Validate through Alpha's real full configuration model and env resolver."""
    from alpha.config.app_config import AppConfig

    prepared = AppConfig.resolve_env_variables(deepcopy(dict(document)))
    return AppConfig.model_validate(prepared)


def _nested(document: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = document
    for segment in path:
        if not isinstance(value, Mapping) or segment not in value:
            return None
        value = value[segment]
    return value


def _current_target_value(document: Mapping[str, Any], target: ConfigTarget) -> Any:
    """Read the exact YAML value, falling back to the owning model's real default."""
    owner = _nested(document, target.owner_path)
    if not isinstance(owner, Mapping) or target.field_name not in owner:
        return getattr(target.owner_model(), target.field_name)
    return owner[target.field_name]


def _set_nested(document: dict[str, Any], path: tuple[str, ...], key: str, value: Any) -> None:
    cursor = document
    for segment in path:
        nested = cursor.get(segment)
        if nested is None:
            nested = {}
            cursor[segment] = nested
        if not isinstance(nested, dict):
            raise ValueError(f"config path {'.'.join(path)!r} crosses a non-mapping value")
        cursor = nested
    cursor[key] = value


def _format_pydantic_error(prefix: str, exc: ValidationError) -> str:
    """Return the first exact Pydantic field location and message."""
    errors = exc.errors(include_url=False)
    if not errors:  # pragma: no cover - Pydantic always supplies one
        return f"{prefix}: validation failed"
    first = errors[0]
    suffix = ".".join(str(part) for part in first.get("loc", ()))
    location = ".".join(part for part in (prefix, suffix) if part)
    return f"{location}: {first.get('msg', 'invalid value')}"


def _blast_class(targets: tuple[ConfigTarget, ...]) -> BlastRadiusClass:
    if any(target.blast_radius.value == "deployment" for target in targets):
        return BlastRadiusClass.STRUCTURAL
    if any(target.blast_radius.value == "process" for target in targets):
        return BlastRadiusClass.TUNABLE
    if targets:
        return BlastRadiusClass.COSMETIC
    return BlastRadiusClass.NOT_ASSESSED


def _is_close_to_bound(value: Any, target: ConfigTarget) -> bool:
    span = target.ceiling - target.floor
    return span > 0 and (value - target.floor <= span * 0.05 or target.ceiling - value <= span * 0.05)


class ConfigChangeValidator:
    """Validate a whole change set without writing anything."""

    def __init__(
        self,
        config: SelfTuningConfig,
        *,
        registry: TargetRegistry | None = None,
        operator_authorizer: OperatorAuthorizer | None = None,
        full_config_validator: FullConfigValidator | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or TargetRegistry(config)
        self.operator_authorizer = operator_authorizer
        self.full_config_validator = full_config_validator or _default_full_config_validator

    def _operator_decision(
        self,
        change: ConfigChange,
        change_set: ChangeSet,
        current_document: Mapping[str, Any],
    ) -> Refusal | None:
        """Gate default-OFF product decisions through an injected external authority."""
        if change.target_path not in default_off_subsystem_paths():
            return None
        current = _nested(current_document, tuple(change.target_path.split(".")))
        if current is not True and change.proposed_value is not False:
            path = change.target_path
            if change.operator_authorization_token is None:
                return Refusal(
                    code=RefusalCode.OPERATOR_AUTHORIZATION_REQUIRED,
                    path=path,
                    reason="enabling a default-OFF subsystem is a product decision and requires an operator-issued authorization token",
                )
            if self.operator_authorizer is None:
                return Refusal(
                    code=RefusalCode.OPERATOR_AUTHORIZATION_UNVERIFIABLE,
                    path=path,
                    reason="an authorization token was supplied but no independent operator authority was injected to verify it",
                )
            try:
                authorized = self.operator_authorizer.authorize(
                    token=change.operator_authorization_token,
                    path=path,
                    proposed_value=change.proposed_value,
                    change_set=change_set,
                )
            except Exception:
                return Refusal(
                    code=RefusalCode.OPERATOR_AUTHORIZATION_UNVERIFIABLE,
                    path=path,
                    reason="the external operator authority could not verify the supplied token",
                )
            if not authorized:
                return Refusal(
                    code=RefusalCode.OPERATOR_AUTHORIZATION_DENIED,
                    path=path,
                    reason="the independent operator authority denied this product decision",
                )
        return None

    def validate(self, change_set: ChangeSet, current_document: Mapping[str, Any]) -> ValidationResult:
        """Run every pre-write check and return closed, actionable diagnostics."""
        if not self.config.enabled:
            return ValidationResult(ok=False, no_op=True, reason="self_tuning_disabled")
        section = current_document.get("self_tuning")
        if not isinstance(section, Mapping) or section.get("enabled") is not True:
            # The operator-controlled file, not an injected object alone, is the
            # final master switch.
            return ValidationResult(ok=False, no_op=True, reason="self_tuning_disabled_in_config")

        errors: list[ValidationIssue] = []
        warnings: list[ValidationWarning] = []
        refusals: list[Refusal] = []
        candidate = deepcopy(dict(current_document))
        targets: list[ConfigTarget] = []

        for change in change_set.changes:
            path = change.target_path
            product_refusal = self._operator_decision(change, change_set, current_document)
            if product_refusal is not None:
                refusals.append(product_refusal)
                continue

            resolution = self.registry.resolve(path)
            if resolution.status is PathResolutionStatus.PROTECTED:
                refusals.append(Refusal(code=RefusalCode.PROTECTED_PATH, path=path, reason=resolution.reason))
                continue
            if resolution.status is PathResolutionStatus.UNDECLARED or resolution.target is None:
                refusals.append(Refusal(code=RefusalCode.UNDECLARED_PATH, path=path, reason=resolution.reason))
                continue
            target = resolution.target
            targets.append(target)

            if change.blast_radius is not target.blast_radius:
                errors.append(
                    ValidationIssue(
                        code=ValidationErrorCode.BLAST_RADIUS_MISMATCH,
                        path=path,
                        message=f"declared blast radius {change.blast_radius.value!r} does not match the registry value {target.blast_radius.value!r}",
                    )
                )
            if not change.reversible:
                errors.append(ValidationIssue(code=ValidationErrorCode.NOT_REVERSIBLE, path=path, message="every automated config change must provide an exact reversal"))
            if change.rollback_value != change.previous_value:
                errors.append(
                    ValidationIssue(
                        code=ValidationErrorCode.ROLLBACK_VALUE_MISMATCH,
                        path=path,
                        message=f"rollback_value must equal previous_value ({change.previous_value!r})",
                    )
                )

            current_value = _current_target_value(current_document, target)
            if current_value != change.previous_value:
                errors.append(
                    ValidationIssue(
                        code=ValidationErrorCode.STALE_PREVIOUS_VALUE,
                        path=path,
                        message=f"proposal expected previous_value={change.previous_value!r}, but current value is {current_value!r}",
                    )
                )

            try:
                proposed = coerce_target_value(target, change.proposed_value)
            except ValueError as exc:
                errors.append(ValidationIssue(code=ValidationErrorCode.INVALID_DOMAIN, path=path, message=str(exc)))
                continue

            if proposed < target.floor or proposed > target.ceiling:
                bound = "floor" if proposed < target.floor else "ceiling"
                errors.append(
                    ValidationIssue(
                        code=ValidationErrorCode.OUTSIDE_BOUNDS,
                        path=path,
                        message=f"proposed value {proposed!r} is below floor {target.floor!r}" if bound == "floor" else f"proposed value {proposed!r} is above ceiling {target.ceiling!r}",
                    )
                )
                continue

            owner_data = _nested(candidate, target.owner_path)
            owner_dict = dict(owner_data) if isinstance(owner_data, Mapping) else {}
            owner_dict[target.field_name] = proposed
            try:
                validated_owner = target.owner_model.model_validate(owner_dict)
            except ValidationError as exc:
                errors.append(ValidationIssue(code=ValidationErrorCode.OWNER_SCHEMA_INVALID, path=path, message=_format_pydantic_error(path, exc)))
                continue
            _set_nested(candidate, target.owner_path, target.field_name, getattr(validated_owner, target.field_name))

            if not target.hot_reloadable:
                warnings.append(ValidationWarning(code=ValidationWarningCode.SCOPED_CANARY_REQUIRED, path=path, message="target is startup-only; unattended apply must stop for operator restart"))
            if target.blast_radius.value in {"process", "deployment"}:
                warnings.append(ValidationWarning(code=ValidationWarningCode.HIGH_BLAST_RADIUS, path=path, message=f"target blast radius is {target.blast_radius.value}"))
            if _is_close_to_bound(proposed, target):
                warnings.append(ValidationWarning(code=ValidationWarningCode.CLOSE_TO_BOUND, path=path, message=f"proposed value is within 5% of a policy bound [{target.floor}, {target.ceiling}]"))

        if refusals:
            return ValidationResult(
                ok=False,
                errors=tuple(errors),
                warnings=tuple(warnings),
                refusals=tuple(refusals),
                blast_radius_class=_blast_class(tuple(targets)),
                reason="proposal_refused",
            )

        if errors:
            return ValidationResult(
                ok=False,
                errors=tuple(errors),
                warnings=tuple(warnings),
                blast_radius_class=_blast_class(tuple(targets)),
                reason="validation_failed",
            )

        try:
            self.full_config_validator(candidate)
        except ValidationError as exc:
            return ValidationResult(
                ok=False,
                errors=(ValidationIssue(code=ValidationErrorCode.FULL_CONFIG_INVALID, path="full_config", message=_format_pydantic_error("config", exc)),),
                warnings=tuple(warnings),
                blast_radius_class=_blast_class(tuple(targets)),
                reason="resulting_full_config_invalid",
            )
        except Exception as exc:
            return ValidationResult(
                ok=False,
                errors=(ValidationIssue(code=ValidationErrorCode.VALIDATION_UNAVAILABLE, path="full_config", message=f"full config validation could not complete: {exc}"),),
                warnings=tuple(warnings),
                blast_radius_class=_blast_class(tuple(targets)),
                reason="validation_unavailable",
            )

        return ValidationResult(
            ok=True,
            warnings=tuple(warnings),
            blast_radius_class=_blast_class(tuple(targets)),
            reason="validated",
        )


def changes_already_applied(current_document: Mapping[str, Any], change_set: ChangeSet, registry: TargetRegistry) -> bool:
    """Return true when every proposed value equals the observed current value."""
    for change in change_set.changes:
        resolution = registry.resolve(change.target_path)
        if resolution.status is not PathResolutionStatus.ALLOWED or resolution.target is None:
            return False
        try:
            proposed = coerce_target_value(resolution.target, change.proposed_value)
        except ValueError:
            return False
        if proposed != _current_target_value(current_document, resolution.target):
            return False
    return True


def materialize_validated_changes(current_document: Mapping[str, Any], change_set: ChangeSet, registry: TargetRegistry) -> dict[str, Any]:
    """Materialize an already checked set, re-checking allowlist at the write boundary."""
    candidate = deepcopy(dict(current_document))
    for change in change_set.changes:
        resolution = registry.resolve(change.target_path)
        if resolution.status is not PathResolutionStatus.ALLOWED or resolution.target is None:
            raise PermissionError(resolution.reason)
        target = resolution.target
        value = coerce_target_value(target, change.proposed_value)
        owner_data = _nested(candidate, target.owner_path)
        owner_dict = dict(owner_data) if isinstance(owner_data, Mapping) else {}
        owner_dict[target.field_name] = value
        validated_owner = target.owner_model.model_validate(owner_dict)
        _set_nested(candidate, target.owner_path, target.field_name, getattr(validated_owner, target.field_name))
    return candidate
