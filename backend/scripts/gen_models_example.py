"""Generate ``models.example.yaml`` from the current in-code model namespaces.

One-shot migration helper. The point is to avoid hand-transcribing model data
into YAML: hand-copying is exactly what produced the capability drift this
catalog exists to eliminate (``union-alpha`` was ``supports_thinking: false`` in
``config.yaml`` and ``true`` in two other places).

Run from ``backend/``::

    uv run --no-sync python scripts/gen_models_example.py

Writes ``../models.example.yaml``. The generated file is committed; this script
is kept so the migration is reproducible and auditable rather than a one-off
edit nobody can verify later.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def build() -> dict:
    from alpha.models.cost_governor import MODEL_COST_PER_1K
    from alpha.models.free_router.providers import PROVIDER_ORDER, PROVIDERS
    from alpha.models.provider_manager import PROVIDER_SPECS

    catalog = []
    for spec in PROVIDER_SPECS:
        models = []
        for descriptor in spec.default_models:
            entry = {
                "id": descriptor.id,
                "name": descriptor.name,
                "model_id": descriptor.model_id,
                "supports_thinking": bool(descriptor.supports_thinking),
            }
            if descriptor.description:
                entry["description"] = descriptor.description
            models.append(entry)
        catalog.append(
            {
                "id": spec.id,
                "name": spec.name,
                "category": spec.category,
                "key_env": spec.key_env,
                "portal_url": spec.portal_url,
                "free_tier_note": spec.free_tier_note,
                "base_url": spec.default_base_url,
                "default_use": spec.default_use,
                "models": models,
            }
        )

    free_gateways = []
    for provider_id in PROVIDER_ORDER:
        spec = PROVIDERS[provider_id]
        free_gateways.append(
            {
                "id": spec.name,
                "base_url": spec.base_url,
                "chat_path": spec.chat_path,
                "models_path": spec.models_path,
                "auth_header": [list(header) for header in spec.auth_header],
                "documented_models": list(spec.documented_models),
                "openai_compat": spec.openai_compat,
            }
        )

    # MODEL_COST_PER_1K is per 1K tokens; the catalog schema is per 1M so it
    # matches `models[].pricing` and needs no conversion at read time.
    pricing = {name: {"input": input_per_1k * 1000, "output": output_per_1k * 1000} for name, (input_per_1k, output_per_1k) in MODEL_COST_PER_1K.items()}

    return {
        "config_version": 1,
        "catalog": catalog,
        "free_gateways": free_gateways,
        "pricing": pricing,
    }


HEADER = """\
# ============================================================================
# Alpha model catalog — every model name in one file
# ============================================================================
#
# Copy this file to `models.yaml` in the project root (`make setup` does it for
# you) and treat it as the single place model names are declared.
#
# WHY A SEPARATE FILE
# -------------------
# Model names used to be declared in a dozen places: `config.yaml -> models[]`,
# a bring-your-own-provider catalog, a keyless-gateway list, three model routers,
# a cost table, a persisted default on a Gateway request model, and three
# frontend copies. Only the first was read by the model factory; the rest were
# hand-maintained copies that drifted. The shipped default declared
# `union-alpha` as `supports_thinking: false` in `models[]` and `true` in two
# other catalogs, which produced a thinking toggle the backend rejected.
#
# Aider, Goose, LiteLLM and OpenHands all split the model catalog out of the
# main config for the same reason. This file is that split for Alpha, plus one
# improvement: every name here is validated against `models` at load, so a typo
# is a startup error rather than a per-request surprise.
#
# WHAT BELONGS HERE
# -----------------
#   models         - runtime-buildable models; what the factory constructs.
#                    Same schema as `config.yaml -> models`, and provider
#                    quirk adapters (patched_deepseek, vllm_provider, ...)
#                    still apply.
#   providers      - named connection profiles (endpoint, key, timeouts)
#                    inherited by `models[].provider` entries.
#   routing        - intent category / cost tier -> ordered model names.
#                    This is the ONLY executable routing source; the routers
#                    fail closed rather than naming a model that is not here.
#   catalog        - bring-your-own-provider offers shown in Settings. These
#                    are offers, not executions; a name also present in
#                    `models` must declare identical capabilities.
#   free_gateways  - keyless public gateways for the free router. Anonymous
#                    reachability changes without notice; no availability
#                    guarantee, and never send secrets through them.
#   pricing        - fallback prices per 1M tokens. `models[].pricing` stays
#                    authoritative for anything you actually run.
#   default_model  - the model a run uses when nothing selects one.
#
# SECRETS
# -------
# Values beginning with `$` resolve from the environment (`$OPENROUTER_API_KEY`).
# The loader resolves them; the resolved catalog is never written back to disk.
#
# PRECEDENCE
# ----------
# `models.yaml` is the BASE layer. `config.yaml` still declares `models:`,
# `providers:` and `model_routing:`, and those OVERRIDE this file, so an existing
# deployment keeps working while you migrate entries here at your own pace. A
# `models[]` entry is replaced wholesale by name, not field-merged, so exactly
# one file is authoritative for any given name.
#
# Path resolution (mirrors `extensions_config.json`):
#   1. explicit path argument
#   2. $ALPHA_MODELS_CONFIG_PATH
#   3. `models.yaml` in the project root
#   4. `backend/models.yaml`, then the repository root
# An explicit path or env var naming a missing file is an error; the file is
# otherwise optional and a deployment may configure everything in `config.yaml`.
#
# Hot-reloaded on content change, like `config.yaml` — a `max_tokens` or routing
# edit applies to the next message without a restart.
#
# Generated from the previous in-code model namespaces by
# `backend/scripts/gen_models_example.py`; edit this file, not that script.
# ============================================================================

"""


def main() -> int:
    data = build()
    target = Path(__file__).resolve().parents[2] / "models.example.yaml"
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=200)
    target.write_text(HEADER + body, encoding="utf-8")
    print(f"wrote {target}")
    print(f"  catalog providers: {len(data['catalog'])}")
    print(f"  catalog models:    {sum(len(c['models']) for c in data['catalog'])}")
    print(f"  free gateways:     {len(data['free_gateways'])}")
    print(f"  pricing entries:   {len(data['pricing'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
