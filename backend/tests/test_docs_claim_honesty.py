"""Guards against retired marketing claims and dangling documentation links.

Two documentation defects were found by an end-to-end review of ``main`` and both
escaped every existing gate:

1. **Retired wording came back / never fully left.** ``README.md`` still said
   agents "plan, execute, and verify long-horizon tasks" and that the trajectory
   recorder "cryptographically logs" steps, while ``AGENTS.md``'s honesty
   contract says a completed run is never "verified" and the lineage store holds
   local SHA-256 digests rather than an attestation. A follow-up commit claimed
   the headline had been reconciled, but it had only rewritten one of the four
   surfaces — ``llms-full.txt``, ``docs/ARCHITECTURE.md``, ``docs/FAQ.md``, and
   ``docs/GLOSSARY.md`` kept the old claim. Claim edits are made on several
   files at once, so the guard scans every user-facing surface together.
2. **``docs/INDEX.md`` linked a document that does not exist.** The index is
   generated and there is a drift gate, but the gate compares generated output
   to the committed file — it does not check that the entries point at real
   files, so a document deleted outside the generator left a dead link behind.

Why phrases at all, when ``test_documented_claims.py`` prefers predicates
--------------------------------------------------------------------------
``test_documented_claims.py`` explains at length why asserting on prose is
brittle, and that reasoning holds. These claims have no predicate over code:
"cryptographic" is a property of a sentence, not of a module. The honest
alternative is a *closed, known-false* phrase list over the surfaces where a
reader meets the claim first — never a list of "must sound like" phrases.
Re-wording the surrounding prose freely still passes; reintroducing the exact
retired wording fails, which is the drift that actually happened.

The list is deliberately excluded from ``END_TO_END_CRITIQUE.md``: that
document quotes the retired wording in order to report that it was retired, and
a guard that cannot tell a quotation from a claim would push reviewers to erase
the record instead of fixing the code.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_docs_index_generator() -> ModuleType:
    """Load `scripts/generate_docs_index.py` as the authority on what counts.

    Imported by path because it is a repository script, not an installed module.
    The `sys.modules` registration is required: `DocumentSpec` is a dataclass and
    dataclasses resolves annotations through `sys.modules[cls.__module__]`, so
    without it the decorator raises AttributeError.
    """
    path = ROOT / "scripts" / "generate_docs_index.py"
    spec = importlib.util.spec_from_file_location("alpha_docs_index_generator", path)
    assert spec is not None and spec.loader is not None, "cannot load scripts/generate_docs_index.py"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

# Pure text assertions over committed documents: no Gateway, no auto-created
# operator, and nothing under `alpha.*` imported.
pytestmark = pytest.mark.no_auto_user

# Historical documents that quote retired wording on purpose.
QUOTATION_ALLOWED = {"END_TO_END_CRITIQUE.md"}

# (phrase, what the honest wording is instead)
RETIRED_CLAIMS: tuple[tuple[str, str], ...] = (
    (
        "plan, execute, and verify",
        "README/llms say agents 'plan and execute' and 'report honestly when a result is unverified'; 'verified' is reserved for evidence-backed verification (AGENTS.md honesty contract).",
    ),
    (
        "cryptographically logs",
        "the trajectory flight recorder writes a local SQLite audit store with JSONL export; it does not cryptographically sign anything.",
    ),
    (
        "cryptographic trajectory",
        "same as above: 'trajectory flight recorder (local SQLite audit store with JSONL export)'.",
    ),
    (
        "cryptographic provenance",
        "lineage stores SHA-256 content digests: 'hash-linked provenance ... stored locally; not a cryptographic attestation'.",
    ),
    (
        "end-to-end cryptographic",
        "see the two claims above; SHA-256 digests are not an end-to-end cryptographic guarantee.",
    ),
)


def _surfaces() -> list[Path]:
    paths: list[Path] = []
    for name in ("README.md", "llms.txt", "llms-full.txt"):
        path = ROOT / name
        if path.is_file():
            paths.append(path)
    docs = ROOT / "docs"
    if docs.is_dir():
        paths.extend(sorted(docs.rglob("*.md")))
        paths.extend(sorted(docs.rglob("*.txt")))
    return paths


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace so Markdown hard-wrapping cannot hide a claim."""
    return re.sub(r"\s+", " ", text).lower()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_retired_claims_do_not_appear_on_user_facing_surfaces() -> None:
    hits: list[str] = []
    for path in _surfaces():
        if path.name in QUOTATION_ALLOWED:
            continue
        haystack = _normalize(_read(path))
        for phrase, remedy in RETIRED_CLAIMS:
            if phrase in haystack:
                hits.append(f"{path.relative_to(ROOT)}: retired claim {phrase!r} — {remedy}")
    assert not hits, "Retired marketing claims found:\n" + "\n".join(hits)


def test_docs_index_links_resolve_to_real_files() -> None:
    index = ROOT / "docs" / "INDEX.md"
    assert index.is_file(), "docs/INDEX.md is missing; run scripts/generate_docs_index.py"
    text = _read(index)
    missing: list[str] = []
    for target in re.findall(r"\]\(([^)#]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        candidate = (index.parent / target.split("#", 1)[0]).resolve()
        if not candidate.exists():
            missing.append(target)
    assert not missing, "docs/INDEX.md links documents that do not exist (regenerate with scripts/generate_docs_index.py): " + ", ".join(missing)


def test_index_covers_every_document_in_docs() -> None:
    """The fail-closed index must not silently drop a document from the tree.

    "Document" is whatever `scripts/generate_docs_index.py` says it is, loaded
    from the generator itself. This test previously hardcoded its own walk and so
    disagreed with the tool it audits: the generator correctly skipped the
    `docs/.tmp/*.tmp.md` working documents by name pattern and this test reported
    that as a dropped document. Importing the authority keeps the invariant (a
    real document is never silently dropped) and removes the second opinion.
    """
    index = ROOT / "docs" / "INDEX.md"
    if not index.is_file():
        pytest.skip("docs/INDEX.md is missing")
    generator = _load_docs_index_generator()
    listed = set(re.findall(r"\]\(([^)#]+\.md)\)", _read(index)))
    listed = {item for item in listed if not item.startswith(("http://", "https://"))}

    docs_root = ROOT / "docs"
    on_disk: set[str] = set()
    for path in docs_root.rglob("*.md"):
        if path.name in generator.SKIP_FILE_NAMES:
            continue
        if any(fnmatchcase(path.name, pattern) for pattern in generator.SKIP_FILE_PATTERNS):
            continue
        if any(part in generator.SKIP_DIRECTORY_NAMES for part in path.relative_to(docs_root).parts[:-1]):
            continue
        on_disk.add(str(path.relative_to(docs_root)))
    # The generator may route some documents through FILE_OVERRIDES with a
    # different relative spelling; only report files that are not mentioned at
    # all by their basename.
    listed_names = {Path(item).name for item in listed}
    dropped = sorted(name for name in on_disk if Path(name).name not in listed_names)
    assert not dropped, "Documents present under docs/ but absent from docs/INDEX.md (run scripts/generate_docs_index.py): " + ", ".join(dropped)
