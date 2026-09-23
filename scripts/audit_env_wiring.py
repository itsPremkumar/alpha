#!/usr/bin/env python3
"""Config-wiring audit: every env var a launcher sets must be read somewhere.

Companion to audit_frontend_wiring.py for the configuration layer. A global
rename once collapsed ``X || Y`` pairs (see ops/version fix), so an
``AGENT_WORKSPACE_*``/``ALPHA_*`` variable that launchers still SET while no
file in the repo READS it is a silently dead override — exactly the
unwired-feature class this audit exists to catch.

Scans tracked source files for:
  SET    .env.example keys, PowerShell ``$env:X =``, shell ``export X=``,
         Dockerfile ``ENV X=``, electron spawn-env object keys, Next public vars.
  READ   Python ``os.environ``/``os.getenv``, JS ``process.env``,
         PowerShell ``$env:X`` used anywhere, shell ``${X:-}``/``"$X"`` in
         .sh/.ps1, pydantic/dotenv contract keys (.env.example = implicit read).

Verdicts (rename families only, ``AGENT_WORKSPACE_*`` and ``ALPHA_*``):
  DEAD-SET   set by a launcher but zero reads anywhere -> unwired config.
  SET-ONLY   set and only ever mentioned in launcher/.env files (no app read).
  READ-ONLY  read explicitly but no launcher/.env sets it (may be user-supplied;
             reported for review, not automatically a bug).
Exit 1 if DEAD-SET is non-empty.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", ".next", "build", "__pycache__",
             "coverage", ".pytest_cache", ".mypy_cache", "sandbox", "logs", "site"}
SOURCE_SUFFIXES = {".py", ".ps1", ".sh", ".js", ".mjs", ".cjs", ".ts", ".tsx",
                   ".yaml", ".yml", ".toml", ".json", ".md", ".example", ".env", ".cfg", ".ini"}
NAME = r"[A-Z][A-Z0-9_]{2,}"

# (regex, group, kind, suffixes) — kind: "set" | "read"; suffixes=None scans
# every source file, otherwise only those extensions (the indented-key set
# pattern is JS spawn-env specific — bare Python/TS dict syntax would match it,
# and shell/Makefile consumption is `$VAR`/`$(VAR)`, which plain patterns miss).
PATTERNS: list[tuple[re.Pattern[str], int, str, frozenset[str] | None]] = [
    (re.compile(rf"(?m)^\s*export\s+({NAME})="), 1, "set", None),
    (re.compile(rf"(?m)^\s*\$env:({NAME})\s*="), 1, "set", None),
    (re.compile(rf"(?m)^ENV\s+({NAME})="), 1, "set", None),
    (re.compile(rf"(?m)^ARG\s+({NAME})(?:\s|=|$)"), 1, "set", None),
    (re.compile(rf"(?m)^({NAME})="), 1, "set", None),  # .env keys / dotenv contract
    (re.compile(rf"^\s+({NAME}):\s", re.M), 1, "set", frozenset({".js", ".mjs", ".cjs"})),
    (re.compile(r"os\.environ(?:\.get)?\(\s*f?[\"']([A-Z0-9_]+)"), 1, "read", None),
    (re.compile(r"os\.getenv\(\s*f?[\"']([A-Z0-9_]+)"), 1, "read", None),
    (re.compile(r"os\.environ\[\s*[\"']([A-Z0-9_]+)"), 1, "read", None),
    (re.compile(r"environ(?:\.get)?\(\s*[\"']([A-Z0-9_]+)"), 1, "read", None),
    (re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"), 1, "read", None),
    (re.compile(r"process\.env\[\s*[\"']([A-Z0-9_]+)"), 1, "read", None),
    (re.compile(r"\$env:([A-Z][A-Z0-9_]*)"), 1, "read", None),  # PS usage (set lines also read as usage)
    (re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-[^}]*)?\}"), 1, "read", None),  # shell ${X:-...}
    (re.compile(r"\$(?:\{|\()?([A-Z][A-Z0-9_]{2,})"), 1, "read",
     frozenset({".sh", ".ps1", ""})),  # shell $X / ${X} / $(VAR) incl. Makefile
    (re.compile(r"\benv\.get\(\s*[\"']([A-Z0-9_]+)"), 1, "read", None),
    # ^ wizard-style env.get("NAME") on a plain mapping (setup_wizard/noninteractive).
]

FAMILIES = ("AGENT_WORKSPACE_", "ALPHA_")


def iter_files():
    # Prune during traversal: filtering after rglob still enumerates every
    # file inside .venv/node_modules, which alone blows the time budget.
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() not in SOURCE_SUFFIXES and name not in {
                "Dockerfile", ".env", ".env.example", ".env.production.example",
            } and not name.startswith(".env"):
                continue
            try:
                if p.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            yield p


def main() -> int:
    set_by: dict[str, list[str]] = {}
    read_by: dict[str, list[str]] = {}
    mentioned: dict[str, int] = {}
    for f in iter_files():
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(ROOT).as_posix()
        # Electron env-object keys are too generic outside main.js.
        for rx, grp, kind, suffixes in PATTERNS:
            if suffixes is not None and f.suffix.lower() not in suffixes:
                continue
            for m in rx.finditer(text):
                name = m.group(grp)
                if not name.endswith("_") and "_" not in name:
                    continue
                bucket = set_by if kind == "set" else read_by
                bucket.setdefault(name, [])
                if rel not in bucket[name]:
                    bucket[name].append(rel)
        for fam in FAMILIES:
            for name in set(re.findall(rf"(?:{'|'.join(FAMILIES)})[A-Z0-9_]+", text)):
                mentioned[name] = mentioned.get(name, 0) + 1

    fam_names = sorted(
        n for n in set(set_by) | set(read_by) if n.startswith(FAMILIES)
    )
    dead_set: list[str] = []
    set_only: list[str] = []
    read_only: list[str] = []
    for n in fam_names:
        s, r = set_by.get(n, []), read_by.get(n, [])
        if s and not r:
            if mentioned.get(n, 0) > len(s):  # appears beyond its set sites
                set_only.append(n)
            else:
                dead_set.append(n)
        elif r and not s:
            read_only.append(n)

    print(f"rename-family variables: {len(fam_names)} "
          f"(set={len(set_by)}, read={len(read_by)})")
    print("-" * 72)
    for n in dead_set:
        print(f"  DEAD-SET  {n}  set in {set_by[n]}, zero reads")
    for n in set_only:
        print(f"  SET-ONLY  {n}  set in {set_by[n]} (no explicit read pattern)")
    for n in read_only:
        print(f"  READ-ONLY {n}  read in {read_by[n]}, no launcher/.env set")
    print("-" * 72)
    print(f"DEAD-SET: {len(dead_set)} | SET-ONLY: {len(set_only)} | READ-ONLY: {len(read_only)}")
    return 1 if dead_set else 0


if __name__ == "__main__":
    raise SystemExit(main())
