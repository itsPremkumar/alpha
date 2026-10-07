#!/usr/bin/env python3
"""Verify the computer_use.py forbidden-pattern '$' anchor with actual CRLF.

The pattern r"\.env(?:\\.local)?$" is anchored with '$' and matched with
re.search(). A command like "cat .env\n" would match the '$' anchor and be
classified as FORBIDDEN, but only because of the '$' line-anchor behavior.
"""
import re

pattern = re.compile(r"(?i)\.env(?:\.local)?$")
cr, lf = "\r", "\n"

print("=== patterns ending in $ matched with re.search ===")
for t in ["cat .env", "cat .env" + lf, "cat .env" + cr + lf, "rm -rf /tmp", "ls -la", "git push --force", "echo 'x'", "cat .env.local" + lf, "vim .env"]:
    m = pattern.search(t)
    print("  input:", repr(t), "->", "MATCH" if m else "no match")

# The $ anchor in re.search means: match at end of string OR before a trailing
# newline. So "cat .env\n" matches as ".env" at the end (before the \n).
print("\nDemonstration of '$' line-anchor semantics:")
for t in ["cat .env", "cat .env\n", "cat .env\r\n"]:
    m = pattern.search(t)
    print("  input", repr(t), "->", "match group:", repr(m.group(0)) if m else None)
