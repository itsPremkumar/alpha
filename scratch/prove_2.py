"""Proof that a $ anchored re.match lets trailing CRLF pass."""
import re

original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== BEFORE (original, with $) ===")
for t in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com"]:
    m = original.match(t)
    print("  input", repr(t), "->", repr(m.group(0)) if m else None)

fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
print("\n=== AFTER (fixed, fullmatch) ===")
for t in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com", "ex.com", "EXAMPLE.COM"]:
    m = fixed.fullmatch(t)
    print("  input", repr(t), "->", m.group(0) if m else None)
