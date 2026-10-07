"""CRLF-byte-perfect proof of the domain anti-pattern fix in egress/policy.py."""
import re

# Original pattern (as it existed before the fix in this cycle)
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== ORIGINAL (pre-fix) pattern, re.match + $ anchor ===")
cr, lf = chr(13), chr(10)
for t in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com", "ex.com"]:
    m = original.match(t)
    print("  input", repr(t), "->", repr(m.group(0)) if m else None)

# Fixed pattern as it now lives in the on-disk file
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
print("\n=== FIXED pattern (unanchored, fullmatch) ===")
for t in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com", "ex.com", "EXAMPLE.COM"]:
    m = fixed.fullmatch(t)
    print("  input", repr(t), "->", m.group(0) if m else None)
