import os
import re

base = "C:/Users/PREM KUMAR/Videos/alpha"
scopes = [
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
argent = re.compile(r'\b(?:re\.match|re\.search)\s*\(\s*["\'].*[$]', re.M)
shelltrue = re.compile(r"\bshell\s*=\s*True")
bad_deser = re.compile(r"\b(?:eval|exec|compile)\s*\(|\bloads?\s*\(|\byaml\.load\s*\(")
bad_sub = re.compile(r"\bos\.system\b|\bsubprocess\.(?:call|run|Popen|check_output|check_call)\s*\([^)]*shell\s*=[^)]*True")

out = []
for s in scopes:
    p = os.path.join(base, s)
    if not os.path.isdir(p):
        out.append("MISSING " + p)
        continue
    for fn in sorted(os.listdir(p)):
        if not fn.endswith(".py"):
            continue
        fp = os.path.join(p, fn)
        try:
            data = open(fp, encoding="utf-8").read()
        except Exception as e:
            out.append("READERR " + fp + ": " + str(e))
            continue
        lines = data.split("\n")
        hits = []
        for i, ln in enumerate(lines, 1):
            if argent.search(ln):
                hits.append(("ARGENT", ln.strip()[:120]))
            if shelltrue.search(ln):
                hits.append(("SHELL", ln.strip()[:120]))
            if bad_deser.search(ln):
                hits.append(("DESER", ln.strip()[:120]))
            if bad_sub.search(ln):
                hits.append(("SBSUB", ln.strip()[:120]))
        if hits:
            out.append("== " + s.replace("backend/", "") + "/" + fn + " (" + str(len(hits)) + ")")
            for h in hits:
                out.append("  " + h[0] + " : " + h[1])

with open("C:/Users/PREM KUMAR/AppData/Local/hermes/profiles/alpha-security/cache/scratch/scan_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("\n".join(out))
