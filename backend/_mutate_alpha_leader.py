"""Mutation harness: prove the alpha-leader tests BITE.

Reverting a fix in-place has already left a source file reverted in this
environment, so this script never touches the working tree. For each mutation it

1. copies the whole ``backend`` python tree to a scratch directory
   (``shutil.copytree`` with REAL BYTES - never ``os.link``; see
   ``_assert_not_hardlinked``),
2. applies a textual revert to ONE file inside the COPY,
3. runs the alpha-leader test file against the copy with that copy first on
   ``sys.path``,
4. reports whether the expected test FAILED (which is the point: a mutation
   that leaves the suite green is a test that does not bite).

An earlier version of this file used hard links and wrote mutations THROUGH the
link into the live source tree, which silently turned the default-on leader off
in ``registry.py``. That is why ``build_copy`` copies bytes and
``_assert_not_hardlinked`` refuses to proceed if an inode is ever shared.

Usage:  python _mutate_alpha_leader.py [index ...]
        (optional positional args select mutations by 0-based index)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"C:\Users\PREM KUMAR\Videos\alpha")
BACKEND = REPO / "backend"
HARNESS = BACKEND / "packages" / "harness"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
TESTS = ["tests/test_alpha_leader_capability_dispatch.py"]

# (label, relative file, pattern, replacement, expected-failing test selector)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "P1 default-off: stop seeding the `alpha` leader on a fresh roster",
        "packages/harness/alpha/bots/registry.py",
        r"\n        # The leader is installed on EVERY construction.*?\n        ensure_alpha_leader\(self\)",
        "\n        pass",
        "test_fresh_install_has_alpha_leader_with_no_configuration",
    ),
    (
        "P2 dispatch ignores capability_tags (selection by any live agent)",
        "packages/harness/alpha/bots/capability_dispatch.py",
        r"        eligibility = eligible_candidates\(profiles, required, exclude=list\(exclude or \[\]\)\)",
        "        eligibility = eligible_candidates(profiles, None, exclude=list(exclude or []))",
        "test_dispatch_routes_to_the_capability_match_and_never_to_a_mismatch",
    ),
    (
        "P2 dispatch stops consulting match_bot_for_task",
        "packages/harness/alpha/bots/capability_dispatch.py",
        r"from alpha\.bots\.work_discovery import match_bot_for_task",
        "def _unused_match(*a, **k):\n    return []\n\n\nmatch_bot_for_task = _unused_match",
        "test_dispatch_wired_match_bot_for_task_for_real",
    ),
    (
        "P2 dispatch stops conducting the contract-net auction",
        "packages/harness/alpha/bots/capability_dispatch.py",
        r"        award = engine\.conduct_auction\(announcement\)",
        "        from alpha.swarm.cnp_auction import ContractAward\n\n        award = ContractAward(announcement.task_id, workers[0].agent_id if workers else '', 1.0, lease_acquired=True)",
        "test_dispatch_wired_the_contract_net_auction_for_real",
    ),
    (
        "P2/P3 depth ceiling removed",
        "packages/harness/alpha/bots/delegation.py",
        r"        if self\.depth >= self\.limits\.max_depth:",
        "        if False:",
        "test_depth_ceiling_stops_a_runaway_tree",
    ),
    (
        "P3 hop ceiling removed",
        "packages/harness/alpha/bots/delegation.py",
        r"        if self\.hops >= self\.limits\.max_hops:",
        "        if False:",
        "test_hop_ceiling_stops_an_endless_reassignment_chain",
    ),
    (
        "P3 fan-out ceiling removed",
        "packages/harness/alpha/bots/delegation.py",
        r"        if self\.fanout_used >= self\.limits\.max_fanout:",
        "        if False:",
        "test_fanout_ceiling_stops_a_runaway_fan_out",
    ),
    (
        "P3 cycle guard removed (back-edge allowed)",
        "packages/harness/alpha/bots/delegation.py",
        r"        if key and key in \{item\.lower\(\) for item in self\.lineage\}:",
        "        if False:",
        "cycle_guard",
    ),
    (
        "P3 token budget no longer inherited downward",
        "packages/harness/alpha/bots/delegation.py",
        r"        inherited_tokens = max\(0, min\(inherited_tokens, self\.tokens_remaining\)\)",
        "        inherited_tokens = self.token_budget",
        "test_a_child_can_never_receive_more_than_its_parent_had_left",
    ),
    (
        "P3/P4 one auction engine shared across dispatches (stale workers keep bidding)",
        "packages/harness/alpha/bots/capability_dispatch.py",
        r"        engine = self\._engine_factory\(\)\n        self\._engine = engine",
        "        engine = self._engine_factory()\n        if self._engine is not None:\n            engine = self._engine\n        self._engine = engine",
        "test_a_capability_mismatch_reassigns_to_a_different_capable_agent",
    ),
    (
        "P1 leader gets allow_all tools (superuser bypass)",
        "packages/harness/alpha/bots/permissions.py",
        r'    "Autonomous Leader & Capability Dispatch Director": RolePermissionRing\(\n        role_name="Autonomous Leader & Capability Dispatch Director",\n        allowed_tools=\{[^}]*\},',
        '    "Autonomous Leader & Capability Dispatch Director": RolePermissionRing(\n        role_name="Autonomous Leader & Capability Dispatch Director",\n        allow_all=True,',
        "test_leader_role_does_not_resolve_to_an_allow_all_ring",
    ),
    (
        "P1 leader authority boundary replaced by an allow-everything bypass",
        "packages/harness/alpha/bots/capability_dispatch.py",
        r"            allowed, why, _blocking = leader_may_direct\(sorted\(required\)\)",
        "            allowed, why, _blocking = (True, 'bypass', ())",
        "test_leader_authority_refuses_a_dispatch_outside_the_allowlist",
    ),
]


def build_copy(dest: Path) -> Path:
    """Clone the python tree so the real working tree is never mutated."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("packages", "app", "tests"):
        src = BACKEND / name
        if src.exists():
            shutil.copytree(src, dest / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("sitecustomize.py", "pyproject.toml"):
        src = BACKEND / name
        if src.exists():
            shutil.copy2(src, dest / name)
    return dest


def _assert_not_hardlinked(copy: Path, rel: str) -> None:
    """Fail loudly if the copy shares an inode with the live source file."""
    live = BACKEND / rel
    copied = copy / rel
    if live.stat().st_ino == copied.stat().st_ino and live.stat().st_dev == copied.stat().st_dev:
        raise RuntimeError(f"copy of {rel} is a hard link to the live source file; refusing to mutate it")


def run_mutation(label: str, rel: str, pattern: str, replacement: str, expected: str) -> tuple[bool, str]:
    scratch = Path(tempfile.mkdtemp(prefix="mut_alpha_"))
    try:
        copy = build_copy(scratch / "tree")
        target = copy / rel
        _assert_not_hardlinked(copy, rel)
        original = target.read_text(encoding="utf-8")
        mutated, count = re.subn(pattern, replacement, original, count=1, flags=re.DOTALL)
        if count != 1:
            return False, f"MUTATION DID NOT APPLY ({count} matches) - test result is meaningless"
        target.write_text(mutated, encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONPATH"] = f"{copy / 'packages' / 'harness'}{os.pathsep}{copy}{os.pathsep}{env.get('PYTHONPATH', '')}"
        env["AGENT_WORKSPACE_HOME"] = str(scratch / "home")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [str(PY), "-m", "pytest", *TESTS, "-q", "-p", "no:randomly", "--no-header", "-x", "-k", expected],
            cwd=str(copy),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        tail = (proc.stdout or "")[-1500:]
        failed = proc.returncode != 0
        verdict = "BITES (test failed as required)" if failed else "*** DID NOT BITE - test still passed ***"
        return True, f"{verdict}\n--- {label}\n{tail}"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    # Optional positional args select a subset by index (0-based), so a long
    # run can be split into chunks without holding one very long process.
    wanted = {int(a) for a in sys.argv[1:] if a.isdigit()}
    print(f"copy source: {HARNESS}")
    failures = 0
    total = 0
    for index, (label, rel, pattern, replacement, expected) in enumerate(MUTATIONS):
        if wanted and index not in wanted:
            continue
        total += 1
        ok, output = run_mutation(label, rel, pattern, replacement, expected)
        print("=" * 100)
        print(f"### mutation #{index}: {label}")
        if not ok:
            failures += 1
            print(output)
            continue
        if "DID NOT BITE" in output:
            failures += 1
        print(output)
        sys.stdout.flush()
    print("=" * 100)
    print(f"{total - failures}/{total} mutations produced the expected test failure")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
