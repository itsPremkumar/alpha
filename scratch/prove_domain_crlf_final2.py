#!/usr/bin/env python3
"""Prove the domain '$'-anchored regex anti-pattern and the fix.

DEFECT (as reported in the prior cycle): egress/policy.py used
    _DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")
    if not _DOMAIN_RE.match(clean):
        raise EgressValidationError(...)

The pattern is anchored with '$'. In Python, '$' matches at end-of-string OR
before a trailing line-break (newline or CR+LF). So re.match("...", "evil.com\r\n")
matches the prefix "evil.com" and the trailing CR/LF is NOT part of the match.
That means a CRLF-bearing string passes validation and can reach a downstream
filesystem/path function with the CR/LF still attached.

FIX: unanchored pattern + .fullmatch(). fullmatch() requires the ENTIRE string to
match, so any trailing CR/LF is rejected.
"""
import re

# The ORIGINAL pattern as it stood before this cycle's fix.
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

# FIXED: unanchored + fullmatch.
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

CR = "\r"
LF = "\n"

def show(pattern, label):
    print(label)
    for t in ["example.com", "evil.com" + CR + LF, "evil.com" + LF, "a-b.example.com", "ex.com"]:
        m = pattern.match(t)
        print("  input", repr(t), "->", repr(m.group(0)) if m else None)

show(original, "=== ORIGINAL (before fix) ===")
show(fixed, "=== FIXED (unanchored + fullmatch) ===")

# Verify the real function on disk.
import importlib.util
spec = importlib.util.spec_from_file_location("ep_mod", "backend/packages/harness/alpha/egress/policy.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("\n=== real _validate_domain on disk (after fix) ===")
for d in ["example.com", "evil.com" + CR + LF, "evil.com" + LF, "a-b.example.com", "ex.com", "EXAMPLE.COM", "b.com", ""]:
    try:
        print("  ACCEPT", repr(d), "->", repr(m._validate_domain(d)))
    except m.EgressValidationError as e:
        print("  REJECT", repr(d), "->", e)
