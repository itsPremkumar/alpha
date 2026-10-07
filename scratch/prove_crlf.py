import re

# 1) The OLD bug: re.match against a pattern anchored with $ lets a trailing
#    CRLF pass validation and reach a filesystem/path function.
broken = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")
for t in [b"example.com", b"evil.com\r\n", b"evil.com\n", b"a-b.example.com"]:
    s = t.decode("latin1")
    m = broken.match(s)
    print("BROKEN", repr(t), "->", repr(m.group(0)) if m else None)

# 2) The FIXED code in egress/policy.py is now re.compile (no anchor);
#    _validate_domain already does .strip().lower() before the check.
_fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
for t in [b"example.com", b"evil.com\r\n", b"evil.com\n", b"a-b.example.com", b"ex.com", b"EXAMPLE.COM"]:
    s = t.decode("latin1")
    m = _fixed.fullmatch(s)
    print("FIXED", repr(t), "->", m.group(0) if m else None)
