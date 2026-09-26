"""Container logs must be bounded on every service, in every compose file.

Docker's default log driver (``json-file``) has **no size cap**. Every Alpha
compose file relied on that default, so a long-running gateway appended its
stdout/stderr forever until the host disk filled - and the eventual failure looks
like an unrelated crash rather than a log that ate the disk.

The bound has to be pinned in the compose file, because that is the only place a
containerised deployment can be bounded: nothing inside the container can cap
what the daemon writes. These tests parse every shipped compose file and require
each service to resolve (YAML anchors included) to a logging config with a finite
``max-size`` and a ``max-file`` of at least 2.

``max-file >= 2`` matters: with ``max-file: 1`` Docker cannot rotate at all and
silently keeps writing, so a bound of 1 is not a bound.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = REPO_ROOT / "docker"
COMPOSE_FILES = sorted(COMPOSE_DIR.glob("docker-compose*.yaml"))

# `${ALPHA_LOG_MAX_SIZE:-10m}` is a real finite bound, not a placeholder: the
# default is what an operator gets with no extra configuration.
_SIZE_WITH_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(\d+[kmgtKMGT]?[bB]?)\}$")
_INT_WITH_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(\d+)\}$")


def _resolve(value: object) -> str:
    """Unwrap a compose ``${VAR:-default}`` to the default an operator gets.

    Compose substitutes the default when the variable is unset, so the *default*
    is the bound that actually ships. Anything that is neither a literal nor a
    defaulted substitution is passed through for the caller to reject.
    """
    text = str(value)
    for pattern in (_SIZE_WITH_DEFAULT, _INT_WITH_DEFAULT):
        match = pattern.match(text)
        if match:
            return match.group(1)
    return text


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _services(path: Path) -> dict[str, dict]:
    services = _load(path).get("services") or {}
    return {name: service for name, service in services.items() if isinstance(service, dict)}


def test_every_shipped_compose_file_is_covered() -> None:
    """A new compose file must be added here, or it ships unbounded logs."""
    names = {path.name for path in COMPOSE_FILES}

    assert names == {
        "docker-compose.yaml",
        "docker-compose-dev.yaml",
        "docker-compose.cli-auth.yaml",
        "docker-compose.dood.yaml",
        "docker-compose.openviking.yaml",
    }


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
def test_every_service_has_a_bounded_logging_driver(compose_path: Path) -> None:
    services = _services(compose_path)
    assert services, f"{compose_path.name} declares no services"

    offenders: list[str] = []
    for name, service in services.items():
        logging = service.get("logging")
        if not isinstance(logging, dict):
            offenders.append(f"{name}: no logging config (falls back to uncapped json-file)")
            continue
        driver = logging.get("driver")
        options = logging.get("options") or {}
        size = _resolve(options.get("max-size", ""))
        files = _resolve(options.get("max-file", ""))

        if not size:
            offenders.append(f"{name}: logging.options.max-size is missing or unbounded ({options.get('max-size')!r})")
        elif driver == "json-file" and not re.match(r"^\d+[kmgtKMGT]?[bB]?$", size):
            offenders.append(f"{name}: max-size {options.get('max-size')!r} does not resolve to a finite byte size")
        try:
            file_count = int(files)
        except ValueError:
            offenders.append(f"{name}: max-file {options.get('max-file')!r} does not resolve to an integer")
            continue
        if file_count < 2:
            offenders.append(
                f"{name}: max-file is {file_count}; Docker cannot rotate below 2, "
                "so this is not a bound"
            )

    assert not offenders, f"{compose_path.name} ships unbounded container logs: {offenders}"


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
def test_logging_bounds_are_operator_tunable(compose_path: Path) -> None:
    """The cap must be adjustable without editing the compose file."""
    text = compose_path.read_text(encoding="utf-8")
    logging_blocks = re.findall(r"^\s+logging:\s*(\*?\S+)\s*$", text, flags=re.MULTILINE)

    assert logging_blocks, f"{compose_path.name} has no per-service logging: key"
    assert all(re.match(r"^\*bounded_logs$", block) for block in logging_blocks), (
        f"{compose_path.name} must bind logging to the shared *bounded_logs anchor so the "
        f"cap cannot drift per service; found: {logging_blocks}"
    )
    assert "ALPHA_LOG_MAX_SIZE" in text
    assert "ALPHA_LOG_MAX_FILE" in text


def test_the_shared_anchor_is_declared_once_per_file() -> None:
    """Each compose file is a separate document and inherits no anchors."""
    for compose_path in COMPOSE_FILES:
        text = compose_path.read_text(encoding="utf-8")
        declarations = re.findall(r"^x-bounded-logs:\s*&bounded_logs\s*$", text, flags=re.MULTILINE)
        assert len(declarations) == 1, (
            f"{compose_path.name} must declare the anchor exactly once; found {len(declarations)}"
        )
        # An anchor defined but never resolved would silently drop the cap.
        assert re.search(r"logging:\s*\*bounded_logs", text), (
            f"{compose_path.name} declares *bounded_logs but never references it"
        )


def test_log_defaults_match_the_start_ps1_budget() -> None:
    """10m x 3 per service is the same order of magnitude as the 5 MiB
    host-file budget ``start.ps1``/``scripts/rotate_logs.py`` use, so a container
    and a bare-metal deploy are bounded on one scale rather than two."""
    for compose_path in COMPOSE_FILES:
        options = _load(compose_path)["x-bounded-logs"]["options"]
        assert options["max-size"] == "${ALPHA_LOG_MAX_SIZE:-10m}"
        assert options["max-file"] == "${ALPHA_LOG_MAX_FILE:-3}"
