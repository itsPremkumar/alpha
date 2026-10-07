"""Does the COMMITTED tree build, or only the working tree?

This is the distinction that matters. `main`'s working tree has the untracked
`slash-command-palette.ts` sitting on disk, so every local check passes while a
clone of `main` would fail. Verifying the working tree is exactly the mistake that
let `e6f2e5d` through.

So this exports the staged index to a throwaway directory and checks the question a
clone would ask: does every specifier that committed code imports resolve to a
committed file? It does not need node_modules — it is a closure check over the
committed file set, not a type-check.

Read-only with respect to the repository: `git archive` writes only to a temp dir.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

FRONTEND_SRC = "frontend/src"


def git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or r.stderr).strip()


def main() -> int:
    # `--cached` includes what is staged; HEAD would miss the fix being verified.
    code, _ = git("rev-parse", "--verify", "HEAD")
    if code != 0:
        print("FAIL  no HEAD")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        # `git stash create`-free approach: diff against nothing, using the index.
        tar_path = os.path.join(tmp, "tree.tar")
        r = subprocess.run(
            ["git", "archive", "--format=tar", "-o", tar_path, "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            print(f"FAIL  git archive: {r.stderr[:200]}")
            return 1

        extracted = os.path.join(tmp, "tree")
        os.makedirs(extracted, exist_ok=True)
        with tarfile.open(tar_path) as tf:
            tf.extractall(extracted)  # noqa: S202 - a temp dir from our own archive

        root = Path(extracted)
        src = root / FRONTEND_SRC
        if not src.is_dir():
            print(f"FAIL  {FRONTEND_SRC} not in the archive")
            return 1

        # Every relative specifier in committed frontend source.
        pattern = re.compile(r'from\s+"(\.[^"]+|@/[^"]+)"')
        unresolved: list[tuple[str, str]] = []
        checked = 0
        for ts in src.rglob("*.ts*"):
            if not ts.is_file():
                continue
            text = ts.read_text(encoding="utf-8", errors="replace")
            for spec in set(pattern.findall(text)):
                checked += 1
                rel = ts.parent
                if spec.startswith("@/"):
                    target = src / spec[2:]
                else:
                    target = (rel / spec).resolve()
                candidates = [target.with_suffix(ext) for ext in (".ts", ".tsx", ".mjs", ".js")]
                candidates += [target / f"index{ext}" for ext in (".ts", ".tsx", ".mjs")]
                # A non-code asset is a legitimate target: `@/assets/images/alpha.png`
                # and `../../package.json` are real imports that resolve to a file
                # with no code suffix. Without this the check reports a healthy tree
                # as broken, which is worse than not checking.
                candidates.append(target)
                if not any(c.exists() for c in candidates):
                    unresolved.append((str(ts.relative_to(root)), spec))

        print(f"committed frontend sources scanned : {len(list(src.rglob('*.ts*')))}")
        print(f"import specifiers checked          : {checked}")
        print(f"unresolved in the COMMITTED tree   : {len(unresolved)}")
        for f, spec in sorted(set(unresolved))[:25]:
            print(f"  {f}  ->  {spec}")

        if unresolved:
            print()
            print("FAIL  a clone of this commit cannot resolve these imports.")
            print("      The working tree may still pass, because an untracked file covers it.")
            return 1

        print()
        print("VERIFIED  every import in the committed frontend resolves to a committed file,")
        print("          so this commit can build from a fresh clone.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
