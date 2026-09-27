"""Every nginx upstream a location proxies to must actually resolve.

nginx resolves the host in ``proxy_pass`` two completely different ways, and the
two spellings differ by a single ``$``:

* ``proxy_pass http://name;``  -- ``name`` is an ``upstream name { ... }`` group,
  resolved once when the configuration is parsed.
* ``proxy_pass http://$name;`` -- ``$name`` is a *variable*, evaluated per
  request.  nginx does **not** fall back to an ``upstream`` group of the same
  name.  A variable nobody ever assigns evaluates to the empty string, and the
  location then answers ``502`` with ``no host in upstream`` in the error log.

The configuration still *loads*, so nothing reports the mistake until a client
opens the stream.  Both styles are legitimate and both are in use here: the
Docker config assigns its hosts with ``set`` precisely so it can resolve the
container name at request time (so nginx survives a container restart on a new
IP), while the Helm and local configs use static ``upstream`` groups.  The
invariant is therefore not "pick one style" but "the style you picked is the one
you actually wired up".

The repository carries three nginx configurations -- two in ``docker/nginx/`` and
one rendered from a Helm ConfigMap template.  All three are checked here, so a
future copy cannot reintroduce the mismatch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = (
    REPO_ROOT / "docker/nginx/nginx.conf",
    REPO_ROOT / "docker/nginx/nginx.local.conf",
    REPO_ROOT / "deploy/helm/alpha/templates/configmap-nginx.yaml",
)

_UPSTREAM_GROUP = re.compile(r"(?m)^\s*upstream\s+([A-Za-z0-9_.-]+)\s*\{")
_SET_ASSIGNMENT = re.compile(r"(?m)^\s*set\s+\$([A-Za-z0-9_]+)\s")
# `proxy_pass http://host`, `http://host:8002`, `http://host/path`, and the
# variable form of each.  The scheme is required so unrelated URLs are ignored.
_PROXY_PASS = re.compile(r"proxy_pass\s+https?://([^;]+);")
_VARIABLE_HOST = re.compile(r"\$(?:\{)?([A-Za-z0-9_]+)")


def _normalise(path: Path) -> str:
    """Return the nginx source with Helm template control lines removed.

    A Helm template's ``{{ ... }}`` lines are directives, not nginx: keeping them
    would let Go templating braces unbalance the block scan.  Dropping only the
    lines that are *entirely* a template action keeps every nginx directive
    (including one guarded by ``{{- if }}``) visible to the assertions.
    """

    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if "{{" not in line)


def _hosts(config: str) -> list[str]:
    return [match.group(1).strip() for match in _PROXY_PASS.finditer(config)]


@pytest.mark.parametrize("path", CONFIGS, ids=lambda path: path.name)
def test_a_proxied_variable_host_is_actually_assigned(path: Path) -> None:
    """A `$host` in proxy_pass must be given a value by a `set` directive.

    This is the failure that shipped: the Helm ConfigMap declares
    `upstream gateway_upstream { ... }` and then proxies two of its locations to
    `http://$gateway_upstream`, which nginx reads as a variable that no `set`
    ever assigns.  The empty value 502s both WebSocket endpoints.
    """

    config = _normalise(path)
    assigned = set(_SET_ASSIGNMENT.findall(config))
    groups = set(_UPSTREAM_GROUP.findall(config))

    unassigned = {host for host in _hosts(config) if (variable := _VARIABLE_HOST.search(host)) and variable.group(1) not in assigned}

    assert not unassigned, (
        f"{path.name}: proxy_pass uses variable host(s) {sorted(unassigned)} that no "
        f"`set $<name>` directive in this file ever assigns. nginx evaluates an unassigned "
        f"variable as an empty host and answers 502 (`no host in upstream`) -- it does not "
        f"fall back to the `upstream` group of the same name (declared here: {sorted(groups)}). "
        f"Either drop the `$` to use the upstream group, or add a `set $<name> host:port;`."
    )


@pytest.mark.parametrize("path", CONFIGS, ids=lambda path: path.name)
def test_a_bare_proxied_host_is_a_declared_upstream_group(path: Path) -> None:
    """The mirror image: `http://name` needs an `upstream name { ... }` group."""

    config = _normalise(path)
    groups = set(_UPSTREAM_GROUP.findall(config)) | {f"${name}" for name in _SET_ASSIGNMENT.findall(config)}

    undeclared = {host.split("/")[0].split(":")[0].strip() for host in _hosts(config) if not _VARIABLE_HOST.search(host)} - groups

    assert not undeclared, f"{path.name}: proxy_pass targets host(s) {sorted(undeclared)} that are neither an `upstream` group nor a `set` variable (declared: {sorted(groups)}); nginx would fail to start with `host not found in upstream`."


def test_helm_websocket_locations_reach_the_gateway_upstream() -> None:
    """Name the two broken locations so the failure is legible when it regresses.

    `upstream gateway_upstream` is the only gateway definition in this file, so a
    correct WebSocket location proxies to exactly that name.
    """

    config = _normalise(CONFIGS[-1])
    for marker in ("location = /api/multimodal/voice", "location ~ ^/api/threads/[^/]+/browser/stream"):
        start = config.index(marker)
        block = config[start : config.index("}", start)]
        assert "proxy_pass http://gateway_upstream;" in block, f"the {marker!r} location must proxy to the declared `upstream gateway_upstream` group, not to a `$variable` form"
