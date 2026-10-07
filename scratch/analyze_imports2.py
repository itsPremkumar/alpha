import os, re, sys

base = "C:/Users/PREM KUMAR/Videos/alpha/backend"

def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None

keywords = re.compile(r"memory_recall|alpha\.agents\.memory|alpha\.memory\.recall|from alpha\.memory import from alpha\.agents\.memory")
roots = [
 "app/gateway",
 "app/agent",
 "packages/harness/alpha",
]
for root, dirs, files in os.walk(base):
    rel = os.path.relpath(root, base)
    if any(rel == r or rel.startswith(r + os.sep) for r in roots):
        pass
    if any(x in root.split(os.sep) for x in ("tests", ".venv", "__pycache__", ".git", "node_modules")):
        continue
    for fn in files:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(root, fn)
        txt = read(p)
        if txt is None:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            if keywords.search(line):
                print(f"{p}:{i}: {line.strip()[:110]}")
