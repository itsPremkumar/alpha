"""Generate the TypeScript wire types from the Pydantic contract registry.

    python -m alpha.wire_contracts.generate --write
    python -m alpha.wire_contracts.generate --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import registered, render_manifest, render_typescript

HERE = Path(__file__).resolve().parent
DEFAULT_TS_PATH = HERE / "generated.ts"
DEFAULT_MANIFEST_PATH = HERE / "manifest.json"


def build(ts_path: Path | None = None, manifest_path: Path | None = None) -> tuple[str, str]:
    contracts = registered()
    return render_typescript(contracts), json.dumps(render_manifest(contracts), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate wire-contract TypeScript types.")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--ts", default=str(DEFAULT_TS_PATH))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    args = parser.parse_args(argv)
    ts_path, manifest_path = Path(args.ts), Path(args.manifest)
    typescript, manifest = build(ts_path, manifest_path)
    if args.write:
        ts_path.parent.mkdir(parents=True, exist_ok=True)
        ts_path.write_text(typescript, encoding="utf-8", newline="\n")
        manifest_path.write_text(manifest, encoding="utf-8", newline="\n")
        print(f"wrote {ts_path} and {manifest_path}")
        return 0
    drift = []
    if not ts_path.exists() or ts_path.read_text(encoding="utf-8") != typescript:
        drift.append(str(ts_path))
    if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8") != manifest:
        drift.append(str(manifest_path))
    if drift:
        print("DRIFT: " + ", ".join(drift))
        return 1 if args.check else 0
    print("wire contracts are current")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
