import os, re, sys

base = "C:/Users/PREM KUMAR/Videos/alpha/backend"

def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None

print("### PROD IMPORTERS OF alpha.memory / memory_recall")
pat = re.compile(r"(alpha\.memory|alpha\.agents\.memory|memory_recall)")
for root, dirs, files in os.walk(base):
    if any(x in root.split(os.sep) for x in ("tests", ".venv", "__pycache__", ".git")):
        continue
    for fn in files:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(root, fn)
        txt = read(p)
        if txt is None:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            if pat.search(line):
                print(f"{p}:{i}: {line.strip()[:120]}")
