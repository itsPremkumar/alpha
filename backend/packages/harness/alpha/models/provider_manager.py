"""Provider manager and credentials persistence for LLM providers.

Supports:
- Keyless free providers (OVHcloud, Pollinations, LLM7, Vireonix, Cehpoint, BlockRun, AI Horde)
- Recurring $0 free quota providers (Gemini, Groq, SambaNova, Mistral, Cohere, Cloudflare)
- Free-model gateways (OpenRouter :free models, Bytez)
- Trial credit providers (NVIDIA NIM, Cerebras)
- Frontier/commercial providers (OpenAI, Anthropic, DeepSeek)
- Custom / Local OpenAI-compatible endpoints (Ollama, LM Studio, vLLM)

Two hardening properties this module owns for bring-your-own-model:

* **Egress policy.** A caller-supplied ``base_url`` is screened by
  :func:`alpha.community.url_safety.assert_model_endpoint_url` *before* it is
  persisted, so configuring a provider can never point model egress at cloud
  metadata, loopback (unless the provider is a declared local endpoint) or an
  internal RFC 1918 host. Persisted endpoints are re-screened offline on every
  read, so a file written before this guard existed cannot keep routing egress
  internally.
* **Credential storage at rest.** Provider keys are no longer written as
  plaintext JSON. The file is an envelope encrypted with Windows DPAPI
  (user scope) where available, a key-file cipher elsewhere, and only falls
  back to plaintext with a loud warning when neither exists. A pre-existing
  plaintext file is migrated transparently on first read, and
  :func:`migrate_plaintext_credentials_file` performs the same migration
  explicitly. See :func:`credentials_storage_status` for the honest, per-host
  state of that guarantee.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from alpha.community.url_safety import ModelEndpointBlockedError, assert_model_endpoint_url
from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

CREDENTIALS_FILE_NAME = "provider_credentials.json"
CREDENTIALS_KEY_FILE_NAME = "provider_credentials.key"

#: Envelope markers written to the credentials file. Absence of ``schema`` is
#: what identifies a legacy plaintext file, so the two formats are told apart by
#: a field that only the new writer emits.
CREDENTIALS_SCHEMA = "alpha.provider_credentials"
CREDENTIALS_VERSION = 2

#: Endpoint kinds. ``remote`` is the default and the strict tier; ``local`` is
#: the declared opt-in for same-host OpenAI-compatible servers. Cloud metadata
#: is refused in both (see the egress policy note above).
PROVIDER_KIND_REMOTE = "remote"
PROVIDER_KIND_LOCAL = "local"
PROVIDER_KINDS = (PROVIDER_KIND_REMOTE, PROVIDER_KIND_LOCAL)
#: Providers whose documented purpose is a local OpenAI-compatible server, so
#: they default to the local tier instead of demanding an explicit opt-in.
LOCAL_BY_DEFAULT_PROVIDERS = frozenset({"custom"})

#: Operator allowlist for a private model endpoint (e.g. a LAN gateway). An
#: allowlisted host is still refused when it resolves to cloud metadata, and an
#: allowlist entry never lifts the metadata refusal.
PRIVATE_HOST_ALLOWLIST_ENV = "ALPHA_MODEL_ENDPOINT_PRIVATE_HOSTS"

#: Header names a model endpoint must never receive from configuration: they
#: either retarget the connection or corrupt the request framing.
_FORBIDDEN_ENDPOINT_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "proxy-authorization",
        "proxy-connection",
        "upgrade",
        "keep-alive",
        "te",
        "trailer",
    }
)
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ModelDescriptor:
    id: str
    name: str
    model_id: str
    supports_thinking: bool = False
    description: str | None = None


@dataclass
class ProviderDescriptor:
    id: str
    name: str
    category: str  # "keyless_free" | "recurring_free" | "free_gateway" | "trial_credits" | "paid" | "custom"
    key_env: str | None
    portal_url: str
    free_tier_note: str
    default_base_url: str | None
    default_models: list[ModelDescriptor]
    default_use: str = "langchain_openai:ChatOpenAI"


def _provider_specs_from_catalog() -> list[ProviderDescriptor]:
    """Build the bring-your-own-provider catalog from ``config.yaml``.

    ``config.yaml -> model_catalog:`` is the single source of truth for every
    provider and model name offered in Settings. This function is the only reader,
    so adding a provider is a YAML edit rather than a code change, and the
    capability a provider declares can no longer disagree with the one ``models[]``
    declares for the same name without
    :mod:`alpha.models.catalog_consistency` noticing.

    Returns ``[]`` when nothing is configured. That is honest rather than a
    regression: with no ``model_catalog`` there is no declared catalog, and
    offering a stale in-code list would be the exact drift this replaced.
    """
    from alpha.config.app_config import get_app_config

    seen: set[str] = set()
    specs: list[ProviderDescriptor] = []
    for entry in get_app_config().model_catalog:
        if entry.id in seen:
            # One file means a duplicate id is a config error, not something to
            # arbitrate between layers. Keep the first and say so.
            logger.warning("provider id '%s' is declared more than once under `model_catalog:`; using the first", entry.id)
            continue
        seen.add(entry.id)
        specs.append(
            ProviderDescriptor(
                id=entry.id,
                name=entry.name,
                category=entry.category,
                key_env=entry.key_env,
                portal_url=entry.portal_url,
                free_tier_note=entry.free_tier_note,
                default_base_url=entry.base_url,
                default_use=entry.default_use,
                default_models=[
                    ModelDescriptor(
                        id=model.id,
                        name=model.name,
                        model_id=model.model_id,
                        supports_thinking=model.supports_thinking,
                        description=model.description,
                    )
                    for model in entry.models
                ],
            )
        )
    return specs


#: Lazily resolved from ``config.yaml`` on first use. Kept as a module-level
#: name so existing importers keep working; call :func:`refresh_provider_specs`
#: after editing the catalog in a long-lived process.
#:
#: No config file anywhere (a fresh clone, or CI running a gitignored-free
#: checkout) is the ``[]`` case this module documents: nothing is declared, so
#: the honest binding is the empty catalog — raising here made
#: ``import alpha.models.provider_manager`` fatal at *collection* and took the
#: whole backend suite down with it. Same treatment as ``alpha.mcp.cache``'s
#: deliberate ``FileNotFoundError`` exception. A config file that exists but is
#: invalid still fails closed, and a runtime :func:`refresh_provider_specs`
#: still raises loudly.
try:
    PROVIDER_SPECS: list[ProviderDescriptor] = _provider_specs_from_catalog()
except FileNotFoundError:
    PROVIDER_SPECS = []


def refresh_provider_specs() -> list[ProviderDescriptor]:
    """Re-read the provider catalog from ``config.yaml`` and rebind ``PROVIDER_SPECS``.

    ``config.yaml`` is hot-reloadable, so a Gateway serving a long-lived process
    re-resolves the catalog on the next request through
    :func:`get_providers_catalog`. This explicit refresh exists for callers that
    captured the module-level list at import time.
    """
    global PROVIDER_SPECS
    PROVIDER_SPECS = _provider_specs_from_catalog()
    return PROVIDER_SPECS


# The provider/model catalog that used to live here as ~470 lines of literals now
# comes from `config.yaml -> model_catalog:` via `_provider_specs_from_catalog`.
# Add a provider there, never here.


def _credentials_path() -> Path:
    store_dir = runtime_home() / "models"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / CREDENTIALS_FILE_NAME


def _credentials_key_path() -> Path:
    return _credentials_path().parent / CREDENTIALS_KEY_FILE_NAME


# ---------------------------------------------------------------------------
# Credential encryption at rest
# ---------------------------------------------------------------------------
#
# Provider API keys are bearer secrets: anything that can read the runtime home
# can spend the operator's money. The envelope below keeps them out of plain
# sight, with the honest caveat that a cipher keyed from the same host only
# protects against at-rest copies, backups and other OS users - never against
# code already running as this user. Windows DPAPI (CryptProtectData, user
# scope) is the real win: the key material lives in the user's credential
# store, not in the file. Elsewhere the fallback key file (0600) is equivalent
# to file permissions, and that is exactly what ``credentials_storage_status``
# reports - no host is told it has protection it does not have.


def _dpapi_available() -> bool:
    return os.name == "nt"


def _dpapi_protect(data: bytes) -> bytes:
    """Encrypt with Windows DPAPI at user scope (CryptProtectData)."""
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(payload: bytes) -> _DATA_BLOB:
        buffer = ctypes.create_string_buffer(payload, len(payload))
        return _DATA_BLOB(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    source = _blob(data)
    result = _DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN: never prompt (this runs in a server process).
    if not ctypes.WinDLL("Crypt32.dll").CryptProtectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.WinDLL("Kernel32.dll").LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _dpapi_unprotect(data: bytes) -> bytes:
    """Decrypt with Windows DPAPI at user scope (CryptUnprotectData)."""
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(payload: bytes) -> _DATA_BLOB:
        buffer = ctypes.create_string_buffer(payload, len(payload))
        return _DATA_BLOB(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    source = _blob(data)
    result = _DATA_BLOB()
    if not ctypes.WinDLL("Crypt32.dll").CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.WinDLL("Kernel32.dll").LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _fernet_key() -> bytes:
    """Load (or create) the local key file used when DPAPI is unavailable.

    Generated with 0600 permissions and living beside the ciphertext, so it
    protects against at-rest copies and other OS users, not against code
    running as this user. The provider catalog reports the active backend so
    nobody has to guess which guarantee applies.
    """
    from cryptography.fernet import Fernet

    path = _credentials_key_path()
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - platform dependent
        logger.warning("Could not restrict permissions on the credential key file %s", path)
    return key


def credential_encryption_backend() -> str:
    """Active at-rest protection: ``dpapi-user``, ``fernet-file`` or ``none``."""
    if _dpapi_available():
        return "dpapi-user"
    try:
        import cryptography  # noqa: F401
    except Exception:
        return "none"
    return "fernet-file"


def _encrypt_payload(plaintext: bytes) -> tuple[str, str]:
    """Return ``(ciphertext_b64, backend)`` for the credential payload."""
    backend = credential_encryption_backend()
    if backend == "dpapi-user":
        return base64.b64encode(_dpapi_protect(plaintext)).decode("ascii"), backend
    if backend == "fernet-file":
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).encrypt(plaintext).decode("ascii"), backend
    logger.warning(
        "No at-rest encryption available for %s (neither Windows DPAPI nor the 'cryptography' package). Provider keys are being stored in PLAINTEXT; restrict access to the runtime home or install one of them.",
        CREDENTIALS_FILE_NAME,
    )
    return base64.b64encode(plaintext).decode("ascii"), "none"


def _decrypt_payload(ciphertext_b64: str, backend: str) -> bytes:
    """Reverse :func:`_encrypt_payload`; raises when the payload cannot be read."""
    raw = base64.b64decode(ciphertext_b64.encode("ascii"), validate=True)
    if backend == "dpapi-user":
        return _dpapi_unprotect(raw)
    if backend == "fernet-file":
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).decrypt(raw)
    return raw


def _restrict_permissions(path: Path) -> None:
    """Best-effort 0600 on the credentials file (no-op on Windows)."""
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass


def _normalize_credentials(data: Any) -> dict[str, Any] | None:
    """Return *data* as a credentials dict, or ``None`` when it is not one."""
    if not isinstance(data, dict):
        return None
    data.setdefault("providers", {})
    data.setdefault("custom_models", [])
    if not isinstance(data["providers"], dict) or not isinstance(data["custom_models"], list):
        return None
    return data


def _provider_kind(entry: dict[str, Any], provider_id: str) -> str:
    """Tier a persisted entry is screened under (legacy entries included).

    Files written before the ``kind`` field existed are tiered by provider id:
    ``custom`` is the documented local-endpoint provider, everything else gets
    the strict remote tier. Without that, an upgrade would either break every
    existing Ollama/LM Studio user or silently keep unvalidated endpoints.
    """
    declared = entry.get("kind")
    if declared in PROVIDER_KINDS:
        return str(declared)
    return PROVIDER_KIND_LOCAL if provider_id in LOCAL_BY_DEFAULT_PROVIDERS else PROVIDER_KIND_REMOTE


def private_host_allowlist() -> tuple[str, ...]:
    """Operator allowlist of private hosts a model endpoint may use."""
    raw = os.getenv(PRIVATE_HOST_ALLOWLIST_ENV, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _screen_persisted_endpoint(provider_id: str, entry: dict[str, Any], base_url: Any) -> bool:
    """Drop a persisted ``base_url`` the egress policy refuses.

    Runs on the read path with ``resolve_dns=False``: scheme, URL-embedded
    credentials, IP literals and reserved names are screened without a network
    round trip (this runs on every config load), and a DNS name is tiered as
    public. Configure-time validation is the DNS-resolving gate. Returns True
    when the endpoint was removed.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    kind = _provider_kind(entry, provider_id)
    try:
        assert_model_endpoint_url(
            base_url,
            allow_loopback=kind == PROVIDER_KIND_LOCAL,
            allowed_private_hosts=private_host_allowlist(),
            resolve_dns=False,
            action="route model traffic to",
        )
    except ModelEndpointBlockedError as exc:
        logger.warning(
            "Ignoring persisted endpoint for provider '%s': %s (endpoint: %s). Re-configure the provider with an allowed base_url.",
            provider_id,
            exc.reason,
            exc.url,
        )
        return True
    return False


def _screen_persisted_endpoints(data: dict[str, Any]) -> None:
    """Apply :func:`_screen_persisted_endpoint` across a loaded credentials doc."""
    for provider_id, entry in list(data.get("providers", {}).items()):
        if not isinstance(entry, dict):
            continue
        if _screen_persisted_endpoint(provider_id, entry, entry.get("base_url")):
            entry.pop("base_url", None)
    for model in data.get("custom_models", []):
        if not isinstance(model, dict):
            continue
        provider_id = str(model.get("provider") or "custom")
        if _screen_persisted_endpoint(provider_id, model, model.get("base_url")):
            model.pop("base_url", None)


def load_credentials_file() -> dict[str, Any]:
    """Load credentials, decrypting the envelope and migrating legacy plaintext.

    The returned shape is unchanged (``{"providers": {...}, "custom_models":
    [...]}`` with plaintext keys in memory) so every existing consumer keeps
    working; only the on-disk representation changed. A legacy plaintext file
    is rewritten encrypted on first read.
    """
    path = _credentials_path()
    if not path.exists():
        return {"providers": {}, "custom_models": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {"providers": {}, "custom_models": []}
    if not isinstance(raw, dict):
        logger.warning("Ignoring %s: unexpected top-level JSON shape", path)
        return {"providers": {}, "custom_models": []}

    if raw.get("schema") == CREDENTIALS_SCHEMA:
        plaintext: Any = None
        try:
            plaintext = _decrypt_payload(str(raw.get("ciphertext", "")), str(raw.get("encryption", "")))
        except Exception as exc:
            logger.error(
                "Failed to decrypt %s with the '%s' backend (%s: %s). Provider keys are unreadable in this process; re-configure the affected providers. Keys encrypted for another OS user or host cannot be recovered here.",
                path,
                raw.get("encryption"),
                type(exc).__name__,
                exc,
            )
            return {"providers": {}, "custom_models": []}
        data = _normalize_credentials(json.loads(plaintext.decode("utf-8")))
        if data is None:
            logger.warning("Ignoring %s: decrypted payload is not a credentials document", path)
            return {"providers": {}, "custom_models": []}
        _screen_persisted_endpoints(data)
        return data

    # Legacy plaintext file: usable immediately, rewritten encrypted below.
    data = _normalize_credentials(raw)
    if data is None:
        logger.warning("Ignoring %s: unexpected credentials shape", path)
        return {"providers": {}, "custom_models": []}
    _screen_persisted_endpoints(data)
    try:
        save_credentials_file(data)
        logger.info("Migrated %s from plaintext to an encrypted credentials envelope (%s).", path, credential_encryption_backend())
    except Exception as exc:  # a failed upgrade must not lose the caller's config
        logger.error("Could not migrate plaintext credentials in %s: %s", path, exc)
    return data


def save_credentials_file(data: dict[str, Any]) -> None:
    """Encrypt and atomically persist the credentials document."""
    path = _credentials_path()
    temp_path = path.with_suffix(".tmp")
    payload = json.dumps(data, indent=2).encode("utf-8")
    ciphertext, backend = _encrypt_payload(payload)
    envelope = {
        "schema": CREDENTIALS_SCHEMA,
        "version": CREDENTIALS_VERSION,
        "encryption": backend,
        "ciphertext": ciphertext,
    }
    try:
        temp_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        _restrict_permissions(temp_path)
        temp_path.replace(path)
    except Exception as exc:
        logger.error("Failed to write credentials file %s: %s", path, exc)
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def migrate_plaintext_credentials_file() -> bool:
    """Rewrite a legacy plaintext credentials file as an encrypted envelope.

    Returns True when a migration happened, False when there was nothing to do
    (already encrypted, or no file). Idempotent, and callable explicitly by an
    operator or a startup hook; :func:`load_credentials_file` performs the same
    migration implicitly on first read.
    """
    path = _credentials_path()
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Cannot migrate %s: unreadable (%s)", path, exc)
        return False
    if not isinstance(raw, dict) or raw.get("schema") == CREDENTIALS_SCHEMA:
        return False
    data = _normalize_credentials(raw)
    if data is None:
        logger.warning("Cannot migrate %s: unexpected credentials shape", path)
        return False
    save_credentials_file(data)
    logger.info("Migrated plaintext credentials in %s to an encrypted envelope (%s).", path, credential_encryption_backend())
    return True


def credentials_storage_status() -> dict[str, Any]:
    """Honest disclosure of credential-at-rest protection on THIS host.

    ``protection`` is one of:

    * ``dpapi-user`` - Windows DPAPI, user scope. Key material is in the OS
      credential store, so another OS user, a stolen disk image or a backup of
      the runtime home does not yield usable keys.
    * ``fernet-file`` - encrypted with a key file stored (0600) beside it.
      Equivalent to file permissions: it stops at-rest copies and other OS
      users, not a process running as this user.
    * ``none`` - no cipher available; keys are stored in plaintext. Treat the
      runtime home as secret material.

    None of these protect against code already executing as the OS user that
    runs the Gateway - that process can decrypt by design, because it must send
    the key to the provider.
    """
    path = _credentials_path()
    backend = credential_encryption_backend()
    file_exists = path.exists()
    encrypted = False
    if file_exists:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            encrypted = isinstance(raw, dict) and raw.get("schema") == CREDENTIALS_SCHEMA
        except Exception:
            encrypted = False
    return {
        "path": str(path),
        "exists": file_exists,
        "encrypted_at_rest": encrypted,
        "protection": backend if encrypted else ("plaintext" if file_exists else "none"),
        "legacy_plaintext_pending_migration": file_exists and not encrypted,
        "plaintext_on_disk": file_exists and not encrypted,
    }


def mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    secret = secret.strip()
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}...{secret[-4:]}"


def resolve_provider_api_key(entry: dict[str, Any]) -> str:
    """Effective key for a persisted provider entry.

    Resolution order: a literal stored in the entry, then the env var named by
    ``api_key_env``. The env indirection is what lets an operator point a
    provider at a key that only ever exists in the environment (for example
    ``$OPENROUTER_API_KEY``) instead of pasting a secret into an API call that
    is then written to disk.
    """
    literal = (entry.get("api_key") or "").strip()
    if literal:
        return literal
    env_name = (entry.get("api_key_env") or "").strip()
    if env_name and _ENV_NAME_PATTERN.match(env_name):
        return (os.getenv(env_name) or "").strip()
    return ""


def resolve_provider_kind(provider_id: str, declared: Any = None) -> str:
    """Validated endpoint tier for *provider_id*.

    Unknown values raise rather than silently falling back, so a typo cannot
    quietly hand a private endpoint the strict-remote expectation (or, worse,
    hand a remote endpoint the local tier).
    """
    if declared is None or (isinstance(declared, str) and not declared.strip()):
        return PROVIDER_KIND_LOCAL if provider_id in LOCAL_BY_DEFAULT_PROVIDERS else PROVIDER_KIND_REMOTE
    clean = str(declared).strip().lower()
    if clean not in PROVIDER_KINDS:
        raise ValueError(f"Unknown provider kind '{declared}'. Allowed: {', '.join(PROVIDER_KINDS)}")
    return clean


def validate_endpoint_headers(headers: Any) -> dict[str, str]:
    """Normalize extra request headers for a model endpoint.

    Rejects framing/connection headers (``Host`` and friends) and any name or
    value carrying CR/LF, because a configured header is attacker-adjacent
    input that would otherwise allow request splitting or a silent retarget of
    the connection.
    """
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise ValueError("headers must be an object of header name -> value")
    cleaned: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("header names must be non-empty")
        if not isinstance(raw_value, (str, int, float)) or isinstance(raw_value, bool):
            raise ValueError(f"header '{name}' must have a string value")
        value = str(raw_value).strip()
        if name.lower() in _FORBIDDEN_ENDPOINT_HEADERS:
            raise ValueError(f"header '{name}' is not allowed on a model endpoint")
        if any(ch in name or ch in value for ch in ("\r", "\n", "\x00")):
            raise ValueError(f"header '{name}' must not contain CR, LF or NUL")
        cleaned[name] = value
    return cleaned


def get_providers_catalog() -> list[dict[str, Any]]:
    """Build honest provider catalog with configuration status and masked keys.

    Re-resolves ``config.yaml`` on every call rather than reading the
    import-time ``PROVIDER_SPECS`` binding, so a catalog edit is visible to the
    next request without a restart — matching how ``config.yaml`` itself is
    hot-reloaded. Returns an empty list when no catalog is configured, which
    renders as "no providers offered" rather than a stale in-code list.
    """
    specs = refresh_provider_specs()
    creds = load_credentials_file()
    saved_providers = creds.get("providers", {})

    catalog = []
    for spec in specs:
        configured = False
        masked = None
        current_base_url = spec.default_base_url
        declared_kind: Any = None
        api_key_env: str | None = None
        header_names: list[str] = []

        if spec.category == "keyless_free":
            configured = True
        elif spec.key_env:
            # Check environment or persisted credentials
            env_key = os.getenv(spec.key_env, "").strip()
            persisted = saved_providers.get(spec.id, {})
            if not isinstance(persisted, dict):
                persisted = {}

            active_key = env_key or resolve_provider_api_key(persisted)
            if active_key:
                configured = True
                masked = mask_secret(active_key)
            if persisted.get("base_url"):
                current_base_url = persisted["base_url"]
            declared_kind = persisted.get("kind")
            api_key_env = (persisted.get("api_key_env") or "").strip() or None
            if persisted.get("headers"):
                header_names = sorted(validate_endpoint_headers(persisted["headers"]))

        catalog.append(
            {
                "id": spec.id,
                "name": spec.name,
                "category": spec.category,
                "key_env": spec.key_env,
                "configured": configured,
                "masked_key": masked,
                "portal_url": spec.portal_url,
                "free_tier_note": spec.free_tier_note,
                "base_url": current_base_url,
                # Endpoint tier + key/header shape, so a UI can offer the local
                # tier explicitly and show where a key comes from. Header
                # *values* are never echoed (they can carry a secret); only
                # names are.
                "kind": resolve_provider_kind(spec.id, declared_kind),
                "api_key_env": api_key_env,
                "header_names": header_names,
                "default_models": [asdict(m) for m in spec.default_models],
            }
        )
    return catalog


def configure_provider(
    provider_id: str,
    api_key: str | None = None,
    base_url: str | None = None,
    model_id: str | None = None,
    display_name: str | None = None,
    remove: bool = False,
    *,
    api_key_env: str | None = None,
    kind: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Configure or remove credentials for an LLM provider and sync environment.

    ``api_key_env`` names the environment variable that holds the key, so the
    secret never travels through the request body or into the credentials file
    (only the variable's *name* is stored). ``kind`` selects the endpoint tier:
    ``remote`` (default) demands a public endpoint, ``local`` is the declared
    opt-in for a same-host OpenAI-compatible server. ``headers`` adds extra
    request headers, with framing/connection headers rejected.

    A caller-supplied ``base_url`` is screened by the shared egress policy
    before anything is persisted; a refused endpoint raises
    :class:`alpha.community.url_safety.ModelEndpointBlockedError` (a
    ``ValueError``), which the Gateway reports as HTTP 400.
    """
    from datetime import datetime

    spec = next((s for s in refresh_provider_specs() if s.id == provider_id), None)
    if not spec and provider_id != "custom":
        raise ValueError(f"Unknown provider '{provider_id}'")

    creds = load_credentials_file()
    providers = creds.setdefault("providers", {})
    custom_models = creds.setdefault("custom_models", [])

    if remove:
        if provider_id in providers:
            del providers[provider_id]
        if spec and spec.key_env and spec.key_env in os.environ:
            del os.environ[spec.key_env]
        if model_id:
            creds["custom_models"] = [m for m in custom_models if m.get("id") != model_id and m.get("name") != model_id]
        save_credentials_file(creds)
        return {
            "success": True,
            "provider": provider_id,
            "configured": False,
            "message": f"Configuration removed for {provider_id}",
        }

    # Endpoint tier + caller-supplied endpoint are validated before any state
    # is touched, so a refused request leaves the stored configuration intact.
    resolved_kind = resolve_provider_kind(provider_id, kind)
    clean_headers = validate_endpoint_headers(headers)
    supplied_base_url = (base_url or "").strip()
    if supplied_base_url:
        screened_base_url = assert_model_endpoint_url(
            supplied_base_url,
            allow_loopback=resolved_kind == PROVIDER_KIND_LOCAL,
            allowed_private_hosts=private_host_allowlist(),
            resolve_dns=True,
            action="route model traffic to",
        )
    else:
        screened_base_url = ""
    effective_base_url = screened_base_url or (spec.default_base_url if spec else None)

    # Updating or adding provider configuration
    clean_key = api_key.strip() if api_key else ""
    clean_env_name = (api_key_env or "").strip()
    if clean_env_name and not _ENV_NAME_PATTERN.match(clean_env_name):
        raise ValueError(f"api_key_env '{clean_env_name}' is not a valid environment variable name")
    env_key = (os.getenv(clean_env_name, "") or "").strip() if clean_env_name else ""
    if clean_env_name and not env_key:
        raise ValueError(f"api_key_env '{clean_env_name}' is not set in the environment; refusing to persist a provider without a key")
    # A key sourced from the environment is never copied into the file: only
    # the variable name is stored, and the value stays in the environment.
    stored_key = clean_key
    effective_key = clean_key or env_key

    existing_entry = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else {}
    if stored_key or clean_env_name:
        entry: dict[str, Any] = {
            "base_url": effective_base_url,
            "configured_at": datetime.now(UTC).isoformat(),
            "kind": resolved_kind,
        }
        if stored_key:
            entry["api_key"] = stored_key
        if clean_env_name:
            entry["api_key_env"] = clean_env_name
        if clean_headers:
            entry["headers"] = clean_headers
        providers[provider_id] = entry
    elif screened_base_url and existing_entry:
        # Endpoint-only update (no new key): keep the stored key, adopt the
        # endpoint and tier the operator just supplied.
        existing_entry["base_url"] = screened_base_url
        existing_entry["kind"] = resolved_kind
        if clean_headers:
            existing_entry["headers"] = clean_headers
    if effective_key and spec and spec.key_env:
        os.environ[spec.key_env] = effective_key

    # Adding a custom model if requested
    if model_id:
        custom_id = f"{provider_id}:{model_id}" if provider_id != "custom" else model_id
        persisted_provider_entry = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else {}
        # Only an already-stored literal is copied into the model entry. A key
        # sourced from ``api_key_env`` stays in the environment (the provider's
        # key_env was synced above, which is where AppConfig reads it from), so
        # the env-indirection keeps working for custom models too.
        model_key = stored_key or (persisted_provider_entry.get("api_key") or "").strip()
        entry = {
            "id": custom_id,
            "name": display_name or model_id,
            "model": model_id,
            "provider": provider_id,
            "base_url": effective_base_url,
            "api_key": model_key,
            "kind": resolved_kind,
            "supports_thinking": False,
        }
        if clean_env_name:
            entry["api_key_env"] = clean_env_name
        if clean_headers:
            entry["headers"] = clean_headers
        # Update or append
        existing_idx = next((i for i, m in enumerate(custom_models) if m.get("id") == custom_id), None)
        if existing_idx is not None:
            custom_models[existing_idx] = entry
        else:
            custom_models.append(entry)

    save_credentials_file(creds)
    return {
        "success": True,
        "provider": provider_id,
        "configured": True,
        "masked_key": mask_secret(effective_key) if effective_key else None,
        "kind": resolved_kind,
        "api_key_env": clean_env_name or None,
        "base_url": effective_base_url,
        "header_names": sorted(clean_headers),
        "message": f"Successfully configured credentials for {provider_id}",
    }


def sync_all_persisted_credentials_to_env() -> None:
    """Load all persisted credentials and populate os.environ so clients find them."""
    creds = load_credentials_file()
    saved = creds.get("providers", {})
    for spec in refresh_provider_specs():
        if not spec.key_env:
            continue
        persisted = saved.get(spec.id)
        if isinstance(persisted, dict) and resolve_provider_api_key(persisted):
            key = resolve_provider_api_key(persisted)
            # If not already present in environment, inject it
            if not os.getenv(spec.key_env):
                os.environ[spec.key_env] = key


def get_active_provider_models(app_config: Any | None = None) -> list[dict[str, Any]]:
    """Return all models unlocked by currently configured providers + keyless models."""
    sync_all_persisted_credentials_to_env()
    catalog = get_providers_catalog()
    models: list[dict[str, Any]] = []

    for prov in catalog:
        if not prov["configured"]:
            continue
        for m in prov["default_models"]:
            is_free = prov["category"] in ("keyless_free", "recurring_free", "free_gateway", "trial_credits")
            models.append(
                {
                    "name": m["id"],
                    "model": m["model_id"],
                    "display_name": m["name"],
                    "description": m.get("description") or f"Model from {prov['name']}",
                    "provider": prov["id"],
                    "is_free": is_free,
                    "quota_type": prov["category"],
                    "free_status": "online",
                    "supports_thinking": m.get("supports_thinking", False),
                    "supports_reasoning_effort": False,
                }
            )

    # Add custom models
    creds = load_credentials_file()
    for cm in creds.get("custom_models", []):
        models.append(
            {
                "name": cm.get("id", cm.get("name")),
                "model": cm.get("model", cm.get("id")),
                "display_name": cm.get("name"),
                "description": f"Custom model ({cm.get('provider', 'custom')})",
                "provider": cm.get("provider", "custom"),
                "is_free": False,
                "quota_type": "custom",
                "free_status": "online",
                "supports_thinking": cm.get("supports_thinking", False),
                "supports_reasoning_effort": False,
            }
        )

    return models
