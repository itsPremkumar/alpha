"""End-to-end verification of the egress domain anti-pattern fix.

Reads the FIXED source straight off disk and proves that a CRLF-bearing domain
rejected by _validate_domain. Also demonstrates the pre-fix defect with a
reproduced broken pattern.
"""
import re

# 1) The PRE-FIX pattern (reproduced from the previous cycle's finding):
#    re.match against "..."$ lets a trailing newline/CR pass through.
pre = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== PRE-FIX pattern      ===")
for t in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com"]:
    m = pre.match(t)
    print("  input", repr(t), "->", repr(m.group(0)) if m else None)

# 2) The CURRENT on-disk module source, to show the pattern we fixed.
src = open("backend/packages/harness/alpha/egress/policy.py", encoding="utf-8").read()
i = src.find("_DOMAIN_RE")
on_disk = src[i:i + 90]
print("\non-disk pattern:", repr(on_disk))

# 3) The FIXED pattern (unanchored + use of the caller's .strip()/.lower()).
post = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

print("\n=== POST-FIX (fullmatch) ===")
for t in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com", "ex.com", "EXAMPLE.COM"]:
    m = post.fullmatch(t)
    print("  input", repr(t), "->", m.group(0) if m else None)

# 4) Actually import the module's _validate_domain and feed raw CRLF.
import importlib.util
spec = importlib.util.spec_from_file_location("ep", "backend/packages/harness/alpha/egress/policy.py")
# give the module a real name so dataclass metadata resolves
spec.name = "ep_mod"
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("\n=== _validate_domain on disk (real module) ===")
for d in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com", "ex.com", "EXAMPLE.COM", "b.com", ""]:
    try:
        print("  ACCEPT", repr(d), "->", repr(m._validate_domain(d)))
    except m.EgressValidationError as e:
        print("  REJECT", repr(d), "->", e)
