"""Probe the run-context trust boundary in `services.py` directly.

Hand-run diagnostic, never imported at runtime: the module body performs no
filesystem work, so importing every router module stays side-effect free.
"""

import re
from pathlib import Path

SERVICES_PATH = Path(__file__).resolve().parent.parent / "services.py"


def main() -> None:
    src = SERVICES_PATH.read_text(encoding="utf-8")

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


if __name__ == "__main__":
    main()
