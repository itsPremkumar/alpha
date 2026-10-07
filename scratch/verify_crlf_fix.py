"""
Verification script for the _DOMAIN_RE CRLF fix in egress/policy.py.

The bug: _DOMAIN_RE was compiled with a $-anchored pattern and validated
with re.match(). In Python, $ matches at end-of-string OR before a trailing
newline. So "evil.com\r\n" matched and passed validation even though the
input contained CRLF.

The fix: replace the $-anchored re.match with a plain fullmatch() - the
pattern has no trailing $, so fullmatch requires the entire input to match.
"""
import re
import sys

# Load the actual policy module from the repo
sys.path.insert(0, "backend/packages")
sys.path.insert(0, "backend/packages/harness/alpha")

from alpha.packages.harness.alpha.egress import policy

# Cases that test the CRLF boundary
CRLF_CASES = [
    ("example.com", True),      # valid, should pass
    ("evil.com\n", False),      # CRLF trailing newline -> should FAIL
    ("evil.com\r\n", False),    # CRLF -> should FAIL
    ("a-b.example.com", True),  # valid hyphenated subdomain
    ("EXAMPLE.COM\n", False),   # mixed case + CRLF
    ("example.com\n\n", False), # double newline
    ("x", False),               # single char - no TLD
    ("example..com", False),    # double dot
    ("localhost", False),       # no TLD
    ("123.45.67.89", False),    # numeric IP - not a domain here
]

print("Testing _validate_domain (the fix):")
all_passed = True
for domain, should_pass in CRLF_CASES:
    try:
        result = policy._validate_domain(domain)
        passed = True
        detail = repr(result)
    except policy.EgressValidationError as e:
        passed = False
        detail = f"EgressValidationError: {e}"
    expected = should_pass
    status = "PASS" if passed == expected else "FAIL"
    if passed != expected:
        all_passed = False
    print(f"  [{status}] {repr(domain):25s} expected={expected!s:5s} got={passed!s:5s} -> {detail}")

print()
print("ALL PASS" if all_passed else "SOME FAILURES")
sys.exit(0 if all_passed else 1)
