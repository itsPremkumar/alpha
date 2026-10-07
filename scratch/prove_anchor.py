import re

# Original defect (reproduced here independently)
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")
print("BEFORE evil.com\r\n ->", _DOMAIN_RE.match("evil.com\r\n").group(0) if _DOMAIN_RE.match("evil.com\r\n") else None)

# Fixed version
_FIXED = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
for t in ["example.com", "evil.com\n", "evil.com\r\n", "a-b.example.com", "ex.com"]:
    m = _FIXED.fullmatch(t)
    print("AFTER  ", repr(t), "->", m.group(0) if m else None)
