"""Live smoke check for model discovery against real provider catalogs.

Not a unit test: it makes outbound requests. Run deliberately:

    cd backend && uv run --no-sync python scripts/check_discovery.py

Prints what each provider reported so the normalized shape can be eyeballed
against the provider's own documentation. A provider that is unreachable, rate
limited, or requires a key prints its recorded error instead of failing the run.
"""

from __future__ import annotations

import sys

from alpha.models import discovery


def main() -> int:
    providers = discovery.known_providers()
    if not providers:
        print("No discoverable providers. Declare a `catalog:` entry with a base_url in models.yaml.")
        return 1
    print(f"discoverable providers: {', '.join(providers)}\n")
    for provider in providers:
        state = discovery.fetch_models(*((provider, *discovery.configured_endpoint(provider))))
        if not state.ok:
            print(f"[{provider}] unavailable: {state.error}")
            continue
        free = [m for m in state.models if m.get("is_free")]
        print(f"[{provider}] {len(state.models)} models ({len(free)} free) from {state.source_url}")
        for model in state.models[:3]:
            print(
                f"    {model['id']}: ctx={model['context_length']}"
                f" endpoint_ctx={model['endpoint_context_length']}"
                f" max_out={model['endpoint_max_completion_tokens']}"
                f" vision={model['supports_vision']}"
                f" thinking={model['supports_thinking']}"
                f" free={model['is_free']}"
            )
        if free:
            print(f"    free example: {free[0]['id']}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
