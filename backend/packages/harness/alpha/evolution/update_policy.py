"""Safe, operator-owned policy for Alpha's self-update engine.

The policy is deliberately separate from ``config.yaml`` and the project
manifest.  The manifest is the repository identity/release-channel contract;
this file is the local execution policy for a source checkout.  It contains
no credentials and is safe to commit as a reviewed default.

The safe default is *check-only and disabled for unattended application*.  An
operator must opt into both ``enabled`` and ``auto_apply`` before the update
loop can mutate a checkout.  This prevents a newly installed Alpha from
pulling code merely because a scheduled task happened to run.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alpha.config.runtime_paths import project_root

POLICY_SCHEMA_VERSION = 1
POLICY_RELPATH = ("config", "update-policy.json")
_ALLOWED_CHANNELS = frozenset({"stable", "beta", "nightly", "main", "development"})
_ALLOWED_MODES = frozenset({"dev", "prod", "none"})


class UpdatePolicyError(ValueError):
    """Raised when the local update policy is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class UpdatePolicy:
    """Validated local update policy.

    ``auto_apply`` is intentionally separate from ``enabled``: an operator can
    enable periodic status checks without allowing a background process to
    change the checkout.
    """

    enabled: bool = False
    auto_apply: bool = False
    channel: str = "stable"
    remote: str = "origin"
    branch: str = "main"
    check_interval_seconds: float = 21_600.0
    jitter_seconds: float = 300.0
    require_clean_worktree: bool = True
    allowed_branches: tuple[str, ...] = ("main",)
    verify_signed_commit: bool = False
    verify_release_version: bool = True
    min_free_disk_mb: int = 1_024
    keep_backups: int = 3
    sync_dependencies: bool = True
    run_config_upgrade: bool = True
    restart_mode: str = "dev"
    health_check_attempts: int = 60
    health_check_interval_seconds: float = 5.0
    health_check_timeout_seconds: float = 10.0
    startup_failure_threshold: int = 3
    rollback_on_failure: bool = True
    restore_dependencies_on_rollback: bool = True
    respect_active_runs: bool = True
    maintenance_drain_seconds: float = 30.0
    health_urls: tuple[str, ...] = (
        "http://127.0.0.1:8001/health/ready",
        "http://127.0.0.1:3000/",
    )
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def public_dict(self) -> dict[str, Any]:
        """Return policy values safe to expose through an operator API."""
        value = asdict(self)
        value.pop("metadata", None)
        value["allowed_branches"] = list(self.allowed_branches)
        value["health_urls"] = list(self.health_urls)
        return value

    @property
    def is_attended_apply_allowed(self) -> bool:
        """Whether an explicit operator/API apply may proceed.

        ``auto_apply`` controls *unattended* initiation only.  An attended
        admin action remains available whenever the hard ``enabled`` switch is
        on; it still has to pass every candidate/worktree safety gate.
        """
        return self.enabled


def _policy_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise UpdatePolicyError(f"Update policy does not exist: {path}")
        return path

    override = os.getenv("ALPHA_UPDATE_POLICY_PATH", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise UpdatePolicyError(f"ALPHA_UPDATE_POLICY_PATH points to a missing file: {path}")
        return path

    candidate = project_root().joinpath(*POLICY_RELPATH)
    if candidate.is_file():
        return candidate

    # A source checkout invoked from a nested directory should still find the
    # repository policy.  The project_root lookup above is the normal path;
    # this bounded parent walk is useful for tests and portable launchers.
    for parent in Path(__file__).resolve().parents:
        candidate = parent.joinpath(*POLICY_RELPATH)
        if candidate.is_file():
            return candidate
    return project_root().joinpath(*POLICY_RELPATH)


def _as_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise UpdatePolicyError(f"update policy field {key!r} must be boolean")
    return value


def _as_number(data: dict[str, Any], key: str, default: float, *, minimum: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UpdatePolicyError(f"update policy field {key!r} must be a number")
    value = float(value)
    if value < minimum:
        raise UpdatePolicyError(f"update policy field {key!r} must be >= {minimum}")
    return value


def _as_int(data: dict[str, Any], key: str, default: int, *, minimum: int, maximum: int | None = None) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise UpdatePolicyError(f"update policy field {key!r} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise UpdatePolicyError(f"update policy field {key!r} must be {bound}")
    return value


def _as_text(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise UpdatePolicyError(f"update policy field {key!r} must be a non-empty string")
    return value.strip()


def _validate_identifier(value: str, field_name: str) -> str:
    # Remote names and branch names are passed as argv to git, never through a
    # shell.  Still reject whitespace/control characters and option-like names
    # so a malformed policy cannot turn into an ambiguous ref.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+-]{0,199}", value) or value.startswith("-") or value.endswith("/") or ".." in value or "@{" in value or any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise UpdatePolicyError(f"update policy field {field_name!r} contains unsafe characters")
    return value


def _validate_health_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UpdatePolicyError(f"health URL must be an absolute http(s) URL: {value!r}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise UpdatePolicyError(f"health URL must not contain credentials/query/fragment: {value!r}")
    # The updater is a local supervisor.  Refuse arbitrary hosts so a policy
    # typo cannot turn the health probe into an SSRF primitive.
    if parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise UpdatePolicyError(f"health URL must target loopback (got {parsed.hostname!r}): {value!r}")
    return value


def _parse_policy(data: dict[str, Any], *, source: str) -> UpdatePolicy:
    if not isinstance(data, dict):
        raise UpdatePolicyError(f"update policy {source} must contain a JSON object")

    schema_version = _as_int(data, "schema_version", POLICY_SCHEMA_VERSION, minimum=1, maximum=POLICY_SCHEMA_VERSION)
    if schema_version != POLICY_SCHEMA_VERSION:
        raise UpdatePolicyError(f"unsupported update policy schema_version {schema_version}; expected {POLICY_SCHEMA_VERSION}")

    channel = _as_text(data, "channel", "stable").lower()
    if channel not in _ALLOWED_CHANNELS:
        raise UpdatePolicyError(f"update policy channel must be one of {sorted(_ALLOWED_CHANNELS)}, got {channel!r}")
    restart_mode = _as_text(data, "restart_mode", "dev").lower()
    if restart_mode not in _ALLOWED_MODES:
        raise UpdatePolicyError(f"update policy restart_mode must be one of {sorted(_ALLOWED_MODES)}, got {restart_mode!r}")

    raw_branches = data.get("allowed_branches", ["main"])
    if not isinstance(raw_branches, list) or not raw_branches:
        raise UpdatePolicyError("update policy allowed_branches must be a non-empty list")
    branches = tuple(_validate_identifier(_as_text({"value": item}, "value", ""), "allowed_branches") for item in raw_branches)
    if len(set(branches)) != len(branches):
        raise UpdatePolicyError("update policy allowed_branches contains duplicates")

    raw_urls = data.get(
        "health_urls",
        [
            "http://127.0.0.1:8001/health/ready",
            "http://127.0.0.1:3000/",
        ],
    )
    if not isinstance(raw_urls, list):
        raise UpdatePolicyError("update policy health_urls must be a list")
    health_urls = tuple(_validate_health_url(_as_text({"value": item}, "value", "")) for item in raw_urls)

    known = {
        "schema_version",
        "enabled",
        "auto_apply",
        "channel",
        "remote",
        "branch",
        "check_interval_seconds",
        "jitter_seconds",
        "require_clean_worktree",
        "allowed_branches",
        "verify_signed_commit",
        "verify_release_version",
        "min_free_disk_mb",
        "keep_backups",
        "sync_dependencies",
        "run_config_upgrade",
        "restart_mode",
        "health_check_attempts",
        "health_check_interval_seconds",
        "health_check_timeout_seconds",
        "startup_failure_threshold",
        "rollback_on_failure",
        "restore_dependencies_on_rollback",
        "respect_active_runs",
        "maintenance_drain_seconds",
        "health_urls",
        "description",
    }
    unknown = sorted(set(data) - known)
    if unknown:
        raise UpdatePolicyError(f"unknown update policy field(s): {', '.join(unknown)}")

    enabled = _as_bool(data, "enabled", False)
    auto_apply = _as_bool(data, "auto_apply", False)
    if auto_apply and not enabled:
        raise UpdatePolicyError("auto_apply=true requires enabled=true")

    return UpdatePolicy(
        enabled=enabled,
        auto_apply=auto_apply,
        channel=channel,
        remote=_validate_identifier(_as_text(data, "remote", "origin"), "remote"),
        branch=_validate_identifier(_as_text(data, "branch", "main"), "branch"),
        check_interval_seconds=_as_number(data, "check_interval_seconds", 21_600.0, minimum=60.0),
        jitter_seconds=_as_number(data, "jitter_seconds", 300.0, minimum=0.0),
        require_clean_worktree=_as_bool(data, "require_clean_worktree", True),
        allowed_branches=branches,
        verify_signed_commit=_as_bool(data, "verify_signed_commit", False),
        verify_release_version=_as_bool(data, "verify_release_version", True),
        min_free_disk_mb=_as_int(data, "min_free_disk_mb", 1_024, minimum=0, maximum=10_000_000),
        keep_backups=_as_int(data, "keep_backups", 3, minimum=1, maximum=20),
        sync_dependencies=_as_bool(data, "sync_dependencies", True),
        run_config_upgrade=_as_bool(data, "run_config_upgrade", True),
        restart_mode=restart_mode,
        health_check_attempts=_as_int(data, "health_check_attempts", 60, minimum=1, maximum=120),
        health_check_interval_seconds=_as_number(data, "health_check_interval_seconds", 5.0, minimum=0.1),
        health_check_timeout_seconds=_as_number(data, "health_check_timeout_seconds", 10.0, minimum=0.1),
        startup_failure_threshold=_as_int(data, "startup_failure_threshold", 3, minimum=1, maximum=20),
        rollback_on_failure=_as_bool(data, "rollback_on_failure", True),
        restore_dependencies_on_rollback=_as_bool(data, "restore_dependencies_on_rollback", True),
        respect_active_runs=_as_bool(data, "respect_active_runs", True),
        maintenance_drain_seconds=_as_number(data, "maintenance_drain_seconds", 30.0, minimum=0.0),
        health_urls=health_urls,
        metadata={"description": data.get("description", "")} if isinstance(data.get("description", ""), str) else {},
    )


def load_update_policy(path: str | Path | None = None) -> UpdatePolicy:
    """Load and validate the policy, returning safe defaults when absent."""
    policy_path = _policy_path(path)
    try:
        raw = policy_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return UpdatePolicy()
    except OSError as exc:
        raise UpdatePolicyError(f"could not read update policy {policy_path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdatePolicyError(f"update policy {policy_path} is not valid JSON: {exc}") from exc
    return _parse_policy(data, source=str(policy_path))


__all__ = [
    "POLICY_RELPATH",
    "POLICY_SCHEMA_VERSION",
    "UpdatePolicy",
    "UpdatePolicyError",
    "load_update_policy",
]
