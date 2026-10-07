"""Proof: the $ anti-pattern must be tested with REAL CRLF bytes."""
import re

# Original pattern (as in egress/policy.py before my fix)
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== ORIGINAL (unfixed) ===")
# Use actual CR and LF characters via chr()
cr = chr(13)
lf = chr(10)
for t in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com"]:
    m = original.match(t)
    print("  input repr:", repr(t))
    print("    match:", repr(m.group(0)) if m else None)

print("\n=== FIXED (fullmatch, unanchored) ===")
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
for t in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com", "ex.com", "EXAMPLE.COM"]:
    m = fixed.fullmatch(t)
    print("  input repr:", repr(t))
    print("    match:", m.group(0) if m else None)

# The real _validate_domain on disk
import importlib.util
spec = importlib.util.spec_from_file_location("ep", "backend/packages/harness/alpha/egress/policy.py")
spec.name = "ep_mod"
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("\n=== real _validate_domain on disk ===")
for d in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com", "ex.com", "EXAMPLE.COM", "b.com", ""]:
    try:
        print("  ACCEPT", repr(d), "->", repr(m._validate_domain(d)))
    except m.EgressValidationError as e:
        print("  REJECT", repr(d), "->", e)
