"""GitHub release detection and the persisted update state.

This module owns the small, stable HTTP-facing state contract used by the
Gateway.  The Phase-2 source transaction lives in
:mod:`alpha.evolution.update_engine`; keeping detection here means the
existing ``/api/evolution/update-check`` and identity endpoints remain
compatible while the richer state machine can evolve independently.

Honesty contract:

* ``_fetch_latest_release`` raises ``RuntimeError`` with the real reason on
  any network/HTTP/decoding failure (rate-limited 403/429 called out). There
  is no mock or fallback release data anywhere in this module.
* ``check_for_update`` never raises and never fabricates: every failure
  becomes a persisted ``CHECK_FAILED`` state carrying the real error message,
  returned in-body (Gateway precedent: failed checks answer 200 with the
  honest state — not a fabricated success, not a 500).
* An unparseable tag or an ``"unknown"`` installed version yields
  ``CHECK_FAILED`` with both values quoted — never a guessed boolean.
* No function in this module mutates a checkout.  Download/install/rollback
  are deliberately delegated to the guarded update engine.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json, resolve_alpha_version
from alpha.evolution.manifest import load_project_manifest

logger = logging.getLogger(__name__)

# Public state machine.  The first five values are the Phase-1 HTTP contract;
# the remaining values are emitted by the Phase-2 source transaction and are
# accepted here so a restart can read a durable in-flight transaction back
# without degrading it to a fabricated IDLE/CHECK_FAILED state.
IDLE = "IDLE"
CHECKING = "CHECKING"
UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
UP_TO_DATE = "UP_TO_DATE"
CHECK_FAILED = "CHECK_FAILED"
DISABLED = "DISABLED"
BLOCKED = "BLOCKED"
APPLY_REQUESTED = "APPLY_REQUESTED"
QUIESCING = "QUIESCING"
DOWNLOADING = "DOWNLOADING"
DOWNLOADED = "DOWNLOADED"
VERIFYING = "VERIFYING"
STAGING = "STAGING"
BACKUP_CREATED = "BACKUP_CREATED"
READY_TO_SWITCH = "READY_TO_SWITCH"
STOPPING = "STOPPING"
INSTALLING = "INSTALLING"
RESTARTING = "RESTARTING"
HEALTH_CHECK = "HEALTH_CHECK"
HEALTHY = "HEALTHY"
ROLLBACK = "ROLLBACK"
RESTORE = "RESTORE"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
FAILED_UPDATE_RECORDED = "FAILED_UPDATE_RECORDED"
STATES = frozenset(
    {
        IDLE,
        CHECKING,
        UPDATE_AVAILABLE,
        UP_TO_DATE,
        CHECK_FAILED,
        DISABLED,
        BLOCKED,
        APPLY_REQUESTED,
        QUIESCING,
        DOWNLOADING,
        DOWNLOADED,
        VERIFYING,
        STAGING,
        BACKUP_CREATED,
        READY_TO_SWITCH,
        STOPPING,
        INSTALLING,
        RESTARTING,
        HEALTH_CHECK,
        HEALTHY,
        ROLLBACK,
        RESTORE,
        RECOVERY_REQUIRED,
        FAILED_UPDATE_RECORDED,
    }
)
IN_FLIGHT_STATES = frozenset(
    {
        APPLY_REQUESTED,
        QUIESCING,
        DOWNLOADING,
        DOWNLOADED,
        VERIFYING,
        STAGING,
        BACKUP_CREATED,
        READY_TO_SWITCH,
        STOPPING,
        INSTALLING,
        RESTARTING,
        HEALTH_CHECK,
        ROLLBACK,
        RESTORE,
        RECOVERY_REQUIRED,
    }
)

UPDATE_STATE_FILE = "update_state.json"
_RELEASE_API_TIMEOUT_SECONDS = 10.0
_MAX_RELEASE_RESPONSE_BYTES = 2 * 1024 * 1024


def _redact_update_error(value: object) -> str:
    text = str(value or "").replace("\x00", "")
    # Keep the persisted diagnostic useful without allowing a token-bearing
    # subprocess/HTTP exception to become an API or support-bundle secret.
    text = re.sub(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\b((?:github[_-]?token|gh[_-]?token|access[_-]?token|api[_-]?key|password|secret)\s*[:=]\s*)[^\s,;&]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    return text[:8_000]


def _reject_redirect_or_oversized_release(response: Any, url: str) -> None:
    status = int(getattr(response, "status_code", 0))
    if 300 <= status < 400:
        raise RuntimeError(f"GitHub release request to {url} returned an unexpected redirect (HTTP {status}).")
    headers = getattr(response, "headers", {}) or {}
    raw_length = headers.get("content-length") if hasattr(headers, "get") else None
    try:
        declared = int(raw_length) if raw_length is not None else 0
    except (TypeError, ValueError):
        declared = 0
    if declared > _MAX_RELEASE_RESPONSE_BYTES:
        raise RuntimeError(f"GitHub release response from {url} exceeds the {_MAX_RELEASE_RESPONSE_BYTES}-byte limit.")
    content = getattr(response, "content", b"")
    if len(content) > _MAX_RELEASE_RESPONSE_BYTES:
        raise RuntimeError(f"GitHub release response from {url} exceeds the {_MAX_RELEASE_RESPONSE_BYTES}-byte limit.")


def _fetch_latest_release(owner: str, name: str) -> dict[str, Any]:
    """GET the latest published GitHub release for ``owner/name`` (spec §22).

    Module-level seam: tests stub this exact symbol. Unauthenticated for the
    public repo; ``GITHUB_TOKEN``/``GH_TOKEN`` are honored from the
    environment when present (env read only — never a hardcoded token).
    Raises ``RuntimeError`` with the real reason on any failure.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        # Pin the API contract instead of relying on GitHub's moving default.
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{owner}/{name}/releases/latest"
    try:
        response = httpx.get(url, headers=headers, timeout=_RELEASE_API_TIMEOUT_SECONDS, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"GitHub latest-release request to {url} failed: {exc}") from exc
    _reject_redirect_or_oversized_release(response, url)
    if response.status_code in (403, 429):
        raise RuntimeError(f"GitHub latest-release request to {url} was refused with HTTP {response.status_code} (rate limited or blocked); retry later or provide GITHUB_TOKEN/GH_TOKEN.")
    if response.status_code != 200:
        raise RuntimeError(f"GitHub latest-release request to {url} returned HTTP {response.status_code} {response.reason_phrase}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"GitHub latest-release response from {url} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub latest-release response from {url} is not a JSON object (got {type(payload).__name__}).")
    return payload


def _fetch_release_for_channel(owner: str, name: str, channel: str) -> dict[str, Any]:
    """Return a published release appropriate for ``channel``.

    ``stable`` deliberately delegates to the original seam so existing tests
    and operators keep the exact ``/releases/latest`` behavior.  Beta/nightly
    are opt-in channels: drafts are ignored, prereleases are accepted only
    for those channels, and the newest publication is selected by the API
    order after validating its shape.  The function never falls back to
    ``main`` or fabricates a release.
    """
    normalized = (channel or "stable").strip().lower()
    if normalized == "stable":
        return _fetch_latest_release(owner, name)
    if normalized not in {"beta", "nightly"}:
        raise RuntimeError(f"Release channel {channel!r} is not a published-release channel.")

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{owner}/{name}/releases?per_page=30"
    try:
        response = httpx.get(url, headers=headers, timeout=_RELEASE_API_TIMEOUT_SECONDS, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"GitHub {normalized} release request to {url} failed: {exc}") from exc
    _reject_redirect_or_oversized_release(response, url)
    if response.status_code in (403, 429):
        raise RuntimeError(f"GitHub {normalized} release request to {url} was refused with HTTP {response.status_code} (rate limited or blocked); retry later or provide GITHUB_TOKEN/GH_TOKEN.")
    if response.status_code != 200:
        raise RuntimeError(f"GitHub {normalized} release request to {url} returned HTTP {response.status_code} {response.reason_phrase}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"GitHub {normalized} release response from {url} is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"GitHub {normalized} release response from {url} is not a JSON list.")
    for release in payload:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if normalized == "nightly" and not release.get("prerelease"):
            # A nightly channel accepts prereleases, but not an accidentally
            # published stable release.
            continue
        if normalized == "beta" and not release.get("prerelease"):
            continue
        if isinstance(release.get("tag_name"), str) and release["tag_name"].strip():
            return release
    raise RuntimeError(f"GitHub has no published {normalized} release for {owner}/{name}.")


def _parse_semver(value: str) -> tuple[int, ...] | None:
    """Parse a stable version/tag (leading ``v`` stripped); None if unparseable."""
    text = value.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts = text.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _is_newer_release(latest_tag: str, installed_version: str) -> bool | None:
    """True/False when both sides parse; None when either side is unparseable.

    ``None`` means "cannot decide" — callers must surface CHECK_FAILED rather
    than guessing an update state.
    """
    latest = _parse_semver(latest_tag)
    installed = _parse_semver(installed_version)
    if latest is None or installed is None:
        return None
    width = max(len(latest), len(installed))
    padded_latest = latest + (0,) * (width - len(latest))
    padded_installed = installed + (0,) * (width - len(installed))
    return padded_latest > padded_installed


def _update_state_path():
    return runtime_home() / UPDATE_STATE_FILE


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _initial_update_state() -> dict[str, Any]:
    return {
        "state": IDLE,
        "checkedAt": None,
        "installedVersion": resolve_alpha_version(),
        "latestTag": None,
        "error": None,
        "stateCorrupt": False,
        # Phase-2 fields are additive and safe for old clients to ignore.
        "availableVersion": None,
        "targetCommit": None,
        "currentCommit": None,
        "source": None,
        "canApply": False,
        "deploymentMode": None,
        "canSelfUpdate": False,
        "reason": None,
        "transactionId": None,
        "backupRef": None,
        "lastAppliedAt": None,
        "lastSuccessfulAt": None,
        "failedAttempts": 0,
        "skippedVersions": [],
        "mutationStarted": False,
        "previousCommit": None,
        "supervisorPid": None,
        "supervisorStartedAt": None,
        "schemaVersion": 1,
        "updatedAt": None,
    }


def load_update_state() -> dict[str, Any]:
    """Persisted update state; IDLE when no check has ever run.

    A corrupt or unreadable state file reports ``CHECK_FAILED`` with the real
    read/parse error — pretending "no failure recorded" would erase history.
    """
    path = _update_state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _initial_update_state()
    except (OSError, ValueError) as exc:
        message = f"Persisted update state {path} is unreadable: {exc}"
        logger.warning(message)
        return {**_initial_update_state(), "state": CHECK_FAILED, "error": message, "stateCorrupt": True}
    if isinstance(raw, dict) and raw.get("state") in STATES:
        return raw
    found = raw.get("state") if isinstance(raw, dict) else type(raw).__name__
    message = f"Persisted update state {path} has an unexpected shape (state={found!r})."
    logger.warning(message)
    return {**_initial_update_state(), "state": CHECK_FAILED, "error": message, "stateCorrupt": True}


def _save_update_state(state: dict[str, Any]) -> bool:
    """Atomically persist the state; failure logs a warning and returns False.

    The check result is always returned in-body regardless, so a read-only
    disk degrades to a logged warning instead of fabricating success or
    raising out of the Gateway route.
    """
    try:
        path = _update_state_path()
        atomic_write_json(path, state)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Could not persist the update state: %s", exc)
        return False


def check_for_update() -> dict[str, Any]:
    """Run one update check end-to-end (spec section 45, through the comparison).

    Never raises: any upstream or local failure becomes a persisted
    ``CHECK_FAILED`` state carrying the real error message, returned in-body.
    """
    existing = load_update_state()
    if existing.get("stateCorrupt") is True:
        return existing
    state: dict[str, Any] = {
        **_initial_update_state(),
        "state": CHECKING,
        "checkedAt": _now_iso(),
        "installedVersion": "unknown",
        "latestTag": None,
        "error": None,
    }
    try:
        state["installedVersion"] = resolve_alpha_version()
        _save_update_state(state)  # transient CHECKING marker before network I/O
        manifest = load_project_manifest()
        repository = manifest["repository"]
        release = _fetch_latest_release(repository["owner"], repository["name"])
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.strip():
            raise RuntimeError("GitHub latest release response has no usable 'tag_name'.")
        state["latestTag"] = tag
        newer = _is_newer_release(tag, state["installedVersion"])
        if newer is None:
            raise RuntimeError(f"Cannot semver-compare installed version {state['installedVersion']!r} with latest tag {tag!r}; refusing to guess an update state.")
        state["state"] = UPDATE_AVAILABLE if newer else UP_TO_DATE
    except Exception as exc:
        state["state"] = CHECK_FAILED
        state["error"] = _redact_update_error(exc)
        logger.warning("Update check failed: %s", state["error"])
    state["checkedAt"] = _now_iso()
    _save_update_state(state)
    return state


def record_check_failure(error: str) -> dict[str, Any]:
    """Build and persist a ``CHECK_FAILED`` state for an escaped check error.

    Last-resort helper for the Gateway route: even a pathological local
    failure answers in-body with the real message — never a 500, never a
    fabricated success.
    """
    installed = "unknown"
    try:
        installed = resolve_alpha_version()
    except Exception as exc:
        logger.warning("Could not resolve the installed version for the failure state: %s", exc)
    state: dict[str, Any] = {
        **_initial_update_state(),
        "state": CHECK_FAILED,
        "checkedAt": _now_iso(),
        "installedVersion": installed,
        "latestTag": None,
        "error": _redact_update_error(error),
    }
    _save_update_state(state)
    return state
