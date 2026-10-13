"""Probe the run-context trust boundary in services.py directly."""

import re

path = "/c/Users/PREM KUMAR/Videos/alpha/backend/app/gateway/services.py"
src = open(path, encoding="utf-8").read()

# Extract the key definitions
for name in ("_CONTEXT_CONFIGURABLE_KEYS", "_CONTEXT_INTERNAL_CALLER_KEYS", "_CONTEXT_RUNTIME_ONLY_KEYS", "_INTERNAL_ONLY_CONTEXT_KEYS"):
    m = re.search(rf"^{name}: frozenset\[str\] = frozenset\(\{{([^)]*)\}}\)$", src, re.M)
    if m:
        vals = re.findall(r'[\'"]\w+[\'"]', m.group(1))
        vals = [v.strip("'\"") for v in vals]
        print(name, ":", sorted(vals))

# Show the two functions
m = re.search(r"def merge_run_context_overrides.*?(?=\ndef |\Z)", src, re.S)
if m:
    print("\n=== merge_run_context_overrides body ===")
    print(m.group(0)[:1500])

m = re.search(r"def strip_internal_context_keys.*?(?=\n\ndef |\Z)", src, re.S)
if m:
    print("\n=== strip_internal_context_keys body ===")
    print(m.group(0)[:1200])
