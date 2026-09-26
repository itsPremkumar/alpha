"""The peer plane must be OFF unless an operator opts in.

The peer network opens an INBOUND listener: `remote/pair`, `inbound/messages`
and the websocket handshake are all reachable from outside the machine once it
is enabled. A feature like that should be dark by default and switched on
deliberately.

It is dark by default in TWO places, and both have to agree:

1. `alpha/peer_network/service.py` sets `_DEFAULT_ENABLED = False`, and the
   `ALPHA_PEER_NETWORK_ENABLED` flag now gates the whole plane - previously the
   flag did not actually guard the inbound routes at all, so flipping the
   default alone would have been cosmetic.
2. `docker/docker-compose.yaml` passes the variable through. It previously
   hard-defaulted it to `:-1`, which SILENTLY OVERRODE the secure code default
   and left every containerised deployment with the inbound plane open - the
   exact "secure default defeated one layer up" bug.

Nothing pinned either value, so both could regress unnoticed. These tests pin
them. They are static checks on purpose: importing the service module for its
default would drag in peer identity and persistence side effects, and a test
that mutates real state to read a constant is worse than a source pin.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICE_PY = _REPO_ROOT / "backend" / "packages" / "harness" / "alpha" / "peer_network" / "service.py"
_COMPOSE = _REPO_ROOT / "docker" / "docker-compose.yaml"

_ENV_KEY = "ALPHA_PEER_NETWORK_ENABLED"


def _code_default_enabled() -> bool | None:
    """Read ``_DEFAULT_ENABLED`` from the module source without importing it."""

    tree = ast.parse(_SERVICE_PY.read_text(encoding="utf-8"), filename=str(_SERVICE_PY))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_DEFAULT_ENABLED" in targets:
                return ast.literal_eval(node.value)
    return None


def _compose_env_values() -> dict[str, str]:
    """Every ``KEY=value`` the gateway service declares, per compose file."""

    doc = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for service in (doc.get("services") or {}).values():
        for entry in service.get("environment") or []:
            if "=" not in entry:
                continue
            key, _, value = entry.partition("=")
            found[key.strip()] = value.strip()
    return found


def test_the_code_default_for_the_peer_plane_is_off():
    """The secure default lives here first."""

    assert _SERVICE_PY.is_file(), f"peer service source moved: {_SERVICE_PY}"
    assert _code_default_enabled() is False, (
        "alpha/peer_network/service.py must default the peer plane to DISABLED. "
        "It opens an unauthenticated inbound listener, so a new install must not "
        "expose it without an operator setting ALPHA_PEER_NETWORK_ENABLED=1."
    )


def test_compose_does_not_silently_re_enable_the_peer_plane():
    """The bug this pins: compose defaulted to 1 and defeated the code default."""

    values = _compose_env_values()
    if _ENV_KEY not in values:
        # Not passed through at all is also correct: the code default applies.
        return

    raw = values[_ENV_KEY]
    # Shape is "${ALPHA_PEER_NETWORK_ENABLED:-<default>}".
    match = re.fullmatch(r"\$\{ALPHA_PEER_NETWORK_ENABLED:-([^}]*)\}", raw)
    assert match, f"unrecognised compose shape for {_ENV_KEY}: {raw!r}"
    default = match.group(1).strip().strip("'\"")

    assert default in {"0", "false", "False", "no", "off"}, (
        f"docker-compose.yaml defaults {_ENV_KEY} to {default!r}. That overrides the "
        "code-level secure default and leaves every containerised deployment with the "
        "inbound peer plane open. Default it to 0 and require operators to opt in."
    )


def test_both_layers_agree_on_the_default():
    """Both layers must resolve to OFF.

    An earlier draft of this test asserted the two layers DISAGREE, which meant
    it passed on the bug and failed on the fix - the assertion was inverted.
    The property that matters is the conjunction: whichever way the app was
    installed, the peer plane is off unless an operator opted in.
    """

    values = _compose_env_values()
    code_default = _code_default_enabled()
    assert code_default is False, "code-level default must be OFF"

    if _ENV_KEY not in values:
        # Compose does not pass the variable, so the code default governs.
        return

    match = re.fullmatch(r"\$\{ALPHA_PEER_NETWORK_ENABLED:-([^}]*)\}", values[_ENV_KEY])
    assert match, f"unrecognised compose shape: {values[_ENV_KEY]!r}"
    compose_default = match.group(1).strip().strip("'\"").lower() in {"1", "true", "yes", "on"}

    assert compose_default is False, (
        f"docker-compose.yaml resolves {_ENV_KEY} to ON by default, so a "
        "containerised install gets the inbound peer plane open even though the "
        "code default is off. The effective default must not depend on HOW the "
        "app was installed."
    )
