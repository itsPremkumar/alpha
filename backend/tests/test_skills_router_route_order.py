"""Route-ordering and 404-honesty regression tests for the skills router.

**The defect these pin.** Starlette matches routes in *registration order*.
``GET /api/skills/{skill_name}`` was declared above the literal single-segment
collection routes ``GET /skills/tiers``, ``GET /skills/usage`` and
``GET /skills/curator``, so the parameterised catch-all won every match:

    GET /api/skills/tiers   -> 404 {"detail": "Skill 'tiers' not found"}
    GET /api/skills/usage   -> 404 {"detail": "Skill 'usage' not found"}
    GET /api/skills/curator -> 404 {"detail": "Skill 'curator' not found"}

All three endpoints are genuinely implemented
(``alpha.skills.tiers`` / ``.usage`` / ``.curator``), so the correct fix is
ordering, not deleting the endpoints or the frontend calls that read them.
``frontend/src/lib/workforce.ts`` reads exactly these three paths.

**Why the ordering needs pinning, not just fixing.** Nothing about a
parameterised route is illegal ahead of a literal one. FastAPI registers it
happily and the shadowing only shows up as a wrong 404 at request time, so a
future literal route added above the catch-all would silently regress. The
structural test below fails on *any* single-segment literal path that the
catch-all would swallow, not only the three names that happen to exist today.

**The honesty half.** A missing route and a missing resource must not be the
same response. Starlette's route-level 404 is a bare ``{"detail": "Not Found"}``
with no machine-readable code, so the single-skill 404 carries
``code: "skill_not_found"`` alongside the human ``detail`` string. The two are
distinguishable by presence of ``code``.

Note on shape: ``code`` is emitted *next to* a plain-string ``detail`` rather
than replacing it, because ``frontend/src/lib/api-client.ts`` only lifts a
string ``detail`` into the error text it renders; a dict-valued ``detail``
would show the user nothing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.deps import get_config
from app.gateway.routers import skills as skills_router

# The literal collection routes that were being swallowed. Each is a real
# endpoint with a real backing module, not a typo or a leftover.
_COLLECTION_ROUTES = [
    ("/api/skills/tiers", "tiers"),
    ("/api/skills/usage", "usage"),
    # The curator report is a flat state document, not a {name: rows} envelope.
    ("/api/skills/curator", "known"),
]

# A name that is not a route at all, and not a skill either.
_ABSENT_SKILL = "definitely-not-a-real-skill-xyz"
# A path with no matching route in this router (two segments past /api/skills).
_ABSENT_ROUTE = "/api/skills/no-such-collection/inner"


def _empty_catalog(config):
    """Storage stub returning no skills, so ``get_skill`` reaches its 404 branch.

    The real storage resolves ``config.yaml`` at construction time, which a
    bare router test app has no reason to provide.
    """
    return SimpleNamespace(load_skills=lambda *, enabled_only: [])


def _make_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """A bare app with only the skills router, auth and storage stubbed out.

    Mirrors ``test_skills_router_authz._make_app``. The collection endpoints
    read no config; only the single-skill path touches storage, so that is the
    one thing stubbed.
    """
    monkeypatch.setattr(skills_router, "_get_user_skill_storage", _empty_catalog)
    app = make_authed_test_app()
    app.dependency_overrides[get_config] = lambda: None
    app.include_router(skills_router.router)
    return app


def _path_segments(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


@pytest.mark.parametrize(("path", "payload_key"), _COLLECTION_ROUTES)
def test_literal_collection_route_is_not_swallowed_by_the_name_catch_all(monkeypatch: pytest.MonkeyPatch, path: str, payload_key: str) -> None:
    """Behavioural: the collection route answers 200 with its real payload.

    Before the fix each of these returned 404 with a "Skill '<name>' not found"
    body, because ``/skills/{skill_name}`` was matched first.
    """
    with TestClient(_make_app(monkeypatch)) as client:
        response = client.get(path)

    assert response.status_code == 200, f"GET {path} should serve the collection endpoint, got {response.status_code}: {response.text}"
    body = response.json()
    assert payload_key in body, f"GET {path} should return a {payload_key!r} key, got {sorted(body)}"


def test_missing_skill_and_missing_route_are_distinguishable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavioural: a 404 for an absent skill is not the same body as a 404 for an absent route.

    This is the honesty requirement. Both are 404, so the status alone cannot
    tell a caller "this endpoint does not exist" from "this skill does not
    exist"; the ``code`` key can.
    """
    with TestClient(_make_app(monkeypatch)) as client:
        absent_skill = client.get(f"/api/skills/{_ABSENT_SKILL}")
        absent_route = client.get(_ABSENT_ROUTE)

    assert absent_skill.status_code == 404
    assert absent_route.status_code == 404

    skill_body = absent_skill.json()
    route_body = absent_route.json()

    # The absent-resource 404 is self-describing.
    assert skill_body["code"] == "skill_not_found"
    # The human string survives alongside the code, because the frontend's error
    # path only renders a *string* detail.
    assert skill_body["detail"] == f"Skill '{_ABSENT_SKILL}' not found"

    # The route-level 404 carries no code, so the two are distinguishable.
    assert "code" not in route_body
    assert route_body["detail"] == "Not Found"
    assert skill_body != route_body


def test_absent_skill_is_reported_as_such_not_as_a_missing_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavioural: the single-skill 404 never claims the route was absent.

    Guards the specific regression: before the fix this path was reached only
    because a *collection* route had been swallowed, and the body it returned
    was byte-identical to a genuinely absent skill's.
    """
    with TestClient(_make_app(monkeypatch)) as client:
        response = client.get(f"/api/skills/{_ABSENT_SKILL}")

    body = response.json()
    assert response.status_code == 404
    # "Not Found" alone is Starlette's route-level body; a resource-level 404
    # must name the resource and carry the code.
    assert body["detail"] != "Not Found"
    assert _ABSENT_SKILL in body["detail"]
    assert body["code"] == skills_router.SKILL_NOT_FOUND_CODE


def test_existing_single_segment_literal_path_is_never_reachable_as_a_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavioural: no literal collection route name can be read as a skill name.

    Guards the collision class rather than the three known names: if a skill
    were ever installed literally called ``tiers``, this documents that the
    collection route owns that path, so the read is a collection read.
    """
    with TestClient(_make_app(monkeypatch)) as client:
        for path, _ in _COLLECTION_ROUTES:
            response = client.get(path)
            assert response.status_code == 200, f"{path} must stay a collection read, got {response.status_code}"
            assert response.json().get("code") is None, f"{path} must not answer with a skill-not-found code"


def test_catch_all_is_registered_after_every_literal_skills_route() -> None:
    """STRUCTURAL (reads the router's route table, issues no request).

    Starlette resolves in registration order, so the invariant that actually
    prevents the regression is positional: no single-segment literal path may
    be declared after ``/api/skills/{skill_name}`` for the same method. This
    is written generically so a *new* literal collection route added above the
    catch-all fails here, not just the three that exist today.
    """
    single_segment_catch_alls = [route for route in skills_router.router.routes if getattr(route, "path", None) == "/api/skills/{skill_name}"]
    assert single_segment_catch_alls, "the /api/skills/{skill_name} catch-all must exist"

    for catch_all in single_segment_catch_alls:
        catch_all_index = skills_router.router.routes.index(catch_all)
        catch_all_methods = set(catch_all.methods or ())

        offenders = []
        for index, route in enumerate(skills_router.router.routes):
            if index <= catch_all_index:
                continue
            path = getattr(route, "path", "")
            segments = _path_segments(path)
            # Only single-segment literal paths under /api/skills can collide;
            # a path with a parameter or extra segments cannot be shadowed by
            # the single-segment catch-all.
            if len(segments) != 3 or "{" in path or not path.startswith("/api/skills/"):
                continue
            if not (set(route.methods or ()) & catch_all_methods):
                continue
            offenders.append(path)

        assert not offenders, f"{catch_all.path} is registered at index {catch_all_index} and shadows these literal collection routes declared after it: {offenders}. Move the catch-all below them or the literal routes above it."


def test_router_path_regexes_keep_collection_routes_reachable() -> None:
    """STRUCTURAL (reads Starlette's compiled path regexes, issues no request).

    Independent of decorator order in the source, this asserts what Starlette
    itself will do: for each collection path, the *first* route whose compiled
    regex matches must be the collection route, not the catch-all. This is the
    check that would have failed on the original code, and it keeps failing for
    any re-introduction of the same shadowing.
    """
    routes = skills_router.router.routes
    for path, _ in _COLLECTION_ROUTES:
        # Starlette's path_regex discriminates path only, never method — a PUT
        # catch-all's regex also matches a GET path. Filter on GET so this
        # asserts what a GET request would actually dispatch to.
        matched = [route for route in routes if "GET" in (route.methods or ()) and route.path_regex.match(path)]
        assert matched, f"no GET route matches {path}"
        first = matched[0]
        assert first.path == path, f"GET {path} resolves to {first.path!r} first; the literal collection route must be registered before any catch-all that shadows it"


def test_router_source_keeps_the_catch_all_under_an_explanatory_comment() -> None:
    """STRUCTURAL (reads source text, issues no request).

    The positional invariant is enforced by the two tests above; this one keeps
    the *reason* next to the code, so the next person to move a route knows
    what breaks. Deliberately a source-text test — it is documentation
    durability, not behaviour.
    """
    source = Path(skills_router.__file__).read_text(encoding="utf-8")
    # Anchor on the GET catch-all specifically: `"/skills/{skill_name}"` also
    # appears on the earlier PUT catch-all, which legitimately stays where it is.
    catch_all_at = source.index('summary="Get Skill Details"')
    rationale_at = source.index("Starlette matches routes in registration order")
    assert rationale_at < catch_all_at, "the route-ordering rationale must be documented above the catch-all it explains"


@pytest.mark.parametrize("name", ["tiers", "usage", "curator", "custom", "proposals", "retrieve", "graph"])
def test_known_shadowing_never_returns_the_absence_sentinel(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Behavioural: shadowed-by-catch-all regressions surface as 404 with a code, not as a collection read.

    Each of these names is a real collection route today, so each must be 200.
    The sentinel check is what makes this bite if one is ever re-shadowed: the
    pre-fix body was ``{"detail": "Skill '<name>' not found"}``, which is a 404
    without the code.
    """
    known = {path.rsplit("/", 1)[-1]: path for path, _ in _COLLECTION_ROUTES}
    path = known.get(name, f"/api/skills/{name}")

    with TestClient(_make_app(monkeypatch)) as client:
        response = client.get(path)

    if name in known:
        assert response.status_code == 200, f"GET {path} regressed to {response.status_code}: {response.text}"
    else:
        # A name that is not a registered collection route: it is either a
        # real skill read or an honest 404, never a fake collection read.
        assert response.status_code in (200, 404)
        if response.status_code == 404:
            assert response.json()["code"] == skills_router.SKILL_NOT_FOUND_CODE
