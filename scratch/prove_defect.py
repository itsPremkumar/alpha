"""REPRODUCE the actual $.strip()-bypass defect and prove the fix.

The described defect: a domain string with a trailing CRLF passes re.match
against a "..."$ anchor (because $ matches at end-of-string-or-line), so the
string reaches a filesystem/path function with the CRLF still attached.
"""
import re

# ORIGINAL pattern (as in egress/policy.py before the fix this cycle)
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== ORIGINAL (pre-fix) ===")
cr, lf = "\r", "\n"
for t in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com", "ex.com"]:
    m = original.match(t)
    print("  input:", repr(t))
    print("    match group(0):", repr(m.group(0)) if m else None)

# FIXED pattern as on disk now
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
print("\n=== FIXED (unanchored, fullmatch) ===")
for t in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com", "ex.com", "EXAMPLE.COM"]:
    m = fixed.fullmatch(t)
    print("  input:", repr(t))
    print("    match:", m.group(0) if m else None)
