"""Comment-Checker Guard (Lazy-Code Omission Prevention).

Inspired by oh-my-openagent (OmO) comment-checker-core:
Intercepts file-write and patch replacements to prevent models from deleting
critical production code with lazy placeholders such as:
- "// ... rest of code remains unchanged ..."
- "# ... existing code ..."
- "// TODO: implement this later"
"""

from __future__ import annotations

import re


class LazyCommentDetectedError(ValueError):
    """Raised when a code replacement contains lazy omission comments."""
    pass


LAZY_PATTERNS = [
    re.compile(r"(?i)(//|#|/\*|<!--)\s*(\.\.\.|…)?\s*(rest of|existing|previous|remaining|original)\s+(code|implementation|logic|methods|functions|classes)\s*(remains|here|unchanged|goes here|same|as before)", re.MULTILINE),
    re.compile(r"(?i)(//|#|/\*|<!--)\s*(todo|fixme)\s*:\s*(implement|fill|add)\s+(this|later|remaining|here)", re.MULTILINE),
    re.compile(r"(?i)(//|#|/\*)\s*\.\.\.\s*(code unchanged|unchanged)\s*\.\.\.", re.MULTILINE),
    re.compile(r"(?i)^\s*(\.\.\.|…)\s*$", re.MULTILINE),
]


def check_for_lazy_comments(code: str, strict: bool = True) -> list[str]:
    """Scan code for lazy omission patterns.
    
    If strict=True, raises LazyCommentDetectedError on first detection.
    Otherwise returns list of matched pattern descriptions.
    """
    violations: list[str] = []
    for pat in LAZY_PATTERNS:
        matches = pat.findall(code)
        if matches:
            for match in matches:
                if isinstance(match, tuple):
                    match_str = " ".join(str(m) for m in match if m)
                else:
                    match_str = str(match)
                violations.append(f"Detected lazy omission placeholder: '{match_str.strip()}'")

    if strict and violations:
        raise LazyCommentDetectedError(
            f"Write rejected by CommentGuard! Code contains {len(violations)} lazy comment omission(s):\n"
            + "\n".join(f"- {v}" for v in violations)
            + "\nPlease provide the full, unmodified code rather than omitting existing logic."
        )

    return violations
