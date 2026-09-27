#!/usr/bin/env python
"""Migrate Alpha's runtime state directory from the pre-rename names to `.alpha`.

The project was renamed from `agent-workspace` to `alpha`. Two on-disk names
changed with it:

    .agent-workspace/        ->  .alpha/
    .agent_workspace_projects/  ->  .alpha_projects/

Neither the old nor the new directory is tracked by git, so this is purely an
operator-local operation on machine state. The script is intentionally
conservative:

* It never overwrites a destination that already has content. If `.alpha/`
  exists and is non-empty, that directory is left completely alone and reported
  as a conflict, because merging two state trees can corrupt checkpoints.
* It refuses to run while the destination filesystem cannot be renamed, and it
  reports the likely cause (a running Gateway holding a handle) rather than
  half-copying 5 GB and leaving a corrupt merge.
* It is idempotent: running it twice is a no-op once migration has happened.

Usage:
    python scripts/migrate_state_dir.py            # migrate if needed
    python scripts/migrate_state_dir.py --check    # report only, change nothing
    python scripts/migrate_state_dir.py --root .   # operate on a specific root
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# (old_name, new_name) pairs, relative to each project root that is checked.
RENAMES: list[tuple[str, str]] = [
    (".agent-workspace", ".alpha"),
    (".agent_workspace_projects", ".alpha_projects"),
]

# Roots to inspect: the repo root, and backend/ which uses its own data dir when
# the Gateway is run from there (`backend/Makefile` sets ALPHA_HOME explicitly).
ROOTS: list[str] = [".", "backend"]


def is_empty(path: Path) -> bool:
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    except OSError:
        return False
    return False


def dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def human(n: int) -> str:
    step = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.1f}{unit}"
        step /= 1024
    return f"{step:.1f}TB"


def migrate(check_only: bool) -> int:
    repo = Path.cwd().resolve()
    conflicts = 0
    moved = 0

    for root_rel in ROOTS:
        root = (repo / root_rel).resolve()
        if not root.is_dir():
            continue
        for old_name, new_name in RENAMES:
            src = root / old_name
            dst = root / new_name

            if not src.is_dir():
                continue

            if dst.exists():
                if is_empty(dst):
                    # Destination is an empty leftover (e.g. created by a test
                    # import). Safe to drop so the real state can move in.
                    if check_only:
                        print(f"[{root_rel}] {old_name}/ -> {new_name}/  (destination empty, would replace)")
                    else:
                        try:
                            dst.rmdir()
                            print(f"[{root_rel}] removed empty {new_name}/")
                        except OSError as exc:
                            print(f"[{root_rel}] ERROR could not remove empty {new_name}/: {exc}")
                            conflicts += 1
                            continue
                else:
                    size = dir_size(dst)
                    print(
                        f"[{root_rel}] CONFLICT {new_name}/ already exists "
                        f"({human(size)}) and {old_name}/ "
                        f"({human(dir_size(src))}) would collide.\n"
                        f"           Left both untouched. To proceed, move or delete\n"
                        f"           {dst} yourself, then re-run this script."
                    )
                    conflicts += 1
                    continue

            if check_only:
                print(f"[{root_rel}] would move {old_name}/ -> {new_name}/  ({human(dir_size(src))})")
                moved += 1
                continue

            try:
                # rename() is atomic within a volume and refuses to cross a
                # handle, which is the safe failure we want.
                src.rename(dst)
            except OSError as exc:
                print(
                    f"[{root_rel}] ERROR could not move {old_name}/ -> {new_name}/: {exc}\n"
                    f"           Most likely cause: Alpha is still running and holds a\n"
                    f"           handle on the state directory. Stop it (make stop, or\n"
                    f"           close the desktop app) and re-run this script."
                )
                conflicts += 1
                continue
            print(f"[{root_rel}] moved {old_name}/ -> {new_name}/  ({human(dir_size(dst))})")
            moved += 1

    print()
    if conflicts:
        print(f"{conflicts} conflict(s) - see above. Nothing was merged or overwritten.")
        return 1
    if check_only:
        print(f"check complete: {moved} migration(s) pending.")
        return 0
    if moved:
        print(f"Done. {moved} state directory/directories migrated to the .alpha names.")
    else:
        print("Nothing to migrate - state already uses the .alpha names.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report what would change, without touching anything")
    parser.add_argument("--root", default=None, help="repository root (default: current directory)")
    args = parser.parse_args()
    if args.root:
        import os

        os.chdir(args.root)
    return migrate(args.check)


if __name__ == "__main__":
    raise SystemExit(main())
