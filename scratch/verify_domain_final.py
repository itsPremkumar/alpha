#!/usr/bin/env python3
"""Verify the domain anti-pattern fix with the real real module on disk."""
import importlib.util
import sys

# Load the module by file path with a fresh module name so dataclasses resolve.
spec = importlib.util.spec_from_file_location("egress_policy_under_test", "backend/packages/harness/alpha/egress/policy.py")
m = importlib.util.module_from_spec(spec)
# put it in sys.modules so its imports (alpha.*, dataclasses, etc.) resolve
sys.modules["egress_policy_under_test"] = m
spec.loader.exec_module(m)

CR, LF = "\r", "\n"
print("=== real _validate_domain on disk (after fix) ===")
for d in ["example.com", "evil.com" + CR + LF, "evil.com" + LF, "a-b.example.com", "ex.com", "EXAMPLE.COM", "b.com", ""]:
    try:
        print("  ACCEPT", repr(d), "->", repr(m._validate_domain(d)))
    except m.EgressValidationError as e:
        print("  REJECT", repr(d), "->", e)
