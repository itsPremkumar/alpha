"""Deployment-surface parity: every nginx surface must route the same paths.

The defect this file exists to prevent is a class, not an incident. Alpha
ships **three** nginx configurations that are all supposed to express one
routing table:

* ``docker/nginx/nginx.conf``            -- the Docker (prod + dev) entry
* ``docker/nginx/nginx.local.conf``      -- the ``make dev`` local entry
* ``deploy/helm/alpha/templates/configmap-nginx.yaml`` -- the Kubernetes entry

and each one is a hand-maintained copy of the others. Copy-drift is silent by
construction: a location that exists in two files and not the third still
*loads*, still passes a syntax check, and still returns a response -- just from
the wrong upstream. That is the failure mode this file closes.

The concrete incident that motivated it: the Helm ConfigMap shipped with none
of the three Alpha-to-Alpha locations, so ``/.well-known/agent-card.json``
fell through to ``location /`` (Next.js, which has no such route) and
``/api/peer-network/ws`` fell into the generic ``/api/`` block, which does not
forward ``Upgrade``/``Connection``, so the WebSocket was downgraded to plain
HTTP. Alpha-to-Alpha was dead in a Helm install with a green build. The guard
that should have caught it, ``test_nginx_peer_network.py``, parametrised over
the two docker files only -- while ``test_nginx_voice_websocket.py`` correctly
included the Helm ConfigMap. **A guard that enumerates some of the surfaces is
worse than no guard**, because it reads as coverage. Every assertion in this
file is therefore written to enumerate *all three*, and a new nginx surface
added to the repository fails the suite until it is either added to
``NGINX_SURFACES`` here or proven not to be a routing surface.

What is deliberately NOT asserted here:

* Body-level directive drift (``proxy_pass`` spelling, ``$scheme`` vs
  ``$forwarded_proto``, redundant ``proxy_buffering off``). Those are
  environment-specific on purpose -- the Docker file resolves container DNS at
  request time, the local and Helm files use static upstream groups -- and
  ``test_infra_audit_nginx_upstream_resolution.py`` already owns the
  spelling question that actually breaks nginx.
* The provisioner location, which is conditional in the Helm file
  (``provisioner.enabled``) and absent from the local file because no
  provisioner runs under ``make dev``. That asymmetry is real and is recorded
  explicitly in ``OPTIONAL_LOCATIONS`` below so it stays a decision rather than
  becoming drift.

Upstream style differs by surface: ``nginx.conf`` uses ``set $gateway_upstream``
(resolve container DNS per request, survive a restart on a new IP) while the
local and Helm files use static ``upstream`` groups. Only the *path set* and the
WebSocket upgrade forwarding are required to match.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every nginx configuration that serves Alpha's public entry point. Adding a
#: fourth surface (a reverse-proxy sidecar, a second Helm release, a new
#: compose file) means adding it here, which is the point.
NGINX_SURFACES: tuple[Path, ...] = (
    REPO_ROOT / "docker/nginx/nginx.conf",
    REPO_ROOT / "docker/nginx/nginx.local.conf",
    REPO_ROOT / "deploy/helm/alpha/templates/configmap-nginx.yaml",
)

SURFACE_IDS = ("docker", "local", "helm")


def _read(path: Path) -> str:
    """Return the nginx source with Helm template control lines removed.

    A ``{{ ... }}`` line in the ConfigMap is a Go-template directive, not nginx.
    Dropping only lines that are *entirely* a template action keeps every nginx
    directive visible -- including one wrapped in ``{{- if }}`` -- while keeping
    Go's braces from unbalancing the block scan.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if "{{" not in line)


_LOCATION = re.compile(r"(?m)^[ \t]*location\s+(=\s+|~\*?\s+|~\s+)?([^\s{]+)[ \t]*\{")


def _locations(config: str) -> dict[str, int]:
    """Return ``{normalised location specifier: start offset}``.

    A specifier is the modifier plus the path with internal whitespace
    collapsed, e.g. ``"= /api/multimodal/voice"`` or
    ``"~ ^/api/threads/[^/]+/uploads"``. The modifier is part of the identity:
    ``location /api/x`` and ``location = /api/x`` route differently and a
    prefix match that shadows an exact match is a real defect.
    """
    found: dict[str, int] = {}
    for match in _LOCATION.finditer(config):
        modifier = (match.group(1) or "").strip()
        found[f"{modifier} {match.group(2).strip()}".strip()] = match.start()
    return found


def _block(config: str, start: int) -> str:
    """Return the brace-balanced block beginning at ``start``."""
    opening = config.index("{", start)
    depth = 0
    for index in range(opening, len(config)):
        if config[index] == "{":
            depth += 1
        elif config[index] == "}":
            depth -= 1
            if depth == 0:
                return config[opening : index + 1]
    raise AssertionError(f"unterminated nginx block at offset {start}")


#: Locations that are legitimately absent from some surfaces, mapped to the
#: reason. ``OPTIONAL_LOCATIONS[spec] = (absent_from, reason)`` asserts that
#: ``spec`` is **missing from exactly** those surfaces and **present in** at
#: least one other, so it fails both when the asymmetry is silently resolved
#: and when the location is deleted everywhere. Each entry is a decision, not
#: an omission.
OPTIONAL_LOCATIONS: dict[str, tuple[tuple[str, ...], str]] = {
    # The provisioner is a separate service on 8002. `make dev` runs no
    # provisioner container and the local Gateway has no /api/sandboxes route,
    # so the request 404s there by design rather than proxying to a host that
    # is not there. Both container surfaces do route it to the provisioner.
    "/api/sandboxes": (("local",), "no provisioner service under `make dev`; the local Gateway serves no /api/sandboxes route"),
    # Helm's liveness-only endpoint so the nginx pod is not restart-looped while
    # the Gateway image pulls. Deliberately not present in the compose configs,
    # which have no nginx healthcheck.
    "= /nginx-health": (("docker", "local"), "Kubernetes liveness probe; the compose profiles have no nginx healthcheck"),
}


@lru_cache(maxsize=1)
def _gateway_websocket_routes() -> tuple[tuple[str, str], ...]:
    """Derive ``(source file, full route)`` for every Gateway WebSocket route.

    Read from the routers rather than hard-coded, so a fourth WebSocket cannot
    be added without this file noticing. A new socket with no nginx location is
    the same defect as a missing peer-network location: it is proxied as plain
    HTTP by whatever location happens to match, and the client sees a confusing
    close or a 400 rather than a routing error.
    """
    routers = REPO_ROOT / "backend/app/gateway/routers"
    decorator = re.compile(r"@(\w+)\.websocket\(")
    routes: list[tuple[str, str]] = []

    for path in sorted(routers.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        # Each router module binds one or more APIRouter objects; a WebSocket
        # declared on `router` inherits that object's prefix.
        prefixes = {name: (prefix or "") for name, prefix in re.findall(r'(?m)^(\w+)\s*=\s*APIRouter\((?:[^\n]*?prefix="([^"]*)")?', text)}
        for match in decorator.finditer(text):
            variable = match.group(1)
            route = text[match.end() : text.index(")", match.end())].strip().strip('"')
            routes.append((str(path.relative_to(REPO_ROOT)), f"{prefixes.get(variable, '')}{route}"))

    return tuple(sorted(routes))


def test_the_websocket_inventory_matches_the_gateway_routers() -> None:
    """``WEBSOCKET_ROUTES`` must describe every socket the Gateway serves.

    A new ``@router.websocket(...)`` with no matching nginx location is a
    downgraded socket on all three deployment surfaces. Deriving the inventory
    from the routers is what makes that visible: the list in this file is
    asserted against the source, so it cannot quietly go stale.
    """
    derived = {route for _, route in _gateway_websocket_routes()}
    declared = set(WEBSOCKET_ROUTES)

    assert derived, "no WebSocket routes found under backend/app/gateway/routers -- the scan is broken, not the Gateway"
    assert derived == declared, (
        f"the Gateway's WebSocket routes and WEBSOCKET_ROUTES disagree. "
        f"gateway-only={sorted(derived - declared)} test-only={sorted(declared - derived)}. "
        f"Every route in the gateway-only set needs a location that forwards Upgrade/Connection."
    )


def test_no_websocket_route_is_served_outside_the_gateway_router_package() -> None:
    """An extension or channel must not add a WebSocket behind nginx's back.

    WebSocket upgrades bypass ``AuthMiddleware`` (it is a ``BaseHTTPMiddleware``
    and handles HTTP only), so a socket has to carry its own authentication and
    Origin check -- which is why ``alpha.extensions.gateway`` refuses to
    contribute WebSocket routes. The inventory above is therefore complete only
    while that refusal holds; this test fails if a WebSocket appears anywhere
    else in the Gateway app, so the omission is a decision rather than a gap.
    """
    import os  # noqa: PLC0415

    app_root = REPO_ROOT / "backend/app"
    decorator = re.compile(r"@(\w+)\.websocket\(")
    outside: list[str] = []

    for root, dirs, files in os.walk(app_root):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", "node_modules"}]
        if Path(root).name == "routers" and Path(root).parent.name == "gateway":
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in decorator.finditer(text):
                route = text[match.end() : text.index(")", match.end())].strip().strip('"')
                outside.append(f"{path.relative_to(REPO_ROOT)}: {route}")

    assert not outside, (
        f"WebSocket route(s) declared outside backend/app/gateway/routers: {outside}. "
        f"Add a forwarding nginx location on all three surfaces, or confirm the route is "
        f"unreachable through nginx (channel clients use outbound sockets, not inbound ones)."
    )


#: The routing table every surface must share: the paths a client reaches
#: through nginx, and which upstream answers them.
#:
#: ``gateway`` = the FastAPI Gateway (native ``/api/*`` routes)
#: ``frontend`` = the Next.js app
#: ``provisioner`` = the optional sandbox provisioner (conditional in Helm)
SHARED_ROUTING_TABLE: dict[str, str] = {
    "/api/langgraph/": "gateway",
    "/api/models": "gateway",
    "/api/memory": "gateway",
    "/api/mcp": "gateway",
    "= /api/skills/install/upload": "gateway",
    "/api/skills": "gateway",
    "/api/agents": "gateway",
    "~ ^/api/threads/[^/]+/uploads": "gateway",
    "= /api/multimodal/voice": "gateway",
    "~ ^/api/threads/[^/]+/browser/stream": "gateway",
    "~ ^/api/threads": "gateway",
    "/docs": "gateway",
    "/redoc": "gateway",
    "/openapi.json": "gateway",
    "/health": "gateway",
    "= /.well-known/agent-card.json": "gateway",
    "= /.well-known/agent.json": "gateway",
    "= /api/peer-network/ws": "gateway",
    "/api/": "gateway",
    "/": "frontend",
}

#: Every Gateway WebSocket route must have a location that forwards the
#: upgrade handshake. Derived from the routers themselves, so a new
#: WebSocket route fails this file until nginx is taught about it.
#:
#: 1. ``/api/multimodal/voice``                    -- voice conversation (PTT, wake word, realtime)
#: 2. ``/api/threads/{thread_id}/browser/stream``   -- Live browser frame stream
#: 3. ``/api/peer-network/ws``                      -- Alpha-to-Alpha transport
#:
#: The health/readiness routes are plain HTTP. Anything else the Gateway
#: serves as a WebSocket must be added here with its nginx location.
WEBSOCKET_ROUTES: dict[str, str] = {
    "/api/multimodal/voice": "= /api/multimodal/voice",
    "/api/threads/{thread_id}/browser/stream": "~ ^/api/threads/[^/]+/browser/stream",
    "/api/peer-network/ws": "= /api/peer-network/ws",
}

#: The only regex location that competes with a regex WebSocket location.
#:
#: ``~ ^/api/threads/[^/]+/uploads`` looks like a candidate but its pattern
#: requires the path to *end* in ``/uploads``, so it can never match
#: ``/api/threads/<id>/browser/stream``. Only the unanchored catch-all
#: ``~ ^/api/threads`` overlaps, and it must come after the WebSocket one.
#: Expressed as data so the ordering assertion below cannot be widened by
#: accident into something that fails for a non-overlapping pattern.
WS_REGEX_SHADOWS: dict[str, tuple[str, ...]] = {
    "~ ^/api/threads/[^/]+/browser/stream": ("~ ^/api/threads",),
}

#: Regex locations proven NOT to match any WebSocket route, so they are exempt
#: from the "every regex must be classified" rule above. Each entry states the
#: reason; a pattern that stops being disjoint has to be moved.
NON_OVERLAPPING_REGEXES: dict[str, str] = {
    "~ ^/api/threads/[^/]+/uploads": "the pattern is anchored on a trailing `/uploads`, so it cannot match `.../browser/stream`",
}

#: A WebSocket location must forward the handshake and keep the socket open.
#: These are the exact directives the voice test pins; restated here so the
#: parity check is self-contained and covers all three WebSocket routes, not
#: only voice.
REQUIRED_WEBSOCKET_DIRECTIVES = (
    "proxy_http_version 1.1;",
    "proxy_set_header Upgrade $http_upgrade;",
    "proxy_set_header Connection 'upgrade';",
    "proxy_read_timeout 600s;",
)


def test_the_surface_list_covers_every_nginx_config_in_the_repository() -> None:
    """A guard that enumerates some of the surfaces reads as coverage.

    This is the asymmetry that let the Helm regression through: the peer-network
    test parametrised over two files and the voice test over three, and nobody
    reconciled them. Any new nginx config under ``docker/``, ``deploy/`` or
    ``build/`` must appear in ``NGINX_SURFACES`` (or be shown not to be a
    routing surface) rather than being silently unguarded.
    """
    # Searched by content across the deployment trees rather than by a
    # hard-coded list, so a new surface cannot be added without a decision.
    # The roots are the directories that ship deployment config; the harness
    # and frontend are not deployment surfaces.
    #
    # ``.tools`` is a vendored nginx *binary distribution* — a stock
    # ``conf/nginx.conf`` plus ``fastcgi.conf`` shipped with the download. It is
    # gitignored (``.gitignore:143``) and git tracks zero files beneath it, so
    # it is not a surface this repository owns and cannot drift. Pruning it is
    # the honest fix; listing it in NON_ROUTING_SURFACES would enshrine a path
    # that must never be committed as a routing surface.
    import os  # noqa: PLC0415

    prune = {
        ".git",
        ".tools",
        "node_modules",
        ".venv",
        "__pycache__",
        ".next",
        "site-packages",
        "alembic",
        "tests",
        "backend",
        "frontend",
    }
    tracked: list[Path] = []
    for root in ("docker", "deploy", "build", "scripts", "."):
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for current, dirs, files in os.walk(base):
            here = Path(current)
            if root == ".":
                dirs[:] = [d for d in dirs if d not in prune]
            for name in files:
                path = here / name
                if name.endswith((".conf", ".yaml", ".yml", ".tmpl", ".tpl")) and "nginx" in name.lower():
                    tracked.append(path)

    # A ConfigMap carries its nginx config inside a YAML literal block, so it
    # is found by content rather than by extension.
    for path in (REPO_ROOT / "deploy").rglob("*.yaml"):
        if "nginx" in path.read_text(encoding="utf-8", errors="replace") and "proxy_pass" in path.read_text(encoding="utf-8", errors="replace"):
            tracked.append(path)

    declared = {path.resolve() for path in NGINX_SURFACES}
    exempt = {(REPO_ROOT / relative).resolve() for relative in NON_ROUTING_SURFACES}
    undeclared = {path.resolve() for path in tracked} - declared - exempt

    assert not undeclared, (
        "these nginx configurations are not covered by the parity guard; add them to "
        f"NGINX_SURFACES in {Path(__file__).name}, or list them in NON_ROUTING_SURFACES with "
        f"the reason they carry no routing: "
        f"{sorted(str(path.relative_to(REPO_ROOT)) for path in undeclared)}"
    )


#: nginx-named files that ship with the deployment but carry no ``location``
#: block, so there is no routing table in them to keep in parity. They are
#: listed rather than ignored by a name filter, because a name filter is
#: exactly the kind of implicit exclusion that let the Helm regression through.
NON_ROUTING_SURFACES: dict[str, str] = {
    "deploy/helm/alpha/templates/nginx-deployment.yaml": "the nginx Pod spec: mounts the ConfigMap, declares the /nginx-health liveness probe, no routing",
    "deploy/helm/alpha/templates/nginx-service.yaml": "the nginx Service: publishes container port 2026, no routing",
}


@pytest.mark.parametrize("relative", sorted(NON_ROUTING_SURFACES))
def test_a_non_routing_surface_still_carries_no_routing(relative: str) -> None:
    """A file exempted from the parity table must stay exempt for its reason.

    If a ``location`` block is ever added to one of these, it becomes a routing
    surface with no parity guard, which is the same class of hole this file
    exists to close. Promote it to ``NGINX_SURFACES`` in the same change.
    """
    path = REPO_ROOT / relative
    assert path.exists(), f"{relative} is listed in NON_ROUTING_SURFACES but no longer exists; remove the entry"

    config = _read(path)
    assert "location " not in config, f"{relative} is listed in NON_ROUTING_SURFACES ({NON_ROUTING_SURFACES[relative]}) but now declares a `location` block, so it is a routing surface. Move it to NGINX_SURFACES."


@pytest.mark.parametrize("path", NGINX_SURFACES, ids=SURFACE_IDS)
def test_the_shared_routing_table_is_present_on_every_surface(path: Path) -> None:
    """Every shared route must exist, with the right modifier, on every surface.

    A missing location is the whole defect: nginx has no "route not found"
    answer, it falls through to the longest matching prefix and returns a
    perfectly good response from the wrong upstream.
    """
    locations = _locations(_read(path))

    missing = sorted(spec for spec in SHARED_ROUTING_TABLE if spec not in locations)
    assert not missing, (
        f"{path.relative_to(REPO_ROOT)} is missing routing-table location(s) {missing}. "
        f"It serves {len(locations)} location(s) today. Copy the block from "
        f"docker/nginx/nginx.conf and match it exactly, including the modifier "
        f"(`= ` for an exact match, `~ ` for a regex) and any body-size/timeout directives."
    )


@pytest.mark.parametrize("path", NGINX_SURFACES, ids=SURFACE_IDS)
def test_no_surface_carries_a_location_the_others_do_not_know(path: Path) -> None:
    """The reverse direction: an extra location is how drift hides.

    A location that exists only here is either a genuine deployment-specific
    need -- in which case it belongs in ``OPTIONAL_LOCATIONS`` with a reason --
    or it is drift that a future edit will propagate by copy-paste.
    """
    surface = path.relative_to(REPO_ROOT)
    surface_id = SURFACE_IDS[NGINX_SURFACES.index(path)]
    here = set(_locations(_read(path)))
    everywhere: set[str] = set()

    for other in NGINX_SURFACES:
        everywhere |= set(_locations(_read(other)))

    unexplained = sorted(spec for spec in here - everywhere if surface_id not in OPTIONAL_LOCATIONS.get(spec, ((), ""))[0])
    assert not unexplained, (
        f"{surface} declares location(s) {unexplained} that no other nginx surface has. "
        f"If the difference is real, record it in OPTIONAL_LOCATIONS with the reason it "
        f"cannot apply everywhere; otherwise the other surfaces are missing the route."
    )


def test_every_allowlisted_asymmetry_is_still_real() -> None:
    """An exemption may not outlive the asymmetry it describes.

    ``OPTIONAL_LOCATIONS`` records that a location is absent from one surface.
    Two ways that entry can go stale, both of which would let a later
    regression hide behind it:

    * the difference is resolved (every surface now declares the location) --
      remove the entry, so the next accidental divergence is a failure;
    * the location is deleted *everywhere* -- the entry would then excuse a
      route that no deployment mode serves.
    """
    for spec, (absent_from, reason) in sorted(OPTIONAL_LOCATIONS.items()):
        assert set(absent_from) <= set(SURFACE_IDS), f"OPTIONAL_LOCATIONS names unknown surface(s) {sorted(set(absent_from) - set(SURFACE_IDS))} for {spec!r}"
        assert absent_from, f"OPTIONAL_LOCATIONS entry for {spec!r} exempts no surface, so it describes no asymmetry; remove it"

        for surface_id, path in zip(SURFACE_IDS, NGINX_SURFACES):
            declared = spec in _locations(_read(path))
            if surface_id in absent_from:
                assert not declared, f"OPTIONAL_LOCATIONS says {spec!r} is absent from {surface_id!r} ({reason}), but that surface declares it -- remove the stale exemption"
            else:
                assert declared, f"{path.relative_to(REPO_ROOT)} is missing {spec!r}, which every non-exempt surface must declare (see OPTIONAL_LOCATIONS: {reason})"


@pytest.mark.parametrize("path", NGINX_SURFACES, ids=SURFACE_IDS)
def test_every_websocket_route_has_a_location_on_every_surface(path: Path) -> None:
    """Every Gateway WebSocket must be reachable, with its upgrade forwarded.

    This is the guard the Helm regression needed and did not have. A WebSocket
    that nginx proxies as plain HTTP fails *at the client*, usually as a
    confusing ``400`` or an immediate close, far from the config that caused it.
    """
    config = _read(path)
    locations = _locations(config)

    for route, spec in WEBSOCKET_ROUTES.items():
        assert spec in locations, f"{path.relative_to(REPO_ROOT)} has no nginx location for Gateway WebSocket {route} (expected `location {spec}`). Without it the socket is downgraded to plain HTTP by whatever location matches instead."
        block = _block(config, locations[spec])
        missing = [directive for directive in REQUIRED_WEBSOCKET_DIRECTIVES if directive not in block]
        assert not missing, f"{path.relative_to(REPO_ROOT)}: `location {spec}` (Gateway WebSocket {route}) is missing {missing}. Copy the block from docker/nginx/nginx.conf."


@pytest.mark.parametrize("path", NGINX_SURFACES, ids=SURFACE_IDS)
def test_regex_websocket_locations_precede_the_regexes_that_shadow_them(path: Path) -> None:
    """nginx picks the *first* matching regex location, so file order is the contract.

    An exact (``= ``) location always wins over any prefix or regex, which is
    why the two exact-match WebSocket routes need no ordering rule. A *regex*
    WebSocket location (``~ ^/api/threads/[^/]+/browser/stream``) does: it is
    evaluated in file order against the other regexes, and the first match
    wins. Placing it after ``~ ^/api/threads`` hands the socket to a location
    with no ``Upgrade``/``Connection`` forwarding, so the WebSocket is
    downgraded to plain HTTP with a valid-looking config.
    """
    config = _read(path)
    locations = _locations(config)

    for spec, shadowing_specs in WS_REGEX_SHADOWS.items():
        route = next(route for route, mapped in WEBSOCKET_ROUTES.items() if mapped == spec)
        for shadowing_spec in shadowing_specs:
            assert shadowing_spec in locations, f"{path.relative_to(REPO_ROOT)} has no `location {shadowing_spec}`; WS_REGEX_SHADOWS is stale"
            assert locations[spec] < locations[shadowing_spec], (
                f"{path.relative_to(REPO_ROOT)}: `location {spec}` (Gateway WebSocket {route}) "
                f"appears AFTER `location {shadowing_spec}`. nginx takes the first matching regex "
                f"location, and a location that forwards no Upgrade/Connection downgrades the socket."
            )

    # Any new regex location is a potential shadow for a regex WebSocket
    # location, so a new one has to be named in WS_REGEX_SHADOWS (or proven
    # non-overlapping) rather than landing unexamined between the two.
    declared_shadows = {shadow for shadows in WS_REGEX_SHADOWS.values() for shadow in shadows}
    unclassified = sorted(spec for spec in locations if spec.startswith("~") and spec not in set(WEBSOCKET_ROUTES.values()) and spec not in declared_shadows and spec not in NON_OVERLAPPING_REGEXES)
    assert not unclassified, (
        f"{path.relative_to(REPO_ROOT)} declares regex location(s) {unclassified} that are classified "
        f"as neither a WebSocket location, a shadow of one, nor provably disjoint. Add it to "
        f"WS_REGEX_SHADOWS (if it can match a WebSocket route, so the ordering gets pinned) or to "
        f"NON_OVERLAPPING_REGEXES (with the reason it cannot)."
    )


@pytest.mark.parametrize("path", NGINX_SURFACES, ids=SURFACE_IDS)
def test_the_agent_card_locations_reach_the_gateway(path: Path) -> None:
    """An Agent Card served by Next.js is a 404, and it looks like absence.

    A peer that cannot fetch the card concludes the node does not exist. There
    is no error to trace, so the location has to be pinned on every surface.
    """
    config = _read(path)
    locations = _locations(config)

    for spec in ("= /.well-known/agent-card.json", "= /.well-known/agent.json"):
        assert spec in locations, f"{path.relative_to(REPO_ROOT)} is missing `{spec}`; an installing peer cannot discover this node"
        block = _block(config, locations[spec])
        assert "gateway" in block, f"{path.relative_to(REPO_ROOT)}: `location {spec}` must proxy to the Gateway upstream, not the frontend"


@pytest.mark.parametrize("path", NGINX_SURFACES, ids=SURFACE_IDS)
def test_the_frontend_location_never_forces_a_connection_upgrade(path: Path) -> None:
    """``Connection: upgrade`` must be conditional on the client asking.

    Both docker configs and the (now-fixed) Helm ConfigMap route the browser
    through a ``$connection_upgrade`` map that is empty unless the request
    carried an ``Upgrade`` header. Hardcoding ``Connection 'upgrade'`` makes
    nginx treat every ordinary frontend response -- HTML, JS, CSS, an SSE
    stream -- as an invalid upgrade handshake, which is a page-load failure
    rather than a WebSocket failure. The Helm ConfigMap shipped that hardcoded
    form because the map was missing from it entirely.
    """
    config = _read(path)
    locations = _locations(config)

    assert "= /" not in locations and "/" in locations, f"{path.relative_to(REPO_ROOT)} has no `location /` frontend catch-all"
    block = _block(config, locations["/"])

    assert "map $http_upgrade $connection_upgrade" in config, f"{path.relative_to(REPO_ROOT)} forwards an upgrade from the frontend without declaring the $connection_upgrade map that keeps it conditional"
    assert "proxy_set_header Connection $connection_upgrade;" in block, (
        f"{path.relative_to(REPO_ROOT)}: `location /` must use `proxy_set_header Connection $connection_upgrade;` (or the frontend location is missing it). A hardcoded `Connection 'upgrade'` breaks ordinary frontend responses."
    )
    assert "proxy_set_header Connection 'upgrade';" not in block, f"{path.relative_to(REPO_ROOT)}: `location /` hardcodes `Connection: upgrade` on non-WebSocket frontend traffic"


# ----------------------------------------------------------------------------
# Config surface
#
# The second deployment surface is the config the code reads. The drift that
# matters is not "the example is older than the code" -- the example is the
# template, it is supposed to be a superset -- it is a config key that a
# deployment surface ships and that **nothing reads**. That is the failure
# documented in MULTI_AGENT_PLAN.md: a cost table shipped eight price entries
# and every cost number was `null`, because the reader had been removed and the
# template was not.
#
# These two checks are scoped to *config-key* drift, which is mechanical and
# checkable. Renaming or removing a key is a breaking change for every existing
# install and is an operator decision, not a test edit -- so a finding here is
# reported, never auto-fixed, and the guard's job is to keep the list from
# growing.
# ----------------------------------------------------------------------------


def _app_config_fields() -> dict[str, str]:
    """Return ``{dotted field path: declaring module file}`` for every AppConfig field.

    Walked from the model rather than grepped, so a key that is *renamed* in the
    model is caught even though its name still appears in the template.
    """
    import sys

    backend = REPO_ROOT / "backend"
    for candidate in (backend, backend / "packages" / "harness"):
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

    from pydantic import BaseModel  # noqa: PLC0415

    from alpha.config.app_config import AppConfig  # noqa: PLC0415

    declared: dict[str, str] = {}
    seen: set[int] = set()

    def walk(model: type, prefix: str = "") -> None:
        if not (isinstance(model, type) and issubclass(model, BaseModel)) or id(model) in seen:
            return
        seen.add(id(model))
        here = Path(sys.modules[model.__module__].__file__ or "").name
        for name in model.model_fields:
            declared.setdefault(f"{prefix}{name}", here)
        for name, info in model.model_fields.items():
            annotation = info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                walk(annotation, f"{prefix}{name}.")

    walk(AppConfig)
    return declared


#: Only these trees can read a config key. `alpha/config/**` is a *consumer* of
#: AppConfig for some subsystems (`self_tuning`, `memory` and others define a
#: sub-config model and read it from sibling modules), so it is in scope; the
#: declaring module itself is excluded per key below, which is the only
#: self-reference that proves nothing. `tests/` is excluded because a test that
#: asserts a default value is not a reader. `backend/scripts/` is excluded: it
#: is operator tooling, not the running product, and a key read only by a
#: diagnostic script is still unread at runtime.
_CONFIG_READER_ROOTS = ("packages", "app")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@lru_cache(maxsize=1)
def _config_key_mentions() -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """Return ``({token: files mentioning it}, {file stem: relative path})``.

    Scanned once per process. Walking ~2k Python files and tokenising them is
    not free, and every assertion below wants the same answer.
    """
    backend = REPO_ROOT / "backend"
    mentions: dict[str, set[str]] = {}
    by_stem: dict[str, str] = {}

    for root_name in _CONFIG_READER_ROOTS:
        root = backend / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            parts = set(path.relative_to(backend).parts)
            if parts & {"tests", "__pycache__", "node_modules", "alembic"}:
                continue
            relative = str(path.relative_to(REPO_ROOT))
            by_stem.setdefault(path.stem, relative)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for word in set(_TOKEN.findall(text)):
                mentions.setdefault(word, set()).add(relative)

    return ({word: frozenset(files) for word, files in mentions.items()}, by_stem)


def test_the_helm_chart_config_template_is_not_behind_the_example() -> None:
    """A chart default below the example version warns on every pod start.

    ``AppConfig._check_config_version`` reads the *example* shipped in the same
    checkout and logs "your config.yaml (version N) is outdated -- the latest
    version is M" whenever the loaded config lags. The Helm chart's default
    ``config:`` blob was pinned at 54 while the example moved to 55, so a
    default ``helm install`` printed that warning on every Gateway start
    forever, with nothing to act on: an operator who follows the advice and
    runs ``make config-upgrade`` in a pod has no example file to merge from.
    """
    import yaml  # noqa: PLC0415

    example = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load((REPO_ROOT / "deploy/helm/alpha/values.yaml").read_text(encoding="utf-8"))
    chart_config = yaml.safe_load(values["config"])

    assert int(chart_config["config_version"]) == int(example["config_version"]), (
        f"the Helm chart's default config declares config_version {chart_config['config_version']} while "
        f"config.example.yaml declares {example['config_version']}. AppConfig._check_config_version compares "
        f"them on every start and logs an 'outdated' warning for a pod that has no way to upgrade itself."
    )


def test_no_config_key_is_shipped_without_a_reader() -> None:
    """Every AppConfig key must be read by production code, not only declared.

    A key that only its own declaration module mentions is a key an operator can
    set, that a comment describes, and that changes nothing. Setting it is not
    an error -- the model accepts it, ``extra="allow"`` even preserves it -- so
    the misconfiguration is invisible until the behaviour the operator
    configured never appears. This is the failure the plan records as "a config
    key documented as the cost fallback was read by nobody, so every cost number
    was ``null`` while the template shipped eight price entries".

    A reader is a mention in ``backend/packages`` or ``backend/app`` outside the
    file that declares the field. Mention, not attribute access, because a
    whole-section hand-off (``EvolutionEvidenceConfig.from_mapping(cfg.evolution_evidence)``)
    is a legitimate read that a per-field access scan would miss.

    Three keys are known-unread today and are listed rather than removed:

    * ``verification.judge_enabled`` / ``judge_model_name`` -- a selective
      acceptance-criteria judge that was specced in the template and never wired.
      ``alpha.subagents.jev_acceptance`` is the implementation that took its
      place and reads ``system_one`` instead.
    * ``evolution_evidence`` -- the evidence subsystem takes its configuration
      through ``evaluate_proposal(config=...)``, never from ``AppConfig``.

    Deleting any of them is a breaking change for every existing install and is
    an operator decision, not a test edit (see
    ``packages/harness/alpha/config/AGENTS.md``), so this test's job is to stop
    the list growing. Remove an entry here only alongside the key itself.
    """
    declared = _app_config_fields()
    mentions, by_stem = _config_key_mentions()

    known_unread = {
        "evolution_evidence": "the evidence subsystem receives its config through evaluate_proposal(config=...), never from AppConfig",
        "verification.judge_enabled": "a specced selective judge that was never wired; alpha.subagents.jev_acceptance is the implementation that took its place",
        "verification.judge_model_name": "same unshipped selective judge as verification.judge_enabled",
    }

    unread: dict[str, str] = {}
    for field_path, declaring_stem in declared.items():
        leaf = field_path.split(".")[-1]
        declaring_file = by_stem.get(declaring_stem)
        readers = {f for f in mentions.get(leaf, ()) if f != declaring_file}
        if not readers:
            unread[field_path] = declaring_file or declaring_stem

    new = sorted(path for path in unread if path not in known_unread)

    assert not new, (
        f"config key(s) {new} are declared on AppConfig and read by nothing outside their own "
        f"declaration module, so an operator can set them and nothing will read them. Either wire "
        f"a reader, or record the key in `known_unread` in this test with the reason it is inert. "
        f"Known-unread today: {sorted(known_unread)}"
    )
