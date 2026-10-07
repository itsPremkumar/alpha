import re

# PROPER reproduction of the real defect: actual CRLF bytes, not literal \r\n text.
# re.match against a pattern anchored with $ lets a trailing newline/CR pass
# validation into a filesystem/path function (the previous cycle's discovered bug).
broken = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

print("=== OLD (broken) pattern ===")
for t in [b"example.com", b"evil.com\r\n", b"evil.com\n", b"a-b.example.com"]:
    s = t.decode("latin1")  # raw CRLF bytes as actual characters
    m = broken.match(s)
    print("input ", repr(t), "| match returns:", repr(m.group(0)) if m else None)

_fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
print("\n=== FIXED (fullmatch, unanchored) ===")
for t in [b"example.com", b"evil.com\r\n", b"evil.com\n", b"a-b.example.com", b"ex.com", b"EXAMPLE.COM"]:
    s = t.decode("latin1")
    m = _fixed.fullmatch(s)
    print("input ", repr(t), "| match returns:", m.group(0) if m else None)
