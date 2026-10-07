import re

# Re-verify the known anti-pattern precisely for THIS scope: re.match against a pattern
# anchored with ^...$ still lets a trailing \r/\n pass (no MULTILINE needed).
name = "skill\r"
pat = re.compile(r"^[a-z0-9-]+$")
print("validation.py line 61 pattern", bool(pat.match(name)), "matches:", pat.match(name) is not None)

# fullmatch behaves correctly
print("fullmatch None:", re.fullmatch(r"[a-z0-9-]+", name) is None)

# re.search with $ anchor (no MULTILINE)
print("search with $", bool(re.search(r"^[a-z0-9-]+$", "skill\r")))

# re.match with MULTILINE
print("match MULTILINE", bool(re.match(r"^[a-z0-9-]+$", "skill\r", re.MULTILINE)))

# The sandbox tools.py line 1177 check
cmd = "cd $(goto C:\\)\\x"
print("sandbox tools.py 1177 match for cd in $(...) with non-path token:",
      bool(re.search(r"\$\([^)]*\b(?:cd|pushd)\b", cmd)))
cmd2 = "cd $(pwd)"
print("sandbox tools.py 1177 match for cd in $(...) with path-ish token:",
      bool(re.search(r"\$\([^)]*\b(?:cd|pushd)\b", cmd2)))
