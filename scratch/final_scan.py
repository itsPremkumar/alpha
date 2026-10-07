import os, re

roots = [
    "backend/packages/harness/alpha/tools",
    "backend/packages/harness/alpha/sandbox",
    "backend/packages/harness/alpha/security",
    "backend/packages/harness/alpha/safety",
    "backend/packages/harness/alpha/guardrails",
    "backend/packages/harness/alpha/authz",
    "backend/packages/harness/alpha/egress",
    "backend/packages/harness/alpha/policy",
    "backend/packages/harness/alpha/computer_use",
    "backend/app/gateway/auth",
]

results = []
for base in roots:
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                lines = open(p, encoding="utf-8").read().splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                # re.match or re.search used with a pattern anchored with $
                if re.search(r're\s*\.\s*(match|search)\s*\(', s):
                    # show the line
                    results.append((p, i, s[:180]))

print("re.match/re.search call sites:", len(results))
for p, i, s in results:
    print(f"{p}:{i}: {s}")
