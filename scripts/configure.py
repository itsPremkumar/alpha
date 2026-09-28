#!/usr/bin/env python3
"""Cross-platform config bootstrap script for Alpha."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def copy_if_missing(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    if not src.exists():
        raise FileNotFoundError(f"Missing template file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent

    # Seed every template, reporting which were created and which already
    # existed. This is idempotent by construction rather than by an early
    # abort: Install.md requires that re-running setup never damages an existing
    # configuration, and a pre-check that refused whenever config.yaml existed
    # made a partially-failed first run impossible to recover with the same
    # command. copy_if_missing already skips an existing destination, so an
    # operator's edits are never overwritten.
    seeds: list[tuple[Path, Path]] = [
        # config.yaml carries EVERY model setting (models[], providers:,
        # model_routing, default_model, model_catalog:, free_gateways:,
        # model_pricing:), so this is the only model file a new install needs.
        # A separate second model file used to be seeded here, which is how the
        # catalog ended up split in two — and why the interactive setup wizard,
        # which never seeded it, produced an install whose keyless gateway list
        # was empty and whose `alpha-free` runs all failed with "no free provider
        # candidates: discovery has not succeeded for any provider yet". Both
        # entry points now seed exactly this set.
        (project_root / "config.example.yaml", project_root / "config.yaml"),
        (project_root / ".env.example", project_root / ".env"),
        (project_root / "frontend" / ".env.example", project_root / "frontend" / ".env"),
    ]

    # extensions_config.json is optional at runtime (the config loader treats a
    # missing file as "no extensions configured"), but the MCP/skills templates
    # are what a new operator needs to enable their first server, so seed it
    # alongside the rest rather than leaving it to the Docker path only.
    seeds.append(
        (
            project_root / "extensions_config.example.json",
            project_root / "extensions_config.json",
        )
    )

    created: list[Path] = []
    skipped: list[Path] = []
    try:
        for src, dst in seeds:
            if dst.exists():
                skipped.append(dst.relative_to(project_root))
                continue
            copy_if_missing(src, dst)
            created.append(dst.relative_to(project_root))
    except (FileNotFoundError, OSError) as exc:
        print("Error while generating configuration files:")
        print(f"  {exc}")
        if isinstance(exc, PermissionError):
            print("Hint: Check file permissions and ensure the files are not read-only or locked by another process.")
        if created:
            print("Already created (re-run to finish the remaining files):")
            for path in created:
                print(f"  + {path}")
        return 1

    for path in created:
        print(f"  + {path}")
    for path in skipped:
        print(f"  = {path} (already present, left unchanged)")
    # ASCII only: this script runs under `make config` on a stock Windows console
    # whose encoding is cp1252, and a non-ASCII status character raises
    # UnicodeEncodeError *after* the files are written -- so the setup appeared
    # to fail on a machine where it had actually succeeded.
    print("[ok] Configuration files ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
