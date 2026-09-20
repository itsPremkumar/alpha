"""Skill authoring standards: the ``/learn`` prompt builder and draft validator.

One prompt turns whatever the operator describes (a code dir, a doc URL,
"what we just did", pasted notes) into a reusable skill that follows the
house authoring bar. The validator enforces the same bar on drafts before
they enter the proposal queue: frontmatter shape, a short capability-first
description, the required body sections, and a verification step.
"""

from __future__ import annotations

import re

MAX_DESCRIPTION_CHARS = 60
MAX_NAME_CHARS = 64
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MARKETING_WORDS = ("powerful", "comprehensive", "seamless", "advanced", "robust", "cutting-edge", "revolutionary", "ultimate")

REQUIRED_SECTIONS = ("When to Use", "Prerequisites", "How to Run", "Procedure", "Pitfalls", "Verification")


def build_learn_prompt(source_description: str) -> str:
    """Build the ONE prompt that turns a source into an authoring turn."""
    source = (source_description or "").strip() or "what we just did"
    return (
        "Turn the following into a reusable skill following the house skill-authoring standards.\n"
        "Gather sources with your existing tools (read_file, search_files, web_extract), then author the skill.\n\n"
        f"Source: {source}\n\n"
        "Frontmatter rules: name is lowercase-hyphenated (<=64 chars, no spaces); description is ONE sentence "
        f"(<=60 chars, ends with a period) stating the capability, not the implementation — no marketing words "
        f"({', '.join(_MARKETING_WORDS)}); version starts at 0.1.0; never copy identity (author, username, host) "
        "from the environment into the skill.\n"
        "Body section order: title + 2-3 sentence intro (what it does, what it does NOT do) > When to Use "
        "(concrete trigger phrases) > Prerequisites (exact env vars, installs, credentials) > How to Run "
        "(canonical invocation) > Quick Reference (flat command list) > Procedure (numbered, copy-paste-exact) "
        "> Pitfalls (limits, rate limits, false alarms) > Verification (one command proving it worked).\n"
        "Quality bar: prefer exact commands, URLs, signatures, and config keys seen VERBATIM in the source — "
        "never invent flags, paths, or APIs. Frame actions through agent tools (read_file not cat, search_files "
        "not grep, patch not sed). End by stating the skill name, the 60-char description with its character "
        "count, and the verification command."
    )


def validate_skill_draft(name: str, description: str, markdown: str) -> list[str]:
    """Return findings (empty = passes the bar). Pure function, no I/O."""
    findings: list[str] = []
    if not _NAME_RE.match(name or ""):
        findings.append(f"name '{name}' must be lowercase-hyphenated, <=64 chars, no spaces.")
    desc = (description or "").strip()
    if len(desc) > MAX_DESCRIPTION_CHARS:
        findings.append(f"description is {len(desc)} chars; must be <={MAX_DESCRIPTION_CHARS}.")
    if desc and not desc.endswith("."):
        findings.append("description must end with a period.")
    lowered_desc = desc.lower()
    for word in _MARKETING_WORDS:
        if word in lowered_desc:
            findings.append(f"description uses marketing word '{word}'.")
            break
    body = markdown or ""
    lowered_body = body.lower()
    for section in REQUIRED_SECTIONS:
        if section.lower() not in lowered_body:
            findings.append(f"body is missing section '{section}'.")
    if "verification" in lowered_body:
        after = lowered_body.split("verification", 1)[1][:800]
        if "`" not in after and "$" not in after and "http" not in after:
            findings.append("Verification section has no concrete command to run.")
    if re.search(r"(?i)\b(api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,})", body):
        findings.append("body appears to contain a hardcoded secret; use Prerequisites with env vars instead.")
    return findings
