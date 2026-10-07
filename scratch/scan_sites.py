import os, re
roots = [
 "backend/packages/harness/alpha/tools","backend/packages/harness/alpha/sandbox",
 "backend/packages/harness/alpha/security","backend/packages/harness/alpha/safety",
 "backend/packages/harness/alpha/egress","backend/packages/harness/alpha/policy",
 "backend/packages/harness/alpha/guardrails","backend/packages/harness/alpha/authz",
 "backend/packages/harness/alpha/computer_use","backend/app/gateway/auth"
]
# find re.match( and re.search( call sites
sites = []
for root in roots:
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in ("__pycache__",)]
        for fn in fns:
            if not fn.endswith(".py"): continue
            p = os.path.join(dp, fn)
            try:
                txt = open(p, encoding="utf-8").read()
            except Exception:
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                for m in re.finditer(r're\.(match|search)\s*\(', line):
                    sites.append((p, i, m.group(1), line.strip()))
for p, i, fn, s in sites:
    print(f"{p}:{i} [{fn}] {s[:150]}")
print("TOTAL_SITES", len(sites))
