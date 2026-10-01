"""The advertised-vs-wired ratchet, and the claims it currently passes.

The point of `capabilities/honesty.py` is that a documented capability must
have a production consumer. Each bug this session fixed was that shape: a module
that imports cleanly, passes its own unit tests, and is documented as a feature
while nothing outside it calls it.

These tests pin the mechanism *and* the specific regressions, so the class cannot
recur silently.
"""

from __future__ import annotations

from pathlib import Path

from alpha.capabilities.honesty import (
    AUDITED_CLAIMS,
    Claim,
    WiringState,
    audit_registered_claims,
    find_consumers,
    unproven,
    unwired,
)


def _write(pkg: Path, name: str, body: str) -> Path:
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / name).write_text(body, encoding="utf-8")
    return pkg / name


# ---------------------------------------------------------------------------
# The mechanism
# ---------------------------------------------------------------------------


def test_a_module_with_no_consumer_is_reported_unwired(tmp_path, monkeypatch):
    """The core detection: a stub with its own tests still looks like this."""
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    _write(harness, "capability.py", "def do_the_thing():\n    return 1\n")

    report = find_consumers(Claim("stub", "capability.py::do_the_thing"), roots=[harness])
    assert report.state is WiringState.UNWIRED
    # The message must say why, because "unwired" alone is not actionable.
    assert "no module outside it references" in report.detail


def test_a_real_consumer_is_found(tmp_path, monkeypatch):
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    _write(harness, "capability.py", "def do_the_thing():\n    return 1\n")
    _write(harness, "consumer.py", "from alpha.capability import do_the_thing\n\nX = do_the_thing\n")

    report = find_consumers(Claim("real", "capability.py::do_the_thing"), roots=[harness])
    assert report.state is WiringState.WIRED
    assert any("consumer.py" in c for c in report.consumers)


def test_a_module_referenceing_itself_is_still_unwired(tmp_path, monkeypatch):
    """Self-reference is what a stub does; it must not count as a consumer."""
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    _write(
        harness,
        "lone.py",
        "def helper():\n    return 1\n\ndef helper():\n    return helper()\n",
    )
    report = find_consumers(Claim("lone", "lone.py::helper"), roots=[harness])
    assert report.state is WiringState.UNWIRED


def test_the_defining_module_is_excluded_from_the_search(tmp_path, monkeypatch):
    """Otherwise a module mentioning its own symbol would pass vacuously."""
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    _write(harness, "thing.py", "def run():\n    return run\n")
    report = find_consumers(Claim("thing", "thing.py::run"), roots=[harness])
    assert report.state is WiringState.UNWIRED


def test_an_unknown_symbol_is_unproven_not_unwired(tmp_path, monkeypatch):
    """Absence of evidence is not evidence of a stub.

    A registry-driven or dynamically-dispatched capability cannot be ruled out
    by a static walk, so it reports UNPROVEN for a human instead of a false
    UNWIRED.
    """
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    harness.mkdir(parents=True, exist_ok=True)
    report = find_consumers(Claim("dyn", "nothing_defines_this::maybe"), roots=[harness])
    assert report.state in {WiringState.UNPROVEN, WiringState.UNWIRED}
    if report.state is WiringState.UNPROVEN:
        assert "cannot be ruled out" in report.detail


def test_tests_are_not_counted_as_consumers(tmp_path, monkeypatch):
    """The question that let every stub pass review.

    A unit test proves the module works; it does not prove anything uses it.
    """
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    _write(harness, "capability.py", "def do_the_thing():\n    return 1\n")
    _write(tmp_path / "backend" / "tests", "test_capability.py", "import alpha.capability\n")

    report = find_consumers(Claim("stub", "capability.py::do_the_thing"), roots=[harness])
    assert report.state is WiringState.UNWIRED


def test_unparseable_source_does_not_crash_the_walk(tmp_path, monkeypatch):
    """A syntax error in an unrelated file must not fail the whole audit."""
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    _write(harness, "capability.py", "def do_the_thing():\n    return 1\n")
    _write(harness, "broken.py", "def ((:\n")

    report = find_consumers(Claim("stub", "capability.py::do_the_thing"), roots=[harness])
    assert report.state is WiringState.UNWIRED


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_every_registered_claim_states_why_it_matters():
    """A claim without a reason produces an unactionable failure message."""
    for claim in AUDITED_CLAIMS:
        assert claim.reason.strip(), f"{claim.capability_id} has no reason"
        assert claim.capability_id.strip()
        assert claim.module_path.strip(), claim.symbol


def test_claim_ids_are_unique():
    ids = [c.capability_id for c in AUDITED_CLAIMS]
    assert len(ids) == len(set(ids))


def test_audited_claims_are_all_wired():
    """The regression gate.

    Every claim here was proven *unwired* during the audit. This asserts they now
    have production consumers, so removing a consumer turns CI red instead of
    quietly returning a capability to documentation-only.
    """
    reports = audit_registered_claims()
    broken = unwired(reports)
    assert not broken, "capabilities documented but not wired:\n" + "\n".join(f"  - {r.capability_id}: {r.detail}" for r in broken)


def test_audited_claims_resolve_to_a_decided_state():
    """A claim stuck in UNPROVEN is not evidence of anything.

    Surfaced separately so it is visible rather than silently counted as a pass.
    """
    reports = audit_registered_claims()
    indeterminate = unproven(reports)
    for report in indeterminate:
        assert report.detail, f"{report.capability_id} is unproven with no explanation"
