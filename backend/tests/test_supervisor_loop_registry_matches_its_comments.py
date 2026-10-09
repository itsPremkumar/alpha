"""The supervisor's loop registry and its comments must agree.

Phase 0 of `docs/FEATURE_COMPLETION_PLAN.md`, task 0.T3. `supervisor.py:196`
said "all eight loops pass through" while `register_default_loops()` registered
**nine** (`supervisor.py:126-142`). Nothing failed, because a comment asserting a
count is not a claim anything checks — the same defect class as the documented
capability counts, one file over.

The check below is deliberately structural: it parses the actual registration
call and counts the loop ids, so it fails the day a loop is added or removed. A
comment that says "nine" and a registry that holds eight would still pass a
literal string test; this does not.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "app" / "gateway" / "autonomy" / "supervisor.py"


def _registered_loop_ids() -> set[str]:
    """The loop ids `register_default_loops` actually registers.

    Parsed from the `defaults` tuple of ``(loop_id, description, tick, interval)``
    rows, which is the shape the function uses. Structural on purpose: the day a
    loop is added or removed, this number moves without anyone editing a string.
    """
    tree = ast.parse(SUPERVISOR.read_text(encoding="utf-8"))
    for func in ast.walk(tree):
        if not (isinstance(func, ast.FunctionDef) and func.name == "register_default_loops"):
            continue
        for stmt in ast.walk(func):
            if not (isinstance(stmt, ast.Assign) and any(getattr(t, "id", None) == "defaults" for t in stmt.targets)):
                continue
            if not isinstance(stmt.value, (ast.Tuple, ast.List)):
                continue
            ids: set[str] = set()
            for row in stmt.value.elts:
                if isinstance(row, (ast.Tuple, ast.List)) and row.elts:
                    head = row.elts[0]
                    if isinstance(head, ast.Constant) and isinstance(head.value, str):
                        ids.add(head.value)
            return ids
    raise AssertionError("the `defaults` tuple in register_default_loops was not found in supervisor.py")


def test_register_default_loops_is_found_and_non_empty() -> None:
    ids = _registered_loop_ids()
    assert len(ids) == 10, f"expected the ten documented loops, parsed {sorted(ids)}"


def test_the_registry_agrees_with_the_generated_manifest() -> None:
    """The two independent inventories of the same set of loops must match.

    `contracts/feature_manifest.json` counts them from the wiring point; this file
    counts them from the registration tuple. Neither is derived from the other, so
    agreement is real evidence rather than a tautology.
    """
    import json

    manifest = json.loads((ROOT.parent / "contracts" / "feature_manifest.json").read_text(encoding="utf-8"))
    manifest_ids = {str(entry.get("id")) for entry in manifest.get("loops", [])}
    registry_ids = _registered_loop_ids()
    assert len(manifest_ids) == len(registry_ids), (
        f"the manifest lists {len(manifest_ids)} loops and register_default_loops registers {len(registry_ids)}: {sorted(manifest_ids)} vs {sorted(registry_ids)}. Regenerate with backend/scripts/generate_feature_manifest.py."
    )


def test_no_comment_in_the_supervisor_asserts_a_loop_count() -> None:
    """A numeral beside the word 'loops' in a comment is an unchecked claim.

    The comment that caused this is gone; the guard is here so the next person who
    writes "all nine loops" gets a failing test instead of a number that quietly
    becomes false.
    """
    source = SUPERVISOR.read_text(encoding="utf-8")
    offenders: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        if re.search(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+loops?\b", stripped, re.I):
            offenders.append(stripped)
    assert not offenders, "a supervisor comment counts the loops, which nothing verifies. Name the registry function instead: " + "; ".join(offenders)
