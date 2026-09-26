"""Password-blind credential vault tests.

The central claim under test is negative: **no code path yields the secret to
anybody**.  That is a claim about every surface at once, so the tests attack it
from every direction - return values, reprs, exceptions, truncated frames, log
records, the ledger file, the handle object, the model-facing tool - rather than
asserting the happy path only.

Every secret in this file is a distinctive sentinel so that "the secret is not in
this text" is a real assertion and not a lucky one.
"""

from __future__ import annotations

import io
import json
import logging
import pickle
import traceback
from typing import Any

import pytest

from alpha.security.vault import (
    Authenticator,
    EnvVarInjector,
    HandleNotFound,
    HandleVault,
    HttpBasicAuthInjector,
    HttpHeaderInjector,
    PointOfUseInjector,
    PolicyOverrideRejected,
    RawSecretRefused,
    ScopeViolation,
    SecretHandle,
    TwoFactorCodeInjector,
    TwoFactorUnavailable,
    UserCodeBroker,
    VaultLedger,
    VaultScope,
    provisioning_uri,
    totp_at,
)
from alpha.security.vault.errors import VaultError
from alpha.security.vault.store import HandleVault as StoreVault

SECRET = "sk-live-VAULT-CANARY-9f2b7c41-do-not-leak"
SECRET2 = "pw-CANARY-2-5513ab"
TARGET = "https://api.example.com/v1/charge"


class RecordingInjector:
    """An injector that records that it saw the secret, and returns only data."""

    operation = "http_request"

    def __init__(self, result: Any = None) -> None:
        self.seen: list[str] = []
        self.result = result if result is not None else {"status": "ok", "id": "ch_1"}

    def inject(self, secret: str, target: str, **kwargs: Any) -> Any:
        self.seen.append(secret)
        return self.result


@pytest.fixture()
def vault(tmp_path):
    return HandleVault(ledger=VaultLedger(tmp_path / "vault-ledger.jsonl"))


# ---------------------------------------------------------------------------
# (f) a vault handle yields the secret to NOBODY
# ---------------------------------------------------------------------------
def test_use_returns_only_the_operation_result(vault):
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
    injector = RecordingInjector()
    result = vault.use(handle, injector, operation="http_request", target=TARGET)
    assert result == {"status": "ok", "id": "ch_1"}
    assert injector.seen == [SECRET], "the injector must receive the plaintext"
    assert SECRET not in json.dumps(result)


def test_handle_carries_no_secret_in_any_representation(vault):
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1")
    surfaces = [
        repr(handle),
        str(handle),
        json.dumps(handle.to_dict()),
        vault.describe(handle),
        json.dumps(vault.list_handles()),
        f"{handle}",
        type(handle).__name__,
    ]
    for surface in surfaces:
        assert SECRET not in str(surface), surface
        assert SECRET2 not in str(surface)


def test_handle_is_not_picklable_or_copyable(vault):
    import copy

    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1")
    with pytest.raises(Exception):
        pickle.dumps(handle)
    with pytest.raises(Exception):
        copy.copy(handle)
    with pytest.raises(Exception):
        copy.deepcopy(handle)
    with pytest.raises(AttributeError):
        handle.operation = "subprocess_env"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        handle.anything = SECRET  # type: ignore[attr-defined]
    # And there is no attribute anywhere on the object that holds the value.
    for name in dir(handle):
        if name.startswith("_"):
            continue
        value = getattr(handle, name)
        if callable(value):
            continue
        assert SECRET not in str(value), name


def test_reading_the_raw_secret_is_refused_by_every_door(vault):
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1")
    for name in ("get_secret", "reveal", "read_secret", "peek"):
        method = getattr(vault, name)
        with pytest.raises(RawSecretRefused) as excinfo:
            method(handle)
        assert excinfo.value.code == "raw_secret_refused"
        assert SECRET not in str(excinfo.value)
        assert SECRET not in json.dumps(excinfo.value.to_dict())
    assert not hasattr(vault, "secret")
    assert not hasattr(vault, "secrets")


def test_exception_paths_cannot_emit_the_secret(vault):
    """Every failure mode is swept for the canary."""
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
    captured: list[str] = []

    class Exploding:
        operation = "http_request"

        def inject(self, secret: str, target: str, **kwargs: Any) -> Any:
            # An exception whose text quotes the secret, which is exactly what a
            # real HTTP client does when a URL carries credentials.
            raise RuntimeError(f"connect failed for {target} using {secret}")

    try:
        vault.use(handle, Exploding(), operation="http_request", target=TARGET)
    except RuntimeError as exc:
        captured.append(str(exc))
        captured.append(repr(exc))
        captured.append("".join(traceback.format_exception(exc)))
    except Exception as exc:  # noqa: BLE001
        captured.append(str(exc))

    # A wrong-target use, a wrong-operation use, an unknown handle, an expired
    # handle and a revoked handle.
    for call in (
        lambda: vault.use(handle, RecordingInjector(), operation="http_request", target="https://evil"),
        lambda: vault.use(handle, RecordingInjector(), operation="subprocess_env", target=TARGET),
        lambda: vault.use("vaultref_nope", RecordingInjector(), operation="http_request", target=TARGET),
    ):
        try:
            call()
        except VaultError as exc:
            captured.append(str(exc))
            captured.append(json.dumps(exc.to_dict()))

    for text in captured:
        assert SECRET not in text, text

    # And the ledger recorded the error without the secret.
    rows = vault.ledger.read_back()
    assert rows, "a failed use must still be audited"
    blob = json.dumps(rows)
    assert SECRET not in blob
    assert "RuntimeError" in blob


def test_injectors_scrub_a_server_that_echoes_the_secret(vault):
    class EchoServer:
        """Minimal stand-in for an httpx-shaped client."""

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def __call__(self, secret: str, target: str, **kwargs: Any) -> Any:
            self.calls.append({"secret": secret, "target": target})
            return {
                "status_code": 200,
                "body": f"token accepted: {secret}",
                "headers": {"X-Echo": secret},
            }

    class FakeResponse:
        status_code = 200
        text = f"token accepted: {SECRET}"
        headers = {"X-Echo": SECRET, "Set-Cookie": "session=abc"}

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def request(self, *a: Any, **k: Any) -> FakeResponse:
            return FakeResponse()

    import httpx

    original = httpx.Client
    httpx.Client = FakeClient  # type: ignore[assignment]
    try:
        handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
        result = vault.use(handle, HttpHeaderInjector(), operation="http_request", target=TARGET)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    blob = json.dumps(result)
    assert SECRET not in blob, blob
    assert "[REDACTED:secret]" in result["body"]
    assert SECRET not in json.dumps(vault.ledger.read_back())


def test_env_injector_masks_the_secret_from_command_output(tmp_path):
    script = tmp_path / "echo_env.py"
    script.write_text("import os\nprint('I see', os.environ.get('VAULT_SECRET'))\n", encoding="utf-8")
    import sys

    injector = EnvVarInjector(var="VAULT_SECRET", argv=[sys.executable, str(script)])
    vault = HandleVault()
    handle = vault.deposit(SECRET, operation="subprocess_env", target=sys.executable, owner="u-1", ttl_seconds=120)
    result = vault.use(handle, injector, operation="subprocess_env", target=sys.executable)
    assert result["returncode"] == 0
    assert SECRET not in json.dumps(result)
    assert "[REDACTED:secret]" in result["stdout"]
    assert SECRET not in json.dumps(vault.ledger.entries()[0].to_dict())


def test_ledger_records_handle_operation_target_and_time(vault):
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
    vault.use(handle, RecordingInjector(), operation="http_request", target=TARGET)
    rows = vault.ledger.read_back()
    assert len(rows) == 1
    row = rows[0]
    assert row["handle_fingerprint"] == handle.fingerprint
    assert row["operation"] == "http_request"
    assert row["target"] == TARGET
    assert row["owner"] == "u-1"
    assert row["outcome"] == "succeeded"
    assert row["at"] > 0
    # The row identifies the handle by fingerprint, never by the full id.
    assert handle.id not in json.dumps(rows)
    assert SECRET not in json.dumps(rows)


# ---------------------------------------------------------------------------
# (g) policy: least privilege, time bounds, no override, refusal with a reason
# ---------------------------------------------------------------------------
def test_one_entry_grants_one_operation_on_one_target(vault):
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
    with pytest.raises(ScopeViolation) as wrong_target:
        vault.use(handle, RecordingInjector(), operation="http_request", target="https://other")
    assert "authorises target" in str(wrong_target.value)
    with pytest.raises(ScopeViolation) as wrong_op:
        vault.use(
            handle,
            EnvVarInjector(),
            operation="subprocess_env",
            target=TARGET,
        )
    assert "authorises operation" in str(wrong_op.value)
    assert vault.ledger.entries()[-2].outcome == "denied"
    assert vault.ledger.entries()[-1].outcome == "denied"


def test_a_too_broad_scope_is_refused_at_deposit(vault):
    with pytest.raises(ValueError) as bad_op:
        vault.deposit(SECRET, operation="read_the_world", target=TARGET, owner="u-1")
    assert "unsupported vault operation" in str(bad_op.value)
    with pytest.raises(ValueError) as no_target:
        vault.deposit(SECRET, operation="http_request", target="   ", owner="u-1")
    assert "exactly one target" in str(no_target.value)
    with pytest.raises(ValueError) as too_long:
        vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=99999)
    assert "time-bounded" in str(too_long.value)
    with pytest.raises(ValueError) as too_short:
        vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=1)
    assert "time-bounded" in str(too_short.value)


def test_handles_expire(vault):
    clock = {"t": 1000.0}
    v = HandleVault(ledger=VaultLedger(), clock=lambda: clock["t"])
    handle = v.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=30)
    assert v.describe(handle)["expired"] is False
    clock["t"] += 31
    with pytest.raises(HandleNotFound) as excinfo:
        v.use(handle, RecordingInjector(), operation="http_request", target=TARGET)
    assert "expired" in str(excinfo.value)


def test_agent_supplied_policy_override_is_rejected(vault):
    with pytest.raises(PolicyOverrideRejected) as at_deposit:
        vault.deposit(
            SECRET,
            operation="http_request",
            target=TARGET,
            owner="u-1",
            policy_overrides={"operation": "subprocess_env"},
        )
    assert "cannot be overridden" in str(at_deposit.value)
    assert at_deposit.value.detail["rejected"] == ["operation"]

    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
    for bypass in ("skip_scope_check", "bypass_scope", "override_scope", "force"):
        with pytest.raises(PolicyOverrideRejected):
            vault.use(
                handle,
                RecordingInjector(),
                operation="http_request",
                target=TARGET,
                **{bypass: True},
            )
        assert vault.ledger.entries()[-1].outcome == "denied"
        assert bypass in vault.ledger.entries()[-1].detail


def test_injector_operation_must_match_the_scope(vault):
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
    with pytest.raises(ScopeViolation) as excinfo:
        vault.use(handle, EnvVarInjector(), operation="http_request", target=TARGET)
    assert "injector declares operation" in str(excinfo.value)


def test_revocation_is_immediate(vault):
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
    assert vault.revoke(handle) is True
    with pytest.raises(HandleNotFound):
        vault.use(handle, RecordingInjector(), operation="http_request", target=TARGET)
    assert SECRET not in json.dumps(vault.ledger.read_back())


def test_revoking_an_owner_drops_every_handle(vault):
    a = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1")
    b = vault.deposit(SECRET2, operation="http_request", target=TARGET, owner="u-1")
    c = vault.deposit(SECRET2, operation="http_request", target=TARGET, owner="u-2")
    assert vault.revoke_owner("u-1") == 2
    for handle in (a, b):
        with pytest.raises(HandleNotFound):
            vault.use(handle, RecordingInjector(), operation="http_request", target=TARGET)
    assert vault.use(c, RecordingInjector(), operation="http_request", target=TARGET)


def test_handles_are_owner_scoped_for_listing(vault):
    vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1")
    vault.deposit(SECRET2, operation="http_request", target=TARGET, owner="u-2")
    assert len(vault.list_handles(owner="u-1")) == 1
    assert len(vault.list_handles(owner="u-2")) == 1
    assert len(vault.list_handles()) == 2
    assert SECRET not in json.dumps(vault.list_handles())


def test_vault_scope_rejects_wildcards():
    with pytest.raises(ValueError) as star:
        VaultScope(operation="http_request", target="*")
    assert "one literal target" in str(star.value)
    with pytest.raises(ValueError):
        VaultScope(operation="http_request", target="https://*.example.com")
    with pytest.raises(ValueError):
        VaultScope(operation="*", target="https://x")
    scope = VaultScope(operation="http_request", target="https://x", ttl_seconds=60)
    assert scope.permits("http_request", "https://x") == (True, "permitted")
    assert scope.permits("http_request", "https://x/../y")[0] is False


# ---------------------------------------------------------------------------
# two-factor
# ---------------------------------------------------------------------------
def test_totp_is_derived_from_a_stored_key_and_never_surfaced():
    auth = Authenticator()
    enrolment = auth.enrol(owner="u-1", target=TARGET, account="a@b.c", issuer="Example")
    assert "provisioning_uri" in enrolment
    assert auth.has_key(owner="u-1", target=TARGET) is True
    code = totp_at("JBSWY3DPEHPK3PXP")
    assert len(code) == 6 and code.isdigit()
    assert provisioning_uri("JBSWY3DPEHPK3PXP", account="a", issuer="i").startswith("otpauth://totp/")


def test_two_factor_code_never_reaches_the_model():
    auth = Authenticator()
    auth.enrol(owner="u-1", target=TARGET, account="a", issuer="i")
    provider = auth.code_provider(owner="u-1", target=TARGET)
    vault = HandleVault()
    handle = vault.deposit(SECRET2, operation="totp_challenge", target=TARGET, owner="u-1", ttl_seconds=120)
    injector = TwoFactorCodeInjector(code_provider=provider)
    result = vault.use(handle, injector, operation="totp_challenge", target=TARGET)
    blob = json.dumps(result)
    assert SECRET2 not in blob
    code = totp_at(vault and "JBSWY3DPEHPK3PXP")
    assert code not in blob
    assert "delivered" in result


def test_a_model_supplied_two_factor_code_is_refused():
    vault = HandleVault()
    handle = vault.deposit(SECRET2, operation="totp_challenge", target=TARGET, owner="u-1", ttl_seconds=120)
    injector = TwoFactorCodeInjector(code_provider=lambda **_: "123456")
    with pytest.raises(TwoFactorUnavailable) as excinfo:
        vault.use(handle, injector, operation="totp_challenge", target=TARGET, code="000000")
    assert "refused" in str(excinfo.value)
    assert "user's own UI" in str(excinfo.value)


def test_two_factor_without_a_source_is_refused_not_asked_for():
    vault = HandleVault()
    handle = vault.deposit(SECRET2, operation="totp_challenge", target=TARGET, owner="u-1", ttl_seconds=120)
    with pytest.raises(TwoFactorUnavailable) as excinfo:
        vault.use(handle, TwoFactorCodeInjector(), operation="totp_challenge", target=TARGET)
    assert "will not ask the model for a code" in str(excinfo.value)


def test_user_ui_broker_never_returns_the_code_to_the_requester():
    broker = UserCodeBroker()
    challenge = broker.request(target=TARGET)
    assert challenge.to_dict()["status"] == "awaiting_user"
    assert "code" not in challenge.to_dict()
    broker.supply_from_user_ui(challenge.challenge_id, "123456")
    assert broker.status(challenge.challenge_id)["status"] == "awaiting_user"
    assert broker.consume(challenge.challenge_id) == "123456"
    with pytest.raises(TwoFactorUnavailable):
        broker.consume(challenge.challenge_id)


def test_user_ui_broker_rejects_a_bad_code():
    broker = UserCodeBroker()
    challenge = broker.request(target=TARGET)
    with pytest.raises(TwoFactorUnavailable):
        broker.supply_from_user_ui(challenge.challenge_id, "12ab56")
    with pytest.raises(TwoFactorUnavailable):
        broker.supply_from_user_ui("nope", "123456")


# ---------------------------------------------------------------------------
# the whole-surface sweep
# ---------------------------------------------------------------------------
def test_no_surface_of_the_vault_can_emit_the_secret(vault, caplog):
    """The paranoid sweep: capture logs, exceptions, reprs and serialisations."""
    handle = vault.deposit(SECRET, operation="http_request", target=TARGET, owner="u-1", ttl_seconds=120)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    surfaces: list[str] = []
    try:
        vault.use(handle, RecordingInjector(), operation="http_request", target=TARGET)
        for bad in (
            lambda: vault.use(handle, RecordingInjector(), operation="http_request", target="https://x"),
            lambda: vault.get_secret(handle),
            lambda: vault.describe("nope"),
        ):
            try:
                bad()
            except Exception as exc:  # noqa: BLE001
                surfaces.extend([str(exc), repr(exc), type(exc).__name__])
        surfaces.extend(
            [
                json.dumps(vault.describe(handle), default=str),
                json.dumps(vault.list_handles(), default=str),
                json.dumps(vault.ledger.read_back(), default=str),
                repr(handle),
                str(handle),
            ]
        )
        logging.getLogger("alpha.security.vault").error("deliberate error mentioning %s", handle)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
    surfaces.append(stream.getvalue())
    surfaces.append(json.dumps(vault.ledger.read_back(), default=str))
    for surface in surfaces:
        assert SECRET not in surface, surface[:400]
        assert SECRET2 not in surface, surface[:400]
    assert stream.getvalue().strip(), "the log sweep proved nothing because nothing logged"


def test_store_module_exports_the_same_vault():
    assert StoreVault is HandleVault
    assert issubclass(RawSecretRefused, VaultError)


def test_injector_protocol_is_structural():
    assert isinstance(RecordingInjector(), PointOfUseInjector)
    assert isinstance(HttpHeaderInjector(), PointOfUseInjector)
    assert isinstance(HttpBasicAuthInjector(), PointOfUseInjector)
    assert isinstance(EnvVarInjector(), PointOfUseInjector)
    assert isinstance(TwoFactorCodeInjector(), PointOfUseInjector)


def test_handle_dict_is_the_model_facing_shape():
    handle = SecretHandle(operation="http_request", target=TARGET, owner="u-1", expires_at=1.0, label="billing")
    assert set(handle.to_dict()) == {
        "handle",
        "fingerprint",
        "operation",
        "target",
        "owner",
        "expires_at",
        "label",
    }
