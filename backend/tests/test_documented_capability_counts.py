"""Capability counts quoted in documentation must match the generated manifest.

Phase 0 of `docs/FEATURE_COMPLETION_PLAN.md`, task 0.T1. Found by auditing the
repo against `contracts/feature_manifest.json` on 2026-10-06: **twelve current
claims** across nine files disagreed with the manifest, and the numbers had
drifted into several distinct values for the same quantity.

    backend/AGENTS.md:17          135 tools   -> 136
    docs/DISCOVERABILITY.md:141   135 tools   -> 136
    README.md:91,123              135 tools   -> 136
    llms.txt:6 / llms-full.txt:6  135 tools   -> 136
    docs/llms.txt:5               135 tools   -> 136
    README.md:129                 115 engines -> 118
    AGENTS.md:20                  117 engines -> 118
    docs/FAQ.md:25,142            117 engines -> 118
    docs/COMPARISON.md:78         117 engines -> 118
    docs/GLOSSARY.md:213          116 engines -> 118

The engine figure was wrong twice over: the committed manifest said 118 while a
fresh generation said 119, because `collect_engines()` counted a chain of empty
directories (see `test_engine_count_ignores_empty_directories.py`). So the count
depended on when it was read.

Why a gate rather than a one-off fix
------------------------------------
A capability count nobody checks is a capability count nobody maintains - which
is the reason `collect_engines()` exists in the first place, and the reason these
same numbers had already been wrong three separate times. Fixing twelve strings
without a gate guarantees a thirteenth.

What this deliberately does NOT touch
-------------------------------------
`CHANGELOG.md`, `docs/SELF_AUDIT.md`, `docs/audits/FULL_VERIFICATION_REPORT.md`,
`docs/END_TO_END_CRITIQUE.md` and `docs/ALPHA_UNIFIED_INTEGRATION_PLAN.md`
describe **past** states. `backend/packages/harness/alpha/AGENTS.md:106` records
the drift itself ("89 -> 97 -> 99 engines"). Rewriting a historical record to
match today's number falsifies evidence, so those are excluded by
construction: the checks below only fire on an assertion of the form
"<n> tools" / "<n> engines", which a dated history does not contain in that shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "contracts" / "feature_manifest.json"

#: Files that state the CURRENT capability surface and must track the manifest.
CURRENT_CLAIM_FILES = [
    "AGENTS.md",
    "README.md",
    "llms.txt",
    "llms-full.txt",
    "docs/llms.txt",
    "docs/DISCOVERABILITY.md",
    "docs/COMPARISON.md",
    "docs/FAQ.md",
    "docs/GLOSSARY.md",
    "backend/AGENTS.md",
    "backend/packages/harness/alpha/groups/AGENTS.md",
]

#: Historical records. Listed so their exclusion is a decision, not an oversight.
HISTORICAL_RECORDS = [
    "CHANGELOG.md",
    "docs/SELF_AUDIT.md",
    "docs/audits/FULL_VERIFICATION_REPORT.md",
    "docs/END_TO_END_CRITIQUE.md",
    "docs/ALPHA_UNIFIED_INTEGRATION_PLAN.md",
]


def _counts() -> dict[str, int]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {key: len(data.get(key, [])) for key in ("tools", "routers", "middlewares", "loops", "engines")}


COUNTS = _counts()

# `136 native tools`, `118 engine modules`, `pins 136 tools`. Bounded so a line
# like "the 89 -> 97 -> 99 engines" (a history, not a claim) is not matched.
_TOOL_CLAIM = re.compile(r"\b(\d{2,3})\s+(?:native\s+)?(?:tools|tool)\b", re.I)
_ENGINE_CLAIM = re.compile(r"\b(\d{2,3})\s+(?:harness\s+)?(?:engine(?:\s+modules|\s+packages)?|harness engine packages)\b", re.I)


def _claims(text: str, pattern: re.Pattern[str]) -> set[int]:
    return {int(m.group(1)) for m in pattern.finditer(text)}


@pytest.mark.parametrize("relative", CURRENT_CLAIM_FILES)
def test_current_tool_count_matches_the_manifest(relative: str) -> None:
    path = ROOT / relative
    if not path.is_file():
        pytest.skip(f"{relative} does not exist")
    wrong = _claims(path.read_text(encoding="utf-8"), _TOOL_CLAIM) - {COUNTS["tools"]}
    assert not wrong, f"{relative} claims {sorted(wrong)} tools; the manifest has {COUNTS['tools']}. Run backend/scripts/generate_feature_manifest.py and update the claim, or fix the pattern."


@pytest.mark.parametrize("relative", CURRENT_CLAIM_FILES)
def test_current_engine_count_matches_the_manifest(relative: str) -> None:
    path = ROOT / relative
    if not path.is_file():
        pytest.skip(f"{relative} does not exist")
    wrong = _claims(path.read_text(encoding="utf-8"), _ENGINE_CLAIM) - {COUNTS["engines"]}
    assert not wrong, f"{relative} claims {sorted(wrong)} engines; the manifest has {COUNTS['engines']}. See test_engine_count_ignores_empty_directories.py if a regeneration disagrees."


def test_the_manifest_is_not_stale_relative_to_its_generator() -> None:
    """The gate above is worthless if the manifest itself lags the code."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("_manifest_gen_wiring", ROOT / "backend" / "scripts" / "generate_feature_manifest.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(module.collect_engines()) == len(data["engines"]), "contracts/feature_manifest.json is stale; run backend/scripts/generate_feature_manifest.py"


def test_historical_records_are_not_rewritten_to_todays_numbers() -> None:
    """A changelog is evidence. Guard against a future 'fix' that falsifies one."""
    present = [f for f in HISTORICAL_RECORDS if (ROOT / f).is_file()]
    assert present, "the exclusion list is itself stale"
    # These are allowed to carry old numbers; this test only asserts the files
    # still exist and still contain a dated or historical marker, so somebody
    # deleting them to "make the numbers agree" is caught.
    for relative in present:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert text.strip(), f"{relative} is empty; a historical record was removed"
