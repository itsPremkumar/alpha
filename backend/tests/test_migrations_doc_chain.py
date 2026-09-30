"""The migration guide's documented chain must be the real one.

`persistence/migrations/AGENTS.md` lists the rolling-forward chain and names a
"current head". Both were stale at once: the listing skipped
`0023_run_events_fts` (whose child `0024_feedback_category` was listed as if it
were a direct child of `0022_scheduled_occurrence_seq`) and stopped at
`0025_run_recovery_index (current head)` while `0026_network_waits` and
`0027_side_effect_ledger` already existed in `versions/`.

A trailing head in a guide is worse than no guide: it sends the reader to a
revision that is neither the newest nor, once `0023` is skipped, even a valid
successor of the one before it. This test reads the revision files as the
authority and checks the prose against them, so the next `make migrate-rev`
cannot drift it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "persistence" / "migrations"
VERSIONS_DIR = MIGRATIONS_DIR / "versions"
GUIDE = MIGRATIONS_DIR / "AGENTS.md"

_REVISION_RE = re.compile(r"(?m)^\s*revision\s*(?::[^=]+)?=\s*[\"']([^\"']+)[\"']")
_DOWN_RE = re.compile(r"(?m)^\s*down_revision\s*(?::[^=]+)?=\s*[\"']([^\"']+)[\"']")


def _load_chain() -> dict[str, str | None]:
    """Map every revision id to its declared parent (`None` at a root)."""
    chain: dict[str, str | None] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision = _REVISION_RE.search(text)
        if revision is None:
            pytest.fail(f"{path.name} declares no `revision`")
        chain[revision.group(1)] = _DOWN_RE.search(text).group(1) if _DOWN_RE.search(text) else None
    return chain


def _documented_chain() -> list[str]:
    """The backticked ids inside the guide's rolling-forward paragraph."""
    guide = GUIDE.read_text(encoding="utf-8")
    marker = "**Rolling forward compatibility**"
    assert marker in guide, "the rolling-forward paragraph moved; update this test's marker"
    paragraph = guide[guide.index(marker) :]
    paragraph = paragraph[: paragraph.index("\n\n")] if "\n\n" in paragraph else paragraph
    return re.findall(r"`(\d{4}_[a-z0-9_]+)`", paragraph)


def test_every_documented_revision_exists() -> None:
    chain = _load_chain()
    documented = _documented_chain()
    assert documented, "no chain found in the guide"
    missing = [revision for revision in documented if revision not in chain]
    assert not missing, f"the guide documents revisions that do not exist: {missing}"


def test_documented_chain_follows_the_real_down_revision_links() -> None:
    chain = _load_chain()
    documented = _documented_chain()
    for parent, child in zip(documented, documented[1:], strict=False):
        actual = chain[child]
        assert actual == parent, f"the guide says {parent} -> {child}, but {child}.py declares down_revision={actual!r}"


def test_documented_chain_ends_at_a_real_head() -> None:
    chain = _load_chain()
    documented = _documented_chain()
    parents = {parent for parent in chain.values() if parent is not None}
    heads = sorted(revision for revision in chain if revision not in parents)
    assert heads, "no head found in versions/"
    assert documented[-1] in heads, f"the guide ends at {documented[-1]}, which is not an alembic head; heads are {heads}"
    # The head named in the prose is what a reader acts on.
    guide = GUIDE.read_text(encoding="utf-8")
    paragraph = guide[guide.index("**Rolling forward compatibility**") :]
    paragraph = paragraph[: paragraph.index("\n\n")] if "\n\n" in paragraph else paragraph
    named = re.search(r"`(\d{4}_[a-z0-9_]+)` \(current head\)", paragraph)
    assert named is not None, "the guide no longer names a current head"
    assert named.group(1) in heads, f"the guide names {named.group(1)} as the current head; the revision files say {heads}"
