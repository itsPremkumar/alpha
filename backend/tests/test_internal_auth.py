"""Tests for Gateway internal auth token handling."""

from __future__ import annotations

import importlib


def test_internal_auth_uses_shared_env_token(monkeypatch):
    import app.gateway.internal_auth as internal_auth

    monkeypatch.setenv("AGENT_WORKSPACE_INTERNAL_AUTH_TOKEN", "shared-token")
    reloaded = importlib.reload(internal_auth)
    try:
        headers = reloaded.create_internal_auth_headers()

        assert headers[reloaded.INTERNAL_AUTH_HEADER_NAME] == "shared-token"
        assert headers[reloaded.LEGACY_INTERNAL_AUTH_HEADER_NAME] == "shared-token"
        assert reloaded.is_valid_internal_auth_token("shared-token") is True
        assert reloaded.is_valid_internal_auth_token("other-token") is False
    finally:
        monkeypatch.delenv("AGENT_WORKSPACE_INTERNAL_AUTH_TOKEN", raising=False)
        importlib.reload(reloaded)


def test_internal_auth_generates_process_local_fallback(monkeypatch):
    import app.gateway.internal_auth as internal_auth

    monkeypatch.delenv("AGENT_WORKSPACE_INTERNAL_AUTH_TOKEN", raising=False)
    reloaded = importlib.reload(internal_auth)
    try:
        token = reloaded.create_internal_auth_headers()[reloaded.INTERNAL_AUTH_HEADER_NAME]

        assert token
        assert reloaded.is_valid_internal_auth_token(token) is True
    finally:
        importlib.reload(reloaded)


def test_internal_auth_headers_can_carry_owner_user_id(monkeypatch):
    import app.gateway.internal_auth as internal_auth

    monkeypatch.setenv("AGENT_WORKSPACE_INTERNAL_AUTH_TOKEN", "shared-token")
    reloaded = importlib.reload(internal_auth)
    try:
        headers = reloaded.create_internal_auth_headers(owner_user_id="owner-1")

        assert headers[reloaded.INTERNAL_AUTH_HEADER_NAME] == "shared-token"
        assert headers[reloaded.LEGACY_INTERNAL_AUTH_HEADER_NAME] == "shared-token"
        assert headers[reloaded.INTERNAL_OWNER_USER_ID_HEADER_NAME] == "owner-1"
        assert headers[reloaded.LEGACY_INTERNAL_OWNER_USER_ID_HEADER_NAME] == "owner-1"
    finally:
        monkeypatch.delenv("AGENT_WORKSPACE_INTERNAL_AUTH_TOKEN", raising=False)
        importlib.reload(reloaded)


def test_get_internal_user_normalises_unsafe_owner_user_id():
    """P2-3: X-Agent-Workspace-Owner-User-Id is at the trust boundary, so the
    synthetic internal user must use a path-safe id. ``make_safe_user_id``
    is lossy but deterministic; two distinct raw inputs never collide.
    """
    import app.gateway.internal_auth as internal_auth
    from alpha.config.paths import make_safe_user_id

    # Path-traversal-style payloads must be normalised away.
    user_a = internal_auth.get_internal_user(owner_user_id="ou_abc/../../etc/passwd")
    user_b = internal_auth.get_internal_user(owner_user_id="ou_abc/../../etc/passwd")
    assert user_a.id == user_b.id
    assert "/" not in user_a.id
    assert ".." not in user_a.id

    # Negative chat ids and unsafe punctuation must be normalised.
    user_neg = internal_auth.get_internal_user(owner_user_id="-1001234567890:alice")
    assert user_neg.id == make_safe_user_id("-1001234567890:alice")
    assert ":" not in user_neg.id
    assert user_neg.system_role == "internal"

    # Already-safe ids pass through unchanged.
    user_safe = internal_auth.get_internal_user(owner_user_id="alice_42")
    assert user_safe.id == "alice_42"

    # Empty / None falls back to default.
    assert internal_auth.get_internal_user().id == "default"
    assert internal_auth.get_internal_user(owner_user_id="").id == "default"


def test_get_trusted_internal_owner_user_id_supports_modern_and_legacy():
    from types import SimpleNamespace
    import app.gateway.internal_auth as internal_auth

    # Modern header
    req_modern = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(system_role="internal")),
        headers={internal_auth.INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-modern"},
    )
    assert internal_auth.get_trusted_internal_owner_user_id(req_modern) == "owner-modern"

    # Legacy header
    req_legacy = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(system_role="internal")),
        headers={internal_auth.LEGACY_INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-legacy"},
    )
    assert internal_auth.get_trusted_internal_owner_user_id(req_legacy) == "owner-legacy"

    # Untrusted caller returns None regardless of header
    req_untrusted = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(system_role="user")),
        headers={internal_auth.INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-attacker"},
    )
    assert internal_auth.get_trusted_internal_owner_user_id(req_untrusted) is None
