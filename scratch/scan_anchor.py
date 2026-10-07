import os, re, fnmatch
roots = [
 "backend/packages/harness/alpha/tools","backend/packages/harness/alpha/sandbox",
 "backend/packages/harness/alpha/security","backend/packages/harness/alpha/safety",
 "backend/packages/harness/alpha/egress","backend/packages/harness/alpha/policy",
 "backend/packages/harness/alpha/guardrails","backend/packages/harness/alpha/authz",
 "backend/packages/harness/alpha/computer_use","backend/app/gateway/auth"
]
pat = re.compile(r're\.(match|search|split|fullmatch|findall|finditer)\(')
hits = []
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
                if pat.search(line):
                    s = line.strip()
                    if '$' in s or 're.MULTILINE' in s or s.startswith('^'):
                        hits.append((p, i, s[:180]))
for p, i, s in hits:
    print(f"{p}:{i}: {s}")
print("TOTAL", len(hits))
