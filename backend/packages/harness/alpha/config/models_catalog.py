"""``models.yaml`` — the single source of truth for every model name in Alpha.

## Why a separate file

Alpha names models in a dozen places. Before this module, ``config.yaml ->
models[]`` was the only namespace ``alpha.models.factory.create_chat_model``
actually read, while eleven other places carried hand-maintained copies of
model names: the bring-your-own-provider catalog, the keyless-gateway list, the
intent/cost-tier routers, the cost table, a persisted default on a Gateway
request model, and three frontend copies. Those copies drift silently — the
shipped default declared ``union-alpha`` as ``supports_thinking: false`` in
``models[]`` and ``true`` in two other catalogs, which produced a thinking
control the backend rejected.

Splitting the model catalog into its own file is the pattern the rest of the
field converged on independently:

* **Aider** splits ``.aider.conf.yml`` (main) from ``.aider.model.settings.yml``
  (per-model behavior) and ``.aider.model.metadata.json`` (window + cost), each
  with its own ``--*-file`` override.
* **Goose** ships ``crates/goose-providers/src/declarative/definitions/*.json``
  — one declarative JSON per provider, separate from ``config.yaml``.
* **LiteLLM** keeps ``model_prices_and_context_window.json`` as a registry
  distinct from the ``model_list`` that selects deployments.
* **OpenHands** keeps named ``[llm.<name>]`` LLM configs addressable by slot
  name rather than by vendor string.

All four separate *what a model is* from *what the app does*, and all four give
the model file its own path override and precedence chain. This module does the
same for Alpha and additionally makes every declared name resolvable, so an
unresolvable entry is a load error rather than a per-request surprise.

## Precedence

``models.yaml`` is the **base** layer. ``config.yaml`` still declares
``models:``, ``providers:`` and ``model_routing:``, and those **override** the
catalog, so an existing deployment keeps working untouched while an operator
migrates entries into the dedicated file at their own pace. A ``models[]``
entry is replaced wholesale by name, not field-merged, so one file is always
authoritative for a given name.

Resolution order for the file path mirrors
:mod:`alpha.config.extensions_config` exactly:

1. explicit ``config_path`` argument
2. ``ALPHA_MODELS_CONFIG_PATH`` environment variable
3. ``models.yaml`` in the caller project root
4. ``backend/models.yaml`` then the repository root (monorepo compatibility)
5. ``None`` — the catalog is optional; a deployment may configure everything
   through ``config.yaml``

An explicit path or a set env var is an operator assertion that one particular
file must be used, so a missing file in those two modes raises
``FileNotFoundError``. Only the fallback *search* returns ``None``.

``$VAR`` strings are resolved from the environment on load, exactly as in
``config.yaml``. A resolved catalog must therefore never be serialized back to
disk: it holds secrets in plaintext and would erase the references. There is no
writer for this file.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from alpha.config.model_config import ModelConfig, ProviderConfig
from alpha.config.model_routing_config import ModelRoutingConfig

logger = logging.getLogger(__name__)

#: Bumped when the ``models.yaml`` schema changes. Mirrors the ``config.yaml``
#: convention: a deployment whose file is older logs a warning and keeps working.
MODELS_CATALOG_VERSION: int = 1

#: Env var that points at the catalog, mirroring
#: ``ALPHA_EXTENSIONS_CONFIG_PATH``.
MODELS_CONFIG_PATH_ENV: str = "ALPHA_MODELS_CONFIG_PATH"

#: Candidate file names, in resolution order.
MODELS_CATALOG_FILENAMES: tuple[str, ...] = ("models.yaml", "models.yml")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class CatalogModelEntry(BaseModel):
    """One model offered by a provider in the bring-your-own-provider catalog.

    These are *catalog* entries — what the Settings picker offers — not
    necessarily what a run executes. A name that also exists in ``models[]``
    must declare identical capabilities, which
    :mod:`alpha.models.catalog_consistency` enforces.
    """

    id: str = Field(..., min_length=1, description="Catalog identifier, unique across the catalog")
    name: str = Field(..., min_length=1, description="Human-readable display name")
    model_id: str = Field(..., min_length=1, description="Provider-side model identifier")
    supports_thinking: bool = Field(default=False, description="Whether this model supports extended thinking")
    supports_vision: bool | None = Field(default=None, description="Whether this model accepts image input")
    context_window: int | None = Field(default=None, gt=0, description="Total context capacity in tokens")
    description: str | None = Field(default=None, description="One-line description shown in the picker")

    model_config = ConfigDict(extra="forbid")


class CatalogProviderEntry(BaseModel):
    """A bring-your-own-provider the operator can attach a key to."""

    id: str = Field(..., min_length=1, description="Unique provider id")
    name: str = Field(..., min_length=1, description="Human-readable provider name")
    category: str = Field(
        ...,
        description="One of: keyless_free, recurring_free, free_gateway, trial_credits, paid, custom",
    )
    key_env: str | None = Field(default=None, description="Environment variable holding the API key; null for keyless providers")
    portal_url: str = Field(default="", description="Where the operator obtains a key")
    free_tier_note: str = Field(default="", description="Free-tier terms shown in the picker")
    base_url: str | None = Field(default=None, description="Default OpenAI-compatible endpoint")
    default_use: str = Field(default="langchain_openai:ChatOpenAI", description="Model class path used to build this provider's clients")
    models: list[CatalogModelEntry] = Field(default_factory=list, description="Models this provider offers")

    model_config = ConfigDict(extra="forbid")

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        allowed = {"keyless_free", "recurring_free", "free_gateway", "trial_credits", "paid", "custom"}
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"unknown provider category '{value}'; allowed: {', '.join(sorted(allowed))}")
        return normalized


class FreeGatewayEntry(BaseModel):
    """A keyless public gateway the free router may fall back to.

    Never carries a personal credential: ``auth_header`` holds anonymous
    constants only (some gateways require a syntactically valid but meaningless
    Authorization value).
    """

    id: str = Field(..., min_length=1, description="Unique gateway id")
    base_url: str = Field(..., min_length=1, description="OpenAI-compatible base URL")
    chat_path: str | None = Field(default=None, description="Relative chat path; null means provider-specific schema")
    models_path: str | None = Field(default=None, description="Relative catalog path; null means no public catalog")
    auth_header: list[list[str]] = Field(
        default_factory=list,
        description="Anonymous constant headers as [name, value] pairs. Never a personal credential.",
    )
    documented_models: list[str] = Field(default_factory=list, description="Model ids documented as free when the catalog omits them")
    openai_compat: bool = Field(default=True, description="Whether the gateway speaks the OpenAI chat schema")

    model_config = ConfigDict(extra="forbid")

    @field_validator("auth_header")
    @classmethod
    def _pairs(cls, value: list[list[str]]) -> list[list[str]]:
        for entry in value:
            if not isinstance(entry, list) or len(entry) != 2 or not all(isinstance(part, str) for part in entry):
                raise ValueError("auth_header entries must be [name, value] string pairs")
        return value


class ModelPriceEntry(BaseModel):
    """Fallback price for a model name, used when ``models[].pricing`` omits it.

    Prices are per one million tokens, matching ``models[].pricing`` so the two
    sources never need conversion. Keep ``models[].pricing`` authoritative for
    anything you actually run: this table is a fallback so a console run of an
    unpriced model still gets an estimate instead of ``None``.
    """

    input: float = Field(..., ge=0, description="Price per 1M input tokens (cache miss)")
    output: float = Field(..., ge=0, description="Price per 1M output tokens")

    model_config = ConfigDict(extra="forbid")


class ModelsCatalog(BaseModel):
    """Root schema of ``models.yaml``."""

    config_version: int = Field(default=MODELS_CATALOG_VERSION, description="Schema version of this file")
    default_model: str | None = Field(default=None, description="Model a run uses when nothing selects one; must exist in `models`")
    models: list[ModelConfig] = Field(default_factory=list, description="Runtime-buildable models (what the factory constructs)")
    providers: dict[str, ProviderConfig] = Field(default_factory=dict, description="Named connection profiles inherited by models[].provider")
    routing: ModelRoutingConfig = Field(default_factory=ModelRoutingConfig, description="Intent-category and cost-tier -> model chains")
    catalog: list[CatalogProviderEntry] = Field(default_factory=list, description="Bring-your-own-provider catalog offered in Settings")
    free_gateways: list[FreeGatewayEntry] = Field(default_factory=list, description="Keyless public gateways for the free router")
    pricing: dict[str, ModelPriceEntry] = Field(default_factory=dict, description="Fallback per-model prices, per 1M tokens")

    model_config = ConfigDict(extra="forbid")

    def catalog_model_names(self) -> set[str]:
        """Every model name declared in the bring-your-own-provider catalog."""
        return {model.id for provider in self.catalog for model in provider.models}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _resolve_env_variables(value: Any) -> Any:
    """Recursively resolve ``$VAR`` and ``${VAR}`` strings from the environment.

    Mirrors ``config.yaml``: an unset variable becomes ``""``. Applied on load
    only — the resolved object must never be written back to disk.
    """
    if isinstance(value, dict):
        return {key: _resolve_env_variables(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_variables(item) for item in value]
    if isinstance(value, str) and value.startswith("$"):
        name = value[1:].strip()
        if name.startswith("{") and name.endswith("}"):
            name = name[1:-1].strip()
        return os.getenv(name, "")
    return value


def _existing_project_file(names: tuple[str, ...]) -> Path | None:
    """First of *names* present in the caller's project root, if any."""
    try:
        from alpha.config.paths import project_root

        root = project_root()
    except Exception:
        return None
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def resolve_models_config_path(config_path: str | None = None) -> Path | None:
    """Resolve the ``models.yaml`` path, mirroring ``ExtensionsConfig``.

    Priority: explicit ``config_path`` → ``ALPHA_MODELS_CONFIG_PATH``
    → project root → ``backend/`` and repository root → ``None``.

    An explicit argument or a set env var raises ``FileNotFoundError`` when the
    file is missing, because both are an operator assertion that one particular
    file must be used. Only the fallback search returns ``None``: the catalog is
    optional and a deployment may configure everything through ``config.yaml``.
    """
    if config_path:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Models catalog specified by param `config_path` not found at {path}")
        return path
    if env_path := os.getenv(MODELS_CONFIG_PATH_ENV):
        path = Path(env_path)
        if not path.exists():
            raise FileNotFoundError(f"Models catalog specified by environment variable `{MODELS_CONFIG_PATH_ENV}` not found at {path}")
        return path

    if project_config := _existing_project_file(MODELS_CATALOG_FILENAMES):
        return project_config

    backend_dir = Path(__file__).resolve().parents[4]
    repo_root = backend_dir.parent
    for directory in (backend_dir, repo_root):
        for name in MODELS_CATALOG_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_models_catalog(config_path: str | None = None) -> ModelsCatalog:
    """Load and validate ``models.yaml``.

    Returns an empty catalog when no file is found (the optional case). Raises
    ``FileNotFoundError`` when an explicit path or env var names a missing file,
    and ``ValidationError`` when the file exists but is malformed — an
    unresolvable or misspelled model is a startup error, never a silent default.
    """
    resolved = resolve_models_config_path(config_path)
    if resolved is None:
        return ModelsCatalog()

    text = resolved.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Models catalog at {resolved} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Models catalog at {resolved} must be a YAML mapping, got {type(raw).__name__}.")

    data = _resolve_env_variables(raw)
    catalog = ModelsCatalog.model_validate(data)

    if catalog.config_version != MODELS_CATALOG_VERSION:
        logger.warning(
            "Models catalog %s declares config_version %s but this build expects %s. Add any new fields by hand; unknown keys are rejected so a newer file fails loudly rather than being half-read.",
            resolved,
            catalog.config_version,
            MODELS_CATALOG_VERSION,
        )
    return catalog


# ---------------------------------------------------------------------------
# Process cache (hot reload on content change)
# ---------------------------------------------------------------------------

_catalog_lock = threading.Lock()
_catalog_cache: tuple[ModelsCatalog, str] | None = None


def _fingerprint(path: Path | None) -> str:
    """Content digest of the catalog file, or ``""`` when absent.

    Content-based rather than mtime-based so an edit is picked up on network and
    object-store mounts where mtime can stay stale — the same reasoning as
    :class:`alpha.config.file_signature.ConfigSignature` for ``config.yaml``.
    """
    if path is None:
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        logger.debug("Could not fingerprint models catalog at %s", path, exc_info=True)
        return ""


def get_models_catalog(config_path: str | None = None) -> ModelsCatalog:
    """Cached :func:`load_models_catalog`, reloaded when the file content changes.

    ``models.yaml`` is hot-reloadable for the same reason ``config.yaml`` is: a
    Gateway request routes through ``get_app_config()`` on every call, so a
    per-run field like a model's ``max_tokens`` or a routing chain must be
    visible on the next message without a restart.
    """
    global _catalog_cache
    path = resolve_models_config_path(config_path)
    fingerprint = _fingerprint(path)
    cached = _catalog_cache
    if cached is not None and cached[1] == fingerprint:
        return cached[0]
    catalog = load_models_catalog(config_path)
    with _catalog_lock:
        _catalog_cache = (catalog, fingerprint)
    return catalog


def reset_models_catalog_cache() -> None:
    """Drop the cached catalog. Test seam for the content-signature reload."""
    global _catalog_cache
    with _catalog_lock:
        _catalog_cache = None


def read_raw_models_catalog(config_path: str | None = None) -> dict[str, Any]:
    """The catalog file exactly as written, with ``$VAR`` references intact.

    Provided so an operator-facing tool can show the file without leaking a
    resolved secret. Alpha has no writer for ``models.yaml``; this is read-only
    by design, because a round-trip through :class:`ModelsCatalog` would persist
    secrets in plaintext and erase the ``$VAR`` references.
    """
    resolved = resolve_models_config_path(config_path)
    if resolved is None:
        return {}
    return yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}


__all__ = [
    "MODELS_CATALOG_FILENAMES",
    "MODELS_CATALOG_VERSION",
    "MODELS_CONFIG_PATH_ENV",
    "CatalogModelEntry",
    "CatalogProviderEntry",
    "FreeGatewayEntry",
    "ModelPriceEntry",
    "ModelsCatalog",
    "get_models_catalog",
    "load_models_catalog",
    "read_raw_models_catalog",
    "reset_models_catalog_cache",
    "resolve_models_config_path",
]
