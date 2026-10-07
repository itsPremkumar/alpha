import os

hits = []
for dirpath, _, fns in os.walk("."):
    for fn in fns:
        if fn.endswith(".pyc"):
            continue
        p = os.path.join(dirpath, fn)
        try:
            src = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        lines = src.splitlines()
        for i, line in enumerate(lines, 1):
            low = line.lower()
            if "re.match" in low or "re.search" in low or "re.fullmatch" in low:
                if "re.M" in line or "multiline" in low or "$" in line:
                    hits.append((p, i, line.strip()))
for h in hits:
    print(h)
print("---count---", len(hits))
