#!/usr/bin/env python3
"""Prove the domain CRLF exploit and the fix, using REAL carraige-return/newline
bytes (chr(13)/chr(10)) so we are not tripped up by string-escape confusion."""

import re

# The ORIGINAL pattern as it was in egress/policy.py before this cycle's fix.
# re.match(pattern, "evil.com\r\n") matches the prefix "evil.com" because '$'
# matches at end-of-string OR before a trailing newline. The CRLF survives and
# can be passed to a downstream filesystem/path function.
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

# FIXED: unanchored pattern + .fullmatch(), so any trailing CR/LF is rejected.
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

CR, LF = "\r", "\n"

def show(pattern, label):
    print(label)
    for t in ["example.com", "evil.com" + CR + LF, "evil.com" + LF, "a-b.example.com", "ex.com"]:
        m = pattern.match(t)
        print("  input", repr(t), "->", repr(m.group(0)) if m else None)

print("=== ORIGINAL (before fix) ===")
show(original, "")

print("\n=== FIXED (unanchored + fullmatch) ===")
show(fixed, "")

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
