"""Print the authoritative counts, and explain any drift, before anything is edited.

The point is to never "correct" a document toward a number I asserted. A
historical record - CHANGELOG, SELF_AUDIT, FULL_VERIFICATION_REPORT - describes a
past state and rewriting it would be falsifying evidence. Only *current claims*
get corrected, and only toward what the generator actually produces right now.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "contracts" / "feature_manifest.json"


def main() -> int:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(f"manifest generated_at: {data.get('generated_at')}")
    print(f"generator:            {data.get('generator')}")
    print()
    for key in ("tools", "routers", "middlewares", "loops", "engines"):
        print(f"{key:14} {len(data.get(key, []))}")
    print()
    unwired = {k: [e for e in data.get(k, []) if not e.get("wired", True)] for k in ("tools", "routers", "middlewares", "loops")}
    for k, v in unwired.items():
        print(f"unwired {k:12} {len(v)}")
    print(f"dormant_packages     {len(data.get('dormant_packages', []))}")
    print(f"intentionally_unwired {len(data.get('intentionally_unwired', []))}")
    print(f"excluded_local_only  {len(data.get('excluded_local_only', []))}")
    print()

    # The stray-tree trap: collect_engines() counts any directory with submodules,
    # so an EMPTY stray directory can still be counted, and the committed number
    # then disagrees with a fresh run. Checked explicitly rather than assumed.
    alpha = ROOT / "backend" / "packages" / "harness" / "alpha"
    stray = alpha / "backend"
    if stray.exists():
        entries = sorted(p.name for p in stray.iterdir())
        print(f"STRAY TREE EXISTS: backend/packages/harness/alpha/backend/ -> {entries}")
        print("  A regeneration may count this; compare against the committed number.")
    else:
        print("no stray backend/ tree under alpha/")

    stray_names = {"find_re.sh", "parse_scan2.py", "url_probe2.py"}
    stray_scripts = sorted({p.name for p in alpha.rglob("*") if p.is_file() and p.name in stray_names})
    print(f"stray alpha-root scripts: {stray_scripts or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
