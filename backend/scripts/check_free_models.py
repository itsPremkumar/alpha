"""Ad-hoc live check of every keyless free-LLM provider.

Runs catalog discovery and a small real chat probe against each provider in
``alpha.models.free_router.providers.PROVIDERS`` and prints an honest
per-provider verdict. This is a manual operator tool, not a test: it performs
real network calls to public anonymous gateways.

Usage (from backend/):
    PYTHONPATH=. uv run python scripts/check_free_models.py [--no-probe]
"""

from __future__ import annotations

import argparse
import sys

from alpha.models.free_router import providers as provider_layer
from alpha.models.free_router.catalog import get_free_router, reset_free_router


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="Only run catalog discovery (skip real chat probes).",
    )
    args = parser.parse_args()

    # Start from a clean slate so results reflect the network right now.
    reset_free_router()

    print("=" * 78)
    print("STEP 1: Catalog discovery per provider")
    print("=" * 78)
    discovery: dict[str, provider_layer.DiscoveryResult] = {}
    for name in provider_layer.PROVIDER_ORDER:
        spec = provider_layer.PROVIDERS[name]
        result = provider_layer.discover(spec)
        discovery[name] = result
        status = "OK " if result.ok else "FAIL"
        models = ", ".join(m["id"] for m in result.models[:6]) or "-"
        more = "" if len(result.models) <= 6 else f" (+{len(result.models) - 6} more)"
        latency = f"{result.latency_ms:.0f}ms" if result.latency_ms is not None else "-"
        print(f"[{status}] {name:<12} {latency:>8}  models={len(result.models):<3} {models}{more}")
        if not result.ok:
            print(f"         error: {result.error}")

    if args.no_probe:
        print("\nSkipping chat probes (--no-probe).")
        return 0

    print()
    print("=" * 78)
    print("STEP 2: Real chat probe per provider (small 'Reply with OK.' call)")
    print("=" * 78)
    probe_ok = 0
    probe_fail = 0
    for name in provider_layer.PROVIDER_ORDER:
        spec = provider_layer.PROVIDERS[name]
        result = discovery[name]
        if not result.models:
            print(f"[SKIP] {name:<12} no candidate model to probe")
            probe_fail += 1
            continue
        model_id = result.models[0]["id"]
        probe = provider_layer.health_probe(spec, model_id)
        if probe.ok:
            probe_ok += 1
            print(f"[OK  ] {name:<12} model={model_id:<28} {probe.latency_ms:.0f}ms")
        else:
            probe_fail += 1
            print(f"[FAIL] {name:<12} model={model_id:<28} {probe.latency_ms:.0f}ms  {probe.error}")

    print()
    print("=" * 78)
    print("STEP 3: Router view (what the app actually uses)")
    print("=" * 78)
    router = get_free_router()
    router.refresh(force=True)
    probes = router.probe()
    view = router.catalog_dict()
    print(f"eligible providers: {view.get('eligible_providers') or view.get('eligible') or '-'}")
    print(f"probe summary: {probes}")
    print(f"discovery ok={sum(1 for r in discovery.values() if r.ok)}/{len(discovery)} "
          f"probe ok={probe_ok}/{probe_ok + probe_fail}")
    return 0 if probe_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
