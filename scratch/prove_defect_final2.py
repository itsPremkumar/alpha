"""REPRODUCE the defect: '$' anchored re.match lets trailing CRLF pass into
a filesystem/path function.

The ORIGINAL pattern from egress/policy.py before the fix this cycle is:
    ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$

This pattern is anchored with $ and matched with re.match. Python's '$' matches
at end-of-string OR before a trailing newline. So an input like "evil.com\r\n"
matches the pattern with the newline still present (the match does NOT consume
the CRLF). If that match result is then passed to a filesystem/path function,
the trailing CRLF rides along as part of the path.

The FIXED pattern is unanchored (no $):
    [a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+
And is matched with .fullmatch(), which requires the ENTIRE string to match,
so any trailing newline or carriage-return is rejected.
"""
import re

# The ORIGINAL pattern, exactly as it was in egress/policy.py before my fix.
# NOTE: "$" here is a literal end-anchor. Python's re.match + "$" allows a
# trailing newline to be "consumed" without matching it, so "evil.com\n" ->
# "evil.com" (newline stripped) and passes to a downstream path function.
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

# FIXED: unanchored, no '$' anchor. Use .fullmatch() so the entire string must
# match; a trailing newline or CRLF is rejected.
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

CR = "\r"
LF = "\n"

def run(pattern, label):
    print(label)
    for t in ["example.com", "evil.com" + CR + LF, "evil.com" + LF, "a-b.example.com", "ex.com"]:
        m = pattern.match(t)
        print("  input", repr(t), "->", repr(m.group(0)) if m else None)

run(original, "=== BEFORE (original) ===")
run(fixed, "=== AFTER (fixed) ===")

# Prove the real function on disk.
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
