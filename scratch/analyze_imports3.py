import os, re

base = "C:/Users/PREM KUMAR/Videos/alpha/backend"

roots = [
    "app/gateway",
    "packages/harness/alpha/agents",
    "packages/harness/alpha/middlewares",
    "packages/harness/alpha/helpers",
    "packages/harness/alpha/harness",
    "packages/harness/alpha/memory",
    "packages/harness/alpha/cognitive",
    "packages/harness/alpha/learning",
    "packages/harness/alpha/continuous",
    "packages/harness/alpha/continual",
    "packages/harness/alpha/retrieval",
    "packages/harness/alpha/persistence",
]
kw = re.compile(r"memory_recall|alpha\.agents\.memory|alpha\.memory\.recall|from alpha\.memory import|from alpha\.agents\.memory import|alpha\.memory\.cognitive|alpha\.memory\.kibitzer|alpha\.memory\.session|alpha\.memory\.dreaming|alpha\.memory\.prospective|alpha\.memory\.narrative|alpha\.memory\.entities|alpha\.memory\.affective|alpha\.memory\.codebase|alpha\.memory\.fusion|alpha\.memory\.policy|alpha\.memory\.social|alpha\.memory\.utility|alpha\.memory\.health|alpha\.memory\.scenarios")
count = 0
for root, dirs, files in os.walk(base):
    rel = os.path.relpath(root, base)
    if not any(rel == r or rel.startswith(r + os.sep) for r in roots):
        continue
    if any(x in root.split(os.sep) for x in ("tests", ".venv", "__pycache__", ".git", "node_modules")):
        continue
    for fn in sorted(files):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(root, fn)
        try:
            txt = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            if kw.search(line):
                print(f"{p}:{i}: {line.strip()[:110]}")
                count += 1
print("TOTAL", count)
