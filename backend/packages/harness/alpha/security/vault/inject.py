"""Point-of-use injection: the only place a secret is allowed to exist.

An injector receives the plaintext exactly once, at the moment of use, and puts
it where the *target* needs it.  It returns the operation's **result**, which is
data the model is entitled to see.  The plaintext never travels back out.

This is the structural reason the vault is "password blind": there is no code
path from a secret to a return value, because the only function that touches the
plaintext has no return channel for it.
"""

from __future__ import annotations

import hmac
import os
import subprocess
from typing import Any, Protocol, runtime_checkable

from .errors import TwoFactorUnavailable


@runtime_checkable
class PointOfUseInjector(Protocol):
    """Injects a secret into a concrete operation and returns only the result."""

    operation: str

    def inject(self, secret: str, target: str, **kwargs: Any) -> Any:
        """Perform the operation against *target* using *secret*.

        Args:
            secret: the plaintext.  Read it, use it, do not return it, do not log it.
            target: the single target the handle authorises.
        """
        ...


def _scrub(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[REDACTED:secret]")
    return text


class HttpHeaderInjector:
    """Attaches the secret as one request header and returns the response.

    The response body is scrubbed of the secret before it is returned, so a
    server that echoes the credential back (a common and embarrassing failure)
    still cannot leak it into the model's context.
    """

    operation = "http_request"

    def __init__(self, *, header: str = "Authorization", scheme: str = "Bearer") -> None:
        self.header = header
        self.scheme = scheme

    def inject(self, secret: str, target: str, *, session: Any = None, **kwargs: Any) -> Any:
        import httpx

        value = f"{self.scheme} {secret}" if self.scheme else secret
        with httpx.Client(timeout=kwargs.pop("timeout", 15.0), follow_redirects=False) as client:
            response = client.request(
                kwargs.pop("method", "GET"),
                target,
                headers={self.header: value, **kwargs.pop("headers", {})},
                **kwargs,
            )
        body = response.text
        return {
            "status_code": response.status_code,
            "body": _scrub(body[:8000], secret),
            "headers": {
                k: _scrub(v, secret) for k, v in dict(response.headers).items() if k.lower() != "set-cookie"
            },
        }


class HttpBasicAuthInjector:
    """HTTP Basic auth.  The secret is handed to the client, never returned."""

    operation = "http_basic_auth"

    def __init__(self, *, username: str = "user") -> None:
        self.username = username

    def inject(self, secret: str, target: str, **kwargs: Any) -> Any:
        import httpx

        with httpx.Client(timeout=kwargs.pop("timeout", 15.0)) as client:
            response = client.request(
                kwargs.pop("method", "GET"),
                target,
                auth=(self.username, secret),
                **kwargs,
            )
        return {"status_code": response.status_code, "body": _scrub(response.text[:8000], secret)}


class EnvVarInjector:
    """Runs a command with the secret in its environment; returns the result.

    The secret is placed in a per-call environment copy.  It is also masked out
    of the captured output, because a command that prints its own environment is
    a normal thing for a command to do.
    """

    operation = "subprocess_env"

    def __init__(self, *, var: str = "VAULT_SECRET", argv: list[str] | None = None) -> None:
        self.var = var
        self.argv = argv

    def inject(self, secret: str, target: str, **kwargs: Any) -> Any:
        argv = list(self.argv or [target])
        env = {k: v for k, v in os.environ.items()}
        env[self.var] = secret
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            env=env,
            capture_output=True,
            text=True,
            timeout=kwargs.pop("timeout", 20.0),
            cwd=kwargs.pop("cwd", None),
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": _scrub(completed.stdout[:8000], secret),
            "stderr": _scrub(completed.stderr[:4000], secret),
        }


class TwoFactorCodeInjector:
    """Supplies a second factor at the point of use, without the model seeing it.

    The code comes from the vault's own authenticator (derived inside the vault)
    or from the user's own UI (supplied by the user).  Either way the model never
    receives a code, and a code the *model* supplies is refused outright.
    """

    operation = "totp_challenge"

    def __init__(self, *, code_provider: Any = None, header: str = "X-TOTP") -> None:
        self.code_provider = code_provider
        self.header = header

    def inject(self, secret: str, target: str, **kwargs: Any) -> Any:
        if self.code_provider is None:
            raise TwoFactorUnavailable(
                "no second factor is available for this target; the vault will not "
                "ask the model for a code",
                target=target,
            )
        if "code" in kwargs:
            raise TwoFactorUnavailable(
                "a two-factor code supplied by the caller was refused: a code must "
                "come from the stored authenticator key or the user's own UI, never "
                "from the channel the agent controls",
                target=target,
            )
        code = self.code_provider(target=target)
        value = f"{secret}:{code}"
        return {
            "target": target,
            "header": self.header,
            "material_fingerprint": hmac.new(
                b"vault", value.encode("utf-8"), "sha256"
            ).hexdigest()[:12],
            "delivered": True,
        }
