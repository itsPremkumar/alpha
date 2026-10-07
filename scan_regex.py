import os, re

base = "backend/packages/harness/alpha"
scopes = ["tools", "sandbox", "security", "safety", "guardrails", "authz", "egress", "policy", "computer_use"]
roots = [os.path.join(base, s) for s in scopes] + ["backend/app/gateway/auth"]

file_list = []
for r in roots:
    for dirpath, dirnames, filenames in os.walk(r):
        if any(t in dirpath.split(os.sep) for t in ("tests", "node_modules", ".git", "venv", "__pycache__")):
            continue
        for f in filenames:
            if f.endswith(".py"):
                file_list.append(os.path.join(dirpath, f))

print("TOTAL_FILES", len(file_list))

pat = re.compile(r"re\.(match|search)\(\s*(r|u|ur|b|br)?['\"]([^'\"]*)\$([^'\"]*)?['\"]")
hits = []
for fp in sorted(file_list):
    try:
        with open(fp, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if "re.match" in line or "re.search" in line:
                    m = pat.search(line)
                    if m:
                        hits.append((fp, i, line.rstrip()))
    except Exception:
        pass

print("HITS", len(hits))
for fp, i, line in hits:
    print(f"{fp}:{i}: {line}")
