import os, re
roots = [
 "backend/packages/harness/alpha/tools",
 "backend/packages/harness/alpha/sandbox",
 "backend/packages/harness/alpha/security",
 "backend/packages/harness/alpha/egress",
 "backend/packages/harness/alpha/policy",
 "backend/packages/harness/alpha/authz",
]
# regex string ending with $ (allowing trailing whitespace) inside r-strings
pat = re.compile(r"r?['\"](.*?)['\"]")
count=0
for root in roots:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1] != ".py": continue
            p = os.path.join(dirpath, fn)
            try:
                lines = open(p, encoding="utf-8", errors="ignore").read().splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                for s in pat.finditer(line):
                    t = s.group(1)
                    # only consider if it looks like a regex (has regex op or is used in re.*)
                    if t.endswith('$') or t.endswith('\$'):
                        # strip possible escaped $
                        raw = t.replace('\$','$')
                        if raw.endswith('$'):
                            count+=1
                            print(f"{p}:{i}: {line.strip()[:200]}  [pat={t[:100]}]")
print("TOTAL", count)
