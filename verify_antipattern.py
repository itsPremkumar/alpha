import re

# Re-verify the $-anchored anti-pattern precisely (no imports, no subprocess).
name_cr = "skill\r"
name_lf = "skill\n"
name_blank = "skill \r\n"

pat = re.compile(r"^[a-z0-9-]+$")

cases = [
    name_cr, name_lf, name_blank,
    "alpha\r", "alpha\n", "alpha \r\n",
    "valid-skill", "skill\\r",
]
print("== re.match(r'^[a-z0-9-]+$', name) on stripped-vs-crlf ==")
for s in cases:
    m = pat.match(s)
    print(f"  {s!r:16} match={m is not None} group={m.group(0) if m else None}")

print()
print("== skills/validation.py actual flow: strip then re.match ==")
name = "alpha\r"
name = name.strip()
print("  after strip:", repr(name), "re.match:", bool(pat.match(name)))

print()
print("== re.search with $ anchor (no MULTILINE) -- worst case -- ==")
print("  search('skill\\n'):", bool(re.search(r"^[a-z0-9-]+$", "skill\n")))
print("  search('skill\\r'):", bool(re.search(r"^[a-z0-9-]+$", "skill\r")))
print("  search('skill '):", bool(re.search(r"^[a-z0-9-]+$", "skill ")))
