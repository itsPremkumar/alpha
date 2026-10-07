import re

# The bug to demonstrate: re.match against a pattern anchored with $ lets a
# trailing newline/CR pass validation into a filesystem/path function.
# Pre-fix pattern (as it existed in egress/policy.py at the start of this cycle).
pre = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== PRE-FIX PATTERN (re.match + anchor $) ===")
for t in ["example.com", "evil.com\n", "evil.com\r\n", "a-b.example.com", "ex.com"]:
    m = pre.match(t)
    print("input", repr(t), "->", repr(m.group(0)) if m else None)

# Fixed pattern: unanchored, use .fullmatch (or rely on the caller's strip/lower).
post = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

print("\n=== POST-FIX (unanchored fullmatch) ===")
for t in ["example.com", "evil.com\n", "evil.com\r\n", "a-b.example.com", "ex.com"]:
    m = post.fullmatch(t)
    print("input", repr(t), "->", m.group(0) if m else None)
