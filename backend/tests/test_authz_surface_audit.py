"""Regression tests for the Gateway authentication / authorization surface audit.

Each test here fails without the corresponding production change. The tests that
matter most go through the *real* ``AuthMiddleware`` / ``CSRFMiddleware`` stack and
assert the refusal a caller actually receives, not the return value of a helper.

Coverage:

* ``/api/credentials/*`` -- the process-global credential vault has no user
  dimension, so the caller-supplied ``thread_id`` is the only boundary. A
  non-owner must not be able to read, inject into, or purge another user's
  thread credentials.
* ``POST /api/peer-network/pair/rotate`` -- the response body is the installation
  inbound bearer, the same credential ``GET /api/peer-network/status`` is
  admin-gated for. A ``threads:write`` holder must not reach it.
* ``/api/webhooks/`` was a *prefix* auth + CSRF exemption, so any future route
  under that namespace became unauthenticated and CSRF-exempt by existing.
* The exact-path exemptions stay exact: no trailing slash, encoded character,
  case variation, dot segment, or alternate prefix may reach a public handler
  from a protected one, or vice versa.
* A revoked session is actually rejected, and the whole public surface is
  enumerated so a new unauthenticated route cannot be added silently.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from alpha.config.authorization_config import AuthorizationConfig
from app.gateway.auth.models import User
from app.gateway.auth_middleware import (
    _PUBLIC_EXACT_PATHS,
    _PUBLIC_PATH_PREFIXES,
    AuthMiddleware,
    _is_public,
)
from app.gateway.authz import require_thread_owner
from app.gateway.csrf_middleware import CSRF_HEADER_NAME, CSRFMiddleware
from app.gateway.routers import credentials as credentials_router
from app.gateway.routers import peer_network as peer_router

VICTIM_THREAD = "victim-thread-0001"
ATTACKER_THREAD = "attacker-thread-0001"
_PAIRING_CODE = "test-only-pairing-code-abcdefghijklmnop"


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    """Force authentication ON.

    ``is_auth_disabled()`` reads ``AGENT_WORKSPACE_AUTH_DISABLED``, and the repo
    ``.env`` ships ``=1`` (loaded by ``load_dotenv``). Left ambient, every refusal
    assertion below would really be asserting a property of the developer's
    ``.env``.
    """
    monkeypatch.setattr("app.gateway.auth_middleware.is_auth_disabled", lambda: False)
    monkeypatch.setattr("app.gateway.csrf_middleware.is_auth_disabled", lambda: False)
    monkeypatch.setenv("AGENT_WORKSPACE_AUTH_DISABLED", "")
    monkeypatch.delenv("AGENT_WORKSPACE_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(
        "app.gateway.authz._get_route_authorization_config",
        lambda: AuthorizationConfig(),
    )


def _user(system_role: str = "user") -> User:
    return User(
        email=f"audit-{system_role}-{uuid4()}@example.com",
        password_hash="x",
        system_role=system_role,
        id=uuid4(),
    )


# ``User.id`` is a pydantic UUID and ``ThreadMetaStore.check_access`` compares it
# as text, so both sides of an ownership test must use the same UUID string.
_VICTIM_USER_ID = "11111111-1111-4111-8111-111111111111"
_INTRUDER_USER_ID = "22222222-2222-4222-8222-222222222222"


def _user_with_id(user_id: str, system_role: str = "user") -> User:
    return User(
        email=f"audit-{user_id}@example.com",
        password_hash="x",
        system_role=system_role,
        id=user_id,
    )


class _OwnerAwareThreadStore:
    """Minimal ``ThreadMetaStore`` stand-in with a real owner column.

    Mirrors the production semantics ``require_permission(owner_check=True)``
    relies on: a missing row is "untracked legacy thread" (allowed unless
    ``require_existing``), a NULL owner is shared data (allowed), and an
    existing row owned by somebody else is a denial.
    """

    def __init__(self, owners: dict[str, str | None]) -> None:
        self.owners = owners

    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        if thread_id not in self.owners:
            return not require_existing
        owner = self.owners[thread_id]
        return owner is None or owner == user_id


def _credentials_app(*, caller_id: str) -> TestClient:
    """Mount the real credentials router behind an authenticated caller."""
    user = _user_with_id(caller_id)
    app = make_authed_test_app(user_factory=lambda: user)
    app.state.thread_store = _OwnerAwareThreadStore({VICTIM_THREAD: _VICTIM_USER_ID})
    app.include_router(credentials_router.router)
    return TestClient(app)


# ── 1. /api/credentials/* cross-user hole ──────────────────────────────────


def test_credential_submit_refuses_a_non_owner_of_the_thread():
    """The regression: no owner check meant any authenticated user could inject.

    ``alpha.security.credential_vault`` is a process-global singleton keyed only
    by ``thread_id``, and ``inject_environment`` folds every deposited value into
    the target thread's sandbox environment. A write from the wrong user is
    therefore a write into somebody else's agent run.
    """
    client = _credentials_app(caller_id=_INTRUDER_USER_ID)

    response = client.post(
        "/api/credentials/submit",
        json={"thread_id": VICTIM_THREAD, "key": "STRIPE_SECRET_KEY", "value": "sk_attacker"},
    )

    assert response.status_code == 404, response.text
    from alpha.security.credential_vault import get_credential_vault

    assert get_credential_vault().get_credential(VICTIM_THREAD, "STRIPE_SECRET_KEY") is None


def test_credential_pending_refuses_a_non_owner_and_leaks_nothing():
    """Read side of the same hole: pending prompts name the secrets being asked for."""
    from alpha.security.credential_vault import get_credential_vault

    vault = get_credential_vault()
    vault.clear_thread(thread_id=VICTIM_THREAD)
    vault.request_credential(VICTIM_THREAD, "AWS_SECRET_ACCESS_KEY", "AWS Secret Access Key")
    try:
        client = _credentials_app(caller_id=_INTRUDER_USER_ID)

        response = client.get("/api/credentials/pending", params={"thread_id": VICTIM_THREAD})

        assert response.status_code == 404, response.text
        assert "AWS_SECRET_ACCESS_KEY" not in response.text
    finally:
        vault.clear_thread(thread_id=VICTIM_THREAD)


def test_credential_clear_refuses_a_non_owner_and_keeps_the_secret():
    """Destructive side: purging another user's vault entry breaks their run."""
    from alpha.security.credential_vault import get_credential_vault

    vault = get_credential_vault()
    vault.clear_thread(thread_id=VICTIM_THREAD)
    vault.deposit_credential(thread_id=VICTIM_THREAD, key="API_TOKEN", value="victim-token")
    try:
        client = _credentials_app(caller_id=_INTRUDER_USER_ID)

        response = client.delete("/api/credentials/clear", params={"thread_id": VICTIM_THREAD})

        assert response.status_code == 404, response.text
        assert vault.get_credential(VICTIM_THREAD, "API_TOKEN") == "victim-token"
    finally:
        vault.clear_thread(thread_id=VICTIM_THREAD)


def test_credential_routes_allow_the_actual_owner():
    """The gate must not over-reject: the owner still gets the full flow."""
    from alpha.security.credential_vault import get_credential_vault

    app = make_authed_test_app(user_factory=lambda: _user_with_id(_VICTIM_USER_ID))
    app.state.thread_store = _OwnerAwareThreadStore({VICTIM_THREAD: _VICTIM_USER_ID})
    app.include_router(credentials_router.router)
    client = TestClient(app)
    vault = get_credential_vault()
    vault.clear_thread(thread_id=VICTIM_THREAD)
    try:
        deposit = client.post(
            "/api/credentials/submit",
            json={"thread_id": VICTIM_THREAD, "key": "API_TOKEN", "value": "owner-token"},
        )
        assert deposit.status_code == 200, deposit.text

        pending = client.get("/api/credentials/pending", params={"thread_id": VICTIM_THREAD})
        assert pending.status_code == 200
        assert pending.json() == []

        clear = client.delete("/api/credentials/clear", params={"thread_id": VICTIM_THREAD})
        assert clear.status_code == 200, clear.text
    finally:
        vault.clear_thread(thread_id=VICTIM_THREAD)


def test_credential_routes_require_authentication_at_all():
    """Fail-closed: with no credential the routes must 401, not serve."""
    import anyio
    from fastapi import HTTPException

    app = FastAPI()
    app.state.thread_store = _OwnerAwareThreadStore({VICTIM_THREAD: _VICTIM_USER_ID})
    request = _owner_request(app, None)

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(require_thread_owner, request, VICTIM_THREAD)

    assert exc_info.value.status_code == 401


# ── 2. peer pairing-code rotation escalation ────────────────────────────────


@pytest.fixture
def stub_peer_service(monkeypatch):
    class _StubService:
        async def rotate_pairing_code(self) -> str:
            return _PAIRING_CODE

        async def status(self, include_pairing_code: bool = False):
            assert include_pairing_code is True, "the router must still request the pairing code"
            return {
                "enabled": True,
                "identity": {"agent_id": "agent-under-test"},
                "discovery": {"mode": "local-first"},
                "transports": {"websocket": "ws://127.0.0.1:8743"},
                "persistence": {"backend": "sqlite"},
                "pairing_code": _PAIRING_CODE,
                "limits": {"max_peers": 32},
                "pairing": {"refused_attempts": 0},
            }

    monkeypatch.setattr(peer_router, "_service", lambda: _StubService())


def _peer_client(system_role: str) -> TestClient:
    app = make_authed_test_app(user_factory=lambda: _user(system_role))
    app.include_router(peer_router.router)
    return TestClient(app)


def test_pair_rotate_refuses_a_threads_write_holder(stub_peer_service):
    """The regression: the rotate response IS the inbound bearer.

    ``service.rotate_pairing_code()`` returns ``identity.pairing_code``, the same
    value ``accept_pair`` compares a presented code against and the same value
    ``find_peer_by_token`` digests to authenticate the PUBLIC inbound plane. So a
    caller holding only ``threads:write`` could read the credential that
    ``GET /api/peer-network/status`` is admin-gated to protect.
    """
    client = _peer_client("user")

    response = client.post("/api/peer-network/pair/rotate", json={})

    assert response.status_code == 403, response.text
    assert _PAIRING_CODE not in response.text


def test_pair_rotate_allows_an_admin(stub_peer_service):
    client = _peer_client("admin")

    response = client.post("/api/peer-network/pair/rotate", json={})

    assert response.status_code == 200, response.text
    assert response.json()["pairing_code"] == _PAIRING_CODE


def test_pairing_code_is_not_reachable_from_any_threads_permission_route(stub_peer_service):
    """No lower-privileged route on this router may return the pairing code.

    This is the duplicated-capability check: ``/status`` and ``/pair/rotate`` are
    the only two responses carrying the inbound bearer, and both must be
    admin-gated, or the gate on one of them is decorative.
    """
    for system_role in ("user", "admin"):
        client = _peer_client(system_role)
        status = client.get("/api/peer-network/status")
        rotate = client.post("/api/peer-network/pair/rotate", json={})
        if system_role == "user":
            assert status.status_code == 403
            assert rotate.status_code == 403
        else:
            assert status.status_code == 200
            assert rotate.status_code == 200


# ── 3. /api/webhooks/ is an exact exemption, not a prefix ───────────────────


def test_webhook_exemption_is_exact_not_a_prefix():
    """A future route under /api/webhooks/ must not inherit the exemption."""
    assert "/api/webhooks/" not in _PUBLIC_PATH_PREFIXES
    assert not any(prefix == "/api/webhooks/" for prefix in _PUBLIC_PATH_PREFIXES)
    assert "/api/webhooks/github" in _PUBLIC_EXACT_PATHS
    # A sibling under the same namespace is authenticated.
    assert _is_public("/api/webhooks/github") is True
    assert _is_public("/api/webhooks/slack") is False
    assert _is_public("/api/webhooks/") is False
    assert _is_public("/api/webhooks") is False


def test_webhook_csrf_exemption_is_exact_too():
    from app.gateway.csrf_middleware import _CSRF_EXEMPT_EXACT_PATHS

    assert "/api/webhooks/github" in _CSRF_EXEMPT_EXACT_PATHS
    assert not any("/api/webhooks/slack" == entry for entry in _CSRF_EXEMPT_EXACT_PATHS)
    assert not any(entry.endswith("/") and entry.startswith("/api/webhooks") for entry in _CSRF_EXEMPT_EXACT_PATHS)


def _public_exemption_app() -> FastAPI:
    """A mini app whose route table mirrors the real public plane's shapes."""
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.add_middleware(CSRFMiddleware)

    @app.get("/.well-known/agent-card.json")
    async def card():
        return {"ok": "card"}

    @app.post("/api/peer-network/remote/pair")
    async def pair():
        return {"ok": "pair"}

    # A hypothetical future webhook route: same namespace, no signature scheme.
    @app.post("/api/webhooks/slack")
    async def slack_webhook():
        return {"ok": "slack-webhook"}

    # A hypothetical future route shadowed under a public exact path's prefix.
    @app.get("/api/peer-network/card-history")
    async def card_history():
        return {"ok": "card-history"}

    @app.get("/api/models")
    async def models():
        return {"ok": "models"}

    return app


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/webhooks/slack"),
        ("GET", "/api/webhooks/slack/"),
        ("GET", "/api/peer-network/card-history"),
        ("GET", "/api/peer-network/card-history/"),
        ("GET", "/api/models"),
        ("GET", "/api/models/"),
        ("GET", "/api/PEER-NETWORK/CARD"),
        ("GET", "/.WELL-KNOWN/AGENT-CARD.JSON"),
        ("GET", "/.well-known/agent-card.json%23x"),
        ("GET", "/api/peer-network//card"),
    ],
)
def test_no_variant_of_a_protected_path_reaches_a_handler(method: str, path: str):
    client = TestClient(_public_exemption_app())
    response = client.request(method, path)
    assert response.status_code in {401, 404, 405}, f"{method} {path} -> {response.status_code} {response.text[:120]}"
    assert "ok" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/agent-card.json",
        "/.well-known/agent-card.json/",
        "/.well-known/agent-card.json%2f",
    ],
)
def test_declared_public_exact_path_stays_reachable_with_a_trailing_slash(path: str):
    """The public plane must keep working; the exemption normalises one slash."""
    client = TestClient(_public_exemption_app())
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": "card"}


def test_public_post_without_a_csrf_token_is_still_reachable():
    """Machine-to-machine ingress: CSRF-exempt, but the route owns its posture."""
    client = TestClient(_public_exemption_app())
    response = client.post("/api/peer-network/remote/pair")
    assert response.status_code == 200, response.text


def test_a_future_webhook_route_is_csrf_checked_too():
    """Auth *and* CSRF must agree: a new namespace route needs both gates."""
    client = TestClient(_public_exemption_app())
    response = client.post("/api/webhooks/slack")
    assert response.status_code == 403, response.text
    assert CSRF_HEADER_NAME in response.text


# ── 4. require_thread_owner semantics ───────────────────────────────────────


def _owner_request(app: FastAPI, user: User | None, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "app": app,
    }
    request = Request(scope)
    if user is not None:
        request.state.user = user
    return request


def test_require_thread_owner_401s_without_a_user():
    import anyio

    app = FastAPI()
    app.state.thread_store = _OwnerAwareThreadStore({VICTIM_THREAD: _VICTIM_USER_ID})
    request = _owner_request(app, None)

    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - HTTPException is imported lazily below
        anyio.run(require_thread_owner, request, VICTIM_THREAD)
    from fastapi import HTTPException

    assert isinstance(exc_info.value, HTTPException)
    assert exc_info.value.status_code == 401


def test_require_thread_owner_404s_for_a_foreign_thread():
    import anyio

    app = FastAPI()
    app.state.thread_store = _OwnerAwareThreadStore({VICTIM_THREAD: _VICTIM_USER_ID})
    request = _owner_request(app, _user_with_id(_INTRUDER_USER_ID))

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(require_thread_owner, request, VICTIM_THREAD)
    assert exc_info.value.status_code == 404


def test_require_thread_owner_allows_the_owner_and_shared_rows():
    import anyio

    app = FastAPI()
    app.state.thread_store = _OwnerAwareThreadStore({VICTIM_THREAD: _VICTIM_USER_ID, "shared-thread": None})
    request = _owner_request(app, _user_with_id(_VICTIM_USER_ID))

    assert anyio.run(require_thread_owner, request, VICTIM_THREAD) is None
    # NULL owner == shared / pre-auth data, matching require_permission.
    assert anyio.run(require_thread_owner, request, "shared-thread") is None
    # Untracked legacy thread is allowed unless require_existing.
    assert anyio.run(require_thread_owner, request, "untracked") is None
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(lambda: require_thread_owner(request, "untracked", require_existing=True))
    assert exc_info.value.status_code == 404


def test_require_thread_owner_resolves_from_the_cookie_when_state_is_absent():
    """A middleware-less composition still resolves the caller, never skips it.

    ``request.state.user`` is stamped by AuthMiddleware in production. When a
    router is mounted without it (tests, an alternative ASGI composition), the
    helper must fall back to the strict cookie resolver rather than treating the
    missing state as "no owner check needed".
    """
    import anyio
    from fastapi import HTTPException

    import app.gateway.deps as deps_module

    app = FastAPI()
    app.state.thread_store = _OwnerAwareThreadStore({VICTIM_THREAD: _VICTIM_USER_ID})
    request = _owner_request(app, None)

    original = deps_module.get_optional_user_from_request

    async def _intruder(_request):
        return _user_with_id(_INTRUDER_USER_ID)

    deps_module.get_optional_user_from_request = _intruder
    try:
        with pytest.raises(HTTPException) as exc_info:
            anyio.run(require_thread_owner, request, VICTIM_THREAD)
        assert exc_info.value.status_code == 404
    finally:
        deps_module.get_optional_user_from_request = original


# ── 5. session revocation and the enumerated public surface ────────────────


def test_revoked_session_is_rejected_end_to_end():
    """``token_version`` is the revocation mechanism; prove it is enforced.

    ``/change-password`` bumps it. A token minted before the bump must stop
    working, otherwise a leaked session outlives the credential change that was
    supposed to end it.
    """
    import anyio
    from fastapi import HTTPException

    import app.gateway.deps as deps_module
    from app.gateway.auth.jwt import create_access_token
    from app.gateway.deps import get_current_user_from_request

    token = create_access_token("user-abc", token_version=3)

    class _App:
        class state:  # noqa: N801 - mirrors app.state
            pass

    app = _App()
    request = _owner_request(app, None, {"cookie": f"access_token={token}"})

    async def _get_user(_user_id: str):
        return _user_with_id("33333333-3333-4333-8333-333333333333").model_copy(update={"token_version": 4})

    original_provider = deps_module.get_local_provider
    deps_module.get_local_provider = lambda: type("P", (), {"get_user": staticmethod(_get_user)})()
    try:
        with pytest.raises(HTTPException) as exc_info:
            anyio.run(get_current_user_from_request, request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["code"] == "token_invalid"
    finally:
        deps_module.get_local_provider = original_provider


def test_expired_token_is_rejected():
    from datetime import timedelta

    from app.gateway.auth.errors import TokenError
    from app.gateway.auth.jwt import create_access_token, decode_token

    assert decode_token(create_access_token("user-abc", expires_delta=timedelta(seconds=-5))) is TokenError.EXPIRED


def test_documented_public_surface_has_not_grown():
    """The public plane is enumerated, so a new unauthenticated route is visible.

    This is the regression that makes the enumeration itself a deliverable: the
    exact set is pinned, so adding an unauthenticated route is a failing test
    rather than an unreviewed diff.
    """
    assert _PUBLIC_EXACT_PATHS == frozenset(
        {
            "/api/v1/auth/login/local",
            "/api/v1/auth/register",
            "/api/v1/auth/logout",
            "/api/v1/auth/setup-status",
            "/api/v1/auth/initialize",
            "/api/v1/auth/providers",
            "/api/webhooks/github",
            "/.well-known/agent-card.json",
            "/.well-known/agent.json",
            "/api/peer-network/card",
            "/api/peer-network/remote/pair",
            "/api/peer-network/inbound/messages",
        }
    )
    assert _PUBLIC_PATH_PREFIXES == (
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/auth/oauth/",
        "/api/v1/auth/callback/",
    )


def test_no_public_exact_path_is_a_prefix_of_another_declared_path():
    """A public exact path must not shadow a longer route at the same prefix.

    ``AuthMiddleware`` decides on the request path while Starlette decides on the
    same string, so the only way they can disagree is when a public entry is a
    strict prefix of some other mounted path. The webhook move below is exactly
    that shape: ``/api/webhooks/github`` must not open ``/api/webhooks/...``.
    """
    all_paths = set(_PUBLIC_EXACT_PATHS) | set(_PUBLIC_PATH_PREFIXES)
    for public in _PUBLIC_EXACT_PATHS:
        for other in all_paths:
            if other == public:
                continue
            assert not other.startswith(public + "/"), f"{other} is shadowed by public {public}"


def test_every_public_exact_path_is_exact_not_a_directory_prefix():
    """No exemption entry may end in a slash (that would be a prefix in disguise)."""
    for entry in _PUBLIC_EXACT_PATHS:
        assert not entry.endswith("/"), entry


def test_csrf_exempt_paths_are_a_subset_of_public_or_signature_verified():
    """CSRF must not exempt a path that authentication still protects.

    The only legitimate reason to skip the double-submit check on a
    session-authenticated route is "this route implements its own request
    posture" (the auth endpoints' origin check, or a webhook's HMAC). Anything
    else is a CSRF hole.
    """
    from app.gateway.csrf_middleware import _AUTH_EXEMPT_PATHS, _CSRF_EXEMPT_EXACT_PATHS

    for entry in _CSRF_EXEMPT_EXACT_PATHS:
        assert entry in _PUBLIC_EXACT_PATHS or entry in _AUTH_EXEMPT_PATHS, f"{entry} is CSRF-exempt without being public or origin-checked"
