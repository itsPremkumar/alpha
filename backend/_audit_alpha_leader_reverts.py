"""Audit: prove no mutation replacement leaked into the live working tree.

An earlier run of ``_mutate_alpha_leader.py`` hard-linked its scratch copy and
therefore wrote several mutations THROUGH the link into the real source files
(one of them, a ``pass`` in place of ``ensure_alpha_leader(self)``, silently
turned the default-on leader off in ``registry.py``). This script is the standing
guard: it greps the live tree for every replacement string the harness can inject
and fails if any of them is present.

Scope is deliberately limited to the files this task owns. Other agents are
actively editing the rest of the tree in parallel, and a forbidden fragment that
legitimately appears in one of THEIR files (a legitimate ``if False:`` in an
unrelated test, say) is not evidence of anything about this change.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mutate_alpha_leader import BACKEND, MUTATIONS  # noqa: E402

#: Only the files this task owns.
SCANNED = (
    "packages/harness/alpha/bots/capability_dispatch.py",
    "packages/harness/alpha/bots/delegation.py",
    "packages/harness/alpha/bots/alpha_leader.py",
    "packages/harness/alpha/bots/reassignment.py",
    "packages/harness/alpha/bots/registry.py",
    "packages/harness/alpha/bots/permissions.py",
    "packages/harness/alpha/capabilities/eligibility.py",
    "packages/harness/alpha/capabilities/__init__.py",
    "packages/harness/alpha/agents/lead_agent/prompt.py",
    "packages/harness/alpha/planning/bridge.py",
    "tests/test_alpha_leader_capability_dispatch.py",
)

#: Replacement fragments that must never appear in the live tree.
FORBIDDEN = (
    "if False:",
    "_shared_engine",
    "_unused_match",
    "(True, 'bypass', ())",
    "eligible_candidates(profiles, None,",
    "inherited_tokens = self.token_budget",
    "ContractAward(announcement.task_id",
)


def main() -> int:
    hits: list[str] = []
    for rel in SCANNED:
        path = BACKEND / rel
        if not path.exists():
            hits.append(f"{rel}: MISSING")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for fragment in FORBIDDEN:
            if fragment in text:
                hits.append(f"{rel}: contains {fragment!r}")
    for _label, rel, _pattern, replacement, _expected in MUTATIONS:
        if rel not in SCANNED:
            continue
        needle = replacement.strip().splitlines()[-1].strip()
        if len(needle) < 12 or needle == "self._engine = engine":
            continue
        path = BACKEND / rel
        if not path.exists():
            continue
        if needle in path.read_text(encoding="utf-8", errors="replace"):
            hits.append(f"{rel}: last line of mutation replacement {needle!r} is present")

    if hits:
        print("LEAKED MUTATION(S) FOUND IN THE LIVE TREE:")
        for hit in hits:
            print("  -", hit)
        return 1
    print(f"clean: {len(SCANNED)} owned files scanned, none contains any of {len(FORBIDDEN)} forbidden fragments or any mutation replacement")
    return 0


if __name__ == "__main__":
    sys.exit(main())
