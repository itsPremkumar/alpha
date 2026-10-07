"""Find files where a merge took one side wholesale and dropped main's edits.

A three-way merge should combine both sides. When a conflict is resolved by
checking out one side, every change the *other* side had made since the fork
point vanishes silently — `26389cc` did exactly that to `Composer.tsx`,
discarding the runnable-toggle feature that `ea01f6b` had landed on main.

For each merge, this reports a file where:
  * main changed it since the fork point, AND
  * the branch changed it too, AND
  * the merge result is byte-identical to the branch side, AND
  * the merge result differs from main's side

That combination means main's delta was not carried forward.

Run: backend/.venv/Scripts/python.exe scratch/merge_dropped_check.py
"""

from __future__ import annotations

import subprocess
import sys


def sh(*args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=r"C:\Users\PREM KUMAR\Videos\alpha",
    )
    return out.stdout


def numstat(*args: str) -> str:
    return sh("diff", "--numstat", *args).strip()


def main() -> int:
    merges = sys.argv[1:] or ["26389cc", "4201233"]
    found = 0
    for merge in merges:
        p1, p2 = f"{merge}^1", f"{merge}^2"
        base = sh("merge-base", p1, p2).strip()
        print(f"\n=== {merge} (base {base[:8]}) ===")
        files = [f for f in sh("diff", "--name-only", p1, merge).splitlines() if f]
        for f in files:
            main_changed = numstat(base, p1, "--", f)
            branch_changed = numstat(base, p2, "--", f)
            if not main_changed or not branch_changed:
                continue
            result_vs_branch = numstat(p2, merge, "--", f)
            if result_vs_branch:
                continue
            result_vs_main = numstat(p1, merge, "--", f)
            if not result_vs_main:
                continue
            added, removed = main_changed.split("\t")[:2]
            print(f"  DROPPED main-side edits: {f}  (main had +{added}/-{removed})")
            found += 1
    print(f"\ntotal: {found}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
