"""Clean, correct proof using actual CR/LF bytes (chr(13)/chr(10))."""
import re

# Original pattern from egress/policy.py before my fix
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== ORIGINAL (pre-fix) ===")
cr, lf = chr(13), chr(10)
for t in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com"]:
    m = original.match(t)
    print("  input", repr(t), "->", repr(m.group(0)) if m else None)

# Fixed pattern now on disk
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
print("\n=== FIXED ===")
for t in ["example.com", "evil.com" + cr + lf, "evil.com" + lf, "a-b.example.com", "ex.com", "EXAMPLE.COM"]:
    m = fixed.fullmatch(t)
    print("  input", repr(t), "->", m.group(0) if m else None)
