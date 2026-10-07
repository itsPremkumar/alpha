import re

# ---- 1. sandbox/tools.py:1177 ``$`` lookahead: does it catch cd/pushd via substitution? ----
pattern = re.compile(r"\$\([^)]*\b(?:cd|pushd)\b")
tests = [
    "cd $(pwd)",            # looks like it should be caught
    "cd $(cd /tmp)",        # nested cd - should it be caught?
    "cd $(goto C:\\)\\x",   # Windows goto trick (prior report's example)
    "echo $(cd)",           # cd alone, no arg
    "cd $(echo hi)",        # substitution containing cd but not as cmd name
    "pushd $(pwd)",         # pushd variant
    "ls $(cd ..)",          # cd inside, already the command, not cd/pushd itself
]
for cmd in tests:
    print(f"  {cmd!r:40} matched={bool(pattern.search(cmd))}")

print()
# ---- 2. The re-match anti-pattern again, directly from the file (skills/validation.py:61) ----
src = open("backend/packages/harness/alpha/skills/validation.py", encoding="utf-8").read()
lines = src.splitlines()
for i, ln in enumerate(lines, 1):
    if "re.match" in ln:
        print(f"skills/validation.py:{i}: {ln.strip()}")
        m = re.match(r"^[a-z0-9-]+$", "skill\r")
        print(f"   re.match(r'^[a-z0-9-]+$', 'skill\\r') -> {m is not None} (group={m.group(0) if m else None})")
        m2 = re.match(r"^[a-z0-9-]+$", "skill\n")
        print(f"   re.match(r'^[a-z0-9-]+$', 'skill\\n') -> {m2 is not None} (group={m2.group(0) if m2 else None})")
