"""Documented-claim guards: two claims that were false, kept false no more.

This repository's contribution rules make claim honesty mandatory, and
``docs/PRODUCTION_READINESS_INVENTORY.md`` is meant to be the authority on what is
implemented. A full source audit found the honesty boundary had drifted in the
embarrassing direction: it *under*-credited two subsystems while the real gap was
narrower and more interesting than the text said.

    runtime/AGENTS.md claimed the side-effect ledger "has no SQL repository yet".
    It has one -- alpha.persistence.side_effects.SqlSideEffectLedger, migration
    0027_side_effect_ledger, covered by tests/test_side_effect_ledger_sql.py.
    Nothing in production constructs it.

    runtime/AGENTS.md claimed a parked session's state "is re-derived rather than
    kept in a dedicated durable registry". It is kept in one --
    runtime/network/wait_registry.py's NetworkWaitService over
    alpha.persistence.network_waits.NetworkWaitRepository, migration
    0026_network_waits, wired at app/gateway/deps.py.

Two things are wrong with those sentences, and only one of them is the fact:

1. The fact was wrong, and is fixed in ``runtime/AGENTS.md`` and
   ``docs/architecture/durable-runtime.md``.
2. **Nothing caught it.** Two independent audits reported the same file
   contradicting *itself* -- lines 41-42 correctly placed the SQL implementation in
   ``alpha.persistence.side_effects``, while lines 57-58 denied it existed.

The fact being fixed is worth one commit. What makes it not happen again is this
file.

How the guard works, and why it is not a brittle mirror of the prose
----------------------------------------------------------------------
It is tempting to assert on document *sentences*. That test would break when
anyone rewords a paragraph, which is exactly the wrong pressure: it trains
reviewers to change the test instead of the truth, and it makes the honest
document harder to read because its prose becomes load-bearing.

So this file's primary guard deliberately does not read prose. It reads a
**machine-readable claim block** -- the ``<!-- honesty-claims ... -->`` comment in
``runtime/AGENTS.md`` -- which is a declaration of *state*, not of wording:

    <!-- honesty-claims
    side_effect_ledger_sql_repository: exists
    side_effect_ledger_production_writer: absent
    ...
    -->

Every key maps to a predicate over the actual code below. The declared value and
the code are compared, so the test bites in both directions:

* A developer wires ``SqlSideEffectLedger`` into a production path and forgets the
  document. ``declared == "absent"``, code says it is wired -> **fails**, naming
  the key and asking for the doc to be upgraded.
* A developer "fixes" the document to claim a capability that was never built.
  ``declared == "exists"``, code says it is not there -> **fails**.

The surrounding paragraphs can be rewritten freely, because nothing here depends
on them.

The secondary guard pins the two specific *false sentences* as never-allowed, so
the exact drift two audits found cannot come back even before anyone touches the
claim block. That part *is* a phrase check, and phrase checks against Markdown are
fragile in ways that were discovered the hard way rather than assumed: injecting
the real false sentences initially left this guard **passing**, because Markdown
hard-wraps sentences across lines and because the same sentence is capitalised
differently in different positions. `_normalized_prose` fixes both. It does not
strip Markdown emphasis, so a phrase wrapped in bold mid-sentence would still slip
past -- a known limitation, not a solved one.

This is precisely why the claim block is the primary guard and the phrase list is
only a backstop: the block has no such fragility, because it contains no prose.
The phrase list is a closed set of *known-false* strings; it does not catch a new
false claim, and no test over document text can.

If a claim is genuinely not expressible as a predicate over the code, the honest
move is to leave it out of the block and say so in ``runtime/AGENTS.md``, not to
write a predicate that trivially passes.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
RUNTIME_AGENTS = BACKEND / "packages" / "harness" / "alpha" / "runtime" / "AGENTS.md"

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".agent", ".pytest_cache", ".ruff_cache", "htmlcov", ".alpha"}

# Every module that is allowed to *construct* a SqlSideEffectLedger. Deliberately
# narrow: an entry here means the ledger has a production writer, which is a
# documented capability change and must be reflected in runtime/AGENTS.md.
#
# Keep it narrow rather than keeping it convenient. A broad allowlist here would
# recreate the exact failure this file exists to prevent -- a document and a test
# that agree with each other while both disagree with the code.
PRODUCTION_LEDGER_WRITER_ALLOWLIST: dict[str, str] = {
    # (empty on purpose -- see above)
}

# Keys the claim block may declare, and the predicate that decides each one.
# Every key in the block must appear here; every key here must appear in the
# block. A new claim is therefore a one-line edit to two places, both reviewed.
#
# "exists"/"absent" are the only legal values. They are opposites on purpose: a
# claim has to be falsifiable from one direction, and allowing a hedged value
# ("partial", "planned") is how a document drifts back into vagueness.


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _mentions(path: Path, needle: str) -> bool:
    """True when the module text contains ``needle``.

    Deliberately a text search, not an AST walk. An AST walk over every module in
    ``backend/packages/`` made this guard take four minutes, which is a guard
    nobody runs before pushing -- and a guard that is slow enough to skip has
    already failed at its only job.

    A text search over-matches rather than under-matches, and over-matching is the
    safe direction for a guard: it can report a writer that is only mentioned in a
    comment, which a reviewer resolves in seconds, but it cannot miss a real one.
    The alternative -- an allowlist entry per over-match -- would just move the
    slowness into maintenance.
    """
    try:
        return needle in path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - unreadable file
        return False


# -- the predicates ----------------------------------------------------------


def _side_effect_ledger_sql_repository_exists() -> bool:
    """The durable ledger implementation exists in the tree."""
    module = BACKEND / "packages" / "harness" / "alpha" / "persistence" / "side_effects" / "sql.py"
    return module.is_file() and "class SqlSideEffectLedger" in module.read_text(encoding="utf-8")


def _side_effect_ledger_has_production_writer() -> bool:
    """True when something outside tests constructs a SqlSideEffectLedger.

    This is the real gap the old text missed. Note the polarity: every predicate in
    this file answers "does the named subject exist?", never "does the gap hold?".
    A predicate phrased as a gap is easy to invert by accident, and an inverted
    guard is worse than no guard -- it would fail while the repository is honest.
    """
    allowed = {p.resolve() for p in PRODUCTION_LEDGER_WRITER_ALLOWLIST}
    for root in (BACKEND / "app", BACKEND / "packages"):
        for path in _iter_python_files(root):
            if path.resolve() in allowed:
                continue
            # The implementation, its re-export, and its ORM row legitimately name
            # the class. Everything else naming it is a candidate writer.
            if path.parent.name == "side_effects":
                continue
            if _mentions(path, "SqlSideEffectLedger"):
                return True
    return False


def _parked_session_durable_registry_exists() -> bool:
    """Parked sessions are kept durably, not re-derived."""
    registry = BACKEND / "packages" / "harness" / "alpha" / "runtime" / "network" / "wait_registry.py"
    repository = BACKEND / "packages" / "harness" / "alpha" / "persistence" / "network_waits" / "sql.py"
    if not registry.is_file() or "class NetworkWaitService" not in registry.read_text(encoding="utf-8"):
        return False
    if not repository.is_file() or "class NetworkWaitRepository" not in repository.read_text(encoding="utf-8"):
        return False
    # ...and the Gateway actually installs it, so "durable" is not aspirational.
    deps = (BACKEND / "app" / "gateway" / "deps.py").read_text(encoding="utf-8")
    return "NetworkWaitRepository(" in deps and "set_network_wait_service(" in deps


def _parked_session_resume_launcher_exists() -> bool:
    """True when the Gateway installs a per-thread resume launcher for parks.

    Expected to be False, because a launcher here would be a second continuation
    authority bypassing the side-effect gate -- SafeRunRecoveryService owns
    resumption. If this ever returns True that is not a doc fix, it is a review.
    """
    deps = (BACKEND / "app" / "gateway" / "deps.py").read_text(encoding="utf-8")
    for line in deps.splitlines():
        if "NetworkWaitService(" not in line:
            continue
        call = line.split("NetworkWaitService(", 1)[1]
        # `wait_service = NetworkWaitService(wait_store, network_state=...)`:
        # one positional plus a known keyword. A third argument -- positional or
        # keyword -- would be the launcher.
        if "launcher" in call:
            return True
        if call.count(",") >= 2:
            return True
    return False


def _cross_process_exactly_once_available() -> bool:
    """True when side effects are announced durably, which is what exactly-once needs.

    Deliberately derived from the writer predicate rather than asserted
    independently: exactly-once would require every effect to be announced, so
    "no production writer" and "no cross-process exactly-once" are the same fact
    counted twice. Deriving it means the two claims cannot drift apart -- which is
    the failure mode this whole file was written after.
    """
    return _side_effect_ledger_has_production_writer()


def _supervisor_in_windows_launcher_exists() -> bool:
    """True when start.ps1 actually uses the process supervisor."""
    launcher = ROOT / "start.ps1"
    if not launcher.is_file():  # pragma: no cover - the launcher is a tracked file
        return False
    text = launcher.read_text(encoding="utf-8", errors="replace")
    return "alpha.runtime.supervisor" in text or "ProcessSupervisor" in text


PREDICATES = {
    "side_effect_ledger_sql_repository": (
        _side_effect_ledger_sql_repository_exists,
        "alpha.persistence.side_effects.SqlSideEffectLedger + migration 0027_side_effect_ledger",
    ),
    "side_effect_ledger_production_writer": (
        _side_effect_ledger_has_production_writer,
        "some module under backend/app/ or backend/packages/ constructs SqlSideEffectLedger outside the implementation",
    ),
    "parked_session_durable_registry": (
        _parked_session_durable_registry_exists,
        "NetworkWaitService over NetworkWaitRepository (migration 0026), installed at app/gateway/deps.py",
    ),
    "parked_session_resume_launcher": (
        _parked_session_resume_launcher_exists,
        "the Gateway constructs NetworkWaitService without a launcher; SafeRunRecoveryService owns resumption",
    ),
    "cross_process_exactly_once": (
        _cross_process_exactly_once_available,
        "derived from side_effect_ledger_production_writer: an unannounced effect cannot be deduplicated",
    ),
    "supervisor_in_windows_launcher": (
        _supervisor_in_windows_launcher_exists,
        "start.ps1 contains no reference to alpha.runtime.supervisor or ProcessSupervisor",
    ),
}

CLAIM_BLOCK = re.compile(r"<!--\s*honesty-claims\s*\n(?P<body>.*?)-->", re.DOTALL)
CLAIM_LINE = re.compile(r"^\s*([a-z0-9_]+)\s*:\s*([a-z]+)\s*$")

# The two sentences that were false, pinned so the exact drift cannot return.
# A closed list of known-false strings, deliberately not a template of the correct
# prose: it catches a regression to a claim already disproven, and it does not
# catch a *new* false claim. Catching new ones is what PREDICATES is for.
FORBIDDEN_CLAIMS: tuple[tuple[str, str, str], ...] = (
    (
        "the side-effect ledger has no SQL repository yet",
        RUNTIME_AGENTS,
        "False: alpha/persistence/side_effects/sql.py defines SqlSideEffectLedger, migration 0027_side_effect_ledger "
        "exists, and tests/test_side_effect_ledger_sql.py covers it. The accurate limitation is that no production "
        "module constructs it.",
    ),
    (
        "a session's parked-across-restart state is re-derived rather than kept in a dedicated durable registry",
        RUNTIME_AGENTS,
        "False: runtime/network/wait_registry.py's NetworkWaitService over persistence/network_waits/"
        "NetworkWaitRepository (migration 0026_network_waits) is wired at app/gateway/deps.py, so a park is durable. "
        "The real gap is that no resume launcher is installed.",
    ),
)


def _declared_claims() -> dict[str, str]:
    text = RUNTIME_AGENTS.read_text(encoding="utf-8")
    match = CLAIM_BLOCK.search(text)
    assert match is not None, (
        "runtime/AGENTS.md must carry a machine-readable `<!-- honesty-claims ... -->` block. It declares the state the guard below verifies against the code, so the honesty boundary cannot drift again without a failing test."
    )
    claims: dict[str, str] = {}
    for raw in match.group("body").splitlines():
        if not raw.strip():
            continue
        parsed = CLAIM_LINE.match(raw)
        assert parsed is not None, f"unparseable honesty-claims line: {raw!r}. Each line must be `key: exists` or `key: absent` with key in {sorted(PREDICATES)}."
        key, value = parsed.group(1), parsed.group(2)
        assert key not in claims, f"duplicate honesty claim key: {key!r}"
        claims[key] = value
    return claims


class TestHonestyClaimsMatchTheCode:
    """Every declared claim is checked against the code, in both directions."""

    def test_claim_block_covers_every_known_key(self) -> None:
        """A new predicate with no declaration is a claim nobody is checking."""
        declared = set(_declared_claims())
        assert declared == set(PREDICATES), f"claim block and PREDICATES disagree. missing from the block: {sorted(set(PREDICATES) - declared)}; declared but unverified: {sorted(declared - set(PREDICATES))}"

    def test_claim_values_are_exists_or_absent(self) -> None:
        """A hedged value cannot be falsified, so it is not a claim."""
        for key, value in sorted(_declared_claims().items()):
            assert value in {"exists", "absent"}, f"honesty claim {key!r} declares {value!r}; only 'exists' and 'absent' are allowed. A hedge like 'partial' or 'planned' is exactly how this boundary rotted."

    def test_each_claim_matches_the_code(self) -> None:
        declared = _declared_claims()
        failures: list[str] = []
        for key in sorted(PREDICATES):
            predicate, evidence = PREDICATES[key]
            actual = bool(predicate())
            wanted = declared[key] == "exists"
            if actual != wanted:
                failures.append(
                    f"\n  honesty claim {key!r}: document says {declared[key]!r}, code says "
                    f"{('exists' if actual else 'absent')!r}\n"
                    f"    predicate: {evidence}\n"
                    f"    fix: if the code is now right, update the claim block in "
                    f"{RUNTIME_AGENTS.relative_to(ROOT)} in this same commit. "
                    f"If the document is right and the code is not, that is a code defect -- "
                    f"do not weaken the document to match it."
                )
        assert not failures, "Documented honesty boundary disagrees with the code:\n" + "\n".join(failures)

    def test_the_ledger_claim_is_not_simply_inverted(self) -> None:
        """Guard against the lazy "fix": declaring the whole thing absent.

        The audit's real finding was that the old text collapsed two distinct
        states -- "no storage" and "no writer" -- into one denial. A future edit
        could pass the checks above by declaring
        ``side_effect_ledger_sql_repository: absent``, which would be a *different*
        false claim rather than a true one. So the pair is asserted explicitly.
        """
        declared = _declared_claims()
        assert declared["side_effect_ledger_sql_repository"] == "exists", "the durable ledger implementation exists and is tested; denying it is a new false claim"
        assert declared["side_effect_ledger_production_writer"] == "absent", "nothing in production constructs the ledger; claiming a writer is a new false claim"


def _normalized_prose(text: str) -> str:
    """Markdown prose flattened for phrase matching.

    A phrase check against raw document text is a trap, and this function exists
    because it was sprung during development. Two defects, both found by injecting
    the false claims and watching the guard pass:

    * **Line wrapping.** Markdown hard-wraps, so the real sentence is split across
      physical lines with a newline and indentation inside it. A raw substring test
      misses it entirely.
    * **Capitalisation.** The same sentence starts a bullet in one place and mid-
      sentence in another, so a case-sensitive test misses the second.

    Both were live failures of the guard, not hypotheticals. Fixing them means
    lowercasing and collapsing all whitespace runs to single spaces, so a phrase
    matches regardless of where the author happened to break the line.

    What this deliberately does *not* do is strip Markdown syntax. Bold markers
    around part of a phrase would still defeat a match, which is a known limitation
    rather than a solved problem -- see the module docstring. The claim-block guard
    above is the primary defence precisely because it has no such fragility; this
    is a backstop for the prose around it.
    """
    return re.sub(r"\s+", " ", text.lower())


class TestKnownFalseClaimsStayFalse:
    """The two disproven sentences, pinned as never-allowed."""

    def test_no_known_false_claim_appears(self) -> None:
        offenders: list[str] = []
        for phrase, target, why in FORBIDDEN_CLAIMS:
            normalized = _normalized_prose(target.read_text(encoding="utf-8"))
            if _normalized_prose(phrase) in normalized:
                offenders.append(f"\n  {target.relative_to(ROOT)} asserts {phrase!r}\n    {why}")
        assert not offenders, "A claim disproven by the source audit has returned:\n" + "\n".join(offenders)

    def test_the_windows_launcher_limitation_is_still_stated(self) -> None:
        """The limitation that is still true must not be deleted in a reword.

        The supervisor is genuinely not wired into ``start.ps1``. A cleanup pass
        that rewrote the honesty boundary could easily drop it along with the two
        false items, and that would be an *over*-claim -- the failure mode this
        repository cares most about. So it is asserted positively.
        """
        text = _normalized_prose(RUNTIME_AGENTS.read_text(encoding="utf-8"))
        assert "supervisor" in text and "start.ps1" in text, "runtime/AGENTS.md must still say that the process supervisor is not wired into the Windows launcher (start.ps1). Removing that is an over-claim, not a cleanup."


# -- dangling `tests/*.py` references ----------------------------------------

TEST_REFERENCE = re.compile(r"((?:\.{1,2}/|[A-Za-z0-9_.\-]+/)*tests/[A-Za-z0-9_.\-]+\.py)")

# Citations that legitimately name a file which does not exist. Every entry needs
# a reason a reviewer can check; entries without one fail the test. This list is
# the interesting part of the guard: each entry is a document that is *honestly*
# wrong-looking, and removing it would make the documentation lie.
ALLOWED_DANGLING_REFERENCES: dict[tuple[str, str], str] = {
    ("docs/RESEARCH_NAMED_AGENTS.md", "test_hermes_ports.py"): ("Historical: the doc describes commit d7bed8c, which RENAMED backend/tests/test_hermes_ports.py to test_ported_subsystems.py. The old path is the point of the sentence."),
    ("docs/ALPHA_UNIFIED_INTEGRATION_PLAN.md", "test_module_reference_scan.py"): (
        "The document explicitly says this file 'was never created - there is no such file' and names test_no_orphan_modules.py as the guard that does exist. The citation is the honest record of a plan item that was not built."
    ),
    ("references/ALPHA_RSI_IMPLEMENTATION_PLAN.md", "test_rsi_hitl_approval.py"): ("Prospective: references/ holds research and planning documents. This is a DRAFT work plan listing tests to write, not a claim that they exist."),
    ("references/ALPHA_RSI_IMPLEMENTATION_PLAN.md", "test_rsi_promotion_gate.py"): ("Prospective: same DRAFT work plan as test_rsi_hitl_approval.py."),
    ("docs/EXTENSIONS.md", "test_extension.py"): (
        "Walkthrough placeholder: the reader is writing their own extension's tests, so the file cannot exist in this repository. The doc says 'your extension's own tests/test_extension.py' to make that explicit."
    ),
    ("docs/SKILLS.md", "test_my_skill.py"): ("Walkthrough placeholder: the reader's own skill, not a shipped one. Same wording as the EXTENSIONS.md entry."),
    ("docs/DEVELOPMENT.md", "test_my_skill.py"): ("Walkthrough placeholder: step 4 of 'add a skill', in the reader's own tree."),
    ("docs/DEVELOPMENT.md", "test_perf.py"): ("A denial, not a citation: the paragraph says 'There is no tests/test_perf.py'. Naming a nonexistent file to say it does not exist is the corrected form."),
    ("docs/TROUBLESHOOTING.md", "test_perf.py"): ("A denial, not a citation: 'There is no tests/test_perf.py and no backend/scripts/sandbox_memory_profile.py ... in this tree'."),
    ("references/ALPHA_RSI_IMPLEMENTATION_PLAN.md", "test_x.py"): ("Hypothetical input to a protected-paths test plan: test_x.py is an example of a path that MUST be denied, so it is defined by not existing."),
}


def _resolve_reference(ref: str, citing_doc: str) -> Path | None:
    """Resolve a `tests/...` citation the three ways this repository writes them.

    ``tests/foo.py`` in a document means ``backend/tests/foo.py`` here, which is a
    convention rather than a path -- so a naive repo-relative check reports almost
    every legitimate citation as broken. Document-relative is tried too, for the
    documents that live under ``backend/``.
    """
    candidates = [ROOT / ref, ROOT / "backend" / ref]
    doc_dir = (ROOT / citing_doc).parent
    candidates.append(doc_dir / ref)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _markdown_files() -> list[Path]:
    return sorted(path for path in ROOT.rglob("*.md") if not any(part in SKIP_DIRS for part in path.parts))


def _unresolved_references() -> list[tuple[str, str]]:
    """Every (document, reference) citation that does not resolve, exemptions ignored."""
    unresolved: list[tuple[str, str]] = []
    for doc in _markdown_files():
        try:
            text = doc.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Non-UTF-8 markdown is a separate defect, reported where it belongs.
            # Reporting it here too would double-count one problem as two.
            continue
        relative = doc.relative_to(ROOT).as_posix()
        for match in TEST_REFERENCE.finditer(text):
            ref = match.group(1)
            if "mnt/user-data" in ref:
                continue  # a sandbox path inside an example payload, not a citation
            if _resolve_reference(ref, relative) is None:
                unresolved.append((relative, ref))
    return unresolved


def _dangling_references() -> list[tuple[str, str]]:
    """Unresolved citations that are not deliberately exempt."""
    return [(doc, ref) for doc, ref in _unresolved_references() if (doc, ref.rsplit("/", 1)[-1]) not in ALLOWED_DANGLING_REFERENCES]


class TestNoDanglingTestReferences:
    """A document must not point a reader at a test file that is not there.

    Nineteen such citations existed across six documents, and the reason nothing
    caught them is that there was no check at all. Unlike the honesty claims above,
    this guard *is* durable: a file path is a fact about the filesystem, so it
    cannot rot the way prose does. There is no sentence to reword and no
    normalisation to get wrong -- the citation either resolves or it does not.
    """

    def test_every_cited_test_file_exists(self) -> None:
        dangling = _dangling_references()
        assert not dangling, (
            "Documents cite test files that do not exist:\n" + "\n".join(f"  {doc}: {ref}" for doc, ref in dangling) + "\n\nFix the path, or -- if the citation is deliberately historical, "
            "prospective, or a walkthrough placeholder -- add an entry to "
            "ALLOWED_DANGLING_REFERENCES in backend/tests/test_documented_claims.py "
            "with a reason a reviewer can check."
        )

    def test_every_allowlist_entry_is_still_needed(self) -> None:
        """An allowlist that never fires is not a guard; it is decoration.

        Every entry must correspond to a citation that genuinely does not resolve.
        When the cited file is created, or the document is reworded away from it,
        the entry must be deleted -- otherwise the exemption silently outlives its
        justification and starts hiding the next real breakage.
        """
        still_unresolved = _unresolved_references()
        stale: list[str] = []
        for (relative, basename), reason in sorted(ALLOWED_DANGLING_REFERENCES.items()):
            assert reason.strip(), f"allowlist entry {relative}:{basename} has no reason"
            doc = ROOT / relative
            assert doc.is_file(), f"allowlist entry cites a document that does not exist: {relative}"
            # The exemption is still needed only if this basename is still cited
            # from this document AND still fails to resolve.
            if not any(citing == relative and ref.rsplit("/", 1)[-1] == basename for citing, ref in still_unresolved):
                stale.append(f"  {relative}: {basename} -- this exemption no longer matches a real citation")
        assert not stale, "Stale ALLOWED_DANGLING_REFERENCES entries; delete them:\n" + "\n".join(stale)
