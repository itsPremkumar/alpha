"""6. SecretRef resolution at transport layer.

Alpha already carries request secrets via config.context.secrets
(runtime.secret_context.SECRETS_CONTEXT_KEY) and strips them from traces.
OpenClaw 2.0 goes one step further: SecretRefs never enter model-visible
text at all — they are resolved at the transport (tool/MCP destination).

This module adds the additive resolver:

- is_secret_ref(value): detects "secretRef://name" strings
- resolve_secret_refs(obj, secrets): deep-replaces refs with values from
  the request-scoped secrets map. Unknown refs are left intact (caller
  decides to error) so dry-runs never crash.
- redact_for_log(obj): replaces secret values with "***" for safe logging.

No existing secret flow is changed; tools opt in by calling resolve on
their outbound payload only.
"""

from __future__ import annotations

from typing import Any

SECRET_REF_PREFIX = "secretRef://"


def is_secret_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(SECRET_REF_PREFIX) and len(value) > len(SECRET_REF_PREFIX)


def _ref_name(value: str) -> str:
    return value[len(SECRET_REF_PREFIX) :].strip()


def resolve_secret_refs(obj: Any, secrets: dict[str, str] | None) -> Any:
    secrets = secrets or {}
    if is_secret_ref(obj):
        return secrets.get(_ref_name(obj), obj)
    if isinstance(obj, dict):
        return {k: resolve_secret_refs(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_secret_refs(v, secrets) for v in obj]
    if isinstance(obj, tuple):
        return tuple(resolve_secret_refs(v, secrets) for v in obj)
    return obj


def redact_for_log(obj: Any, secrets: dict[str, str] | None) -> Any:
    secrets = secrets or {}
    values = {v for v in secrets.values() if isinstance(v, str) and v}
    if isinstance(obj, str):
        redacted = obj
        for secret in values:
            if secret in redacted:
                redacted = redacted.replace(secret, "***")
        if is_secret_ref(obj):
            return "*** (secretRef)"
        return redacted
    if isinstance(obj, dict):
        return {k: redact_for_log(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_for_log(v, secrets) for v in obj]
    return obj
