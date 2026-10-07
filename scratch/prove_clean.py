"""Clean proof of the domain $-anchored regex anti-pattern fix.

The defect (as reported in the prior cycle): a pattern anchored with $ via
re.match lets a trailing CR/LF pass validation into a filesystem/path function.
"""
import re

# The ORIGINAL pattern as it lived in egress/policy.py before the fix.
# re.match stops matching at the position of the $ anchor when the input has a
# trailing newline or carriage-return+newline -> the newline/CR is stripped.
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== ORIGINAL (pre-fix), re.match + pattern anchored with $ ===")
for t in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com"]:
    m = original.match(t)
    print("  input", repr(t), "-> matched:", repr(m.group(0)) if m else None)

# The FIXED pattern in egress/policy.py: unanchored, no $, so fullmatch is
# strict. Also show what _validate_domain does with .strip().lower().
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

print("\n=== FIXED (fullmatch) ===")
for t in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com", "ex.com", "EXAMPLE.COM"]:
    m = fixed.fullmatch(t)
    print("  input", repr(t), "->", m.group(0) if m else None)
