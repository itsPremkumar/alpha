import re

# Read the FIXED source of the module directly to prove the on-disk state.
# The real defect: re.match against a pattern anchored with $ lets trailing
# newline/CR pass validation into a filesystem path.
fix_src = open("backend/packages/harness/alpha/egress/policy.py", encoding="utf-8").read()
i = fix_src.find("_DOMAIN_RE")
print("ON-DISK PATTERN:", repr(fix_src[i:i+80]))

# Now reproduce the EXACT BUG with a pattern that still uses $ + re.match.
broken = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")
for t in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com"]:
    # re.match stops at the $ anchor without consuming the trailing newline -> matches
    m = broken.match(t)
    print("BROKEN", repr(t), "->", repr(m.group(0)) if m else None)

# The fixed module uses re.compile (no anchor) + the caller's .strip()/.lower() via _validate_domain.
_fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
for t in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com"]:
    m = _fixed.fullmatch(t)
    print("FIXED", repr(t), "->", m.group(0) if m else None)
