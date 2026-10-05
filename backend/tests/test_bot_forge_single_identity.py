"""A forged Bot must present exactly one identity.

`alpha.bots.forge._retitle` exists to enforce "One identity per Bot": it
rewrites the persona's first heading to name *this* Bot and strips a stale
"You are <Other>" line. Both of those were correct. What it did not do was
notice a persona that states its own identity heading **twice**.

Observed live on 2026-10-05. The agent was asked to forge a new specialist; it
supplied a SOUL that carried its `# SOUL.md - <name> (<role>)` opener twice, and
the forged profile came back as::

    # SOUL.md - Capability-curator (Capability Surface & Prompt Steward)

    # SOUL.md - Capability-curator (Capability Surface & Prompt Steward)
    1. Trigger & description quality. ...

`_retitle` retitled the *first* heading and passed every later one through
verbatim, so a duplicate survived intact.

This is not cosmetic. The duplicated identity is in the prompt the Bot reads on
turn one, and it teaches the model that its own name is a value it may assert
repeatedly -- the same failure mode as the stale "You are <Other>" heading the
guard already existed to prevent, arriving through the other door. The irony
that this particular Bot's declared job is detecting SOUL duplication is
recorded in `docs/audits/BOT_FORGE_VERIFICATION.md`; the defect is not about
whether one Bot notices it.

The negative-controlled cases are what make this test worth having: the fix must
collapse a repeat **without** deleting a legitimate sub-section, and must still
rewrite a heading that names a *different* Bot rather than discarding it.
"""

from __future__ import annotations

import pytest

from alpha.bots.forge import _retitle, build_guardrail_soul

# The exact text the live forge produced, before the fix.
LIVE_DUPLICATE = (
    "# SOUL.md - capability-curator (Capability Surface & Prompt Steward)\n"
    "\n"
    "# SOUL.md - capability-curator (Capability Surface & Prompt Steward)\n"
    "1. Trigger & description quality. Audit descriptions.\n"
    "2. Trigger-collision detection. Find overlaps.\n"
)


def test_a_duplicated_identity_heading_is_collapsed() -> None:
    """The regression: one identity, not two verbatim copies."""
    out = _retitle(LIVE_DUPLICATE, "capability-curator", "Capability Surface & Prompt Steward")

    assert out.count("# SOUL.md - capability-curator") == 1, f"the duplicate identity heading survived:\n{out!r}"
    # And the body must still be there -- this is a dedupe, not a truncation.
    assert "Trigger & description quality" in out
    assert "Trigger-collision detection" in out


def test_the_collapsed_form_reads_as_one_document() -> None:
    """The surviving heading must be separated from the body, not glued to it."""
    out = _retitle(LIVE_DUPLICATE, "capability-curator", "Capability Surface & Prompt Steward")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0].startswith("# SOUL.md - capability-curator")
    assert lines[1].startswith("1. Trigger"), f"body was glued to the heading: {lines[:2]}"


def test_three_repeats_collapse_to_one() -> None:
    soul = "# SOUL.md - x (R)\n\n# SOUL.md - x (R)\n\n# SOUL.md - x (R)\nbody line\n"
    out = _retitle(soul, "x", "R")
    assert out.count("# SOUL.md - x") == 1
    assert "body line" in out


def test_a_legitimate_sub_section_is_never_removed() -> None:
    """The fix must not become a general heading-destroyer.

    ``build_guardrail_soul`` appends `## Ask first`, `## Escalate to` and
    `## Where you run`; a dedupe that ate those would silently remove the
    approvals the forge exists to write into the SOUL.
    """
    soul = build_guardrail_soul(
        "capability-curator",
        "Capability Surface & Prompt Steward",
        reports_to="architect",
        sandbox="none",
        base_soul="# SOUL.md - capability-curator (Capability Surface & Prompt Steward)\n\n## Ask first\nnothing destructive\n",
    )
    for marker in ("## Ask first", "## Escalate to", "## Where you run"):
        assert marker in soul, f"{marker} was destroyed by the identity dedupe"
    assert soul.count("# SOUL.md - capability-curator") == 1


def test_a_sub_section_named_after_the_bot_is_still_kept() -> None:
    """Deduping is exact-match on the identity opener, not "contains the name".

    A Bot may legitimately own a section headed with its own name; deleting it
    would lose content the author wrote on purpose.
    """
    soul = "# SOUL.md - auditor (R)\n\n## Auditor escalation ladder\nstep 1\n"
    out = _retitle(soul, "auditor", "R")
    assert "## Auditor escalation ladder" in out


def test_a_heading_naming_another_bot_is_rewritten_not_discarded() -> None:
    """The original single-identity guard must still do its job."""
    out = _retitle("# SOUL.md - coder (Software Engineer)\n\nreal body\n", "secops", "Security Engineer")
    assert "# SOUL.md - Secops (Security Engineer)" in out
    assert "coder" not in out.splitlines()[0].lower()
    assert "real body" in out


def test_a_soul_with_no_heading_still_gets_exactly_one() -> None:
    out = _retitle("just prose, no heading\n", "fresh-bot", "Some Role")
    assert out.count("# SOUL.md - Fresh-bot (Some Role)") == 1
    assert "just prose, no heading" in out


def test_the_guard_converges_and_never_edits_content_twice() -> None:
    """Repeated application must reach a fixed point without touching content.

    Not byte-idempotence: `_retitle` normalizes the trailing newline away
    (``splitlines()`` + ``join``), so the first pass and the second differ by
    that single character forever. Asserting byte-equality here would be
    asserting a property the function has never had and does not need; what
    matters is that it stops changing the *document* after one pass.
    """
    once = _retitle("body only\n", "auditor", "R")
    twice = _retitle(once, "auditor", "R")
    thrice = _retitle(twice, "auditor", "R")
    assert twice == thrice, "the guard is still editing the document on every pass"
    assert once.rstrip("\n") == twice.rstrip("\n")
    assert once.count("# SOUL.md - Auditor (R)") == 1


def test_a_repeat_separated_by_prose_still_collapses() -> None:
    """The duplicate is not necessarily adjacent.

    A model that restates its identity after a paragraph is the same defect.
    """
    soul = "# SOUL.md - x (R)\n\npara one\n\n# SOUL.md - x (R)\n\npara two\n"
    out = _retitle(soul, "x", "R")
    assert out.count("# SOUL.md - x") == 1
    assert "para one" in out and "para two" in out


def test_content_is_never_lost_measurably() -> None:
    """Belt and braces: the dedupe removes headings, never prose."""
    soul = "# SOUL.md - x (R)\n\n# SOUL.md - x (R)\n" + "\n".join(f"line {i}" for i in range(40))
    out = _retitle(soul, "x", "R")
    for i in range(40):
        assert f"line {i}" in out, f"prose line {i} was lost"


@pytest.mark.parametrize("name", ["x", "capability-curator", "a-bot-with-a-long-name"])
def test_repeat_detection_is_anchored_on_the_whole_name(name: str) -> None:
    """A prefix match would eat a *different* bot's heading.

    `x` must not match `x2`'s heading; that is how a dedupe turns into deletion.
    """
    soul = f"# SOUL.md - {name} (R)\n\n# SOUL.md - {name}2 (R)\nbody\n"
    out = _retitle(soul, name, "R")
    assert out.count(f"# SOUL.md - {name} ") == 1
    assert f"# SOUL.md - {name}2" in out, "a different bot's heading was deleted"
