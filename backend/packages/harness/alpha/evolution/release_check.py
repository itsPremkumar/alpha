"""GitHub latest-release detection and the persisted update state.

Phase-1 subset of the architecture spec: section 22 (published GitHub
releases as the update source) and section 45 (the check algorithm through
"compare semantic versions"). The state machine is a deliberate, documented
SUBSET of spec section 25 — ``IDLE -> CHECKING -> UPDATE_AVAILABLE /
UP_TO_DATE / CHECK_FAILED`` — because download/stage/install/rollback stages
do not exist yet; the full machine arrives with the Phase-2 update engine and
no in-place mutation ever happens here (spec section 26).

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
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json, resolve_alpha_version
from alpha.evolution.manifest import load_project_manifest

logger = logging.getLogger(__name__)

# Update state machine — deliberate subset of spec section 25 (the full
# machine adds DOWNLOADING..HEALTH_CHECK once an install pipeline exists).
IDLE = "IDLE"
CHECKING = "CHECKING"
UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
UP_TO_DATE = "UP_TO_DATE"
CHECK_FAILED = "CHECK_FAILED"
STATES = frozenset({IDLE, CHECKING, UPDATE_AVAILABLE, UP_TO_DATE, CHECK_FAILED})

UPDATE_STATE_FILE = "update_state.json"
_RELEASE_API_TIMEOUT_SECONDS = 10.0


def _fetch_latest_release(owner: str, name: str) -> dict[str, Any]:
    """GET the latest published GitHub release for ``owner/name`` (spec §22).

    Module-level seam: tests stub this exact symbol. Unauthenticated for the
    public repo; ``GITHUB_TOKEN``/``GH_TOKEN`` are honored from the
    environment when present (env read only — never a hardcoded token).
    Raises ``RuntimeError`` with the real reason on any failure.
    """
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{owner}/{name}/releases/latest"
    try:
        response = httpx.get(url, headers=headers, timeout=_RELEASE_API_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"GitHub latest-release request to {url} failed: {exc}") from exc
    if response.status_code in (403, 429):
        raise RuntimeError(
            f"GitHub latest-release request to {url} was refused with HTTP {response.status_code} "
            "(rate limited or blocked); retry later or provide GITHUB_TOKEN/GH_TOKEN."
        )
    if response.status_code != 200:
        raise RuntimeError(f"GitHub latest-release request to {url} returned HTTP {response.status_code} {response.reason_phrase}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"GitHub latest-release response from {url} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub latest-release response from {url} is not a JSON object (got {type(payload).__name__}).")
    return payload


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
        return {**_initial_update_state(), "state": CHECK_FAILED, "error": message}
    if isinstance(raw, dict) and raw.get("state") in STATES:
        return raw
    found = raw.get("state") if isinstance(raw, dict) else type(raw).__name__
    message = f"Persisted update state {path} has an unexpected shape (state={found!r})."
    logger.warning(message)
    return {**_initial_update_state(), "state": CHECK_FAILED, "error": message}


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
    state: dict[str, Any] = {
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
            raise RuntimeError(
                f"Cannot semver-compare installed version {state['installedVersion']!r} with latest tag {tag!r}; "
                "refusing to guess an update state."
            )
        state["state"] = UPDATE_AVAILABLE if newer else UP_TO_DATE
    except Exception as exc:
        state["state"] = CHECK_FAILED
        state["error"] = str(exc) or repr(exc)
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
        "state": CHECK_FAILED,
        "checkedAt": _now_iso(),
        "installedVersion": installed,
        "latestTag": None,
        "error": error,
    }
    _save_update_state(state)
    return state
