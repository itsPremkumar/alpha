"""Direct verification of the _DOMAIN_RE CRLF fix in egress/policy.py."""
import re

# Copy the exact pattern from egress/policy.py (working tree)
_DOMAIN_RE = re.compile(
    r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+"
)

# Print the pattern exactly as it appears in policy.py
print("_DOMAIN_RE.pattern =", repr(_DOMAIN_RE.pattern))
print("_DOMAIN_RE.flags   =", _DOMAIN_RE.flags)

# Test the CRLF case
CRLF_CASES = [
    ("example.com", True),
    ("evil.com\n", False),
    ("evil.com\r\n", False),
    ("a-b.example.com", True),
    ("EXAMPLE.COM\n", False),
    ("example.com\n\n", False),
]

print("\nResults:")
all_pass = True
for domain, should_pass in CRLF_CASES:
    m = _DOMAIN_RE.fullmatch(domain)
    got = m.group(0) if m else None
    expected = should_pass
    status = "PASS" if (got is not None) == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  [{status}] {repr(domain):25s} expected={expected!s:5s} got={got!s:20s}")

print("\nALL PASS" if all_pass else "\nSOME FAILURES")
