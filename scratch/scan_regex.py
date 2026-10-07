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

hits = []
for base in roots:
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = open(p, encoding="utf-8").read()
            except Exception:
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if re.search(r"re\s*\.\s*(match|search|fullmatch)\s*\(", s):
                    hits.append((p, i, s[:160]))

print(f"TOTAL re.match/re.search/re.fullmatch call sites in scope: {len(hits)}")
for p, i, s in hits:
    print(f"{p}:{i}: {s}")
