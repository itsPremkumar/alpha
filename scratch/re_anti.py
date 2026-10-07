import os
import re

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
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"re\.(match|search|fullmatch|findall)\(", line):
                if re.search(r"\\\$$", line) or "MULTILINE" in line or "re.M" in line:
                    hits.append((p, i, line.strip()))
for h in hits:
    print(h)
print("---count---", len(hits))
