import os, subprocess, sys
import re

# Probe the exact patterns in the codebase
PROBS = {
    "computer_use env CRLF": (re.compile(r"(?i)\.env(?:\.local)?$"), "/.env.local\r"),
    "computer_use env LF":    (re.compile(r"(?i)\.env(?:\.local)?$"), "/.env.local\n"),
    "computer_use env clean": (re.compile(r"(?i)\.env(?:\.local)?$"), "/.env.local"),
    "tool_shell_cmd_sep":     (re.compile(r"^(?P<force>\+)?(?P<src>[^:]+):(?P<dst>.+)$"), "main:+new:main"),
    "bash exit marker CRLF":  (re.compile(r"(?:\nExit Code: -?\d+|\n?Command exited with code -?\d+)\s*$"), "\nExit Code: 0\r"),
}

for name, (pat, s) in PROBS.items():
    m = pat.search(s)
    print(f"{name}: {bool(m)} (input={s!r})")

# The _ENV_NAME_PATTERN in sandbox/path_patterns.py
import re
ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
print("ENV fullmatch CRLF:", bool(ENV.fullmatch("A=b\r")))
